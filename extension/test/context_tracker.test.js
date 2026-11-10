import test from "node:test";
import assert from "node:assert/strict";
import { createContextTracker } from "../src/context_tracker.js";

test("acumula e ausente = 0", () => {
  const t = createContextTracker();
  assert.equal(t.get("a"), 0);
  t.add("a", 10);
  t.add("a", 5);
  assert.equal(t.get("a"), 15);
  t.add(undefined, 5);
  assert.equal(t.get(undefined), 0);
});

test("LRU remove o menos recente", () => {
  const t = createContextTracker({ max: 2 });
  t.add("a", 1);
  t.add("b", 2);
  t.get("a"); // a mais recente
  t.add("c", 3);
  assert.equal(t.get("b"), 0);
  assert.equal(t.get("a"), 1);
  assert.equal(t.get("c"), 3);
});
