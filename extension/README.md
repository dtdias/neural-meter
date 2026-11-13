# NeuralMeter Bridge (extensão MV3)

Lê passivamente o uso de claude.ai e chatgpt.com e envia ao app local via `ws://127.0.0.1:8765`. Gemini web está em modo experimental.

Para usuarios finais: use o zip `NeuralMeter-Bridge-<versao>.zip` (gerado por `scripts/build_installer.py`; ver README raiz, secao Instalacao).

## Carregar sem empacotar
1. Abra `chrome://extensions` (ou `edge://extensions`), ative **Modo do desenvolvedor**.
2. **Carregar sem compactação** → selecione esta pasta `extension/`.
3. Badge: `OFF` cinza = sem app; `…` amarelo = conectado, sem aba claude/chatgpt; sem texto verde = conectado com aba.

## Testar conexão
1. Na raiz do repo: `uv run neuralmeter --server-only`.
2. Badge deve sair de `OFF`; abra uma aba de claude.ai/chatgpt.com para ficar verde.
3. Parar o servidor → `OFF`; reiniciar → reconecta sozinho (backoff 1–30 s, alarme de 1 min).

## Testes unitários
`cd extension; npm test`

## Gemini Web (experimental)
- Observa somente POST `StreamGenerate` em `gemini.google.com`; captura passiva fetch/XHR e lê `f.req` clonando/normalizando corpo dentro de 64 KiB.
- Base do envelope: fonte pública [Gemini-API, commit `f14269ab3271f7d481191610195bd8c9ffee02e4`](https://github.com/HanaokaYuzu/Gemini-API/tree/f14269ab3271f7d481191610195bd8c9ffee02e4); formato interno não é contrato oficial e pode mudar.
- Conta envios de texto localmente; prompt não vai para WebSocket/SQLite. HUD mostra contagem, tokens aproximados (~4 chars/token) e quota/reset como desconhecidos. Nenhum limite oficial, janela de cota ou bloqueio é inferido.
- Nenhum pedido ativo é feito pela extensão. Captura não validada em navegador real; parser usa fixtures sintéticas.
- Gemini parser roda em MAIN e só atravessa `postMessage` com contagens/metadados; texto, `f.req`, token `at` e corpo bruto ficam fora do handoff. Claude/ChatGPT XHR observam respostas textuais/error limitadas sem substituir event handlers da página.
- Status de aba Gemini também aciona badge verde. Gemini fica fora de diálogos de limite/calibração: nenhum limite/reset oficial é conhecido.

## Captura passiva (claude.ai)
- page_hook.js (MAIN world) envolve etch sem alterar request/resposta; ridge.js (ISOLATED) calcula contagens e envia usage/limit ao SW. Texto de prompt nunca sai da aba.
- Formato de API do Claude e mapeamento de limites: PREMISSA em progress.md ("Decis�es", 2026-11-10); calibrar com tr�fego real.
- Debug: no console da aba claude.ai ou chatgpt.com, localStorage.neuralmeterDebug = "1" e recarregue; eventos/campos desconhecidos aparecem como console.debug("[NeuralMeter]", ...).
- Ap�s mudar a extens�o, recarregue-a em chrome://extensions e a aba do claude.ai.

### Snapshots de cota (experimental)
- Inspiração arquitetural: [Splunk Token Meter, snapshot fixo `a34b14095a95bd21afdac60be8ca46e0cd5ae6c`](https://github.com/splunk/token-meter/tree/a34b14095a95bd21afdac60be8ca46e0cd5ae6c): adaptamos normalização de métricas/fonte e validade explícita por janela/modelo. Implementação NeuralMeter própria: extensão passiva → service worker → WS loopback → SQLite/HUD; sem copiar conector desktop, MCP, OAuth, tokens CLI, APIs externas Python ou endpoint Codex. Sem código substancial copiado.
- Observa somente GET JSON em host HTTPS exato `claude.ai`, rota `/api/organizations/{org}/usage`; verifica URL final pós-redirect, não lê Request body, não altera Promise nem Response.
- Parser aceita fixture sintética `five_hour`/`seven_day`/`seven_day_opus`/`seven_day_sonnet`, campos `utilization` e opcional `resets_at`; somente métricas permitidas seguem ao app.
- Rota/esquema PREMISSA, sem confirmação pública estática ou captura real. Org id HMAC-SHA256 com segredo aleatório em `chrome.storage.local`; WS/SQLite recebem somente hash. Toda resposta com identidade validada gera observação de identidade mesmo se corpo, content type, tamanho ou janelas forem inválidos; observação não cria percentual. Org id cru cruza só handoff transitório MAIN→ISOLATED→SW, nunca WS/DB. HUD só usa oficial com exatamente uma aba Claude confirmada; zero/múltiplas abas, identidade não confirmada ou SW offline = estimativa explícita. Troca sem nova observação permanece indetectável. ChatGPT segue estimado; nada de Codex.
- Frescor/reset resolvidos independentemente por 5h, semanal, Opus e Sonnet; tick do HUD aplica limite exato sem recarregar DB. Percentual de modelo fora de validade aparece somente como `snapshot desatualizado · origem oficial` (histórico; não estimativa atual); origem agregada permanece independente. Campo ausente/erro não implica 0; fallback/status explícito nos dois layouts.
- WS separado `quota_observation{provider,account_hash,observed_at}` + `quota{provider,account_hash,window,model?,percent,reset_at?,observed_at}`. `5h`/`weekly` agregados sem modelo; `opus`/`sonnet` exigem modelo correspondente. Snapshot não altera `usage_events` nem calibração; toast de reset oficial só sobrevive ao limite se mesma identidade e conexão segura ainda confirmadas.
- Transição insegura cancela geração renewal oficial; reconnect/status não rearmam. Só observação de quota nova posterior à invalidação cria nova geração; refresh de linhas/tick não contam.
