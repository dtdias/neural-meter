# Evidência de validação manual

## Relato informado pelo usuário — 2026-11-13

- Claude: usuário relata teste manual funcionando.
- ChatGPT: usuário relata que teste manual não funcionou.
- Gemini: solicitado para inclusão; nenhuma validação manual relatada.

Limites: relato não inclui navegador/versão, sistema/conta/plano, roteiro, timestamp além da sessão, métricas, logs, HAR ou evidência de payload. Não permite afirmar qual etapa do Claude funcionou, cobertura de todas as cotas, isolamento de conta, nem causa específica da falha ChatGPT. Não foi executado browser real nesta validação de código. Fixtures automatizadas são sintéticas, não dados reais de usuário.

## Implementação Gemini experimental — 2026-11-13

Captura passiva observa `POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate` e interpreta formulário `f.req` como envelope JSON `[null, JSON.stringify(innerRequest)]`, contando texto em `innerRequest[0][0]`. Referência pública consultada: [HanaokaYuzu/Gemini-API `client.py`](https://github.com/HanaokaYuzu/Gemini-API/blob/f14269ab3271f7d481191610195bd8c9ffee02e4/src/gemini_webapi/client.py) e [`constants.py`](https://github.com/HanaokaYuzu/Gemini-API/blob/f14269ab3271f7d481191610195bd8c9ffee02e4/src/gemini_webapi/constants.py); commit `f14269ab3271f7d481191610195bd8c9ffee02e4`. Fonte usa AGPL-3.0-or-later; implementação NeuralMeter usa apenas formato factual observado, sem copiar código. Formato é interno, sujeito a mudança, não especificação oficial Google.

Gemini sem quota/reset/modelo confiáveis. MAIN extrai somente contagens antes do `postMessage`; prompt, `at`, `f.req` e corpo original não cruzam para listener. HUD mostra quotas desconhecidas e contagem de envios HTTP observados (retries com novos IDs podem contar como novo envio); tokens `~chars/4`, estimativa simples. Gemini fica fora de limite manual/calibração. Testes usam fixtures sintéticas derivadas da estrutura pública; não confirmam tráfego atual em browser. Nenhuma sessão autenticada, cookie, credencial ou inferência foi usada.

ChatGPT/Claude XHR usam `addEventListener` (sem trocar `onload`/`onerror` etc.), limite 2 MiB para resposta textual e 64 KiB para corpo de erro. `responseType` incompatível não é lido; status e fim ainda são encaminhados. Fixtures cobrem 429/reset, HTTP 500, erro de rede, body text e `responseType="blob"`. Reteste browser ChatGPT segue pendente; esses casos sintéticos não diagnosticam falha original.
