// Experimental passive Gemini web request parser. Never emits prompt text.
const ENDPOINT =
  "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate";
const MAX_BODY_CHARS = 65536;

export function matchGeminiStreamUrl(raw) {
  try {
    const url = new URL(String(raw), "https://gemini.google.com");
    return (
      url.protocol === "https:" &&
      url.hostname === "gemini.google.com" &&
      !url.port &&
      !url.username &&
      !url.password &&
      url.pathname === ENDPOINT
    );
  } catch {
    return false;
  }
}

export function parseGeminiRequest(bodyText) {
  if (typeof bodyText !== "string" || bodyText.length > MAX_BODY_CHARS) return null;
  try {
    const encoded = new URLSearchParams(bodyText).get("f.req");
    if (!encoded) return null;
    const outer = JSON.parse(encoded);
    if (!Array.isArray(outer) || typeof outer[1] !== "string") return null;
    const inner = JSON.parse(outer[1]);
    // Public Gemini-API source documents StreamGenerate f.req as
    // [null, JSON.stringify(innerReq)], with innerReq[0][0] = prompt text.
    const prompt = inner?.[0]?.[0];
    if (!Array.isArray(inner) || typeof prompt !== "string" || prompt.length === 0) return null;
    return { promptChars: prompt.length };
  } catch {
    return null;
  }
}

export function createGeminiCapture({ emit, now = Date.now }) {
  const seenIds = new Set();
  return {
    onRequest(id, url, method, bodyText) {
      if (String(method || "GET").toUpperCase() !== "POST") return false;
      if (!matchGeminiStreamUrl(url)) return false;
      if (seenIds.has(id)) return false;
      const request = parseGeminiRequest(bodyText);
      if (!request) return false;
      seenIds.add(id);
      if (seenIds.size > 1000) seenIds.delete(seenIds.values().next().value);
      emit({
        nm: "usage",
        payload: {
          provider: "gemini",
          model: "gemini-unknown",
          prompt_chars: request.promptChars,
          context_chars: request.promptChars,
          ts: now(),
        },
      });
      return true;
    },
    onResponse() {},
    onChunk() {},
    onEnd() {},
  };
}
