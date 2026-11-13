import { providerMatchesSender, sanitizeBridgeMessage, sanitizeQuotaCandidate } from "./bridge_msg.js";
import { processClaudeQuotaCandidate } from "./quota_capture.js";
import { WS_URL } from "./constants.js";
import { computeTabsStatus } from "./tabs_status.js";
import { createWsClient } from "./ws_client.js";

const BADGES = {
  offline: { text: "OFF", color: "#808080" },
  waiting: { text: "…", color: "#d4a017" },
  active: { text: "", color: "#2e7d32" },
};

let hasTabs = false;

function updateBadge() {
  let b = BADGES.offline;
  if (client.state === "online") b = hasTabs ? BADGES.active : BADGES.waiting;
  chrome.action.setBadgeText({ text: b.text });
  chrome.action.setBadgeBackgroundColor({ color: b.color });
}

const client = createWsClient({
  url: WS_URL,
  WebSocketImpl: WebSocket,
  setTimeout: globalThis.setTimeout.bind(globalThis),
  clearTimeout: globalThis.clearTimeout.bind(globalThis),
  setInterval: globalThis.setInterval.bind(globalThis),
  clearInterval: globalThis.clearInterval.bind(globalThis),
  now: Date.now,
  rand: Math.random,
  version: chrome.runtime.getManifest().version,
  onState: updateBadge,
});

async function refreshTabs() {
  const tabs = await chrome.tabs.query({
    url: ["https://claude.ai/*", "https://chatgpt.com/*"],
  });
  const status = computeTabsStatus(tabs.map((t) => t.url));
  const claudeCount = tabs.filter((tab) => {
    try {
      const u = new URL(tab.url);
      return u.protocol === "https:" && u.hostname === "claude.ai" && !u.port && !u.username && !u.password;
    } catch { return false; }
  }).length;
  hasTabs = status.claude || status.chatgpt;
  client.setTabs({ ...status, claude_count: claudeCount });
  updateBadge();
}

let debounceTimer = null;
function scheduleRefresh() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    refreshTabs().catch(() => {});
  }, 500);
}

chrome.tabs.onUpdated.addListener((_id, changeInfo) => {
  if (changeInfo.url || changeInfo.status === "complete") scheduleRefresh();
});
chrome.tabs.onRemoved.addListener(scheduleRefresh);
chrome.runtime.onStartup.addListener(scheduleRefresh);
chrome.runtime.onInstalled.addListener(scheduleRefresh);

chrome.alarms.create("nm-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "nm-keepalive" && client.state === "offline") client.start();
});

let quotaSecretPromise;
function getQuotaSecret() {
  if (!quotaSecretPromise) quotaSecretPromise = (async () => {
    const stored = await chrome.storage.local.get("nmQuotaSecret");
    if (Array.isArray(stored.nmQuotaSecret) && stored.nmQuotaSecret.length === 32 &&
        stored.nmQuotaSecret.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255)) {
      return new Uint8Array(stored.nmQuotaSecret);
    }
    const secret = crypto.getRandomValues(new Uint8Array(32));
    await chrome.storage.local.set({ nmQuotaSecret: Array.from(secret) });
    return secret;
  })();
  return quotaSecretPromise;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const u = (sender && sender.tab && sender.tab.url) || "";
  const candidate = sanitizeQuotaCandidate(msg, u);
  if (candidate) {
    (async () => {
      const messages = await processClaudeQuotaCandidate(candidate.orgId, candidate.body, await getQuotaSecret());
      for (const message of messages) {
        if (message.nm === "quotaObservation") {
          if (providerMatchesSender("claude", u)) client.send({ type: "quota_observation", ...message.payload });
          continue;
        }
        const safe = sanitizeBridgeMessage(message);
        if (safe && providerMatchesSender(safe.provider, u)) client.send(safe);
      }
    })().then(
      () => sendResponse({ ok: true }),
      () => sendResponse({ ok: false }),
    );
    return true;
  }
  const m = sanitizeBridgeMessage(msg);
  if (m && providerMatchesSender(m.provider, u)) client.send(m);
});

globalThis.nmSend = client.send;

updateBadge();
client.start();
scheduleRefresh();
