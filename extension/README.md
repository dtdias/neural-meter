# NeuralMeter Bridge (extensão MV3)

Lê passivamente o uso de claude.ai e chatgpt.com e envia ao app local via `ws://127.0.0.1:8765`.

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

## Captura passiva (claude.ai)
- page_hook.js (MAIN world) envolve etch sem alterar request/resposta; ridge.js (ISOLATED) calcula contagens e envia usage/limit ao SW. Texto de prompt nunca sai da aba.
- Formato de API do Claude e mapeamento de limites: PREMISSA em progress.md ("Decis�es", 2026-11-10); calibrar com tr�fego real.
- Debug: no console da aba claude.ai, localStorage.neuralmeterDebug = "1" e recarregue; eventos/campos desconhecidos aparecem como console.debug("[NeuralMeter]", ...).
- Ap�s mudar a extens�o, recarregue-a em chrome://extensions e a aba do claude.ai.
