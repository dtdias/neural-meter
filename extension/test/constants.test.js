import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PROVIDERS, WS_URL } from "../src/constants.js";

const manifest = JSON.parse(
  readFileSync(new URL("../manifest.json", import.meta.url), "utf8"),
);

test("WS_URL fixo em loopback", () => {
  assert.equal(WS_URL, "ws://127.0.0.1:8765");
  assert.deepEqual(PROVIDERS, ["claude", "chatgpt", "gemini"]);
});

test("manifest MV3 com hosts Claude, ChatGPT e Gemini", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, [
    "https://claude.ai/*",
    "https://chatgpt.com/*",
    "https://gemini.google.com/*",
  ]);
  assert.deepEqual(manifest.content_scripts[0].matches, manifest.host_permissions);
  assert.deepEqual(manifest.content_scripts[1].matches, manifest.host_permissions);
  assert.ok(manifest.web_accessible_resources[0].resources.includes("src/gemini_capture.js"));
  assert.ok(manifest.web_accessible_resources[0].resources.includes("src/bridge_msg.js"));
});

test("manifest: service worker module e permissions necessárias", () => {
  assert.equal(manifest.background.service_worker, "src/background.js");
  assert.equal(manifest.background.type, "module");
  assert.deepEqual(manifest.permissions, ["alarms", "storage"]);
});
