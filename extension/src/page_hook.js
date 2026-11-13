// MAIN world, classic. Sem logica de dominio: so repassa dados crus via postMessage.
(function () {
  if (window.__nmHooked) return;
  window.__nmHooked = true;
  const orig = window.fetch;
  let seq = 0;
  const post = (o) => {
    try {
      o.__nm = 1;
      window.postMessage(o, "*");
    } catch (e) {}
  };

  const matchers = {
    "claude.ai": /\/completion$/,
    "chatgpt.com": /\/backend-api\/(f\/)?conversation(\?|$)/,
  };
  const rx = matchers[location.hostname];
  const wanted = (url) => {
    if (!rx) return false;
    return rx.test(rx === matchers["claude.ai"] ? url.split("?")[0].split("#")[0] : url);
  };

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
        post({ k: "resp", id, status: resp.status, body: await resp.clone().text() });
        return;
      }
      post({ k: "resp", id, status: resp.status });
      const c = resp.clone();
      if (!c.body) return;
      const reader = c.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const r = await reader.read();
        if (r.done) break;
        post({ k: "chunk", id, text: dec.decode(r.value, { stream: true }) });
      }
      post({ k: "end", id });
    } catch (e) {}
  }

  window.fetch = function (input, init) {
    const p = orig.apply(this, arguments);
    try {
      // input Request sem init: ignora (nao le body, evita consumir stream)
      const url = typeof input === "string" || input instanceof URL ? String(input) : (input && input.url) || String(input);
      const method = (init && init.method) || (input && input.method) || "GET";
      const initialQuota = typeof method === "string" && method.toUpperCase() === "GET" ? usageIdentity(url, location.href) : null;
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
      if (init && typeof init.body === "string" && wanted(url)) {
        const id = "r" + ++seq;
        post({ k: "req", id, url, method: init.method || "GET", body: init.body });
        p.then((resp) => pump(id, resp), () => {});
      }
    } catch (e) {}
    return p;
  };
})();
