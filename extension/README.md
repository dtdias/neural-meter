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
