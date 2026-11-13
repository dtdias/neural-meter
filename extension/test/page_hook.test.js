import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { hashClaudeAccount, parseClaudeQuota } from "../src/quota_capture.js";
import { sanitizeBridgeMessage, sanitizeQuotaCandidate } from "../src/bridge_msg.js";
import { createChatgptCapture } from "../src/chatgpt_capture.js";
import { createClaudeCapture } from "../src/claude_capture.js";
import { createContextTracker } from "../src/context_tracker.js";

const source = readFileSync(new URL("../src/page_hook.js", import.meta.url), "utf8");

function setup({ response, hostname = "claude.ai" }) {
  const messages = [];
  const calls = [];
  const requestBodyUsedAtFetch = [];
  const win = { postMessage: (data) => messages.push(data) };
  const location = { hostname, href: `https://${hostname}/chat` };
  let responsePromise = Promise.resolve(response);
  const originalFetch = function (...args) {
    calls.push(args);
    if (typeof Request !== "undefined" && args[0] instanceof Request) requestBodyUsedAtFetch.push(args[0].bodyUsed);
    return responsePromise;
  };
  win.fetch = originalFetch;
  const context = { window: win, location, URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array, Promise, console };
  vm.runInNewContext(source, context, { filename: "page_hook.js" });
  return { fetch: win.fetch, originalFetch, messages, calls, requestBodyUsedAtFetch, promise: () => responsePromise, setResponse: (value) => { responsePromise = Promise.resolve(value); } };
}

function response({ url = "https://claude.ai/api/organizations/org-A/usage", type = "application/json", chunks = ["{}"], length, streamError = false } = {}) {
  let cancelCount = 0;
  let originalReadCount = 0;
  const makeBody = (original = false) => ({
    getReader() {
      let i = 0;
      return {
        async read() {
          if (original) originalReadCount++;
          if (streamError) throw new Error("synthetic reader failure");
          return i < chunks.length ? { done: false, value: new TextEncoder().encode(chunks[i++]) } : { done: true };
        },
        async cancel() { cancelCount++; },
      };
    },
  });
  const value = {
    url, ok: true, status: 200, headers: { get: (name) => name === "content-type" ? type : name === "content-length" ? length ?? null : null },
    body: makeBody(true), clone() { return { body: makeBody(false) }; },
  };
  return { value, cancelCount: () => cancelCount, originalReadCount: () => originalReadCount };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test("GET allowlist supports string, URL and Request-like inputs; returns exact original Promise", async () => {
  for (const kind of ["string", "url", "request"]) {
    const mock = response();
    const env = setup({ response: mock.value });
    const raw = "https://claude.ai/api/organizations/org-A/usage";
    const input = kind === "string" ? raw : kind === "url" ? new URL(raw) : {
      url: raw, method: "GET", get body() { throw new Error("GET Request body must not be read"); },
    };
    const original = env.promise();
    const returned = env.fetch(input);
    assert.equal(returned, original);
    await returned;
    await flush();
    assert.equal(env.calls.length, 1);
    assert.equal(env.messages.filter((m) => m.k === "quota").length, 1);
    assert.equal(mock.originalReadCount(), 0);
  }
});

test("ChatGPT absolute URL regex already matched before hardening", () => {
  assert.equal(/\/backend-api\/(f\/)?conversation(\?|$)/.test("https://chatgpt.com/backend-api/conversation?x=1"), true);
});

test("captures Request body clone, URLSearchParams and Blob without consuming original", async () => {
  const multibyte = new TextEncoder().encode('{"action":"next","prompt":"fi😀xture"}');
  const emojiStart = multibyte.indexOf(0xf0);
  const splitRequest = new Request("https://chatgpt.com/backend-api/conversation", {
    method: "POST",
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(multibyte.slice(0, emojiStart + 2));
        controller.enqueue(multibyte.slice(emojiStart + 2));
        controller.close();
      },
    }),
    duplex: "half",
  });
  for (const body of [
    new Request("https://chatgpt.com/backend-api/conversation", { method: "POST", body: "{\"action\":\"next\"}" }),
    splitRequest,
    new URLSearchParams({ action: "next", value: "synthetic" }),
    new Blob(["{\"action\":\"next\"}"]),
  ]) {
    const env = setup({ hostname: "chatgpt.com", response: response({ url: "https://chatgpt.com/backend-api/conversation" }).value });
    const input = body instanceof Request ? body : "https://chatgpt.com/backend-api/conversation";
    const init = body instanceof Request ? undefined : { method: "POST", body };
    const original = env.promise();
    assert.equal(env.fetch(input, init), original);
    await original;
    await flush();
    assert.equal(env.messages.some((message) => message.k === "req"), true);
    if (body instanceof Request) {
      assert.deepEqual(env.requestBodyUsedAtFetch, [false]);
      assert.equal(body.bodyUsed, false);
      const forwarded = env.messages.find((message) => message.k === "req").body;
      assert.doesNotThrow(() => JSON.parse(forwarded));
      if (body === splitRequest) assert.equal(forwarded, '{"action":"next","prompt":"fi😀xture"}');
    }
  }
});

