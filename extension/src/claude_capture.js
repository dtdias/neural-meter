// Captura passiva claude.ai (pura). Nunca emite texto de prompt/resposta.
import { createSseParser } from "./sse.js";

const URL_RE =
  /\/api\/organizations\/([^/]+)\/chat_conversations\/([^/]+)\/(?:retry_)?completion$/;

export function matchClaudeCompletionUrl(url) {
  try {
    const path = String(url).split("?")[0].split("#")[0];
    const m = URL_RE.exec(path);
    return m ? { orgId: m[1], conversationId: m[2] } : null;
  } catch {
    return null;
  }
}

function resetMs(sec) {
  const n = Number(sec);
  return Number.isFinite(n) && n > 0 ? Math.round(n * 1000) : null;
}

export function createClaudeCapture({ emit, tracker, onUnknown = () => {}, now = Date.now }) {
  const pending = new Map();
  const seenUnknown = new Set();
  const known = new Set([
    "message_start",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "message_delta",
    "message_stop",
    "message_limit",
    "ping",
  ]);

  function unknownOnce(type, info) {
    if (seenUnknown.has(type)) return;
    seenUnknown.add(type);
    onUnknown(type, info);
  }

  function handleLimit(ml) {
    if (!ml || typeof ml !== "object") return;
    const exceeded = ml.type === "exceeded_limit";
    const windows = ml.windows;
    if (windows && typeof windows === "object" && Object.keys(windows).length) {
      for (const [name, w] of Object.entries(windows)) {
        const reset_at = resetMs(w && w.resets_at);
        if (!reset_at) continue;
        const wExceeded = w.status ? w.status === "exceeded_limit" : exceeded;
        emit({
          nm: "limit",
          payload: {
            provider: "claude",
            scope: name === "7d" ? "weekly" : "5h",
            reset_at,
            rate_limited: wExceeded,
          },
        });
      }
      return;
    }
    const reset_at = resetMs(ml.resetsAt);
    if (!reset_at) return;
    emit({
      nm: "limit",
      payload: { provider: "claude", scope: "5h", reset_at, rate_limited: exceeded },
    });
  }

  return {
    onRequest(id, url, method, bodyText) {
      if (String(method || "GET").toUpperCase() !== "POST") return;
      const m = matchClaudeCompletionUrl(url);
      if (!m) return;
      let body;
      try {
        body = JSON.parse(bodyText);
        if (!body || typeof body !== "object") throw new Error("body");
      } catch {
        onUnknown("invalid_request_body", {});
        return;
      }
      let promptChars = typeof body.prompt === "string" ? body.prompt.length : 0;
      if (Array.isArray(body.attachments)) {
        for (const a of body.attachments) {
          if (a && typeof a.extracted_content === "string") promptChars += a.extracted_content.length;
        }
      }
      const model = typeof body.model === "string" && body.model ? body.model : "claude-unknown";
      const conv = m.conversationId;
      const ctx = tracker.get(conv) + promptChars;
      emit({
        nm: "usage",
        payload: {
          provider: "claude",
          model,
          conversation_id: conv,
          prompt_chars: promptChars,
          context_chars: ctx,
          ts: now(),
        },
      });
      pending.set(id, { conv, promptChars, respChars: 0, sse: createSseParser() });
    },

    onResponse(id, status, bodyText) {
      if (status !== 429) return;
      try {
        const err = JSON.parse(bodyText).error;
        let inner = err && err.message;
        if (typeof inner === "string") inner = JSON.parse(inner);
        const reset_at = resetMs(inner && inner.resetsAt);
        if (!reset_at) throw new Error("no reset");
        emit({
          nm: "limit",
          payload: { provider: "claude", scope: "5h", reset_at, rate_limited: true },
        });
      } catch {
        onUnknown("rate_limit_body", {});
      }
    },

    onChunk(id, text) {
      const p = pending.get(id);
      if (!p) return;
      this._events(p, p.sse.push(text));
    },

    _events(p, events) {
      for (const ev of events) {
        let data;
        try {
          data = JSON.parse(ev.data);
        } catch {
          continue;
        }
        const type = (data && data.type) || ev.event;
        if (type === "content_block_delta") {
          const t = data.delta && data.delta.text;
          if (typeof t === "string") p.respChars += t.length;
        } else if (type === "message_limit") {
          handleLimit(data.message_limit);
        } else if (!known.has(type)) {
          unknownOnce(String(type), {});
        }
      }
    },

    onEnd(id) {
      const p = pending.get(id);
      if (!p) return;
      this._events(p, p.sse.flush());
      tracker.add(p.conv, p.promptChars + p.respChars);
      pending.delete(id);
    },
  };
}
