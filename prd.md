# Documento de Requisitos de Produto (PRD) — NeuralMeter v1.0

**Produto:** NeuralMeter  
**Plataforma:** Windows Desktop + Extensão de Navegador (Chromium/Firefox)  
**Versão:** 1.0 (MVP)  
**Status:** Especificação  

---

## 1. Visão Geral e Objetivos

O **NeuralMeter** é um utilitário desktop leve para Windows que monitora, em tempo real e sem consumo de tokens, os limites de uso de **5 horas** e **semanais** do Claude (`claude.ai`) e do ChatGPT (`chatgpt.com`) diretamente a partir da sessão ativa do navegador do usuário.

### 1.1. Problema
Usuários dos planos web (Claude Pro/Max/Team e ChatGPT Plus/Pro/Team) sofrem com a falta de visibilidade clara sobre quantas interações ou volume de contexto restam antes de atingirem o bloqueio temporário de uso (rate limit). Além disso, consultar a IA via API ou enviar mensagens de teste consome a própria franquia que o usuário deseja economizar.

### 1.2. Objetivos Principais
* **Consumo Zero de Tokens:** Toda coleta de dados deve ocorrer de forma 100% passiva, interceptando metadados do tráfego de rede existente na aba do navegador e lendo endpoints internos de configuração que não acionam inferência de modelos.
* **Visibilidade Sempre Ativa (HUD):** Fornecer uma janela flutuante compacta (*Always-on-Top*) no Windows, integrada à bandeja do sistema (*System Tray*).
* **Unificação Multi-IA:** Exibir lado a lado, em um único painel suspenso, as métricas da janela de 5 horas e da janela semanal tanto do Claude quanto do ChatGPT.

---

## 2. Arquitetura Zero-Token (Conexão Nativa via Browser)

Como as plataformas web utilizam proteções rigorosas (Cloudflare Turnstile, TLS Fingerprinting e cookies `HttpOnly`), realizar requisições externas por fora do navegador gera bloqueios frequentes. Para contornar isso de forma nativa e segura, o sistema é dividido em dois módulos locais que se comunicam via **WebSocket (`ws://127.0.0.1:8765`)**.

```
[ Aba Claude.ai / ChatGPT.com ]
              │ (Leitura passiva de headers/streams e endpoints GET de sessão)
              ▼
[ Extensão NeuralMeter Bridge (Manifest V3) ]
              │ (WebSocket Local: 127.0.0.1:8765)
              ▼
[ App Desktop NeuralMeter (Windows) ] ──► [ Banco Local SQLite ]
              │
              ▼
[ Popup Flutuante (Always-on-Top) + System Tray ]
```

### 2.1. Módulo Collector (Browser Bridge)
* **Extensão Leve (Manifest V3):** Roda silenciosamente no navegador padrão do usuário (Chrome, Edge, Brave, Opera) onde a sessão já está autenticada.
* **Interceptação Passiva:** Escuta os eventos de envio e resposta nas abas `claude.ai` e `chatgpt.com` sem modificar o comportamento da página e sem injetar prompts.
* **Sincronização de Limites Oficiais:** Captura automaticamente cabeçalhos de rate-limit, payloads de erro de limite atingido e timestamps exatos de reset retornados pelas APIs internas das plataformas.

### 2.2. Estratégia de Coleta por Plataforma

| Plataforma | Fonte Primária (Rede) | Dados Extraídos | Janela de 5 Horas | Janela Semanal |
| :--- | :--- | :--- | :--- | :--- |
| **Claude Web** | Rotas de organização, limites e stream de conversa | Headers de rate-limit, timestamp de reset, modelo usado e tamanho do contexto | Calculada via reset oficial do servidor + peso acumulado do contexto | Soma de mensagens e estimativa de tokens por modelo nos últimos 7 dias |
| **ChatGPT Web** | Rotas de conta/limites e stream SSE (`backend-api/conversation`) | Status da conta, modelo utilizado (`gpt-4o`, `o1`, `o3-mini`, etc.) e avisos de limite | Janela deslizante de 3h a 5h por mensagem enviada + sincronização de reset | Agregação de cotas de modelos com trava semanal e volume total de 7 dias |

---

## 3. Requisitos Funcionais (RF)

