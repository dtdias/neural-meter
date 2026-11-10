import test from "node:test";
import assert from "node:assert/strict";
import { createClaudeCapture, matchClaudeCompletionUrl } from "../src/claude_capture.js";
import { createContextTracker } from "../src/context_tracker.js";

const URL1 = "https://claude.ai/api/organizations/o1/chat_conversations/c1/completion";

function setup() {
  const out = [];
  const unk = [];
  const cap = createClaudeCapture({
    emit: (m) => out.push(m),
    tracker: createContextTracker(),
    onUnknown: (t) => unk.push(t),
    now: () => 42,
  });
  return { out, unk, cap };
}
const sse = (type, obj) => `event: ${type}\ndata: ${JSON.stringify({ type, ...obj })}\n\n`;

test("match url", () => {
  assert.deepEqual(matchClaudeCompletionUrl(URL1 + "?x=1"), { orgId: "o1", conversationId: "c1" });
  assert.deepEqual(matchClaudeCompletionUrl(URL1.replace("completion", "retry_completion")), {
    orgId: "o1",
    conversationId: "c1",
  });
  assert.equal(matchClaudeCompletionUrl("https://claude.ai/api/organizations/o1/usage"), null);
});

test("GET e outra rota ignorados", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "GET", "{}");
  cap.onRequest("2", "https://claude.ai/api/x", "POST", "{}");
  assert.equal(out.length, 0);
});

test("usage: model default, anexos, sem texto do prompt", () => {
  const { out, cap } = setup();
  const prompt = "segredo-do-prompt";
  cap.onRequest("1", URL1, "POST", JSON.stringify({ prompt, attachments: [{ extracted_content: "abcd" }] }));
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].payload, {
    provider: "claude",
    model: "claude-unknown",
    conversation_id: "c1",
    prompt_chars: prompt.length + 4,
    context_chars: prompt.length + 4,
    ts: 42,
  });
  assert.ok(!JSON.stringify(out).includes(prompt));
});

test("body inválido -> onUnknown sem emit", () => {
  const { out, unk, cap } = setup();
  cap.onRequest("1", URL1, "POST", "not json");
  assert.equal(out.length, 0);
  assert.equal(unk.length, 1);
});

test("contexto acumula entre 2 turnos", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", JSON.stringify({ prompt: "aaaaa", model: "claude-x" }));
  cap.onChunk("1", sse("content_block_delta", { delta: { text: "123456" } }));
  cap.onEnd("1");
  cap.onRequest("2", URL1, "POST", JSON.stringify({ prompt: "bb" }));
  assert.equal(out[1].payload.context_chars, 5 + 6 + 2);
  assert.equal(out[1].payload.prompt_chars, 2);
});

test("message_limit exceeded -> limit rate_limited", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", JSON.stringify({ prompt: "a" }));
  cap.onChunk("1", sse("message_limit", { message_limit: { type: "exceeded_limit", resetsAt: 1000 } }));
  assert.deepEqual(out[1], {
    nm: "limit",
    payload: { provider: "claude", scope: "5h", reset_at: 1000000, rate_limited: true },
  });
});

test("windows 7d -> weekly; within_limit sem reset -> nada", () => {
  const { out, cap } = setup();
  cap.onRequest("1", URL1, "POST", JSON.stringify({ prompt: "a" }));
  cap.onChunk(
    "1",
    sse("message_limit", {
      message_limit: {
        type: "approaching_limit",
        windows: { "5h": { status: "within_limit", resets_at: 10 }, "7d": { status: "approaching_limit", resets_at: 20 } },
      },
    }),
  );
  assert.equal(out[1].payload.scope, "5h");
  assert.equal(out[1].payload.rate_limited, false);
  assert.equal(out[2].payload.scope, "weekly");
  assert.equal(out[2].payload.reset_at, 20000);
  cap.onChunk("1", sse("message_limit", { message_limit: { type: "within_limit" } }));
  assert.equal(out.length, 3);
});

test("429 com message JSON string -> limit", () => {
  const { out, cap } = setup();
  const body = JSON.stringify({
    error: { type: "rate_limit_error", message: JSON.stringify({ type: "exceeded_limit", resetsAt: 500 }) },
  });
  cap.onResponse("1", 429, body);
  assert.deepEqual(out[0].payload, { provider: "claude", scope: "5h", reset_at: 500000, rate_limited: true });
});

test("429 ilegível -> onUnknown sem emit", () => {
  const { out, unk, cap } = setup();
  cap.onResponse("1", 429, "garbage");
  assert.equal(out.length, 0);
  assert.equal(unk.length, 1);
});

test("evento desconhecido 1x", () => {
  const { unk, cap } = setup();
  cap.onRequest("1", URL1, "POST", JSON.stringify({ prompt: "a" }));
  cap.onChunk("1", sse("weird_event", {}) + sse("weird_event", {}));
  assert.deepEqual(unk, ["weird_event"]);
});
