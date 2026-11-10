import assert from "node:assert/strict";
import test from "node:test";

import { BACKOFF_MAX_MS, BACKOFF_MIN_MS, nextBackoffDelay } from "../src/backoff.js";

test("constantes", () => {
  assert.equal(BACKOFF_MIN_MS, 1000);
  assert.equal(BACKOFF_MAX_MS, 30000);
});

test("attempt 0 rand 0.5 = 1000", () => {
  assert.equal(nextBackoffDelay(0, () => 0.5), 1000);
});

test("attempt 10 limitado a 30000 +-20%", () => {
  const lo = nextBackoffDelay(10, () => 0);
  const hi = nextBackoffDelay(10, () => 1);
  assert.ok(Math.abs(lo - 24000) < 1e-6);
  assert.ok(Math.abs(hi - 36000) < 1e-6);
});

test("rand 0 = x0.8, rand 1 = x1.2", () => {
  assert.ok(Math.abs(nextBackoffDelay(1, () => 0) - 1600) < 1e-6);
  assert.ok(Math.abs(nextBackoffDelay(1, () => 1) - 2400) < 1e-6);
});
