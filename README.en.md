# NeuralMeter

Local web dashboard for read-only CLI data from **Claude Code**, **Codex CLI**, and opt-in **Antigravity CLI** history. It serves only on `127.0.0.1`; it does not monitor browsers, send prompts, or initiate inference.

## Local dashboard

1. Start `uv run neuralmeter`; open `http://127.0.0.1:8766/` (or URL printed by app).
2. Dashboard discovery does not enable collection. Explicitly opt in per source before read-only collection.
3. Consent, installation, authentication, and quota availability are distinct. Disconnect stops collection and clears displayed source data. Theme and period selection are visual preferences only.
4. Manual refresh respects per-source minimum read interval and provider `Retry-After`; rejected requests distinguish cooldown from missing opt-in.
5. Antigravity CLI history is local usage data, not official Gemini quota. Unknown or expired quota/reset remains unknown, never `0%`.

CLI readers use bounded, read-only methods. No login/refresh, setup automation, browser collection, port scanning, config/transcript edits, or interference with other processes. No Gemini CLI or ChatGPT Web source.

Installation, consent, and quota availability are distinct states. Detecting an executable does not confirm quota availability. Claude may require compatible OAuth subscription auth; API-key/third-party auth may not expose subscription quotas. Codex and Antigravity may report unavailable session/service or incompatible response shape. Quota snapshots stay in memory; credentials, raw identity, and percentages are not persisted.

## Privacy and data

- No AI website connection, extension, WebSocket, or prompt capture.
- HTTP server binds only to loopback; no LAN access.
- Quota queries run only after explicit persistent per-source consent.
- Credentials are not copied to settings, logs, HUD, or SQLite.
- Existing SQLite history and settings are preserved during CLI-only migration; startup removes no user data.
- Provider requests occur only after source-specific opt-in; Antigravity history reader accesses only allowlisted local transcript roots/layouts and does not persist transcript contents.

## Install

Run from source with `uv sync` then `uv run neuralmeter`. No installer/release status implied by this guide. Any installer may be unsigned; Windows may display a security warning.

Uninstall via Windows Settings → Apps → NeuralMeter. Data under `%LOCALAPPDATA%\NeuralMeter` remains; delete manually only if you want to remove local data.

## Development

Requires Windows, Python 3.12+ (`uv`/Python 3.13 configured). Node is not required.

```powershell
uv sync
uv run neuralmeter
uv run neuralmeter --self-test
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/perf_web_check.py --json
uv run --with playwright python scripts/browser_e2e.py
uv run python scripts/build_exe.py --smoke
uv run python scripts/build_installer.py [--skip-exe]
```

`perf_web_check.py` measures backend USS/CPU and loopback GET latency, not provider latency. `browser_e2e.py` uses synthetic temporary settings/transcript fixtures with headless Chrome, never the personal Chrome profile or real providers. Its 18-image matrix covers Graphite/Light/Aurora at 800×600 and 1440×900 target rasters with CSS `zoom` and DPR 1/1.5/2; CSS zoom is distinct from browser-toolbar zoom and physical DPI. Legacy browser/extension tests are N/A; local web UI E2E remains an applicable gate. Installer creates Setup only; missing ISCC exits 2 with manual Inno Setup installation guidance.

## Scope and limitations

- Claude Code: OAuth quota depends on compatible existing local auth; auth state is not quota confirmation.
- Codex CLI: quotas returned by official app-server; not ChatGPT Web.
- Antigravity CLI: bounded, consented local-history counts; official Gemini quota remains unknown.
- Missing, expired, or incompatible limits/resets remain unknown; never infer `0%`.
 - Perf latency gate measures local backend/loopback HTTP only, not real provider response or browser rendering.

Requirements and historical decisions: [`prd.md`](prd.md); progress: [`progress.md`](progress.md); development instructions: [`AGENTS.md`](AGENTS.md).