### RF01 — Monitoramento da Janela de 5 Horas
* **RF01.1 (Cálculo de Janela Deslizante):** O sistema deve registrar o timestamp exato de cada mensagem enviada pelo usuário no navegador.
* **RF01.2 (Ciclo do Claude):** Deve iniciar o cronômetro de 5 horas a partir da primeira mensagem enviada em uma nova janela de uso e sincronizar o horário de reset caso o backend do Claude informe o timestamp oficial.
* **RF01.3 (Ponderação por Contexto no Claude):** Como o Claude consome a cota de 5 horas com base no tamanho total da conversa (histórico reenviado a cada turno), o sistema deve estimar o peso da mensagem somando os caracteres acumulados naquela thread.
* **RF01.4 (Ciclo do ChatGPT):** Deve contabilizar cada mensagem enviada por modelo e expirar individualmente cada interação após a janela correspondente (padrão de 3h ou 5h, configurável por modelo).
* **RF01.5 (Cronômetro de Reset em Tempo Real):** Exibir uma contagem regressiva dinâmica (ex: `Reseta em 01h 42m`) indicando quando a cota será renovada.

### RF02 — Monitoramento de Uso Semanal (7 Dias)
* **RF02.1 (Cotas Semanais de Modelos):** Rastrear limites específicos de modelos que operam com franquia semanal (ex: modelos de raciocínio ou limites semanais da conta).
* **RF02.2 (Histórico Rolante de 7 Dias):** Exibir o total de interações e estimativa de tokens consumidos nos últimos 7 dias para cada provedor.
* **RF02.3 (Reset Semanal):** Permitir a visualização de quando as cotas semanais serão liberadas.

### RF03 — Interface Suspensa no Windows (Floating Popup)
* **RF03.1 (Always-on-Top):** Janela sem bordas (*frameless*) que permanece sobreposta a todas as outras janelas do Windows, podendo ser posicionada em qualquer canto da tela (com *snap* magnético nas bordas do monitor).
* **RF03.2 (Modo Compacto / Pílula):** Visualização mínima exibindo apenas os ícones do Claude e do ChatGPT, suas respectivas barras de progresso da janela de 5 horas e a porcentagem restante.
* **RF03.3 (Modo Expandido):** Ao passar o mouse (*hover*) ou clicar para expandir, o painel exibe:
  * Uso detalhado das 5 horas (porcentagem, mensagens estimadas restantes e relógio de reset).
  * Barra de progresso do uso semanal (7 dias).
  * Último modelo utilizado em cada plataforma.
  * Status de conexão com o navegador (Online / Aguardando aba).
* **RF03.4 (Controle de Opacidade e Click-Through):** Permitir ajustar a transparência da janela (de 20% a 100%) e ativar o modo *Click-Through* (cliques atravessam o popup para não atrapalhar o uso de outros programas abaixo dele).
* **RF03.5 (Bandeja do Sistema / System Tray):** Ícone ao lado do relógio do Windows com menu de contexto para: Mostrar/Ocultar HUD, Alternar Modo Compacto, Configurar Limites Manuais, Iniciar com o Windows e Sair.

### RF04 — Sistema de Alertas e Calibração
* **RF04.1 (Cores Semafóricas):**
  * **Verde:** Uso abaixo de 70% da capacidade estimada.
  * **Amarelo:** Uso entre 70% e 89% (alerta de atenção).
  * **Vermelho:** Uso igual ou superior a 90% ou conta temporariamente bloqueada.
* **RF04.2 (Auto-Calibração):** Caso o usuário atinja o limite real na interface do Claude ou ChatGPT antes de a barra chegar a 100%, a extensão detecta o banner/JSON de bloqueio, crava a barra em 100% imediatamente, captura o horário exato de desbloqueio e ajusta o teto estimado para os próximos ciclos.
* **RF04.3 (Notificação de Cota Renovada):** Emitir uma notificação discreta do Windows (Toast Notification) quando o cronômetro de 5 horas zerar.

---

## 4. Requisitos Não-Funcionais (RNF)

* **RNF01 — Desempenho e Consumo de Recursos:** O aplicativo desktop deve consumir menos de 50 MB de memória RAM e menos de 1% de CPU em estado ocioso.
* **RNF02 — Latência de Atualização:** O tempo entre o envio de um prompt no navegador e a atualização visual na barra do popup não deve ultrapassar 500 milissegundos.
* **RNF03 — Privacidade e Segurança:** 100% dos dados devem permanecer na máquina local (`localhost`). O programa não deve salvar o conteúdo confidencial dos prompts caso o usuário opte por registrar apenas a contagem de caracteres/tokens e metadados.
* **RNF04 — Persistência:** Todos os dados das janelas de 5 horas e 7 dias devem ser persistidos em disco (`SQLite`) para que o fechamento do app ou reinicialização do Windows não zere os contadores.

---

## 5. Stack Tecnológica Recomendada

