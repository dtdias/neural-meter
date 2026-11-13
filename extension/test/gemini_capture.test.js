import test from "node:test";
import assert from "node:assert/strict";
import { createGeminiCapture, matchGeminiStreamUrl, parseGeminiRequest } from "../src/gemini_capture.js";

const endpoint = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?hl=en";
const requestBody = (prompt) => new URLSearchParams({
  at: "synthetic-token-never-forwarded",
  "f.req": JSON.stringify([null, JSON.stringify([[prompt, 0, null, null], ["en"]])]),
}).toString();

test("matches exact same-origin Gemini StreamGenerate endpoint only", () => {
  assert.equal(matchGeminiStreamUrl(endpoint), true);
  assert.equal(matchGeminiStreamUrl("https://evil.example/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"), false);
  assert.equal(matchGeminiStreamUrl("https://gemini.google.com" + new URL(endpoint).pathname + "/retry"), false);
  assert.equal(matchGeminiStreamUrl("http://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"), false);
});

test("parses public f.req envelope, emits counts only; rejects malformed/empty/replay bodies", () => {
  const secret = "fixture-private-text-😀";
  assert.deepEqual(parseGeminiRequest(requestBody(secret)), { promptChars: secret.length });
  for (const body of ["", "f.req=broken", requestBody(""), new URLSearchParams({ "f.req": "[]" }).toString()]) {
    assert.equal(parseGeminiRequest(body), null);
  }
  const sent = [];
  const cap = createGeminiCapture({ emit: (event) => sent.push(event), now: () => 1234 });
  assert.equal(cap.onRequest("1", endpoint, "POST", requestBody(secret)), true);
  assert.equal(cap.onRequest("1", endpoint, "POST", requestBody(secret)), false);
  assert.equal(cap.onRequest("2", endpoint, "POST", ""), false);
  assert.deepEqual(sent, [{ nm: "usage", payload: {
    provider: "gemini", model: "gemini-unknown", prompt_chars: secret.length,
    context_chars: secret.length, ts: 1234,
  } }]);
  assert.equal(JSON.stringify(sent).includes(secret), false);
  assert.equal(JSON.stringify(sent).includes("synthetic-token"), false);
});

test("GET, other hosts and oversized bodies ignored", () => {
  const sent = [];
  const cap = createGeminiCapture({ emit: (event) => sent.push(event) });
  assert.equal(cap.onRequest("1", endpoint, "GET", requestBody("text")), false);
  assert.equal(cap.onRequest("2", "https://evil.example" + new URL(endpoint).pathname, "POST", requestBody("text")), false);
  assert.equal(cap.onRequest("3", endpoint, "POST", "x".repeat(65537)), false);
  assert.deepEqual(sent, []);
});
