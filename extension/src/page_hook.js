// MAIN world, classic. Sem logica de dominio: so repassa dados crus via postMessage.
(function () {
  if (window.__nmHooked) return;
  window.__nmHooked = true;
  const orig = window.fetch;
  let seq = 0;
  let wrappedFetchDepth = 0;
  const post = (o) => {
    try {
      o.__nm = 1;
      window.postMessage(o, "*");
    } catch (e) {}
  };

  const wanted = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (url.protocol !== "https:" || url.hostname !== location.hostname ||
          url.port || url.username || url.password) return false;
      if (location.hostname === "claude.ai") return /\/completion$/.test(url.pathname);
      if (location.hostname === "chatgpt.com") return /^\/backend-api\/(?:f\/)?conversation$/.test(url.pathname);
      if (location.hostname === "gemini.google.com") {
        return url.pathname === "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate";
      }
      return false;
    } catch { return false; }
  };

  const MAX_BODY_BYTES = 65536;
  const MAX_STREAM_BYTES = 2 * 1024 * 1024;
  function truncateUtf8(text, limit) {
    if (typeof text !== "string") return "";
    let result = "";
    let bytes = 0;
    for (const char of text) {
      const size = new TextEncoder().encode(char).byteLength;
      if (bytes + size > limit) break;
      result += char;
      bytes += size;
    }
    return result;
  }
  async function bodyText(value) {
    try {
      let text;
      if (typeof value === "string") {
        if (value.length > MAX_BODY_BYTES) return null;
        text = value;
      } else if (value instanceof URLSearchParams) {
        text = value.toString();
        if (text.length > MAX_BODY_BYTES) return null;
      } else if (typeof Blob !== "undefined" && value instanceof Blob) {
        if (value.size > MAX_BODY_BYTES) return null;
        text = await value.text();
      } else if (value instanceof ArrayBuffer) {
        if (value.byteLength > MAX_BODY_BYTES) return null;
        text = new TextDecoder().decode(value);
      } else if (ArrayBuffer.isView(value)) {
        if (value.byteLength > MAX_BODY_BYTES) return null;
        text = new TextDecoder().decode(value);
      } else return null;
      return new TextEncoder().encode(text).byteLength <= MAX_BODY_BYTES ? text : null;
    } catch { return null; }
  }

  async function requestBody(input, init) {
    if (init && Object.prototype.hasOwnProperty.call(init, "body")) return bodyText(init.body);
    if (typeof Request !== "undefined" && input instanceof Request && input.method !== "GET" && input.method !== "HEAD") {
      try {
        const clone = input.clone();
        const length = Number(clone.headers.get("content-length") || 0);
        if (length > MAX_BODY_BYTES) return null;
        const reader = clone.body?.getReader();
        if (!reader) return null;
        const decoder = new TextDecoder();
        let text = "";
        let size = 0;
        for (;;) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > MAX_BODY_BYTES) { await reader.cancel(); return null; }
          text += decoder.decode(part.value, { stream: true });
        }
        text += decoder.decode();
        return text;
      } catch { return null; }
    }
    return null;
  }

  async function boundedResponseText(resp) {
    let reader;
    try {
      reader = resp.clone().body?.getReader();
      if (!reader) return "";
      const chunks = [];
      let size = 0;
      for (;;) {
        const item = await reader.read();
        if (item.done) break;
        size += item.value.byteLength;
        if (size > MAX_BODY_BYTES) { await reader.cancel(); return ""; }
        chunks.push(item.value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      return new TextDecoder().decode(bytes);
    } catch {
      try { await reader?.cancel(); } catch (e) {}
      return "";
    }
  }

  function geminiMetrics(rawBody) {
    if (typeof rawBody !== "string" || rawBody.length > MAX_BODY_BYTES) return null;
    try {
      const outer = JSON.parse(new URLSearchParams(rawBody).get("f.req") || "null");
      if (!Array.isArray(outer) || typeof outer[1] !== "string") return null;
      const inner = JSON.parse(outer[1]);
      const prompt = inner?.[0]?.[0];
      if (!Array.isArray(inner) || typeof prompt !== "string" || prompt.length === 0) return null;
      // Never pass prompt, f.req, at token, or original body beyond MAIN world.
      return { prompt_chars: prompt.length, context_chars: prompt.length };
    } catch { return null; }
  }

  function postGeminiUsage(id, url, method, body) {
    if (String(method).toUpperCase() !== "POST" || !wanted(url)) return;
    const metrics = geminiMetrics(body);
    if (!metrics) return;
    post({ k: "geminiUsage", id, ...metrics, ts: Date.now() });
  }

  const usageIdentity = (raw, base) => {
    try {
      const url = new URL(raw, base);
      const match = /^\/api\/organizations\/([^/]{1,256})\/usage$/.exec(url.pathname);
      if (url.protocol !== "https:" || url.hostname !== "claude.ai" || url.port || url.username || url.password || !match) return null;
      return { url, orgId: match[1] };
    } catch { return null; }
  };

  async function readBoundedJson(resp) {
    let reader;
    try {
      const type = resp.headers.get("content-type") || "";
      const lengthHeader = resp.headers.get("content-length");
      const length = lengthHeader === null ? 0 : Number(lengthHeader);
      if (!resp.ok || !/^application\/(?:[a-z0-9.+-]*\+)?json(?:\s*;|$)/i.test(type) ||
          (lengthHeader !== null && (!Number.isFinite(length) || length > 65536))) return null;
      reader = resp.clone().body?.getReader();
      if (!reader) return null;
      const chunks = [];
      let size = 0;
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.byteLength;
        if (size > 65536) { await reader.cancel(); return null; }
        chunks.push(part.value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      return new TextDecoder().decode(bytes);
    } catch {
      try { await reader?.cancel(); } catch (e) {}
      return null;
    }
  }

  async function pump(id, resp) {
    try {
      if (resp.status >= 400) {
        post({ k: "resp", id, status: resp.status, body: await boundedResponseText(resp) });
        return;
      }
      post({ k: "resp", id, status: resp.status });
      const c = resp.clone();
      if (!c.body) return;
      const reader = c.body.getReader();
      const dec = new TextDecoder();
      let size = 0;
      for (;;) {
        const r = await reader.read();
        if (r.done) break;
        size += r.value.byteLength;
        if (size > MAX_STREAM_BYTES) {
          await reader.cancel();
          post({ k: "end", id });
          return;
        }
        post({ k: "chunk", id, text: dec.decode(r.value, { stream: true }) });
      }
      const tail = dec.decode();
      if (tail) post({ k: "chunk", id, text: tail });
      post({ k: "end", id });
    } catch (e) {}
  }

  window.fetch = function (input, init) {
    let url = "";
    let method = "GET";
    let initialQuota = null;
    let inspectBody = null;
    try {
      url = typeof input === "string" || input instanceof URL ? String(input) : (input && input.url) || String(input);
      method = (init && init.method) || (input && input.method) || "GET";
      initialQuota = typeof method === "string" && method.toUpperCase() === "GET" ? usageIdentity(url, location.href) : null;
      if (typeof method === "string" && method.toUpperCase() === "POST" && wanted(url)) {
        // Clone/read inspectable request body before native fetch may lock it.
        inspectBody = requestBody(input, init);
      }
    } catch (e) {}

    let p;
    wrappedFetchDepth++;
    try { p = orig.apply(this, arguments); }
    finally { wrappedFetchDepth--; }
    try {
      if (inspectBody) p.catch(() => {}); // body normalization can finish after a fast rejection
      if (location.hostname === "claude.ai" && initialQuota) {
        p.then(async (resp) => {
          try {
            const finalQuota = usageIdentity(resp.url, location.href);
            if (!finalQuota || finalQuota.url.origin !== initialQuota.url.origin || finalQuota.orgId !== initialQuota.orgId) return;
            post({ k: "quotaIdentity", orgId: initialQuota.orgId });
            const body = await readBoundedJson(resp);
            if (body !== null) post({ k: "quota", body, orgId: initialQuota.orgId });
          } catch (e) {}
        }, () => {});
      }
      if (inspectBody) {
        inspectBody.then((body) => {
          if (body === null) return;
          if (location.hostname === "gemini.google.com") {
            postGeminiUsage("g" + ++seq, url, method, body);
            return;
          }
          const id = "r" + ++seq;
          post({ k: "req", id, url, method: String(method).toUpperCase(), body });
          p.then((resp) => pump(id, resp), () => {});
        }).catch(() => {});
      }
    } catch (e) {}
    return p;
  };

  if (typeof XMLHttpRequest !== "undefined") {
    const proto = XMLHttpRequest.prototype;
    const open = proto.open;
    const send = proto.send;
    const xhrRequests = new WeakMap();
    proto.open = function (method, url) {
      xhrRequests.set(this, { method: String(method || "GET").toUpperCase(), url: String(url) });
      return open.apply(this, arguments);
    };
    proto.send = function (body) {
      const request = xhrRequests.get(this);
      // Synchronous XHR-backed fetch polyfills are observed by outer fetch hook.
      if (wrappedFetchDepth > 0) return send.apply(this, arguments);
      if (request && request.method === "POST" && wanted(request.url)) {
        const isGemini = location.hostname === "gemini.google.com";
        const state = { id: (isGemini ? "g" : "x") + ++seq, requestSent: false, queued: [], responseLength: 0, size: 0, ended: false };
        const emitXhrChunk = (delta) => {
          if (!delta || state.size > MAX_STREAM_BYTES) return;
          if (delta.length > MAX_STREAM_BYTES) { state.size = MAX_STREAM_BYTES + 1; return; }
          state.size += new TextEncoder().encode(delta).byteLength;
          if (state.size <= MAX_STREAM_BYTES) post({ k: "chunk", id: state.id, text: delta });
        };
        const emitWhenReady = (event) => {
          if (!state.requestSent) state.queued.push(event);
          else event();
        };
        const flush = () => {
          state.requestSent = true;
          for (const event of state.queued.splice(0)) event();
        };
        if (!isGemini) {
          try {
            this.addEventListener("progress", () => emitWhenReady(() => {
              try {
                if (this.responseType && this.responseType !== "text") return;
                if (this.status >= 400) return;
                const current = this.responseText;
                if (typeof current !== "string" || current.length < state.responseLength) return;
                const delta = current.slice(state.responseLength);
                state.responseLength = current.length;
                emitXhrChunk(delta);
              } catch (e) {}
            }));
            const finish = (networkError = false) => emitWhenReady(() => {
              if (state.ended) return;
              state.ended = true;
              let status = 0;
              let errorBody = "";
              try {
                status = networkError ? 0 : this.status;
                if (status >= 400 && (!this.responseType || this.responseType === "text")) {
                  errorBody = truncateUtf8(this.responseText, MAX_BODY_BYTES);
                }
                else if (status >= 400 && this.responseType === "json" && this.response && typeof this.response === "object") {
                  errorBody = truncateUtf8(JSON.stringify(this.response), MAX_BODY_BYTES);
                }
              } catch (e) {}
              post({ k: "resp", id: state.id, status, ...(errorBody ? { body: errorBody } : {}) });
              if (status < 400) {
                try {
                  if (!this.responseType || this.responseType === "text") {
                    const current = this.responseText;
                    if (typeof current === "string" && current.length >= state.responseLength) {
                      const delta = current.slice(state.responseLength);
                      state.responseLength = current.length;
                      emitXhrChunk(delta);
                    }
                  }
                } catch (e) {}
              }
              post({ k: "end", id: state.id });
            });
            this.addEventListener("loadend", () => finish(false));
            this.addEventListener("error", () => finish(true));
            this.addEventListener("abort", () => finish(true));
          } catch (e) {}
        }
        bodyText(body).then((text) => {
          if (text === null) { state.ended = true; state.queued.length = 0; return; }
          if (isGemini) postGeminiUsage(state.id, request.url, request.method, text);
          else post({ k: "req", id: state.id, url: request.url, method: request.method, body: text });
          flush();
        }).catch(() => { state.ended = true; state.queued.length = 0; });
      }
      return send.apply(this, arguments);
    };
  }
})();
