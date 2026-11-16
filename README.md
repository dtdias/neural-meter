# NeuralMeter

Dashboard web local para acompanhar fontes CLI **Claude Code**, **Codex CLI** e **Antigravity CLI** no Windows. NeuralMeter não captura conversas do navegador e não envia prompts ou inicia inferência.

## Conexões

1. Rode `uv run neuralmeter`; dashboard abre em `http://127.0.0.1:8766`.
2. Ative cada opção **Permitir leitura local** separadamente. Descoberta/instalação não ativa coleta.
3. **Atualizar autorizadas** solicita leitura consentida respeitando intervalo mínimo e `Retry-After`; resposta recusada informa aguarde ou ausência de opt-in. **Encerrar NeuralMeter** para servidor local.
4. Selecione período e tema. Tema é preferência visual, não opt-in.

Claude Code consulta quota oficial via sessão local existente; Codex CLI usa app-server oficial em modo read-only. Antigravity CLI lê apenas contagens/metadata de layouts de histórico allowlisted após opt-in próprio; quota oficial Antigravity/Gemini permanece desconhecida. Sem login, refresh, setup de statusLine, varredura de portas, inferência ou alteração de processos de terceiros. Gemini CLI standalone e ChatGPT Web não são fontes suportadas.

Estados de instalação, autenticação, consentimento, quota e histórico local são distintos. Dado ausente/expirado/incompatível aparece desconhecido, nunca `0%`. Snapshot de quota fica em memória; credenciais, identidade bruta, texto de conversas e caminhos locais não entram na API.

## Privacidade e dados

- Interface HTTP somente em `127.0.0.1`; sem captura de navegador/extensão/WebSocket ou conteúdo transmitido.
- Consultas de quota somente após consentimento explícito persistente por fonte.
- Credenciais não são copiadas para settings, logs, HUD ou SQLite.
- Dados históricos SQLite e settings existentes são preservados durante migração para CLI-only; nenhum dado é removido na inicialização.
- Conteúdo de histórico Antigravity pode ser lido em memória somente após consentimento independente; persistem somente consentimentos e preferências da interface, preservando settings anteriores.

## Instalação

Esta revisão web local está em desenvolvimento e não tem release/instalador publicado. Rode a partir do código-fonte; artefatos antigos não refletem esta interface. Instalador/executável futuros podem não ter assinatura digital; Windows pode exibir aviso de segurança.

Desinstalação: Configurações do Windows → Aplicativos → NeuralMeter. Dados em `%LOCALAPPDATA%\NeuralMeter` permanecem; remova manualmente apenas se desejar apagar dados locais.

## Desenvolvimento

Requer Windows, Python 3.12+ (projeto usa `uv`/Python 3.13); Node não é necessário.

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

`perf_web_check.py` mede USS/CPU do processo backend e latência GET loopback real; não mede latência de provider. `browser_e2e.py` usa Chrome headless + settings/transcript sintéticos temporários, sem perfil Chrome pessoal ou provider. Matriz gera 18 screenshots dos temas Grafite/Claro/Aurora nos rasters 800×600 e 1440×900, com CSS `zoom` e DPR 1/1.5/2; CSS zoom é distinto de zoom da toolbar e DPI físico. Testes browser/extension legados são N/A; E2E da UI web local é gate aplicável. Instalador gera somente Setup; ISCC ausente → exit 2 com instrução Inno Setup.

## Escopo e limitações

- Claude Code: quota OAuth depende de autenticação/sessão local compatível; estado de autenticação não equivale a quota confirmada.
- Codex CLI: quotas retornadas pelo app-server oficial; não representa ChatGPT Web.
- Antigravity CLI: parser bounded para histórico local, opt-in independente; tokens calculados por caracteres indicados com `~`; cobertura e semântica de eventos podem ser parciais. Quota oficial não disponível.
- Limites/reset omitidos, vencidos ou incompatíveis aparecem desconhecidos; não inferir `0%`.
- Gate de latência mede apenas backend+HTTP loopback, não resposta real do provider nem renderização de navegador.

Requisitos e decisões históricas em [`prd.md`](prd.md), progresso em [`progress.md`](progress.md), instruções de desenvolvimento em [`AGENTS.md`](AGENTS.md).