test("XHR passes through unchanged while passively forwarding request body", async () => {
  const calls = [];
  class FakeXHR {
    open(...args) { calls.push(["open", ...args]); }
    send(...args) { calls.push(["send", ...args]); return "original-return"; }
  }
  const responseMock = response({ url: "https://chatgpt.com/backend-api/conversation" }).value;
  const env = setup({ hostname: "chatgpt.com", response: responseMock });
  const context = { window: { postMessage: (data) => env.messages.push(data), fetch: env.originalFetch },
    location: { hostname: "chatgpt.com", href: "https://chatgpt.com/chat" }, URL, URLSearchParams,
    Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array, Promise, console, XMLHttpRequest: FakeXHR };
  vm.runInNewContext(source, context, { filename: "page_hook.js" });
  const xhr = new FakeXHR();
  xhr.open("POST", "https://chatgpt.com/backend-api/conversation");
  assert.equal(xhr.send(new URLSearchParams({ action: "next" })), "original-return");
  await flush();
  assert.equal(calls[0][0], "open");
  assert.equal(calls[1][0], "send");
  assert.equal(env.messages.some((message) => message.k === "req"), true);
});

test("Gemini bodies never cross MAIN postMessage for fetch body variants or XHR", async () => {
  const privatePrompt = "fixture-PROMPT-😀-must-not-leak";
  const privateToken = "fixture-at-TOKEN-must-not-leak";
  const geminiPath = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate";
  const formBody = (prompt) => new URLSearchParams({
    at: privateToken,
    "f.req": JSON.stringify([null, JSON.stringify([[prompt, 0, null, null], ["en"]])]),
  }).toString();
  const request = new Request(geminiPath, { method: "POST", body: formBody(privatePrompt) });
  const bodies = [
    formBody(privatePrompt),
    new URLSearchParams(formBody(privatePrompt)),
    new Blob([formBody(privatePrompt)]),
    request,
  ];
  for (const body of bodies) {
    const env = setup({ hostname: "gemini.google.com", response: response({ url: geminiPath }) });
    const input = body instanceof Request ? body : geminiPath;
    const init = body instanceof Request ? undefined : { method: "POST", body };
    await env.fetch(input, init);
    await flush();
    const serialized = JSON.stringify(env.messages);
    assert.equal(serialized.includes(privatePrompt), false);
    assert.equal(serialized.includes(privateToken), false);
    assert.equal(env.messages.length, 1);
    assert.deepEqual(Object.keys(env.messages[0]).sort(), ["__nm", "context_chars", "id", "k", "prompt_chars", "ts"]);
    assert.equal(env.messages[0].k, "geminiUsage");
  }

  class FakeXHR {
    listeners = {};
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    open() {}
    send() { return "native-send"; }
  }
  const env = setup({ hostname: "gemini.google.com", response: response({ url: geminiPath }) });
  const context = {
    window: { postMessage: (data) => env.messages.push(data), fetch: env.originalFetch },
    location: { hostname: "gemini.google.com", href: "https://gemini.google.com/app" },
    URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array,
    Promise, console, XMLHttpRequest: FakeXHR,
  };
  vm.runInNewContext(source, context, { filename: "page_hook.js" });
  const xhr = new FakeXHR();
  xhr.open("POST", geminiPath);
  assert.equal(xhr.send(formBody(privatePrompt)), "native-send");
  await flush();
  const serialized = JSON.stringify(env.messages);
  assert.equal(serialized.includes(privatePrompt), false);
  assert.equal(serialized.includes(privateToken), false);
  assert.equal(env.messages.some((message) => message.k === "geminiUsage"), true);
  assert.equal(env.messages.some((message) => "body" in message || "text" in message), false);
});

