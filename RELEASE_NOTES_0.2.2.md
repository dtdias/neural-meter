# NeuralMeter 0.2.2 (Experimental)

## Incluído

- Claude Code CLI, Codex CLI / Work e Antigravity (experimental) como fontes separadas; seleção independente das quotas/buckets exibidos e consentimento de coleta opt-in separado.
- Preferências de fontes/janelas/buckets exibidos persistem localmente. Snapshots CLI não são persistidos; fontes browser ficam separadas e Gemini Web não é tratado como Antigravity.
- Correções de lifecycle: callbacks antigos cercados por identidade/generation; discovery Codex não aceita entradas relativas de PATH/cwd como busca implícita.
- Bandeja: **Conectar Claude CLI**, **Conectar Codex CLI**, **Conectar Antigravity (experimental)** habilitam coleta individualmente. **Quotas exibidas…** abre seleção independente de fontes/janelas/buckets; selecionar não habilita coleta.
- Coleta CLI opt-in, desligada por padrão; sem geração/inferência. O conector Claude CLI lê autenticação local existente em `~/.claude/.credentials.json` para autorizar consulta de quota. Token fica somente em memória; não é copiado para configurações, SQLite, logs ou HUD. Não lê cookies nem transcrições de prompts. Codex usa autenticação gerida pelo CLI oficial; Antigravity usa segredo transitório do serviço local identificado.

## Teste de instalação/atualização

1. Antes de instalar/atualizar, feche manualmente processos antigos do NeuralMeter. Build não encerra processos.
2. Instale `NeuralMeter-Setup.exe` ou atualize instalação existente.
3. Extraia `NeuralMeter-Bridge-0.2.2.zip`; substitua arquivos na pasta da extensão já carregada, ou carregue sem compactação a pasta extraída em `chrome://extensions` / `edge://extensions`. Recarregue NeuralMeter Bridge e abas de provedor. ZIP não é instalado diretamente pelo navegador.
4. Na bandeja, habilite conectores só quando desejado; configure **Quotas exibidas…** separadamente.

## Limites de validação e ressalvas

- Auditoria anterior: **APROVADO COM RESSALVAS**; aprovação desta preparação/publicação ainda pendente.
- Claude CLI local `not_authenticated`; quota autenticada não validada.
- Antigravity: `server_not_found`; conector API interna experimental validado com fixtures, não RPC real. Checagem PID/listener antes de TCP não elimina TOCTOU local.
- Codex validado em consulta local anterior; não representa quota atual. Protocolo app-server oficial sem geração.
- Capturas ChatGPT Web e Gemini Web reais pendentes. Codex não equivale a ChatGPT Web. Gemini Web continua experimental; quota/reset desconhecidos.
- Coleta CLI opt-in e desligada por padrão; Claude CLI lê autenticação local existente para quota, token apenas em memória. Sem leitura de cookies ou transcrições de prompts.
- Gate de desempenho pendente nesta validação: porta 8765 ocupada; processo preservado. Não interpretar como aprovação de desempenho.
- Warnings PyInstaller opcionais serão preservados integralmente; smoke não prova inocuidade de todos imports opcionais. Verificação do pacote executável limitada a inventário/TOC ou import frozen safe, não quota real nem inferência.
- Instalador/EXE sem assinatura. Sem instalação, desinstalação, extração literal de setup ou alteração de processos durante validação.
- Checksums são evidência local; nenhum `SHA256SUMS` público.

## Assets previstos

- `NeuralMeter-Setup.exe` (alias estável do instalador desta versão).
- `NeuralMeter-Bridge-0.2.2.zip`.

Release e assets ainda não publicados nesta etapa. Source archives automáticos do GitHub não são assets manuais.
