// Conservative adapter for observed Claude usage JSON; never forwards raw payload.
const WINDOWS = { five_hour: "5h", seven_day: "weekly", seven_day_opus: "opus", seven_day_sonnet: "sonnet" };

export async function hashClaudeAccount(orgId, secret) {
  if (typeof orgId !== "string" || !orgId || !(secret instanceof Uint8Array)) return null;
  const key = await crypto.subtle.importKey("raw", secret, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signed = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(orgId));
  return Array.from(new Uint8Array(signed), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function processClaudeQuotaCandidate(orgId, body, secret, now = Date.now()) {
  const accountHash = await hashClaudeAccount(orgId, secret);
  if (!accountHash) return [];
  const observation = {
    nm: "quotaObservation",
    payload: { provider: "claude", account_hash: accountHash, observed_at: now },
  };
  const quotas = typeof body === "string" ? parseClaudeQuota(body, accountHash, now) : [];
  return [observation, ...quotas];
}

export function parseClaudeQuota(body, accountHash, now = Date.now()) {
  if (!/^[0-9a-f]{64}$/.test(accountHash || "")) return [];
  if (typeof body === "string" && new TextEncoder().encode(body).byteLength > 65536) return [];
  let data;
  try { data = typeof body === "string" ? JSON.parse(body) : body; } catch { return []; }
  if (!data || typeof data !== "object" || Array.isArray(data)) return [];
  const out = [];
  for (const [key, window] of Object.entries(WINDOWS)) {
    const item = data[key];
    if (!item || typeof item !== "object") continue;
    const percent = item.utilization;
    if (typeof percent !== "number" || !Number.isFinite(percent) || percent < 0 || percent > 100) continue;
    let reset_at = null;
    if (item.resets_at !== undefined && item.resets_at !== null) {
      const raw = typeof item.resets_at === "number" ? item.resets_at * 1000 : Date.parse(item.resets_at);
      if (!Number.isFinite(raw) || raw <= now || raw > now + 366 * 86400000) continue;
      reset_at = raw;
    }
    out.push({ nm: "quota", payload: { provider: "claude", account_hash: accountHash, window, model: window === "opus" || window === "sonnet" ? window : undefined, percent, reset_at, observed_at: now } });
  }
  return out;
}
