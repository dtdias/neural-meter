import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeBridgeMessage as s, providerMatchesSender as pms, sanitizeQuotaCandidate, geminiEventToBridgeMessage } from "../src/bridge_msg.js";

test("providerMatchesSender", () => {
  assert.equal(pms("claude", "https://claude.ai/chat/1"), true);
  assert.equal(pms("chatgpt", "https://chatgpt.com/c/1"), true);
  assert.equal(pms("gemini", "https://gemini.google.com/app"), true);
  assert.equal(pms("claude", "https://chatgpt.com/c/1"), false);
  assert.equal(pms("chatgpt", "https://claude.ai/"), false);
  assert.equal(pms("chatgpt", "https://chatgpt.com.evil.io/"), false);
  assert.equal(pms("chatgpt", "http://chatgpt.com/"), false);
  assert.equal(pms("claude", "https://claude.ai:8443/"), false);
  assert.equal(pms("claude", "https://user@claude.ai/"), false);
  assert.equal(pms("x", "https://chatgpt.com/"), false);
  assert.equal(pms("chatgpt", undefined), false);
});

const usage = { provider: "claude", model: "m", conversation_id: "c", prompt_chars: 3, context_chars: 5, ts: 1 };

test("usage válido, extras removidos", () => {
  assert.deepEqual(s({ nm: "usage", payload: { ...usage, evil: "x" } }), { type: "usage", ...usage });
});

test("Gemini usage accepted only through Gemini host; no Gemini quota message", () => {
  const event = { provider: "gemini", model: "gemini-unknown", prompt_chars: 4, context_chars: 4 };
  assert.deepEqual(s({ nm: "usage", payload: event }), { type: "usage", ...event });
  assert.equal(pms("gemini", "https://evil.example/"), false);
  assert.equal(s({ nm: "limit", payload: { provider: "gemini", scope: "5h", reset_at: 10 } }), null);
});

test("Gemini MAIN handoff adapter whitelists counts and strips arbitrary fields", () => {
  const normalized = geminiEventToBridgeMessage({
    k: "geminiUsage", prompt_chars: 42, context_chars: 42, ts: 123,
    prompt: "secret", token: "secret", body: "secret", f_req: "secret",
  });
  assert.deepEqual(normalized, { nm: "usage", payload: {
    provider: "gemini", model: "gemini-unknown", prompt_chars: 42, context_chars: 42, ts: 123,
  } });
  assert.equal(geminiEventToBridgeMessage({ k: "geminiUsage", prompt_chars: -1, context_chars: 1, ts: 1 }), null);
});

test("usage inválido", () => {
  assert.equal(s({ nm: "usage", payload: { ...usage, provider: "x" } }), null);
  assert.equal(s({ nm: "usage", payload: { ...usage, prompt_chars: -1 } }), null);
  assert.equal(s({ nm: "usage", payload: { ...usage, context_chars: 1.5 } }), null);
  assert.equal(s({ nm: "usage", payload: { ...usage, model: "x".repeat(101) } }), null);
});

test("limit model opcional: válido passa, inválido só descarta o campo", () => {
  const l = { provider: "chatgpt", scope: "5h", reset_at: 5, rate_limited: true };
  assert.deepEqual(s({ nm: "limit", payload: { ...l, model: "o3" } }), { type: "limit", ...l, model: "o3" });
  assert.deepEqual(s({ nm: "limit", payload: { ...l, model: 5 } }), { type: "limit", ...l });
  assert.deepEqual(s({ nm: "limit", payload: { ...l, model: "" } }), { type: "limit", ...l });
  assert.deepEqual(s({ nm: "limit", payload: { ...l, model: "x".repeat(101) } }), { type: "limit", ...l });
});

test("limit válido e inválido", () => {
  const l = { provider: "chatgpt", scope: "weekly", reset_at: 5, rate_limited: true };
  assert.deepEqual(s({ nm: "limit", payload: { ...l, extra: 1 } }), { type: "limit", ...l });
  assert.equal(s({ nm: "limit", payload: { ...l, scope: "1h" } }), null);
  assert.equal(s({ nm: "limit", payload: { ...l, reset_at: 0 } }), null);
  assert.equal(s({ nm: "other", payload: l }), null);
  assert.equal(s(null), null);
});

test("quota valida fonte, janela e valores finitos", () => {
  const q = { provider: "claude", account_hash: "a".repeat(64), window: "5h", percent: 0, observed_at: 1000, reset_at: 2000 };
  assert.deepEqual(s({ nm: "quota", payload: q }), { type: "quota", ...q });
  for (const bad of [{ provider: "chatgpt" }, { account_hash: "raw-org" }, { percent: NaN }, { percent: 101 }, { window: "codex" }, { reset_at: 999 }, { window: "5h", model: "opus" }, { window: "opus", model: "sonnet" }]) {
    assert.equal(s({ nm: "quota", payload: { ...q, ...bad } }), null);
  }
  assert.equal(s({ nm: "quota", payload: { ...q, prompt: "secret" } }).prompt, undefined);
});

test("quota candidate aceita apenas sender Claude HTTPS exato e payload limitado", () => {
  const msg = { nm: "quotaCandidate", payload: { orgId: "synthetic-org", body: "{}" } };
  assert.deepEqual(sanitizeQuotaCandidate(msg, "https://claude.ai/"), msg.payload);
  assert.deepEqual(sanitizeQuotaCandidate({ ...msg, payload: { orgId: "synthetic-org", body: null } }, "https://claude.ai/"), { orgId: "synthetic-org", body: null });
  for (const url of ["https://evil.example/", "https://claude.ai:8443/", "http://claude.ai/"]) {
    assert.equal(sanitizeQuotaCandidate(msg, url), null);
  }
  assert.equal(sanitizeQuotaCandidate({ ...msg, payload: { ...msg.payload, body: "x".repeat(65537) } }, "https://claude.ai/"), null);
});