test("synchronous XHR-backed fetch polyfill does not double-count Gemini submission", async () => {
  const messages = [];
  class FakeXHR {
    open(_method, _url) {}
    send(_body) { return undefined; }
  }
  const endpoint = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate";
  const body = new URLSearchParams({
    at: "secret-token",
    "f.req": JSON.stringify([null, JSON.stringify([["same prompt", 0], ["en"]])]),
  }).toString();
  const xhr = new FakeXHR();
  const window = {
    postMessage: (event) => messages.push(event),
    fetch(_input, init) {
      xhr.open("POST", endpoint);
      xhr.send(init.body);
      return Promise.resolve({});
    },
  };
  vm.runInNewContext(source, {
    window, location: { hostname: "gemini.google.com", href: "https://gemini.google.com/app" },
    URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array,
    Promise, console, XMLHttpRequest: FakeXHR,
  }, { filename: "page_hook.js" });
  await window.fetch(endpoint, { method: "POST", body });
  await flush();
  assert.equal(messages.filter((event) => event.k === "geminiUsage").length, 1);
  assert.equal(JSON.stringify(messages).includes("secret-token"), false);
  assert.equal(JSON.stringify(messages).includes("same prompt"), false);
});

test("XHR captures limited text streams and 429 bodies without replacing handlers or native return", async () => {
  class FakeXHR {
    listeners = {};
    status = 200;
    responseType = "";
    responseText = "";
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type) { for (const fn of this.listeners[type] || []) fn.call(this, { type }); }
    open(...args) { this.openArgs = args; return "open-return"; }
    send(...args) { this.sendArgs = args; return "send-return"; }
  }
  const env = setup({ hostname: "chatgpt.com", response: response({ url: "https://chatgpt.com/backend-api/conversation" }) });
  const context = {
    window: { postMessage: (data) => env.messages.push(data), fetch: env.originalFetch },
    location: { hostname: "chatgpt.com", href: "https://chatgpt.com/c/1" },
    URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array,
    Promise, console, XMLHttpRequest: FakeXHR,
  };
  vm.runInNewContext(source, context, { filename: "page_hook.js" });
  const xhr = new FakeXHR();
  const onload = () => {};
  xhr.onload = onload;
  assert.equal(xhr.open("POST", "https://chatgpt.com/backend-api/conversation"), "open-return");
  const userBody = JSON.stringify({
    action: "next", model: "gpt-5", conversation_id: "xhr-conversation",
    messages: [{ author: { role: "user" }, content: { parts: ["fixture user turn"] } }],
  });
  assert.equal(xhr.send(userBody), "send-return");
  await flush();
  assert.equal(xhr.onload, onload);
  assert.deepEqual(xhr.openArgs[0], "POST");
  const assistantText = "fixture assistant response";
  xhr.responseText = `data: ${JSON.stringify({
    message: { author: { role: "assistant" }, content: { parts: [assistantText] } },
  })}\n\n`;
  xhr.fire("progress");
  xhr.fire("loadend");
  await flush();
  assert.equal(env.messages.some((message) => message.k === "chunk" && message.text.includes(assistantText)), true);
  assert.equal(env.messages.some((message) => message.k === "resp" && message.status === 200), true);
  assert.equal(env.messages.some((message) => message.k === "end"), true);
  const chatEvents = [];
  const chatTracker = createContextTracker();
  const chatCapture = createChatgptCapture({ emit: (event) => chatEvents.push(event), tracker: chatTracker, now: () => 1_000_000 });
  for (const message of env.messages) {
    if (message.k === "req") chatCapture.onRequest(message.id, message.url, message.method, message.body);
    else if (message.k === "resp") chatCapture.onResponse(message.id, message.status, message.body);
    else if (message.k === "chunk") chatCapture.onChunk(message.id, message.text);
    else if (message.k === "end") chatCapture.onEnd(message.id);
  }
  assert.equal(chatEvents.some((event) => event.nm === "usage" && event.payload.prompt_chars === "fixture user turn".length), true);
  assert.equal(chatTracker.get("xhr-conversation"), "fixture user turn".length + assistantText.length);

  const xhr429 = new FakeXHR();
  xhr429.status = 429;
  xhr429.responseType = "text";
  const chatgpt429Body = JSON.stringify({ detail: { code: "rate_limit_exceeded", clears_in: 90 } });
  xhr429.responseText = chatgpt429Body;
  xhr429.open("POST", "https://chatgpt.com/backend-api/conversation");
  xhr429.send(userBody);
  await flush();
  xhr429.fire("progress");
  xhr429.fire("loadend");
  await flush();
  const latestResponse = [...env.messages].reverse().find((message) => message.k === "resp" && message.status === 429);
  assert.equal(latestResponse?.body, chatgpt429Body);
  const latestRequest = [...env.messages].reverse().find((message) => message.k === "req");
  const rateEvents = [];
  const rateCapture = createChatgptCapture({ emit: (event) => rateEvents.push(event), tracker: createContextTracker(), now: () => 1_000_000 });
  rateCapture.onRequest(latestRequest.id, latestRequest.url, latestRequest.method, latestRequest.body);
  rateCapture.onResponse(latestRequest.id, latestResponse.status, latestResponse.body);
  assert.equal(rateEvents.some((event) => event.nm === "limit" && event.payload.reset_at === 1_090_000), true);

  const xhr500 = new FakeXHR();
  xhr500.status = 500;
  xhr500.responseText = JSON.stringify({ error: "server failure" });
  xhr500.open("POST", "https://chatgpt.com/backend-api/conversation");
  xhr500.send(JSON.stringify({ action: "next" }));
  await flush();
  xhr500.fire("loadend");
  await flush();
  assert.equal(env.messages.some((message) => message.k === "resp" && message.status === 500 && message.body.includes("server failure")), true);

  const networkError = new FakeXHR();
  networkError.status = 0;
  networkError.responseText = "";
  networkError.open("POST", "https://chatgpt.com/backend-api/conversation");
  networkError.send(JSON.stringify({ action: "next" }));
  await flush();
  networkError.fire("error");
  networkError.fire("loadend");
  await flush();
  assert.equal(env.messages.filter((message) => message.k === "resp" && message.status === 0).length, 1);

  const claude429 = new FakeXHR();
  claude429.status = 429;
  claude429.responseType = "text";
  const resetsAt = Math.floor(Date.now() / 1000) + 90;
  const claude429Body = JSON.stringify({ error: { message: JSON.stringify({ resetsAt }) } });
  claude429.responseText = claude429Body;
  const claudeEnv = setup({ hostname: "claude.ai", response: response() });
  const claudeContext = {
    window: { postMessage: (data) => claudeEnv.messages.push(data), fetch: claudeEnv.originalFetch },
    location: { hostname: "claude.ai", href: "https://claude.ai/chat" },
    URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder, TextEncoder, Uint8Array,
    Promise, console, XMLHttpRequest: FakeXHR,
  };
  vm.runInNewContext(source, claudeContext, { filename: "page_hook.js" });
  claude429.open("POST", "https://claude.ai/api/organizations/org/chat_conversations/c/completion");
  claude429.send(JSON.stringify({ prompt: "fixture" }));
  await flush();
  claude429.fire("loadend");
  await flush();
  const claudeReq = claudeEnv.messages.find((message) => message.k === "req");
  const claudeResp = claudeEnv.messages.find((message) => message.k === "resp");
  assert.equal(claudeResp?.body, claude429Body);
  const claudeEvents = [];
  const claudeCapture = createClaudeCapture({ emit: (event) => claudeEvents.push(event), tracker: createContextTracker() });
  claudeCapture.onRequest(claudeReq.id, claudeReq.url, claudeReq.method, claudeReq.body);
  claudeCapture.onResponse(claudeReq.id, claudeResp.status, claudeResp.body);
  assert.equal(claudeEvents.some((event) =>
    event.nm === "limit" && event.payload.rate_limited &&
    event.payload.scope === "5h" && event.payload.reset_at === resetsAt * 1000), true);

  const jsonResponse = new FakeXHR();
  jsonResponse.status = 429;
  jsonResponse.responseType = "json";
  jsonResponse.response = { detail: { code: "rate_limit_exceeded", clears_in: 45 } };
  Object.defineProperty(jsonResponse, "responseText", {
    get() { throw new DOMException("JSON responseType", "InvalidStateError"); },
  });
  jsonResponse.open("POST", "https://chatgpt.com/backend-api/conversation");
  jsonResponse.send(userBody);
  await flush();
  jsonResponse.fire("loadend");
  await flush();
  const json429 = [...env.messages].reverse().find((message) => message.k === "resp" && message.status === 429);
  assert.deepEqual(JSON.parse(json429.body), jsonResponse.response);

  const multibyteError = new FakeXHR();
  multibyteError.status = 429;
  multibyteError.responseType = "text";
  multibyteError.responseText = `😀`.repeat(17000);
  multibyteError.open("POST", "https://chatgpt.com/backend-api/conversation");
  multibyteError.send(userBody);
  await flush();
  multibyteError.fire("loadend");
  await flush();
  const bounded = [...env.messages].reverse().find((message) => message.k === "resp" && message.status === 429);
  assert.ok(new TextEncoder().encode(bounded.body).byteLength <= 65536);
  assert.equal(bounded.body.charCodeAt(bounded.body.length - 1) >= 0xd800 &&
    bounded.body.charCodeAt(bounded.body.length - 1) <= 0xdbff, false);

  const incompatible = new FakeXHR();
  incompatible.responseType = "blob";
  Object.defineProperty(incompatible, "responseText", { get() { throw new DOMException("invalid responseType", "InvalidStateError"); } });
  incompatible.open("POST", "https://chatgpt.com/backend-api/conversation");
  incompatible.send(JSON.stringify({ action: "next" }));
  await flush();
  assert.doesNotThrow(() => incompatible.fire("loadend"));
});

