# NeuralMeter

Local Windows HUD for quotas from **Claude Code**, **Codex CLI**, and **Antigravity IDE**. NeuralMeter does not connect to AI websites, monitor browsers, send prompts, or initiate inference.

## Connections

1. Open NeuralMeter; HUD shows three separate sources.
2. Select **Gerenciar conexões** → **Procurar CLIs novamente**.
3. Review separate installation, authentication, consent, and quota states. Choose **Conectar** or **Conectar fontes detectadas**; collection is opt-in and can stop with **Desconectar**. **Atualizar quotas autorizadas** triggers an early read-only query only for opted-in sources.
4. Use **Quotas exibidas…** to choose windows/buckets. Display selection does not enable collection.

Discovery checks local installations in a background task. Queries use read-only documented/allowlisted methods: existing Claude Code OAuth session, Codex app-server managed by the CLI, and identified Antigravity IDE loopback service. No login, refresh, statusLine setup, port scanning, inference, or interference with other processes. Antigravity is experimental; missing/unknown quota never becomes `0%`. Gemini CLI and ChatGPT Web are not supported sources.

Installation, consent, and quota availability are distinct states. Detecting an executable does not confirm quota availability. Claude may require compatible OAuth subscription auth; API-key/third-party auth may not expose subscription quotas. Codex and Antigravity may report unavailable session/service or incompatible response shape. Quota snapshots stay in memory; credentials, raw identity, and percentages are not persisted.

## Privacy and data

- No AI website connection, extension, local WebSocket, or prompt capture.
- Quota queries run only after explicit persistent per-source consent.
- Credentials are not copied to settings, logs, HUD, or SQLite.
- Existing SQLite history and settings are preserved during CLI-only migration; startup removes no user data.
- Requests are limited to allowlisted local services or official Claude quota endpoint; no inference is triggered.

## Install

This CLI-only revision has no published release/installer yet. Run from source or use a previous release knowing it still includes legacy Web flow; do not replace published artifacts. Future installer/executable may be unsigned; Windows may display a security warning.

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
uv run python scripts/perf_check.py --json
uv run python scripts/build_exe.py --smoke
uv run python scripts/build_installer.py [--skip-exe]
```

Performance check uses a synthetic Qt source; it does not query real quotas. Browser/extension integration tests are N/A because that feature was removed. Installer creates Setup only; missing ISCC exits 2 with manual Inno Setup installation guidance.

## Scope and limitations

- Claude Code: OAuth quota depends on compatible existing local auth; auth state is not quota confirmation.
- Codex CLI: quotas returned by official app-server; not ChatGPT Web.
- Antigravity IDE: experimental local-service connector; API, availability, and real quotas are not guaranteed.
- Missing, expired, or incompatible limits/resets remain unknown; never infer `0%`.
- Perf latency gate measures synthetic collector→Qt signal only, not real provider response.

Requirements and historical decisions: [`prd.md`](prd.md); progress: [`progress.md`](progress.md); development instructions: [`AGENTS.md`](AGENTS.md).
