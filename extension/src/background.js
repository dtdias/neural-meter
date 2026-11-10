import { sanitizeBridgeMessage } from "./bridge_msg.js";
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
  hasTabs = status.claude || status.chatgpt;
  client.setTabs(status);
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

chrome.runtime.onMessage.addListener((msg, sender) => {
  const u = (sender && sender.tab && sender.tab.url) || "";
  if (!u.startsWith("https://claude.ai/") && !u.startsWith("https://chatgpt.com/")) return;
  const m = sanitizeBridgeMessage(msg);
  if (m) client.send(m);
});

globalThis.nmSend = client.send;

updateBadge();
client.start();
scheduleRefresh();