test("rejects foreign host, credentials, nonstandard port, bad path/method/content type and cross-origin redirect", async () => {
  const cases = [
    { input: "https://evil.example/api/organizations/o/usage" },
    { input: "https://claude.ai@evil.example/api/organizations/o/usage" },
    { input: "https://claude.ai:8443/api/organizations/o/usage" },
    { input: "https://claude.ai/api/organizations/o/usage/extra" },
    { input: "https://claude.ai/api/organizations/o/usage", init: { method: "POST" } },
    { input: "https://claude.ai/api/organizations/o/usage", type: "text/html" },
    { input: "https://claude.ai/api/organizations/o/usage", responseUrl: "https://evil.example/api/organizations/o/usage" },
  ];
  for (const item of cases) {
    const resp = response({ url: item.responseUrl, type: item.type });
    const env = setup({ response: resp.value });
    await env.fetch(item.input, item.init);
    await flush();
    assert.equal(env.messages.some((m) => m.k === "quota"), false, JSON.stringify(item));
  }
});

test("same-origin redirects must retain exact route and organization; bounded reader cancels oversize/error", async () => {
  for (const url of [
    "https://claude.ai/api/organizations/org-B/usage",
    "https://claude.ai/elsewhere",
  ]) {
    const env = setup({ response: response({ url }).value });
    await env.fetch("https://claude.ai/api/organizations/org-A/usage");
    await flush();
    assert.equal(env.messages.some((m) => m.k === "quota"), false);
  }
  for (const opts of [{ chunks: ["x".repeat(65537)] }, { streamError: true }]) {
    const resp = response(opts);
    const env = setup({ response: resp.value });
    await env.fetch("https://claude.ai/api/organizations/org-A/usage");
    await flush();
    assert.equal(resp.cancelCount(), 1);
    assert.equal(env.messages.some((m) => m.k === "quota"), false);
  }
});

