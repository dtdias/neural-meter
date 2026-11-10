import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

function pngSize(rel) {
  const buf = readFileSync(new URL(`../${rel}`, import.meta.url));
  assert.equal(buf.toString("latin1", 1, 4), "PNG");
  assert.equal(buf.toString("latin1", 12, 16), "IHDR");
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

test("manifest.icons aponta para PNGs com dimensoes corretas", () => {
  for (const n of [16, 32, 48, 128]) {
    assert.deepEqual(pngSize(manifest.icons[String(n)]), [n, n]);
  }
});

test("action.default_icon aponta para PNGs corretos", () => {
  for (const n of [16, 32]) {
    assert.deepEqual(pngSize(manifest.action.default_icon[String(n)]), [n, n]);
  }
  assert.equal(manifest.action.default_popup, undefined);
});
