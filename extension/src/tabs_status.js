export function computeTabsStatus(urls) {
  const status = { claude: false, chatgpt: false };
  for (const raw of urls || []) {
    let u;
    try {
      u = new URL(raw);
    } catch {
      continue;
    }
    if (u.protocol !== "https:") continue;
    if (u.hostname === "claude.ai") status.claude = true;
    else if (u.hostname === "chatgpt.com") status.chatgpt = true;
  }
  return status;
}
