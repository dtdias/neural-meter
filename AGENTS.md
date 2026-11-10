# AGENTS.md — NeuralMeter

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
- Fim de cada etapa (feito pelo builder, só com testes passando):
  1. Branch nova a partir de `dev` nomeada pelas funções: `feat/<fn1>-<fn2>` (snake_case→hífen, ≤60 chars; sem funções: `chore/<tema>`).
  2. Commit Conventional citando as funções → `git push -u origin <branch>` → `gh pr create --base dev` → `gh pr merge --merge --delete-branch` → `git pull --ff-only origin dev`.
  3. `main` nunca é tocada; sem `--force`; sem `git add -f`.
- **Nunca entram em commit**: `graphify-out/`, `opencode.json(c)`, `.opencode/` (já no `.gitignore`). Conferir `git diff --cached --name-only` antes de commitar. Entram: `AGENTS.md`, `prd.md`, `progress.md`, `.gitignore` (1ª etapa os commita).
- Remote `origin` = `github.com/dtdias/neural-meter`; branches `main` e `dev` existem; `gh` autenticado.

## Objetivo e progresso
- `prd.md` = objetivo final e fonte de verdade de requisitos (RF01–RF04, RNF01–RNF04, schema SQLite §6, roadmap §8). Não editar sem pedido do usuário.
- `progress.md` = estado de execução. Ao concluir etapa: marcar `[x]`, data (AAAA-MM-DD) e nota curta (arquivos/decisões). Etapa iniciada: `[~]`. Nova decisão técnica → seção "Decisões". Ler `progress.md` no início de toda sessão para saber a próxima etapa.

## Stack (decidida: PRD §5 Opção 1)
- Python: `python` no PATH é **3.15.2** (PySide6 pode não ter wheel). Usar sempre `uv` (0.11.32) com `.python-version` = 3.13: `uv run ...`, `uv add ...`. Node 26 / npm 11 disponíveis.
- Desktop: Python 3.12+, `PySide6` (HUD com `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool`, tray), `websockets` em thread de background, `sqlite3` nativo, build `.exe` via PyInstaller ou Nuitka.
- Extensão "NeuralMeter Bridge": Manifest V3 (Chrome/Edge/Brave/Opera), content script + service worker.

## Comandos
- Instalar deps: `uv sync`
- Rodar app: `uv run neuralmeter`
- Testes: `uv run pytest`
- 1 teste: `uv run pytest tests/test_config.py::<nome> -q`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Testes da extensão: `cd extension; npm test`
- Build: a definir (Fase 3)
- Console Windows: se `—` falhar na saída, definir `$env:PYTHONIOENCODING="utf-8"`.

## Restrições do produto que agentes erram
- **Zero token**: extensão só lê passivamente (headers, streams SSE, payloads de erro de limite, GETs de sessão/config). Nunca enviar prompts, nunca chamar endpoint que dispare inferência, nunca alterar comportamento da página.
- Não fazer requisições a claude.ai/chatgpt.com de fora do navegador (Cloudflare/TLS fingerprint/cookies HttpOnly bloqueiam) — tudo passa pela extensão.
- WebSocket fixo `ws://127.0.0.1:8765`; bind só em loopback, nunca `0.0.0.0`. Dados 100% locais.
- Privacidade: por padrão guardar só contagem de caracteres/tokens + metadados, não o texto do prompt.
- Persistir janelas 5h/7d em SQLite (reinício não zera). Usar schema de `prd.md` §6 (`usage_events`, `provider_state`, índice `idx_usage_provider_time`).
- Claude: peso por contexto acumulado da thread (histórico reenviado a cada turno); reset oficial do servidor sobrescreve estimativa. ChatGPT: cada mensagem expira individualmente por modelo (3h/5h configurável).
- Auto-calibração: ao detectar bloqueio real, cravar 100%, salvar horário de reset e ajustar teto.
- Cores: verde <70%, amarelo 70–89%, vermelho ≥90% ou bloqueado.
- Metas: <50 MB RAM, <1% CPU ocioso, <500 ms do envio no browser à atualização do HUD.

## Design e assets (MCP via `execute`)
- **aicanvas** = inspiração de design do HUD/popup/tray (layout, cores, tokens): `search_components`, `list_systems`, `get_system`, `list_templates`, `get_component`. Componentes são React/shadcn (.tsx): só referência visual — traduzir p/ PySide6/QSS; nunca rodar `get_install_command`/shadcn neste projeto.
- **vectorcraft** = criar imagens vetoriais (ícones Claude/ChatGPT do HUD, ícone tray, logo, ícones da extensão): `draw_shape`/`draw_path`/`set_paint` → conferir com `render_preview` → `export` (SVG; PNG via `scale` p/ 16/32/48/128 da extensão MV3). Vectorcraft não exporta `.ico`: gerar `.ico` (tray/exe) a partir dos PNGs com outra ferramenta. Salvar fonte editável com `save_file` (`.vectorcraft`).

## Ambiente
- Windows + PowerShell. `.gitattributes` normaliza LF (`text=auto`).
