import test from "node:test";
import assert from "node:assert/strict";
import { createSseParser } from "../src/sse.js";

test("evento simples", () => {
  const p = createSseParser();
  assert.deepEqual(p.push("event: a\ndata: {\"x\":1}\n\n"), [{ event: "a", data: '{"x":1}' }]);
});

test("chunk partido no meio", () => {
  const p = createSseParser();
  assert.deepEqual(p.push("event: a\nda"), []);
  assert.deepEqual(p.push("ta: 12"), []);
  assert.deepEqual(p.push("3\n\n"), [{ event: "a", data: "123" }]);
});

test("CRLF", () => {
  const p = createSseParser();
  assert.deepEqual(p.push("event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n"), [
    { event: "a", data: "1" },
    { event: "b", data: "2" },
  ]);
});

test("CRLF partido entre \\r e \\n", () => {
  const p = createSseParser();
  assert.deepEqual(p.push("data: 1\r\n\r"), []);
  assert.deepEqual(p.push("\n"), [{ event: "message", data: "1" }]);
});

test("multi data e comentário", () => {
  const p = createSseParser();
  assert.deepEqual(p.push(": ping\ndata: a\ndata: b\n\n"), [{ event: "message", data: "a\nb" }]);
});

test("flush emite resto", () => {
  const p = createSseParser();
  p.push("event: z\ndata: 9");
  assert.deepEqual(p.flush(), [{ event: "z", data: "9" }]);
});