test("validated response records identity even when content type, size, or JSON body invalid", async () => {
  for (const opts of [
    { type: "text/html" },
    { chunks: ["x".repeat(65537)] },
    { chunks: ["not-json"] },
    { streamError: true },
  ]) {
    const mock = response({ ...opts, url: "https://claude.ai/api/organizations/org-B/usage" });
    const env = setup({ response: mock.value });
    await env.fetch("https://claude.ai/api/organizations/org-B/usage");
    await flush();
    assert.equal(env.messages.some((message) => message.k === "quotaIdentity"), true);
    if (opts.type === "text/html" || opts.streamError || opts.chunks?.[0]?.length > 65536) {
      assert.equal(env.messages.some((message) => message.k === "quota"), false);
    }
  }
});

test("synthetic hook event -> parser -> bridge whitelist removes org identity and raw payload", async () => {
  const fixture = JSON.stringify({ five_hour: { utilization: 0 } });
  const env = setup({ response: response({ url: "https://claude.ai/api/organizations/synthetic-org/usage", chunks: [fixture] }).value });
  await env.fetch("https://claude.ai/api/organizations/synthetic-org/usage");
  await flush();
  const event = env.messages.find((message) => message.k === "quota");
  assert.equal(event.orgId, "synthetic-org"); // transient MAIN→ISOLATED handoff only
  const candidate = sanitizeQuotaCandidate(
    { nm: "quotaCandidate", payload: { orgId: event.orgId, body: event.body } },
    "https://claude.ai/chat/"
  );
  assert.ok(candidate);
  const secret = new Uint8Array(32).fill(9);
  const hash = await hashClaudeAccount(candidate.orgId, secret);
  const safe = sanitizeBridgeMessage(parseClaudeQuota(candidate.body, hash, 1794312000000)[0]);
  assert.equal(safe.type, "quota");
  assert.equal(safe.account_hash, hash);
  assert.equal(JSON.stringify(safe).includes("synthetic-org"), false);
  assert.equal(JSON.stringify(safe).includes("utilization"), false);
});
