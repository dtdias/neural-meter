# Progresso — NeuralMeter v1.0

Legenda: `[ ]` pendente · `[~]` em andamento · `[x]` concluído (AAAA-MM-DD — nota)
Objetivo final: `prd.md`.

## Decisões
- 2026-11-09 — Stack: Opção 1 do PRD (Python 3.12+ + PySide6 + websockets + sqlite3; extensão MV3).
- 2026-11-10 — Layout: projeto uv na raiz, pacote `src/neuralmeter/` (src-layout), testes `tests/`, extensão em `extension/`. Motivo: 1 repo, 1 lockfile, imports limpos.
- 2026-11-10 — Python fixo 3.13 (`.python-version`) via uv; `python` global 3.15 sem wheel PySide6.
- 2026-11-10 — Ferramentas Python: pytest (testes), ruff (lint+format, line-length 100). Deps runtime: `PySide6`, `websockets`. Sem pytest-asyncio por ora (testes de WS usam `asyncio.run`).
- 2026-11-10 — Extensão: JavaScript puro ES modules, sem bundler/TS (menos build, MV3 aceita direto); testes via `node --test` (Node 26). Navegadores alvo: Chromium (Chrome/Edge/Brave/Opera); Firefox fora do v1.0.
- 2026-11-10 — Dados locais em `%LOCALAPPDATA%\NeuralMeter\neuralmeter.db` (override por env `NEURALMETER_DATA_DIR`, usado nos testes).
- 2026-11-10 — DB: timestamps gravados em UTC, texto `YYYY-MM-DD HH:MM:SS` (mesmo formato de `CURRENT_TIMESTAMP`, comparável lexicograficamente); Python sempre passa `created_at` explícito. Migração via `PRAGMA user_version` (v1 = schema §6). WAL + `check_same_thread=False` + `threading.Lock` (WS thread e UI thread compartilham). Seed `provider_state` para `claude` e `chatgpt` com defaults §6.
- 2026-11-10 — Motor: funções puras em `src/neuralmeter/engine/` (recebem eventos+estado+`now`, sem I/O, sem relógio interno) + wrappers finos que leem DB. Claude 5h = janelas encadeadas: 1ª msg abre janela [t, t+5h); msg após fim abre nova. Lookback 24h. Reset oficial futuro em `provider_state.window_5h_reset_at` sobrescreve (janela = [reset-5h, reset)). Reset oficial passado = ignorado. `is_rate_limited=1` + reset futuro → 100%. Percentual limitado a [0,100].
- 2026-11-10 — Motor ChatGPT: janela deslizante por evento (cada msg expira em `created_at + window` do seu modelo). Limites por modelo em tabela `DEFAULT_CHATGPT_LIMITS` (match por prefixo mais longo, case-insensitive) sobrescrevível por parâmetro `limits`; persistência de overrides fica p/ etapa tray "limites manuais". Modelo desconhecido → janela 3h, teto = `provider_state.window_5h_max_units`. Agregado do provedor (barra HUD) = modelo com maior percentual. `reset_at` por modelo = msg mais antiga ativa + janela (próxima liberação). `is_rate_limited=1` + `window_5h_reset_at` futuro → agregado 100% com reset oficial.
- 2026-11-10 — PREMISSA (sem captura real de limites ChatGPT): defaults `gpt-4o`→3h/80, `gpt-4.1`→3h/80, `gpt-5`→3h/160, `o4-mini`→3h/150, `o3`→5h/100, default 3h. Cotas semanais (RF02) ficam p/ etapa do motor 7d. Valores só estimativa; calibração (RF04.2) ajusta.
- 2026-11-10 — Motor 7d: módulo único `engine/weekly.py` p/ ambos provedores. Histórico rolante = eventos em (now-7d, now]. Agregado: used=Σweight, teto=`provider_state.weekly_max_units`. `reset_at`: `weekly_reset_at` oficial futuro sobrescreve; senão msg mais antiga ativa + 7d (próxima liberação). `is_rate_limited=1` + `weekly_reset_at` futuro → 100% (bloqueio 5h usa `window_5h_reset_at`, independente). Tokens estimados = Σ max(prompt_chars, context_chars) // 4 (heurística ~4 chars/token).
- 2026-11-10 — PREMISSA cotas semanais por modelo (RF02.1), só estimativa: ChatGPT `o3`→100/sem, `o4-mini-high`→700/sem (expandível); Claude sem cota por modelo (só total da conta). Match por prefixo mais longo (reusar `_match_key` → tornar público `match_model_key` em `engine/window.py`). Persistência de overrides = etapa tray "limites manuais".
- 2026-11-10 — Protocolo WS v1 (JSON texto, 1 objeto/frame, campo `type`). Ext→App: `hello{v,client,version}`, `status{tabs:{claude,chatgpt}}`, `usage{provider,model,conversation_id?,prompt_chars,context_chars,ts?}`, `limit{provider,scope:"5h"|"weekly",reset_at(epoch ms),rate_limited?}`, `ping`. App→Ext: `ack{ref?}`, `pong`, `error{reason}`. `ts` epoch ms UTC; ausente/futuro>60s/anterior a 24h → hora do servidor. `weight_units`=1.0 até etapa peso progressivo (RF01.3). Texto do prompt nunca trafega (só contagens). Segurança: bind só `127.0.0.1`; handshake rejeita `Origin` que não seja `chrome-extension://…` (ausente permitido p/ testes/clientes locais); frame máx 64 KB. Motivo: impedir que sites conectem ao WS local via JS.
- 2026-11-10 — Servidor WS: `asyncio` + `websockets` (API `websockets.asyncio.server`) em `threading.Thread` daemon próprio; notifica UI via callback `on_update(kind, payload)` chamado na thread WS (HUD fará ponte p/ Qt signal). Parsing/validação = funções puras em `server/protocol.py` (TDD), aplicação no DB em `server/handler.py`.

