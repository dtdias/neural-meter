// Whitelist/sanitização de mensagens content script -> SW -> servidor (pura).
const PROVIDERS = new Set(["claude", "chatgpt"]);
const isStr = (v, max) => typeof v === "string" && v.length > 0 && v.length <= max;
const isCount = (v) => Number.isInteger(v) && v >= 0;

const PROVIDER_HOSTS = { claude: "claude.ai", chatgpt: "chatgpt.com" };

export function providerMatchesSender(provider, url) {
  try {
    const host = PROVIDER_HOSTS[provider];
    if (!host || typeof url !== "string") return false;
    const u = new URL(url);
    return u.protocol === "https:" && u.hostname === host && !u.port && !u.username && !u.password;
  } catch {
    return false;
  }
}

export function sanitizeQuotaCandidate(msg, senderUrl) {
  try {
    const p = msg && msg.payload;
    if (msg?.nm !== "quotaCandidate" || !providerMatchesSender("claude", senderUrl)) return null;
    if (!p || typeof p.orgId !== "string" || !p.orgId || p.orgId.length > 256 ||
        (p.body !== null && (typeof p.body !== "string" || new TextEncoder().encode(p.body).byteLength > 65536))) return null;
    return { orgId: p.orgId, body: p.body };
  } catch {
    return null;
  }
}

export function sanitizeBridgeMessage(msg) {
  try {
    if (!msg || typeof msg !== "object") return null;
    const p = msg.payload;
    if (!p || typeof p !== "object" || !PROVIDERS.has(p.provider)) return null;
    if (msg.nm === "usage") {
      if (!isStr(p.model, 100)) return null;
      if (!isCount(p.prompt_chars) || !isCount(p.context_chars)) return null;
      const out = {
        provider: p.provider,
        model: p.model,
        prompt_chars: p.prompt_chars,
        context_chars: p.context_chars,
      };
      if (p.conversation_id !== undefined) {
        if (!isStr(p.conversation_id, 100)) return null;
        out.conversation_id = p.conversation_id;
      }
      if (p.ts !== undefined) {
        if (typeof p.ts !== "number" || !Number.isFinite(p.ts)) return null;
        out.ts = p.ts;
      }
      return { type: "usage", ...out };
    }
    if (msg.nm === "limit") {
      if (p.scope !== "5h" && p.scope !== "weekly") return null;
      if (!Number.isInteger(p.reset_at) || p.reset_at <= 0) return null;
      const out = { type: "limit", provider: p.provider, scope: p.scope, reset_at: p.reset_at };
      if (p.rate_limited !== undefined) {
        if (typeof p.rate_limited !== "boolean") return null;
        out.rate_limited = p.rate_limited;
      }
      if (isStr(p.model, 100)) out.model = p.model; // inválido: só descarta o campo
      return out;
    }
    if (msg.nm === "quota") {
      if (p.provider !== "claude" || !/^[0-9a-f]{64}$/.test(p.account_hash || "") || !["5h", "weekly", "opus", "sonnet"].includes(p.window)) return null;
      if ((["5h", "weekly"].includes(p.window) && p.model !== undefined) || ((p.window === "opus" || p.window === "sonnet") && p.model !== p.window)) return null;
      if (typeof p.percent !== "number" || !Number.isFinite(p.percent) || p.percent < 0 || p.percent > 100) return null;
      if (!Number.isSafeInteger(p.observed_at) || p.observed_at <= 0) return null;
      if (p.reset_at !== null && p.reset_at !== undefined && (!Number.isSafeInteger(p.reset_at) || p.reset_at <= p.observed_at)) return null;
      if (p.model !== undefined && !["opus", "sonnet"].includes(p.model)) return null;
      return { type: "quota", provider: "claude", account_hash: p.account_hash, window: p.window, percent: p.percent, observed_at: p.observed_at, ...(p.reset_at ? { reset_at: p.reset_at } : {}), ...(p.model ? { model: p.model } : {}) };
    }
    return null;
  } catch {
    return null;
  }
}
