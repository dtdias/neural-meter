# neural-meter

Medidor local de uso/limites de Claude e ChatGPT (HUD sempre visivel). Zero token: so leitura passiva via extensao de navegador.

## Instalacao

1. Rode `NeuralMeter-Setup-<versao>.exe`. Instala por usuario em `%LOCALAPPDATA%\Programs\NeuralMeter`, sem admin. Tarefas opcionais: atalho na area de trabalho e "Iniciar com o Windows".
   - O instalador nao e assinado digitalmente: o SmartScreen pode avisar ("Windows protegeu seu PC"). Clique em **Mais informacoes** -> **Executar assim mesmo**.
2. Instale a extensao **NeuralMeter Bridge** (Chrome/Edge/Brave/Opera):
   1. Descompacte `NeuralMeter-Bridge-<versao>.zip` em uma pasta fixa (nao apague depois).
   2. Abra `chrome://extensions` (ou `edge://extensions`) e ative **Modo do desenvolvedor**.
   3. **Carregar sem compactacao** e selecione a pasta descompactada.
3. Abra o NeuralMeter e use claude.ai / chatgpt.com normalmente.

**Desinstalar:** Configuracoes do Windows -> Aplicativos -> NeuralMeter (ou `unins000.exe` na pasta de instalacao). Seus dados em `%LOCALAPPDATA%\NeuralMeter` sao mantidos; apague a pasta manualmente para remover tudo.

### Gerar o instalador (dev)

`uv run python scripts/build_installer.py [--skip-exe]` — requer Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e --scope user`). Saidas em `dist/`.
