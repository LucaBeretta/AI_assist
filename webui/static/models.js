/* Models view: hardware summary, installed models, recommendations and downloads (Api.recommend_models / pull_model). */
"use strict";

window.modelsui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const element = id => document.getElementById(id);
  let pull = null;            // {id, model, completed, total, status}
  let advice = null;          // last recommend_models result
  let installed = [];
  let loaded = {};

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(result?.error || t("Invalid API response"));
    return result;
  }
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = text;
    return item;
  }
  function btn(label, className, onClick) {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", onClick);
    return item;
  }
  function gb(bytes) { return Number.isFinite(bytes) ? (bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1) + " GB" : "?"; }
  function note(text) { element("models-status").textContent = text; }

  // ---------- hardware ----------
  function renderHardware(hw) {
    const box = element("hw-summary");
    if (!hw) { box.replaceChildren(node("p", "dev-hint", t("Reading your hardware…"))); return; }
    const gpu = (hw.gpus || []).find(item => item.supported !== false) || (hw.gpus || [])[0];
    const tiles = [
      ["GPU", gpu ? gpu.name : t("None detected"), gpu?.total_bytes ? `${gb(gpu.total_bytes)} VRAM${gpu.free_bytes != null ? " · " + gb(gpu.free_bytes) + t(" free") : ""}` : t("CPU only")],
      [t("Memory"), hw.ram_total_bytes ? gb(hw.ram_total_bytes) + " RAM" : "?", hw.ram_available_bytes ? gb(hw.ram_available_bytes) + t(" available now") : ""],
      [t("Processor"), hw.cpu || "?", hw.logical_processors ? hw.logical_processors + t(" threads") : ""],
      [t("Disk"), hw.disk_free_bytes ? gb(hw.disk_free_bytes) + t(" free") : "?", hw.model_directory || ""],
      ["Ollama", hw.ollama_running ? t("Running") : t("Not running"), hw.ollama_version ? "v" + hw.ollama_version : (hw.server_local === false ? t("remote server") : "")],
    ];
    box.replaceChildren(...tiles.map(([label, value, detail]) => {
      const tile = node("div", "hw-tile");
      tile.append(node("span", "hw-label", label), node("span", "hw-value", value), node("span", "hw-detail", detail));
      return tile;
    }));
    const notes = (hw.notes || []).filter(Boolean);
    element("hw-notes").replaceChildren(...notes.map(text => node("p", "dev-hint", text)));
  }

  // ---------- installed ----------
  function renderInstalled() {
    const list = element("models-installed");
    if (!installed.length) { list.replaceChildren(node("p", "dev-hint", t("No models installed yet. Pick one below to download it."))); return; }
    list.replaceChildren(...installed.map(name => {
      const row = node("div", "model-row");
      const info = node("div", "model-info");
      const title = node("span", "model-name mono", name);
      info.append(title);
      const details = loaded[name];
      if (details) {
        const split = details.size_vram != null && details.size ? Math.round(details.size_vram / details.size * 100) : null;
        info.append(node("span", "pill ok", t("loaded") + (split != null ? ` · ${split}% gpu${split < 100 ? " · " + (100 - split) + "% cpu" : ""}` : "")));
      }
      const actions = node("div", "model-actions");
      actions.append(btn(t("Use in chat"), "btn", () => { window.app?.selectModel(name); window.app?.showView("chat"); }));
      if (details) actions.append(btn(t("Unload"), "btn ghost", async () => {
        try { await call("unload_model", name); note(t("{v0} unloaded.", {v0: (name)})); await refresh(); } catch (error) { note(error.message); }
      }));
      actions.append(btn(t("Delete"), "btn ghost danger", async () => {
        if (window.confirm && !window.confirm(t("Delete {v0} from this computer? You can download it again later.", {v0: (name)}))) return;
        try { await call("delete_model", name); note(`${name} deleted.`); await refresh(); window.app?.refreshModels(); }
        catch (error) { note(error.message); }
      }));
      row.append(info, actions);
      return row;
    }));
  }

  // ---------- recommendations ----------
  function fitClass(fit) {
    if (/GPU memory|fits GPU|Run one model/i.test(fit || "")) return "ok";
    if (/split|fallback/i.test(fit || "")) return "warn";
    return "";
  }

  function renderRecommendations() {
    const list = element("models-recommended");
    if (!advice) { list.replaceChildren(node("p", "dev-hint", t("Checking which models fit this computer…"))); return; }
    const cards = advice.recommendations || [];
    if (!cards.length) { list.replaceChildren(node("p", "dev-hint", t("No recommendations: the model catalog could not be read (offline?) or nothing fits."))); return; }
    list.replaceChildren(...cards.map(card => {
      const item = node("article", "model-card");
      const head = node("div", "model-card-head");
      head.append(node("span", "model-name mono", card.name), node("span", "pill " + fitClass(card.fit), card.fit));
      item.append(head);
      if (card.purpose) item.append(node("p", "model-purpose", card.purpose));
      if (card.reason) item.append(node("p", "dev-hint", card.reason));
      const foot = node("div", "model-card-foot");
      foot.append(node("span", "dev-hint mono",
        `${card.size_bytes ? gb(card.size_bytes) + t(" download") : ""}${card.estimated_memory_bytes ? t(" · needs ~") + gb(card.estimated_memory_bytes) : ""}`));
      foot.append(node("span", "grow"));
      if (installed.includes(card.name)) foot.append(node("span", "pill ok", t("installed")));
      else if (pull?.model === card.name) foot.append(progress());
      else {
        const download = btn(t("Download"), "btn primary", () => start(card.name));
        download.disabled = Boolean(pull);
        foot.append(download);
      }
      item.append(foot);
      return item;
    }));
  }

  // ---------- downloads ----------
  function progress() {
    const wrap = node("span", "pull-progress");
    const bar = node("progress");
    bar.max = pull.total || 1;
    bar.value = pull.completed || 0;
    const pct = pull.total ? Math.floor(pull.completed / pull.total * 100) + "%" : "";
    wrap.append(bar, node("span", "dev-hint mono", `${pull.status || t("starting")} ${pct}`), btn(t("Stop"), "btn ghost", stop));
    return wrap;
  }

  async function start(name) {
    if (pull) return;
    try {
      const result = await call("pull_model", name);
      pull = {id: result.pull_id, model: name, completed: 0, total: 0, status: t("starting")};
      note(t("Downloading {v0}…", {v0: (name)}));
      renderAll();
    } catch (error) { note(error.message); }
  }

  async function stop() {
    if (!pull) return;
    try { await call("stop_pull", pull.id); note(t("Stopping download…")); } catch (error) { note(error.message); }
  }

  function renderPullOther() {
    const box = element("pull-other-progress");
    if (pull && !(advice?.recommendations || []).some(card => card.name === pull.model)) box.replaceChildren(node("span", "mono", pull.model + " "), progress());
    else box.replaceChildren();
    element("pull-other-button").disabled = Boolean(pull);
  }

  function renderAll() { renderInstalled(); renderRecommendations(); renderPullOther(); }

  function onEvent(event) {
    switch (event.type) {
      case "pull_progress":
        if (!pull || pull.id !== event.pull_id) pull = {id: event.pull_id, model: event.model};
        Object.assign(pull, {completed: event.completed || 0, total: event.total || 0, status: event.status});
        renderRecommendations(); renderPullOther();
        return true;
      case "pull_done": pull = null; note(`${event.model} downloaded.`); refresh(); return true;
      case "pull_stopped": pull = null; note(t("Download stopped.")); renderAll(); return true;
      case "pull_error": pull = null; note(event.error || t("Download failed.")); renderAll(); return true;
      case "models_changed": refresh(); window.app?.refreshModels(); return true;
      default: return false;
    }
  }

  async function refresh() {
    try {
      const state = await call("state");
      installed = state.ollama?.models || (state.engine === "Ollama" ? state.models || [] : []);
      loaded = state.loaded || {};
      renderInstalled();
    } catch (error) { note(error.message); }
  }

  async function loadAdvice() {
    renderHardware(advice?.hardware);
    renderRecommendations();
    try {
      advice = await call("recommend_models");
      renderHardware(advice.hardware);
      renderRecommendations();
    } catch (error) { note(error.message); element("models-recommended").replaceChildren(node("p", "dev-hint", error.message)); }
  }

  function onShow() { refresh(); if (!advice) loadAdvice(); }

  element("pull-other").addEventListener("submit", event => {
    event.preventDefault();
    const name = element("pull-other-name").value.trim();
    if (name) start(name);
  });
  element("models-refresh").addEventListener("click", () => { advice = null; refresh(); loadAdvice(); });
  window.addEventListener("languagechange", () => { if (!element("models-view").hidden) { renderHardware(advice?.hardware); renderAll(); } });
  return {onEvent, onShow};
})();
