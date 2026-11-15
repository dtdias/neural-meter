# NeuralMeter

HUD local para acompanhar quotas de **Claude Code**, **Codex CLI** e **Antigravity IDE** no Windows. NeuralMeter não conecta sites, não monitora navegador e não envia prompts ou inicia inferência.

## Conexões

1. Abra NeuralMeter; HUD mostra três fontes separadas.
2. Clique **Gerenciar conexões** → **Procurar CLIs novamente**.
3. Revise os estados separados de instalação, autenticação, consentimento e quota. Clique **Conectar** ou **Conectar fontes detectadas**; coleta é opt-in e pode parar com **Desconectar**. **Atualizar quotas autorizadas** antecipa próxima consulta somente das fontes autorizadas.
4. Use **Quotas exibidas…** para escolher janelas/buckets. Seleção visual não ativa coleta.

Discovery verifica instalações locais em background. Consultas usam somente métodos read-only documentados/allowlisted: Claude Code OAuth de sessão já existente, Codex app-server gerido pelo CLI e serviço loopback identificado do Antigravity IDE. Sem login, refresh, setup de statusLine, varredura de portas, inferência ou alteração de processos de terceiros. Antigravity é experimental; quota indisponível ou desconhecida não vira `0%`. Gemini CLI e ChatGPT Web não são fontes suportadas.

Estados de instalação, consentimento e disponibilidade de quotas são distintos. A UI não declara quota confirmada só por detectar executável. Claude pode exigir assinatura OAuth compatível; auth de API key/terceiro pode não expor quotas. Codex e Antigravity também podem informar sessão/serviço ausente ou shape incompatível. Snapshots de quota ficam em memória; credenciais, identidade bruta e percentuais não são persistidos.

## Privacidade e dados

- Sem conexão com sites de IA, extensão, WebSocket local ou captura de prompts.
- Consultas de quota somente após consentimento explícito persistente por fonte.
- Credenciais não são copiadas para settings, logs, HUD ou SQLite.
- Dados históricos SQLite e settings existentes são preservados durante migração para CLI-only; nenhum dado é removido na inicialização.
- App consulta serviços estritamente locais/allowlisted ou endpoint oficial de quota Claude; nenhuma chamada dispara inferência.

## Instalação

Esta revisão CLI-only ainda não tem release/instalador publicado. Rode a partir do código-fonte ou use release anterior sabendo que ela ainda contém o fluxo Web legado; não substitua artefatos publicados. Instalador/executável futuros podem não ter assinatura digital; Windows pode exibir aviso de segurança.

Desinstalação: Configurações do Windows → Aplicativos → NeuralMeter. Dados em `%LOCALAPPDATA%\NeuralMeter` permanecem; remova manualmente apenas se desejar apagar dados locais.

## Desenvolvimento

Requer Windows, Python 3.12+ (projeto usa `uv`/Python 3.13), Node não é necessário.

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

O teste de desempenho usa fonte Qt sintética; não consulta quotas reais. Testes de integração browser/extension são N/A porque feature removida. Instalador gera somente Setup; ISCC ausente → exit 2 com instrução de instalação manual do Inno Setup.

## Escopo e limitações

- Claude Code: quota OAuth depende de autenticação/sessão local compatível; estado de autenticação não equivale a quota confirmada.
- Codex CLI: quotas retornadas pelo app-server oficial; não representa ChatGPT Web.
- Antigravity IDE: conector experimental de serviço local; API, disponibilidade e quotas reais não garantidas.
- Limites/reset omitidos, vencidos ou incompatíveis aparecem desconhecidos; não inferir `0%`.
- Meta de latência da suíte perf cobre apenas sinal sintético collector→Qt, não resposta real do provider.

Requisitos e decisões históricas em [`prd.md`](prd.md), progresso em [`progress.md`](progress.md), instruções de desenvolvimento em [`AGENTS.md`](AGENTS.md).
