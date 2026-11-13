import test from "node:test";
import assert from "node:assert/strict";
import { hashClaudeAccount, parseClaudeQuota, processClaudeQuotaCandidate } from "../src/quota_capture.js";

const now = 1794312000000;
const accountHash = "a".repeat(64);
test("parses synthetic known windows without forwarding raw data", () => {
  const got = parseClaudeQuota({ five_hour: { utilization: 0, resets_at: new Date(now + 3600000).toISOString() }, seven_day_opus: { utilization: 50 } }, accountHash, now);
  assert.equal(got.length, 2);
  assert.equal(got[0].payload.percent, 0);
  assert.equal(got[1].payload.window, "opus");
  assert.equal(JSON.stringify(got).includes("organization"), false);
});
test("ignores absent, malformed, oversized and invalid windows", () => {
  assert.deepEqual(parseClaudeQuota({ seven_day: { utilization: 101 }, five_hour: { utilization: NaN } }, accountHash, now), []);
  assert.deepEqual(parseClaudeQuota("x".repeat(100000), accountHash, now), []);
  assert.deepEqual(parseClaudeQuota({ unknown: { utilization: 20 } }, accountHash, now), []);
});

test("HMAC account scope stable with extension-local secret; raw id never leaves adapter", async () => {
  const secret = new Uint8Array(32).fill(7);
  const a = await hashClaudeAccount("synthetic-org", secret);
  assert.equal(a, await hashClaudeAccount("synthetic-org", secret));
  assert.notEqual(a, await hashClaudeAccount("other-org", secret));
  assert.equal(a.length, 64);
  const outbound = await processClaudeQuotaCandidate(
    "synthetic-org", JSON.stringify({ five_hour: { utilization: 10 } }), secret, now
  );
  assert.equal(outbound[0].nm, "quotaObservation");
  assert.equal(outbound[1].payload.account_hash, a);
  assert.equal(JSON.stringify(outbound).includes("synthetic-org"), false);
});

test("identity observation survives empty, invalid-window and malformed quota bodies without inventing percent", async () => {
  const secret = new Uint8Array(32).fill(4);
  for (const body of [{}, { five_hour: { utilization: 101 } }, "not-json"]) {
    const messages = await processClaudeQuotaCandidate("org-B", body, secret, now);
    assert.equal(messages.length, 1);
    assert.equal(messages[0].nm, "quotaObservation");
    assert.equal(messages[0].payload.account_hash.length, 64);
    assert.equal("percent" in messages[0].payload, false);
  }
});
