const SOURCES = {
  claude: "Claude Code",
  codex: "Codex CLI",
  antigravity_cli: "Antigravity CLI",
};
const root = document.documentElement;
const cards = document.querySelector("#source-cards");
const rows = document.querySelector("#model-rows");
const period = document.querySelector("#period");
const controls = document.querySelector("#consent-controls");
const theme = document.querySelector("#theme");
const refresh = document.querySelector("#refresh");
const shutdown = document.querySelector("#shutdown");
const notice = document.querySelector("#notice");
let csrf = "";
let snapshot = null;

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function number(value, approximate = false) {
  if (value === null || value === undefined || !Number.isSafeInteger(value)) return "—";
  const formatted = new Intl.NumberFormat("pt-BR").format(value);
  return approximate ? `~${formatted}` : formatted;
}

function renderQuota(card, label, quota) {
  const line = node("div", "quota-line");
  const percent = quota && Number.isFinite(quota.percent) ? quota.percent : null;
  line.append(node("span", "", label), node("strong", "", percent === null ? "—" : `${percent}%`));
  card.append(line);
  const reset = quota?.reset_at ? new Date(quota.reset_at) : null;
  const resetLine = node("div", "quota-reset");
  if (reset && Number.isFinite(reset.getTime()) && reset.getTime() > Date.now() && percent !== null) {
    resetLine.textContent = `Renova em ${reset.toLocaleString("pt-BR")}`;
    resetLine.title = reset.toISOString();
  } else {
    resetLine.textContent = quota?.expired ? "Janela expirada · verificar novamente" : "Renovação desconhecida";
  }
  card.append(resetLine);
  if (percent === null) return;
  const meter = node("progress", "meter");
  meter.max = 100;
  meter.value = Math.max(0, Math.min(100, percent));
  meter.setAttribute("aria-label", `${label} utilizado`);
  card.append(meter);
}

function selectedModels(source) {
  return source.periods?.[period.value]?.models || [];
}

function reportedTotal(models, field) {
  if (!models.length || models.some((model) => model[field] === null)) return null;
  return models.reduce((total, model) => total + model[field], 0);
}

function calculatedTotal(models, field) {
  return models.reduce((total, model) => total + (model[field] || 0), 0);
}

function renderCards(data) {
  cards.replaceChildren();
  for (const [id, name] of Object.entries(SOURCES)) {
    const source = data.sources[id] || {};
    const card = node("article", "source-card");
    card.setAttribute("aria-label", name);
    const heading = node("div", "card-top");
    heading.append(node("h3", "", name), node("span", "status", source.status_label || "Desconhecido"));
    card.append(heading);

    if (id === "antigravity_cli") {
      const models = selectedModels(source);
      const input = reportedTotal(models, "input_tokens");
      const output = reportedTotal(models, "output_tokens");
      const calcInput = calculatedTotal(models, "calculated_input_tokens");
      const calcOutput = calculatedTotal(models, "calculated_output_tokens");
      card.append(node("div", "metric-label", "Tokens de entrada · medidos"));
      const inputValue = node("div", "metric", number(input));
      inputValue.append(node("small", "", " medidos"));
      card.append(inputValue, node("div", "metric-label", "Tokens de saída · medidos"));
      const outputValue = node("div", "metric", number(output));
      outputValue.append(node("small", "", " medidos"));
      card.append(outputValue);
      if (calcInput || calcOutput) {
        card.append(node("div", "card-foot", `${number(calcInput, true)} entrada · ${number(calcOutput, true)} saída, aproximados por caracteres`));
      }
    } else {
      card.append(node("div", "metric-label", id === "claude" ? "Quota Claude · janelas oficiais" : "Quota Codex · janelas oficiais"));
      const stack = node("div", "quota-stack");
      for (const quota of source.quotas || []) renderQuota(stack, quota.label, quota);
      if (!source.quotas?.length) stack.append(node("div", "metric", "—"));
      card.append(stack);
    }

    const footer = node("div", "card-foot");
    if (id === "antigravity_cli") {
      footer.textContent = `Histórico local ${source.consent ? "autorizado" : "desativado"} · quota oficial desconhecida`;
    } else {
      const install = source.installed === true ? "CLI detectada" : source.installed === false ? "CLI não detectada" : "Instalação desconhecida";
      const auth = source.auth_status || "Autenticação desconhecida";
      footer.textContent = `${install} · ${auth} · ${source.consent ? "Leitura autorizada" : "Opt-in desativado"}`;
    }
    card.append(footer);
    cards.append(card);
  }
  cards.setAttribute("aria-busy", "false");
}

