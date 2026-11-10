import test from "node:test";
import assert from "node:assert/strict";
import { createChatgptCapture, matchChatgptConversationUrl } from "../src/chatgpt_capture.js";
import { createContextTracker } from "../src/context_tracker.js";

const URL1 = "https://chatgpt.com/backend-api/conversation";

function setup() {
  const out = [];
  const unk = [];
  const cap = createChatgptCapture({
    emit: (m) => out.push(m),
    tracker: createContextTracker(),
    onUnknown: (t) => unk.push(t),
    now: () => 1000,
  });
  return { out, unk, cap };
}
const msg = (...parts) => ({ author: { role: "user" }, content: { content_type: "text", parts } });
const body = (o) => JSON.stringify({ action: "next", messages: [msg("hello")], ...o });
const ev = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

test("match url", () => {
  assert.equal(matchChatgptConversationUrl(URL1), true);
  assert.equal(matchChatgptConversationUrl("https://chatgpt.com/backend-api/f/conversation"), true);
  assert.equal(matchChatgptConversationUrl(URL1 + "?a=1"), true);
  assert.equal(matchChatgptConversationUrl("/backend-api/conversation"), true);
  assert.equal(matchChatgptConversationUrl(URL1 + "/init"), false);
  assert.equal(matchChatgptConversationUrl("https://chatgpt.com/backend-api/conversations"), false);
});

test("GET ignorado", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "GET", body());
  assert.equal(out.length, 0);
});

test("usage com model do body; soma user e ignora objetos", () => {
  const { out, cap } = setup();
  const b = JSON.stringify({
    model: "gpt-5",
    conversation_id: "c1",
    messages: [
      msg("abc", { x: "yyyyyy" }, "de"),
      { author: { role: "assistant" }, content: { parts: ["zzzz"] } },
    ],
  });
  cap.onRequest("1", URL1, "POST", b);
  assert.deepEqual(out[0], {
    nm: "usage",
    payload: {
      provider: "chatgpt",
      model: "gpt-5",
      conversation_id: "c1",
      prompt_chars: 5,
      context_chars: 5,
      ts: 1000,
    },
  });
});

test("model auto/ausente -> gpt-unknown, sem conversation_id", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body({ model: "auto" }));
  cap.onRequest("2", URL1, "POST", body());
  assert.equal(out[0].payload.model, "gpt-unknown");
  assert.equal(out[1].payload.model, "gpt-unknown");
  assert.equal("conversation_id" in out[0].payload, false);
});

test("conversa nova recebe id via SSE; 2o turno acumula", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body()); // 5 chars
  cap.onChunk("1", ev({ conversation_id: "cN", message: { author: { role: "assistant" }, content: { parts: ["abc"] } } }));
  cap.onChunk("1", "data: [DONE]\n\n");
  cap.onEnd("1");
  cap.onRequest("2", URL1, "POST", body({ conversation_id: "cN" }));
  assert.equal(out[1].payload.context_chars, 5 + 3 + 5);
});

test("delta v1 append + patch array", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body({ conversation_id: "c" }));
  cap.onChunk("1", `event: delta\n${ev({ o: "append", p: "/message/content/parts/0", v: "abcd" })}`);
  cap.onChunk("1", ev({ v: [{ o: "append", p: "/message/content/parts/0", v: "ef" }, { o: "replace", p: "/x", v: 1 }] }));
  cap.onChunk("1", ev({ v: "gh" }));
  cap.onEnd("1");
  cap.onRequest("2", URL1, "POST", body({ conversation_id: "c" }));
  assert.equal(out[1].payload.context_chars, 5 + 8 + 5);
});

test("legado: max length", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body({ conversation_id: "c" }));
  for (const t of ["ab", "abcde", "abc"]) {
    cap.onChunk("1", ev({ message: { author: { role: "assistant" }, content: { parts: [t] } } }));
  }
  cap.onEnd("1");
  cap.onRequest("2", URL1, "POST", body({ conversation_id: "c" }));
  assert.equal(out[1].payload.context_chars, 5 + 5 + 5);
});

test("[DONE] e JSON invalido ignorados; tipo desconhecido 1x", () => {
  const { unk, cap } = setup();
  cap.onRequest("1", URL1, "POST", body({ conversation_id: "c" }));
  cap.onChunk("1", "data: [DONE]\n\ndata: {nao-json\n\n");
  cap.onChunk("1", ev({ type: "novo_tipo" }) + ev({ type: "novo_tipo" }));
  cap.onEnd("1");
  assert.deepEqual(unk, ["novo_tipo"]);
});

test("429 clears_in", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body());
  cap.onResponse("1", 429, JSON.stringify({ detail: { code: "model_cap_exceeded", clears_in: 60 } }));
  assert.deepEqual(out[1], {
    nm: "limit",
    payload: { provider: "chatgpt", scope: "5h", reset_at: 1000 + 60000, rate_limited: true },
  });
});

test("429 inclui model da requisicao; auto/ausente omite", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", body({ model: "o3" }));
  cap.onResponse("1", 429, JSON.stringify({ detail: { code: "model_cap_exceeded", clears_in: 60 } }));
  cap.onRequest("2", URL1, "POST", body({ model: "auto" }));
  cap.onResponse("2", 429, JSON.stringify({ detail: { code: "model_cap_exceeded", clears_in: 60 } }));
  assert.equal(out[1].payload.model, "o3");
  assert.equal("model" in out[3].payload, false);
});

test("reset_at ISO e numerico", () => {
  const { out, cap } = setup();
  const iso = "2030-01-01T00:00:00Z";
  cap.onResponse("1", 429, JSON.stringify({ detail: { reset_at: iso } }));
  cap.onResponse("2", 403, JSON.stringify({ detail: { code: "rate_limit_exceeded", reset_at: 2000000000 } }));
  assert.equal(out[0].payload.reset_at, Date.parse(iso));
  assert.equal(out[1].payload.reset_at, 2000000000000);
});

test("429 sem reset -> onUnknown sem emit; 4xx sem codigo -> nada", () => {
  const { out, unk, cap } = setup();
  cap.onResponse("1", 429, JSON.stringify({ detail: "too many" }));
  cap.onResponse("2", 400, JSON.stringify({ detail: { code: "other" } }));
  assert.equal(out.length, 0);
  assert.deepEqual(unk, ["rate_limit_body"]);
});

test("body invalido -> onUnknown", () => {
  const { out, unk, cap } = setup();
  cap.onRequest("1", URL1, "POST", "{nope");
  assert.equal(out.length, 0);
  assert.deepEqual(unk, ["invalid_request_body"]);
});

test("emits e unknowns nao contem texto do prompt", () => {
  const { out, unk, cap } = setup();
  const secret = "segredo-do-prompt-xyz";
  cap.onRequest("1", URL1, "POST", JSON.stringify({ messages: [msg(secret)] }));
  cap.onChunk("1", ev({ type: "t", message: { author: { role: "assistant" }, content: { parts: [secret] } } }));
  cap.onEnd("1");
  assert.equal(JSON.stringify([out, unk]).includes(secret), false);
});
