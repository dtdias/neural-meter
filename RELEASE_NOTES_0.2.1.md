# NeuralMeter 0.2.1

Patch release with updated ChatGPT capture cases and experimental Gemini counting.

## Downloads

- `NeuralMeter-Setup.exe`: per-user Windows installer; not code-signed.
- `NeuralMeter-Bridge-0.2.1.zip`: Chromium extension package.
- SHA-256 checksums retained as local release evidence; no checksum manifest published.

## Included

- User reports Claude capture working. ChatGPT capture has not been validated in a real browser; reported failure's root cause remains unknown. Changes cover cases supported by synthetic fixtures only.
- Gemini Web remains experimental: local submission counts persist; official quotas and reset times remain unknown.
- Passive browser observation only: no prompts sent, no inference requests, no session/cookie/user-token reads. Prompt text stays out of WebSocket, SQLite and logs.

## Validation limits

- Synthetic fixtures and local tests do not prove real-browser capture for ChatGPT or Gemini.
- Browser-to-HUD behavior and official provider quota/reset values remain unverified.
- Installer and executable are not signed.
