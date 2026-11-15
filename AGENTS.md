# AGENTS.md — NeuralMeter

## Escopo de produto vigente (2026-11-15)
- CLI-only: Claude Code, Codex CLI, Antigravity IDE (experimental). Browser/Web/extensão/MV3/WebSocket removidos; release notes antigas são histórico e não devem ser reescritas.
- HUD lista fontes detectáveis; descoberta local não autoriza coleta. Consentimento explícito opt-in persiste por fonte. Seleção de quotas não habilita conexão.
- Proibidos: prompt/inferência, login/refresh, browser endpoints, setup automático, alteração de settings/transcripts, varredura de portas e encerramento de processos do usuário.
- Auth, instalação, consentimento e disponibilidade de quota são estados distintos. Dados/reset ausentes, expirados ou incompatíveis permanecem unknown, nunca `0%`.
- SQLite/settings existentes preservados. Startup CLI-only não aplica purge/retention destrutiva. Installer produz somente NeuralMeter Setup.
- Testes removidos de browser/extensão são N/A por feature removida, não aprovados.

## Regras obrigatórias de sessão
- **Caveman ultra obrigatório**: toda resposta em modo `/caveman ultra`. Exceções: código, commits, PRs, avisos de segurança/ações irreversíveis (escrever normal).
- **Graphify obrigatório**: antes de responder qualquer pergunta sobre arquitetura/código ou antes de editar, consultar o grafo: `graphify query "<pergunta>"` (também `graphify explain "X"`, `graphify path "A" "B"`).
  - Se `graphify-out/graph.json` não existir e já houver código: construir com `/graphify .` (skill graphify).
  - Após mudanças de código: `graphify update .` (sem LLM; `--force` se removeu código).
- **Headroom obrigatório**: saída grande (>~4 KB: logs, testes, build, grep, JSON, diffs) passa por `headroom_compress` (via `execute`) antes de entrar no contexto. `headroom_retrieve <hash>` em vez de re-rodar comando. Arquivo a editar / bytes exatos: ler direto.

## Loop autônomo Plan (Opus) ↔ Build (Sonnet)
- Iniciar: `/loop` (ou sessão nova: `default_agent` = `autopilot`). Config em `opencode.jsonc` + `.opencode/agents/{autopilot,planner,builder}.md` + `.opencode/commands/loop.md` (todos locais, fora do git).
- Ciclo: `autopilot` (só orquestra) → `planner` (`claude-agent/opus` = Opus 5.5 conectado, não edita código; só `progress.md`) devolve ESPECIFICAÇÃO DE ETAPA → `builder` (`claude-agent/sonnet` = Sonnet 5.5 conectado) implementa → `ETAPA_OK`/`ETAPA_FALHA` → volta ao planner. Repete até o planner responder `PROJETO_COMPLETO` (ou `BLOQUEADO_TOTAL`; 3 falhas seguidas na mesma etapa também param).
- **Sem perguntas ao usuário** (`question` negado). Toda decisão em aberto é do planner e vai para "Decisões" do `progress.md`; dependência externa faltando = `PREMISSA` explícita + adaptador substituível, não bloqueio.
- Fim de etapa: branch `feat/<funções>` de `dev`; fase de implementação fica sem commit/push/PR/merge até auditoria explícita aprovada. Após aprovação, Conventional Commit → PR base `dev` → merge commit → atualizar `dev`; `main` nunca é tocada. Sem force ou `git add -f`.
- **Nunca entram em commit**: `graphify-out/`, `opencode.json(c)`, `.opencode/` (já no `.gitignore`). Conferir `git diff --cached --name-only` antes de commitar. Entram: `AGENTS.md`, `prd.md`, `progress.md`, `.gitignore` (1ª etapa os commita).
- Remote `origin` = `github.com/dtdias/neural-meter`; branches `main` e `dev` existem; `gh` autenticado.

## Objetivo e progresso
- `prd.md` = fonte de requisitos; seção 0 CLI-only vigente supersede requisitos browser históricos. Alterações de escopo exigem pedido do usuário.
- `progress.md` = estado de execução. Ao concluir etapa: marcar `[x]`, data (AAAA-MM-DD) e nota curta (arquivos/decisões). Etapa iniciada: `[~]`. Nova decisão técnica → seção "Decisões". Ler `progress.md` no início de toda sessão para saber a próxima etapa.

## Stack (decisão vigente: CLI-only, supersedes PRD historical stack)
- Python: `python` no PATH é **3.15.2** (PySide6 pode não ter wheel). Usar sempre `uv` (0.11.32) com `.python-version` = 3.13: `uv run ...`, `uv add ...`. Node 26 / npm 11 disponíveis.
- Desktop: Python 3.12+, `PySide6` (HUD com `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool`, tray), CLI collectors em background, `sqlite3` nativo, build `.exe` via PyInstaller ou Nuitka.

## Comandos
- Instalar deps: `uv sync`
- Rodar app: `uv run neuralmeter`
- Testes: `uv run pytest`
- 1 teste: `uv run pytest tests/test_config.py::<nome> -q`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- `--server-only` legado retorna exit 2, sem bind/socket.
- Desempenho CLI-only (fonte Qt sintética): `uv run python scripts/perf_check.py [--json] [--report-only]`; teste perf: `uv run pytest -m perf -q`.
- Gerar ícones (PNG/ICO a partir de assets/logo.svg): `uv run python scripts/build_icons.py`
- Build exe: `uv run python scripts/build_exe.py --smoke` (gera `dist/NeuralMeter.exe`; `--self-test` = smoke sem UI)
- Build installer: `uv run python scripts/build_installer.py [--skip-exe]` (gera somente `dist/NeuralMeter-Setup-<v>.exe`; ISCC ausente → exit 2 e dica winget)
- Console Windows: se `—` falhar na saída, definir `$env:PYTHONIOENCODING="utf-8"`.

## Restrições do produto que agentes erram
- Claude quota: session OAuth local existente; allowlisted GET quota; sem login/refresh. Codex: official app-server read-only com `refreshToken:false`; sem raw auth JSON. Antigravity: only identified literal loopback listener; experimental, no port scan.
- Dados locais. Nunca persistir tokens, snapshots de quota ou identidade bruta. Preservar banco/settings legados; não rodar retenção destrutiva na inicialização CLI-only.
- Collector workers devem permanecer generation-fenced, owned até finalização, no máximo um ativo por fonte e todos parados/joined no shutdown. Não matar processo de CLI/IDE do usuário.
- Metas de performance se reportam com medida real e escopo; fonte sintética não prova latência real do provider.

## Design e assets (MCP via `execute`)
- **aicanvas** = inspiração visual do HUD/tray; traduzir referências React/shadcn para PySide6/QSS, sem instalar shadcn neste projeto.
- **vectorcraft** = assets de logo, HUD, tray e executável; exportar SVG/PNG e `.ico` local. Nenhum asset de extensão.

## Ambiente
- Windows + PowerShell. `.gitattributes` normaliza LF (`text=auto`).
