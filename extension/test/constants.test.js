import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PROVIDERS, WS_URL } from "../src/constants.js";

const manifest = JSON.parse(
  readFileSync(new URL("../manifest.json", import.meta.url), "utf8"),
);

test("WS_URL fixo em loopback", () => {
  assert.equal(WS_URL, "ws://127.0.0.1:8765");
  assert.deepEqual(PROVIDERS, ["claude", "chatgpt"]);
});

test("manifest MV3 com apenas 2 hosts", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, [
    "https://claude.ai/*",
    "https://chatgpt.com/*",
  ]);
});

test("manifest: service worker module e permissions alarms", () => {
  assert.equal(manifest.background.service_worker, "src/background.js");
  assert.equal(manifest.background.type, "module");
  assert.deepEqual(manifest.permissions, ["alarms"]);
});
