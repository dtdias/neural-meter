import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createWsClient } from "../src/ws_client.js";
import {
  geminiEventToBridgeMessage,
  providerMatchesSender,
  sanitizeBridgeMessage,
} from "../src/bridge_msg.js";

const port = Number(process.argv[2]);
assert.ok(Number.isInteger(port) && port > 0);
const prompt = "synthetic integration prompt 🪨";
const token = "synthetic-at-token-DO-NOT-LEAK";
const endpoint = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate";
const body = new URLSearchParams({
  at: token,
  "f.req": JSON.stringify([null, JSON.stringify([[prompt, 0, null, null], ["en"]])]),
}).toString();

const pageEvents = [];
const window = { postMessage: (event) => pageEvents.push(event) };
window.fetch = () => Promise.resolve({});
const location = { hostname: "gemini.google.com", href: "https://gemini.google.com/app" };
const hook = readFileSync(new URL("../src/page_hook.js", import.meta.url), "utf8");
vm.runInNewContext(hook, {
  window, location, URL, URLSearchParams, Blob, Request, ArrayBuffer, TextDecoder,
  TextEncoder, Uint8Array, Promise, console,
});
await window.fetch(endpoint, { method: "POST", body });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(pageEvents.length, 1);
assert.equal(JSON.stringify(pageEvents).includes(prompt), false);
assert.equal(JSON.stringify(pageEvents).includes(token), false);

// Actual bridge.js Gemini adapter + background.js allowlist sanitizer.
const fromBridge = geminiEventToBridgeMessage(pageEvents[0]);
const sanitized = sanitizeBridgeMessage(fromBridge);
assert.deepEqual(sanitized, {
  type: "usage", provider: "gemini", model: "gemini-unknown",
  prompt_chars: prompt.length, context_chars: prompt.length, ts: pageEvents[0].ts,
});
assert.equal(providerMatchesSender(sanitized.provider, location.href), true);
assert.equal(JSON.stringify(sanitized).includes(prompt), false);
assert.equal(JSON.stringify(sanitized).includes(token), false);

let resolveAck;
const ackPromise = new Promise((resolve) => { resolveAck = resolve; });
let resolveSocketClose;
const socketClosed = new Promise((resolve) => { resolveSocketClose = resolve; });
class TrackingWebSocket extends WebSocket {
  constructor(url) {
    super(url);
    this.addEventListener("close", () => resolveSocketClose());
  }
}
const client = createWsClient({
  url: `ws://127.0.0.1:${port}`,
  WebSocketImpl: TrackingWebSocket,
  setTimeout: globalThis.setTimeout.bind(globalThis),
  clearTimeout: globalThis.clearTimeout.bind(globalThis),
  setInterval: globalThis.setInterval.bind(globalThis),
  clearInterval: globalThis.clearInterval.bind(globalThis),
  version: "pipeline-test",
  onState: (state) => { if (state === "online") client.send(sanitized); },
  onMessage: (message) => { if (message.type === "ack" && message.ref === "usage") resolveAck(message); },
});
client.start();
const ackMessage = await Promise.race([
  ackPromise,
  new Promise((_, reject) => setTimeout(() => reject(new Error("usage ack timeout")), 5000)),
]);
client.stop();
await Promise.race([
  socketClosed,
  new Promise((_, reject) => setTimeout(() => reject(new Error("websocket close timeout")), 3000)),
]);
assert.equal(ackMessage.accepted, undefined);
process.stdout.write(JSON.stringify({
  page_event: pageEvents[0], bridge_message: sanitized, server_ack: ackMessage,
  contains_prompt: false, contains_token: false,
}) + "\n");
