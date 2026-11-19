// @ts-check
/** Compact, read-only local compute panel; safe DOM only. */
import { h, replace } from "./dom.js";

const GIB = 1024 ** 3;
function percent(n) {
  return typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 100
    ? n.toFixed(0) + "%" : "—";
}
function ratio(n, total) {
  return typeof n === "number" && typeof total === "number" &&
    Number.isFinite(n) && Number.isFinite(total) && total > 0 && n >= 0 && n <= total
    ? Math.round(n / total * 100) : null;
}
function gib(n) {
  return typeof n === "number" && Number.isFinite(n) && n >= 0 ? (n / GIB).toFixed(1) + " GiB" : "unknown";
}
function card(title) {
  const value = h("span", { class: "compute-reading" }, "—");
  const detail = h("span", { class: "compute-detail" }, "Awaiting local metrics");
  const gauge = h("div", { class: "compute-track", role: "meter", "aria-label": title,
    "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" },
    h("div", { class: "compute-fill" }));
  const el = h("section", { class: "compute-card" },
    h("div", { class: "compute-label" }, title), value, gauge, detail);
  return { el, value, detail, gauge };
}
function fill(card, value, detail, amount) {
  card.value.textContent = value;
  card.detail.textContent = detail;
  const known = amount !== null && Number.isFinite(amount);
  card.gauge.setAttribute("aria-valuetext", known ? value : "Unavailable");
  if (known) card.gauge.setAttribute("aria-valuenow", String(amount));
  else card.gauge.removeAttribute("aria-valuenow");
  const bar = card.gauge.firstElementChild;
  if (bar instanceof HTMLElement) bar.style.width = known ? amount + "%" : "0%";
}

/** @param {any} ctx */
export function createComputeSidebar(ctx) {
  const element = h("div", { class: "sidebar-body" },
    h("div", { class: "sidebar-heading" }, "Compute network"),
    h("p", { class: "muted" }, "Live hardware readings from this computer only."),
    h("button", { type: "button", class: "button", onClick: () => ctx.loadCompute() }, "Refresh local metrics"),
    ctx.openComputeMonitor ? h("button", { type: "button", class: "button button-primary", onClick: () => ctx.openComputeMonitor() }, "Open separate monitor") : null);
  return { element, render: () => {} };
}

/** @param {any} ctx */
export function createComputeView(ctx) {
  const cpu = card("CPU"), gpu = card("GPU"), ram = card("RAM");
  const models = h("div", { class: "compute-content" }, "Loading model data...");
  const peers = h("div", { class: "compute-content" }, "No paired devices currently available.");
  const notice = h("span", { class: "compute-state" }, "Local only");
  const element = h("section", { class: "compute-view" },
    h("header", { class: "compute-header" },
      h("div", null, h("h1", null, "Compute monitor"),
        h("p", null, "AI Assist / local telemetry · 5-second refresh")),
      notice,
      ctx.openComputeMonitor ? h("button", { class: "button button-small", type: "button", onClick: () => ctx.openComputeMonitor() }, "Open window") : null,
      ctx.toggleFullscreen ? h("button", { class: "button button-small", type: "button", id: "monitor-fullscreen", onClick: () => ctx.toggleFullscreen() }, "Fullscreen") : null,
      h("button", { class: "button button-small", type: "button", onClick: () => ctx.loadCompute() }, "Refresh")),
    h("div", { class: "compute-grid" },
      cpu.el, gpu.el, ram.el,
      h("section", { class: "compute-card compute-models" },
        h("div", { class: "compute-label" }, "OLLAMA MODELS"), models),
      h("section", { class: "compute-card compute-peers" },
        h("div", { class: "compute-label" }, "PAIRED DEVICES"), peers)),
    h("footer", { class: "compute-footer" },
      "Metrics from this computer only. No device discovery, remote polling, or automatic sharing."));

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "compute" || (state.compute === previous?.compute && state.view === previous?.view)) return;
    const report = state.compute;
    if (!report || report.loading) {
      notice.textContent = "Reading hardware…";
      return;
    }
    if (report.error) {
      notice.textContent = "Metrics unavailable";
      fill(cpu, "—", "Cannot read CPU", null);
      fill(gpu, "—", "Cannot read GPU", null);
      fill(ram, "—", "Cannot read RAM", null);
      replace(models, h("p", null, report.error));
      replace(peers, h("p", null, "No remote telemetry"));
      return;
    }
    const local = report.local || {};
    const c = local.cpu || {};
    const r = local.ram || {};
    const g = Array.isArray(local.gpus) ? local.gpus[0] || {} : {};
    const used = typeof r.total_bytes === "number" && typeof r.available_bytes === "number" ?
      r.total_bytes - r.available_bytes : null;
    fill(cpu, percent(c.percent), `${c.logical_cores || "?"} cores · ${c.name || "CPU unavailable"}`,
      typeof c.percent === "number" && c.percent >= 0 && c.percent <= 100 ? c.percent : null);
    fill(gpu, percent(g.percent), `${g.name || "GPU usage unavailable"} · ${gib(g.used_bytes)} / ${gib(g.total_bytes)}`,
      typeof g.percent === "number" && g.percent >= 0 && g.percent <= 100 ? g.percent : null);
    const memoryRatio = ratio(used, r.total_bytes);
    fill(ram, memoryRatio === null ? "—" : memoryRatio + "%",
      `${gib(used)} / ${gib(r.total_bytes)} used`, memoryRatio);
    const model = local.models || {};
    const loaded = Array.isArray(model.loaded) ? model.loaded : [];
    const installed = Array.isArray(model.installed) ? model.installed : [];
    replace(models,
      h("strong", null, `${loaded.length} loaded · ${installed.length} installed`),
      h("div", { class: "compute-names" },
        ...(loaded.length ? loaded.slice(0, 5).map((name) => h("span", { class: "compute-pill" }, name))
          : [h("span", { class: "muted" }, model.available === false ? "Local Ollama unavailable" : "No loaded models")])));
    const nodes = Array.isArray(report.peers) ? report.peers : [];
    replace(peers, h("strong", null, `${nodes.length} paired records`),
      ...(nodes.length ? nodes.slice(0, 4).map((node) =>
        h("div", { class: "compute-peer" },
          h("span", null, node.id), h("small", null, "Live telemetry unavailable")))
        : [h("p", { class: "muted" }, "No paired nodes · sharing stays off")]));
    notice.textContent = "LOCAL · " + (local.device || "this computer");
  }
  return { element, render };
}