## Fase 0 — Setup
- [x] 2026-11-09 — AGENTS.md e progress.md criados.
- [x] 2026-11-10 — uv, src-layout, extensão MV3 esqueleto. Estrutura de pastas (app desktop + extensão), `pyproject.toml`, dependências.
- [x] 2026-11-10 — pytest, ruff, seção Comandos no AGENTS.md. Ferramentas: testes, lint/format; comandos registrados no AGENTS.md.
- [x] 2026-11-10 — graphify update . ok (77 nós). Construir grafo graphify inicial (`/graphify .`) quando houver código.

## Fase 1 — Core e Extensão (MVP funcional)
- [x] 2026-11-10 — `src/neuralmeter/db.py` + `tests/test_db.py`. SQLite: schema `usage_events` / `provider_state` / índice (PRD §6) + migração inicial.
- [x] 2026-11-10 — Motor janela 5h — Claude: ciclo a partir da 1ª msg + sync reset oficial (RF01.2). `engine/{window,claude}.py`: `compute_claude_5h` (pura), `get_claude_5h_snapshot`, `sync_claude_official_reset`; `db.from_db_ts`. Testes em `tests/test_engine_claude.py`.
- [x] 2026-11-10 — Motor janela 5h — ChatGPT: expiração individual por modelo, 3h/5h configurável (RF01.4). `engine/chatgpt.py`: `compute_chatgpt_5h` (pura), `get_chatgpt_5h_snapshot`, `resolve_model_limit`, `sync_chatgpt_official_reset`, `ModelLimit`, `DEFAULT_CHATGPT_LIMITS`; helpers `weight`/`sync_official_reset` movidos p/ `engine/window.py`. Testes em `tests/test_engine_chatgpt.py`.
- [x] 2026-11-10 — `insert_usage_event` grava UTC. Registro de timestamp por mensagem (RF01.1).
- [x] Motor semanal 7 dias: cotas por modelo, histórico rolante, reset semanal (RF02.1–RF02.3). (2026-11-10: engine/weekly.py compute_weekly/get_weekly_snapshot/sync_weekly_reset/estimate_tokens; match_model_key em window.py)
- [x] Servidor WebSocket local `127.0.0.1:8765` em thread de background. (2026-11-10: `server/{protocol,handler,ws_server}.py`: parse_message, handle_message, WsServer/start_ws_server, check_origin; `uv run neuralmeter --server-only`)
- [ ] Extensão MV3: conexão automática ao WS + status online/aguardando aba.
- [ ] Extensão: captura passiva envio/modelo/tamanho em `claude.ai`.
- [ ] Extensão: captura passiva SSE `backend-api/conversation` em `chatgpt.com`.

## Fase 2 — Interface suspensa (Windows HUD)
- [ ] Janela frameless always-on-top com snap magnético nas bordas (RF03.1).
- [ ] Modo compacto/pílula (RF03.2).
- [ ] Modo expandido: 5h detalhado, barra semanal, último modelo, status conexão (RF03.3).
- [ ] Contagem regressiva de reset em tempo real (RF01.5).
- [ ] Cores semafóricas (RF04.1).
- [ ] Opacidade 20–100% + click-through (RF03.4).
- [ ] System tray: mostrar/ocultar, compacto, limites manuais, iniciar com Windows, sair (RF03.5).
- [ ] Salvar posição da janela.

## Fase 3 — Calibração inteligente e polimento
- [ ] Detecção de bloqueio (banner/JSON) + horário oficial de reset + auto-calibração do teto (RF04.2).
- [ ] Peso progressivo por tamanho de contexto no Claude (RF01.3).
- [ ] Toast do Windows ao renovar cota 5h (RF04.3).
- [ ] Opção de não salvar conteúdo de prompts (RNF03).
- [ ] Validar RNF01 (<50 MB RAM, <1% CPU) e RNF02 (<500 ms).
- [ ] Instalador `.exe` / `.msi` (PyInstaller ou Nuitka).
