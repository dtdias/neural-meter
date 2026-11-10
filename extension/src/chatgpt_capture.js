// Captura passiva chatgpt.com (pura). Nunca emite texto de prompt/resposta.
import { createSseParser } from "./sse.js";

const URL_RE = /^\/backend-api\/(f\/)?conversation$/;
const LIMIT_CODES = new Set(["model_cap_exceeded", "rate_limit_exceeded"]);
const KNOWN_TYPES = new Set([
  "message_stream_complete",
  "conversation_detail_metadata",
  "title_generation",
  "server_ced",
  "input_message",
  "message_marker",
  "moderation",
  "url_moderation",
  "resume_conversation_token",
  "message_stream_error",
]);

export function matchChatgptConversationUrl(url) {
  try {
    const u = new URL(String(url), "https://chatgpt.com");
    return URL_RE.test(u.pathname);
  } catch {
    return false;
  }
}

function toResetMs(v, nowMs) {
  if (typeof v === "number" && Number.isFinite(v) && v > 0) {
    return Math.round(v > 1e12 ? v : v * 1000);
  }
  if (typeof v === "string" && v) {
    const t = Date.parse(v);
    if (Number.isFinite(t) && t > 0) return t;
  }
  return null;
}

export function createChatgptCapture({ emit, tracker, onUnknown = () => {}, now = Date.now }) {
  const pending = new Map();
  const seenUnknown = new Set();

  function unknownOnce(type, info) {
    if (seenUnknown.has(type)) return;
    seenUnknown.add(type);
    onUnknown(type, info);
  }

  function applyOp(p, o, path, v) {
    if (typeof v !== "string") return;
    const isPath = typeof path === "string" && path.includes("/message/content/parts");
    if (o === "append" || isPath || (o === undefined && path === undefined)) {
      p.respChars += v.length;
    }
  }

  function legacy(p, msg) {
    const m = msg && msg.message;
    if (!m || !m.author || m.author.role !== "assistant") return;
    const parts = m.content && m.content.parts;
    if (Array.isArray(parts) && typeof parts[0] === "string") {
      p.respChars = Math.max(p.respChars, parts[0].length);
    }
  }

  function handleEvents(p, events) {
    for (const ev of events) {
      if (ev.data === "[DONE]") continue;
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch {
        continue;
      }
      if (!data || typeof data !== "object") continue;
      if (!p.conv && typeof data.conversation_id === "string" && data.conversation_id) {
        p.conv = data.conversation_id;
      }
      if (typeof data.type === "string" && !KNOWN_TYPES.has(data.type)) {
        unknownOnce(data.type, {});
      }
      if (data.message) {
        legacy(p, data);
      } else if ("v" in data) {
        const v = data.v;
        if (Array.isArray(v)) {
          for (const op of v) {
            if (op && typeof op === "object") applyOp(p, op.o, op.p, op.v);
          }
        } else if (v && typeof v === "object") {
          if (v.message) legacy(p, v);
        } else {
          applyOp(p, data.o, data.p, v);
        }
      }
    }
  }

  return {
    onRequest(id, url, method, bodyText) {
      if (String(method || "GET").toUpperCase() !== "POST") return;
      if (!matchChatgptConversationUrl(url)) return;
      let body;
      try {
        body = JSON.parse(bodyText);
        if (!body || typeof body !== "object") throw new Error("body");
      } catch {
        onUnknown("invalid_request_body", {});
        return;
      }
      let promptChars = 0;
      if (Array.isArray(body.messages)) {
        for (const m of body.messages) {
          if (!m || !m.author || m.author.role !== "user") continue;
          const parts = m.content && m.content.parts;
          if (!Array.isArray(parts)) continue;
          for (const part of parts) {
            if (typeof part === "string") promptChars += part.length;
          }
        }
      }
      const model =
        typeof body.model === "string" && body.model && body.model !== "auto"
          ? body.model
          : "gpt-unknown";
      const conv =
        typeof body.conversation_id === "string" && body.conversation_id
          ? body.conversation_id
          : null;
      const ctx = (conv ? tracker.get(conv) : 0) + promptChars;
      const payload = { provider: "chatgpt", model };
      if (conv) payload.conversation_id = conv;
      payload.prompt_chars = promptChars;
      payload.context_chars = ctx;
      payload.ts = now();
      emit({ nm: "usage", payload });
      pending.set(id, {
        conv,
        promptChars,
        respChars: 0,
        sse: createSseParser(),
        model: model === "gpt-unknown" ? null : model,
      });
    },

    onResponse(id, status, bodyText) {
      if (typeof status !== "number" || status < 400) return;
      const req = pending.get(id);
      const reqModel = req ? req.model : null;
      pending.delete(id);
      let detail = null;
      try {
        const b = JSON.parse(bodyText);
        detail = b && b.detail;
      } catch {
        // corpo ilegivel
      }
      const code = detail && typeof detail === "object" ? detail.code : undefined;
      if (status !== 429 && !LIMIT_CODES.has(code)) return;
      let reset_at = null;
      if (detail && typeof detail === "object") {
        const c = Number(detail.clears_in);
        if (Number.isFinite(c) && c > 0) reset_at = Math.round(now() + c * 1000);
        else reset_at = toResetMs(detail.reset_at);
      }
      if (reset_at) {
        const payload = { provider: "chatgpt", scope: "5h", reset_at, rate_limited: true };
        if (reqModel) payload.model = reqModel;
        emit({ nm: "limit", payload });
      } else {
        onUnknown("rate_limit_body", {});
      }
    },

    onChunk(id, text) {
      const p = pending.get(id);
      if (!p) return;
      handleEvents(p, p.sse.push(text));
    },

    onEnd(id) {
      const p = pending.get(id);
      if (!p) return;
      handleEvents(p, p.sse.flush());
      if (p.conv) tracker.add(p.conv, p.promptChars + p.respChars);
      pending.delete(id);
    },
  };
}
