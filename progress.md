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

## Fase 0 — Setup
- [x] 2026-11-09 — AGENTS.md e progress.md criados.
- [x] 2026-11-10 — uv, src-layout, extensão MV3 esqueleto. Estrutura de pastas (app desktop + extensão), `pyproject.toml`, dependências.
- [x] 2026-11-10 — pytest, ruff, seção Comandos no AGENTS.md. Ferramentas: testes, lint/format; comandos registrados no AGENTS.md.
- [x] 2026-11-10 — graphify update . ok (77 nós). Construir grafo graphify inicial (`/graphify .`) quando houver código.

## Fase 1 — Core e Extensão (MVP funcional)
- [ ] SQLite: schema `usage_events` / `provider_state` / índice (PRD §6) + migração inicial.
- [ ] Motor janela 5h — Claude: ciclo a partir da 1ª msg + sync reset oficial (RF01.2).
- [ ] Motor janela 5h — ChatGPT: expiração individual por modelo, 3h/5h configurável (RF01.4).
- [ ] Registro de timestamp por mensagem (RF01.1).
- [ ] Motor semanal 7 dias: cotas por modelo, histórico rolante, reset semanal (RF02.1–RF02.3).
- [ ] Servidor WebSocket local `127.0.0.1:8765` em thread de background.
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
