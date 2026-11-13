import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { hashClaudeAccount, parseClaudeQuota } from "../src/quota_capture.js";
import { sanitizeBridgeMessage, sanitizeQuotaCandidate } from "../src/bridge_msg.js";

const source = readFileSync(new URL("../src/page_hook.js", import.meta.url), "utf8");

function setup({ response, hostname = "claude.ai" }) {
  const messages = [];
  const calls = [];
  const win = { postMessage: (data) => messages.push(data) };
  const location = { hostname, href: `https://${hostname}/chat` };
  let responsePromise = Promise.resolve(response);
  const originalFetch = function (...args) { calls.push(args); return responsePromise; };
  win.fetch = originalFetch;
  const context = { window: win, location, URL, TextDecoder, TextEncoder, Uint8Array, Promise, console };
  vm.runInNewContext(source, context, { filename: "page_hook.js" });
  return { fetch: win.fetch, originalFetch, messages, calls, promise: () => responsePromise, setResponse: (value) => { responsePromise = Promise.resolve(value); } };
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
