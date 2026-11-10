import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeBridgeMessage as s, providerMatchesSender as pms } from "../src/bridge_msg.js";

test("providerMatchesSender", () => {
  assert.equal(pms("claude", "https://claude.ai/chat/1"), true);
  assert.equal(pms("chatgpt", "https://chatgpt.com/c/1"), true);
  assert.equal(pms("claude", "https://chatgpt.com/c/1"), false);
  assert.equal(pms("chatgpt", "https://claude.ai/"), false);
  assert.equal(pms("chatgpt", "https://chatgpt.com.evil.io/"), false);
  assert.equal(pms("chatgpt", "http://chatgpt.com/"), false);
  assert.equal(pms("x", "https://chatgpt.com/"), false);
  assert.equal(pms("chatgpt", undefined), false);
});

const usage = { provider: "claude", model: "m", conversation_id: "c", prompt_chars: 3, context_chars: 5, ts: 1 };

test("usage válido, extras removidos", () => {
  assert.deepEqual(s({ nm: "usage", payload: { ...usage, evil: "x" } }), { type: "usage", ...usage });
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