### Opção 1: Python Nativo (Desenvolvimento Rápido e Leve)
* **Linguagem:** Python 3.12+
* **Interface Gráfica (HUD & Tray):** `PySide6` (Qt for Python) utilizando flags `Qt.WindowStaysOnTopHint`, `Qt.FramelessWindowHint` e `Qt.Tool`.
* **Comunicação Local:** Biblioteca `websockets` ou `FastAPI` rodando em background thread.
* **Banco de Dados:** `sqlite3` nativo.
* **Build/Executável:** `PyInstaller` ou `Nuitka` (gera `.exe` único para Windows).

### Opção 2: Tauri v2 + React (Visual Web Moderno e Baixo Consumo)
* **Backend Desktop:** `Rust` (via Tauri v2) gerenciando janela transparente sobreposta, System Tray e SQLite.
* **Frontend (HUD):** `React` + `TypeScript` + `TailwindCSS`.
* **Extensão do Navegador:** TypeScript (Manifest V3) com `content_script` e `background service worker`.

---

## 6. Modelo de Dados Local (SQLite)

```sql
-- Tabela de eventos de uso capturados em tempo real
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,              -- 'claude' | 'chatgpt'
    model_name TEXT NOT NULL,            -- ex: 'claude-3-7-sonnet', 'gpt-4o', 'o3-mini'
    conversation_id TEXT,                -- ID da conversa para calcular acúmulo de contexto
    prompt_chars INTEGER DEFAULT 0,      -- Tamanho da mensagem enviada
    context_chars INTEGER DEFAULT 0,     -- Tamanho acumulado estimado da thread
    weight_units REAL DEFAULT 1.0,       -- Peso calculado da interação na cota
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de estado atual e limites sincronizados por provedor
CREATE TABLE IF NOT EXISTS provider_state (
    provider TEXT PRIMARY KEY,           -- 'claude' | 'chatgpt'
    plan_tier TEXT DEFAULT 'pro',        -- 'free' | 'pro' | 'plus' | 'max'
    window_5h_max_units REAL DEFAULT 45.0,
    window_5h_reset_at DATETIME,         -- Timestamp oficial caso reportado pela API
    weekly_max_units REAL DEFAULT 500.0,
    weekly_reset_at DATETIME,
    is_rate_limited INTEGER DEFAULT 0,   -- 0 = Liberado, 1 = Bloqueado no momento
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Índices para consultas rápidas de janelas de tempo
CREATE INDEX IF NOT EXISTS idx_usage_provider_time 
ON usage_events (provider, created_at);
```

---

## 7. Fluxo de Funcionamento (Passo a Passo)

1. **Inicialização:** O usuário inicia o `NeuralMeter.exe` no Windows. O popup flutuante aparece no canto da tela e abre o servidor WebSocket local na porta `8765`.
2. **Conexão do Browser:** Ao abrir o navegador no `claude.ai` ou `chatgpt.com`, a extensão **NeuralMeter Bridge** conecta-se automaticamente ao WebSocket local e muda o indicador do popup para "Conectado".
3. **Captura Passiva:** O usuário envia uma mensagem no Claude ou ChatGPT. A extensão intercepta o evento de envio e os cabeçalhos da resposta, calculando o tamanho do texto sem fazer nenhuma nova requisição aos servidores da IA.
4. **Processamento Local:** O app recebe o evento, grava no SQLite e recalcula instantaneamente:
   * O percentual consumido na janela de **5 horas**.
   * O percentual consumido na janela **semanal (7 dias)**.
   * O tempo restante para o próximo reset.
5. **Atualização Visual:** O popup no Windows atualiza as barras de progresso em tempo real com animação suave.

---

## 8. Roadmap de Desenvolvimento

### Fase 1 — Core e Extensão (MVP Funcional)
* [ ] Criar banco de dados SQLite e motor de cálculo das janelas de 5h e 7 dias.
* [ ] Desenvolver servidor WebSocket local no app desktop.
* [ ] Criar a extensão Manifest V3 para capturar envios de mensagens e modelos usados no `claude.ai` e `chatgpt.com`.

### Fase 2 — Interface Suspensa (Windows HUD)
* [ ] Construir a janela *Frameless* e *Always-on-Top* com modo Compacto e Expandido.
* [ ] Adicionar ícone na bandeja do sistema (*System Tray*) e salvamento da posição da janela na tela.
* [ ] Implementar modo *Click-Through* e controle de opacidade.

### Fase 3 — Calibração Inteligente e Polimento
* [ ] Implementar detecção automática de bloqueio (Rate Limit) e captura do horário exato de reset oficial.
* [ ] Adicionar cálculo de peso progressivo por tamanho de contexto para conversas longas no Claude.
* [ ] Gerar instalador executável para Windows (`.exe` / `.msi`).