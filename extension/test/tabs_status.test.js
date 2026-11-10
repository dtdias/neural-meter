import assert from "node:assert/strict";
import test from "node:test";

import { computeTabsStatus } from "../src/tabs_status.js";

test("urls mistas", () => {
  assert.deepEqual(
    computeTabsStatus(["https://claude.ai/chat/1", "https://example.com/", "lixo"]),
    { claude: true, chatgpt: false },
  );
  assert.deepEqual(
    computeTabsStatus(["https://chatgpt.com/c/1", "https://claude.ai/new"]),
    { claude: true, chatgpt: true },
  );
});

test("host falso, http e subdominio nao contam", () => {
  assert.deepEqual(computeTabsStatus(["https://evil.com/claude.ai"]), {
    claude: false,
    chatgpt: false,
  });
  assert.deepEqual(computeTabsStatus(["http://claude.ai"]), { claude: false, chatgpt: false });
  assert.deepEqual(computeTabsStatus(["https://www.claude.ai/", "https://x.chatgpt.com/"]), {
    claude: false,
    chatgpt: false,
  });
});

test("lista vazia", () => {
  assert.deepEqual(computeTabsStatus([]), { claude: false, chatgpt: false });
});