function renderModels(data) {
  rows.replaceChildren();
  const source = data.sources.antigravity_cli || {};
  const selected = source.periods?.[period.value];
  const models = selected?.models || [];
  const start = selected?.boundary_start_local || selected?.observed_start || "intervalo desconhecido";
  const end = selected?.boundary_end_local || selected?.observed_end || "agora";
  document.querySelector("#history-note").textContent =
    `Contagens locais · ${start} até ${end}. Valores calculados por caracteres são aproximações (~), não tokenizer exato. ${selected?.coverage_partial ? "Cobertura parcial: alguns dados ou horários não podem ser confirmados." : ""}`;

  if (!models.length) {
    const row = node("tr");
    const message = source.consent ? "Nenhuma contagem com horário conhecido neste período." : "Autorize Antigravity CLI para ler histórico local.";
    const cell = node("td", "empty", message);
    cell.colSpan = 8;
    row.append(cell);
    rows.append(row);
    return;
  }

  for (const model of models.slice(0, 256)) {
    const row = node("tr");
    row.append(node("td", "", model.model));
    for (const field of ["input_tokens", "calculated_input_tokens", "output_tokens", "calculated_output_tokens", "cache_read_tokens", "cache_write_tokens"]) {
      const cell = node("td");
      const calculated = field.startsWith("calculated_");
      const rowsWithCalculation = field === "calculated_input_tokens" ? model.calculated_input_rows : field === "calculated_output_tokens" ? model.calculated_output_rows : 1;
      if (calculated && rowsWithCalculation > 0) {
        cell.className = "approx";
        cell.textContent = number(model[field], true);
      } else if (calculated) {
        cell.textContent = "—";
      } else {
        cell.textContent = number(model[field]);
      }
      row.append(cell);
    }
    row.append(node("td", "", model.coverage_partial ? "Parcial" : "Observada"));
    rows.append(row);
  }
}

function renderConsent(data) {
  for (const [id, name] of Object.entries(SOURCES)) {
    let checkbox = controls.querySelector(`[data-source="${id}"]`);
    if (!checkbox) {
      const row = node("div", "consent-row");
      const label = node("label");
      checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.dataset.source = id;
      checkbox.setAttribute("aria-label", `Permitir leitura local: ${name}`);
      checkbox.addEventListener("change", async () => {
        checkbox.disabled = true;
        try {
          await mutate("/api/consent", { source: id, enabled: checkbox.checked });
          await load();
        } catch {
          checkbox.checked = !checkbox.checked;
          notify("Não foi possível salvar autorização.");
        } finally {
          checkbox.disabled = false;
        }
      });
      label.append(checkbox, node("span", "", `Permitir leitura local · ${name}`));
      row.append(label);
      controls.append(row);
    }
    checkbox.checked = Boolean(data.sources[id]?.consent);
  }
}

function notify(message) {
  notice.textContent = message;
}

async function mutate(path, payload = {}) {
  if (!csrf) throw new Error("session");
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-NeuralMeter-CSRF": csrf },
    body: JSON.stringify(payload),
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("request");
  return response.json();
}

async function load() {
  const response = await fetch("/api/snapshot", { headers: { Accept: "application/json" }, credentials: "same-origin" });
  if (!response.ok) return;
  snapshot = await response.json();
  renderCards(snapshot);
  renderModels(snapshot);
  renderConsent(snapshot);
}

theme.value = ["graphite", "light", "aurora"].includes(localStorage.getItem("nm-theme")) ? localStorage.getItem("nm-theme") : "graphite";
root.dataset.theme = theme.value;
theme.addEventListener("change", () => {
  if (!["graphite", "light", "aurora"].includes(theme.value)) theme.value = "graphite";
  root.dataset.theme = theme.value;
  localStorage.setItem("nm-theme", theme.value);
});
period.value = ["today", "last_7_days", "history"].includes(localStorage.getItem("nm-period"))
  ? localStorage.getItem("nm-period")
  : "today";
period.addEventListener("change", () => {
  localStorage.setItem("nm-period", period.value);
  if (snapshot) renderCards(snapshot), renderModels(snapshot);
});
refresh.addEventListener("click", async () => {
  refresh.disabled = true;
  try {
    const result = await mutate("/api/refresh", { source: "all" });
    notify(result.accepted
      ? "Atualização solicitada às fontes autorizadas."
      : result.reason === "cooldown"
        ? "Aguarde o intervalo mínimo antes de atualizar novamente."
        : "Nenhuma fonte autorizada para atualizar.");
    await load();
  } catch {
    notify("Atualização indisponível ou temporariamente limitada.");
  } finally {
    refresh.disabled = false;
  }
});
shutdown.addEventListener("click", async () => {
  shutdown.disabled = true;
  try {
    await mutate("/api/shutdown", {});
    notify("NeuralMeter será encerrado. Pode fechar esta aba.");
  } catch {
    shutdown.disabled = false;
    notify("Não foi possível encerrar servidor local.");
  }
});
fetch("/api/bootstrap", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : Promise.reject())
  .then((bootstrap) => { csrf = bootstrap.csrf; return load(); })
  .catch(() => notify("Servidor local não autenticado. Recarregue dashboard."));
setInterval(load, 3000);
