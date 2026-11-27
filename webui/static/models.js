/* Models view: hardware summary, installed models, recommendations and downloads (Api.recommend_models / pull_model). */
"use strict";

window.modelsui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const element = id => document.getElementById(id);
  let pull = null;            // {id, model, completed, total, status}
  let advice = null;          // last recommend_models result
  let installed = [];
  let shared = [];            // paired computers that share their models with this one
  let ollamaError = null;     // why this computer's Ollama could not list its models, if it could not
  const abilities = new Map(); // model name -> {keys, context, failed} from this computer's Ollama (/api/show), or "reading"
  const abilityLabels = {vision: "reads pictures", tools: "can use tools (needed by Developer)", thinking: "thinks before answering"};
  let loaded = {};
  const finishedPulls = new Set();

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(window.i18n?.gate?.(result?.error) || result?.error || t("Invalid API response"));
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
  // Memory (RAM, VRAM) in binary gigabytes, as Task Manager and Activity Monitor show it.
  function gb(bytes) { return Number.isFinite(bytes) ? (bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1) + " GB" : "?"; }
  // Download sizes in decimal gigabytes, as ollama.com and Hugging Face list them.
  function downloadGb(bytes) { return Number.isFinite(bytes) ? (bytes / 1000 ** 3).toFixed(bytes >= 10 * 1000 ** 3 ? 0 : 1) + " GB" : "?"; }
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
  // What a model can do, in plain words, read from this computer's Ollama (no internet).
  async function readAbilities(name) {
    if (abilities.has(name) || !window.pywebview?.api?.model_capabilities) return;
    abilities.set(name, "reading");
    try {
      const data = (await call("model_capabilities", name, "Ollama")).metadata || {};
      const reported = data.capabilities || [];
      abilities.set(name, {keys: Object.keys(abilityLabels).filter(key => reported.includes(key)), context: data.context_length || null});
    } catch (error) { abilities.set(name, {keys: [], context: null, failed: true}); }  // read again at the next refresh
    renderInstalled();
  }
  // Use a model in the open chat; stay here and say why if the chat cannot change model right now.
  async function use(name, backend = "Ollama") {
    try {
      if (backend !== "Ollama") await window.app?.refreshModels?.();  // the chat's list may predate the paired computer's sharing
      await window.app?.selectModel(name, backend);
      window.app?.showView("chat");
    } catch (error) { note(error.message); }
  }
  const sharing = () => shared.some(computer => computer.state === "ready" && computer.models?.length);
  function renderInstalled() {
    const list = element("models-installed");
    if (!installed.length) {
      // Ollama not running is not the same as nothing installed: the models are still on disk.
      const text = ollamaError ? (sharing() ? t("Ollama is not running on this computer, so its models are not listed. Chats can use your paired computer.")
                                            : t("Ollama is not running on this computer, so its models are not listed. Start Ollama, then click Refresh models."))
        : sharing() ? t("Nothing installed here. Chats use your paired computer.") : t("No models installed yet. Pick one below to download it.");
      list.replaceChildren(node("p", "dev-hint", text));
      return;
    }
    list.replaceChildren(...installed.map(name => {
      const row = node("div", "model-row");
      const info = node("div", "model-info");
      const title = node("span", "model-name mono", name);
      info.append(title);
      const known = abilities.get(name);
      if (known && known !== "reading") {
        for (const key of known.keys) info.append(node("span", "pill", t(abilityLabels[key])));  // in today's language
        if (known.context) title.title = t("Remembers up to {v0} tokens of a conversation.", {v0: known.context});
      } else if (!known) readAbilities(name);
      const details = loaded[name];
      if (details) {
        const split = details.size_vram != null && details.size ? Math.round(details.size_vram / details.size * 100) : null;
        info.append(node("span", "pill ok", t("loaded") + (split != null ? ` · ${split}% gpu${split < 100 ? " · " + (100 - split) + "% cpu" : ""}` : "")));
      }
      const actions = node("div", "model-actions");
      actions.append(btn(t("Use in chat"), "btn", () => use(name)));
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
    if (!cards.length && advice.checked === false) { list.replaceChildren(node("p", "dev-hint", t("To see models that fit this computer, click Check online for suggestions. It asks before going online."))); return; }
    if (!cards.length) { list.replaceChildren(node("p", "dev-hint", advice.catalog_error ? (window.i18n?.gate?.(advice.catalog_error) || advice.catalog_error) : t("No recommendations: the model catalog could not be read (offline?) or nothing fits."))); return; }
    list.replaceChildren(...cards.map(card => {
      const item = node("article", "model-card");
      const head = node("div", "model-card-head");
      head.append(node("span", "model-name mono", card.name), node("span", "pill " + fitClass(card.fit), card.fit));
      item.append(head);
      if (card.purpose) item.append(node("p", "model-purpose", card.purpose));
      if (card.reason) item.append(node("p", "dev-hint", card.reason));
      const foot = node("div", "model-card-foot");
      foot.append(node("span", "dev-hint mono",
        `${card.size_bytes ? downloadGb(card.size_bytes) + t(" download") : ""}${card.estimated_memory_bytes ? t(" · needs ~") + gb(card.estimated_memory_bytes) : ""}`));
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
      if (finishedPulls.has(result.pull_id)) return;
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
    if (["pull_done", "pull_stopped", "pull_error"].includes(event.type)) {
      finishedPulls.add(event.pull_id);
      if (finishedPulls.size > 32) finishedPulls.delete(finishedPulls.values().next().value);
    }
    switch (event.type) {
      case "pull_progress":
        if (!pull || pull.id !== event.pull_id) pull = {id: event.pull_id, model: event.model};
        Object.assign(pull, {completed: event.completed || 0, total: event.total || 0, status: event.status});
        renderRecommendations(); renderPullOther();
        return true;
      case "pull_done": pull = null; abilities.delete(event.model); note(t("{v0} downloaded.", {v0: event.model})); renderAll(); refresh(); return true;
      case "pull_stopped": pull = null; note(t("Download stopped.")); renderAll(); return true;
      case "pull_error": pull = null; note(event.error || t("Download failed.")); renderAll(); return true;
      case "models_changed": refresh(); window.app?.refreshModels(); return true;
      default: return false;
    }
  }

  // Models a paired computer shares: chat only, so they are listed apart with "Use in chat".
  function renderShared() {
    const ready = shared.filter(computer => computer.state === "ready" && computer.models?.length);
    element("models-shared-section").hidden = !ready.length;
    element("models-shared").replaceChildren(...ready.flatMap(computer => computer.models.map(name => {
      const row = node("div", "model-row");
      const info = node("div", "model-info");
      info.append(node("span", "model-name mono", name), node("span", "pill", computer.name || computer.address || t("a paired computer")));
      const actions = node("div", "model-actions");
      actions.append(btn(t("Use in chat"), "btn", () => use(name, computer.backend)));
      row.append(info, actions);
      return row;
    })));
  }
  async function refresh() {
    for (const [name, known] of abilities) if (known?.failed) abilities.delete(name);
    // This computer's list never waits for the paired computers (each can take seconds when it is off).
    const paired = window.pywebview?.api?.shared_ai_models
      ? call("shared_ai_models").then(result => result.computers || [], () => [])
          .then(computers => { shared = computers; renderShared(); renderInstalled(); })
      : null;
    try {
      const state = await call("state");
      installed = state.ollama?.models || (state.engine === "Ollama" ? state.models || [] : []);
      loaded = state.loaded || {};
      ollamaError = state.ollama?.error || null;
    } catch (error) { installed = []; ollamaError = error.message; }
    if (ollamaError) note(window.i18n?.gate?.(ollamaError) || ollamaError);
    renderInstalled();
    await paired;
  }

  async function loadAdvice(refresh = false) {
    renderHardware(advice?.hardware);
    renderRecommendations();
    try {
      advice = await call("recommend_models", refresh, refresh);  // only the button goes online
      renderHardware(advice.hardware);
      renderRecommendations();
    } catch (error) { note(error.message); element("models-recommended").replaceChildren(node("p", "dev-hint", error.message)); }
  }

  // Opening the screen re-reads this computer and the installed models, offline: it shows suggestions
  // already read in this session, and going online is its own button.
  function onShow() { refresh(); loadAdvice(); }

  element("pull-other").addEventListener("submit", event => {
    event.preventDefault();
    const name = element("pull-other-name").value.trim();
    if (name) start(name);
  });
  element("models-check-online").addEventListener("click", () => { advice = null; refresh(); loadAdvice(true); });
  // The one "Refresh models" button also refreshes this screen when it is open.
  element("refresh")?.addEventListener("click", () => { if (!element("models-view").hidden) onShow(); });
  window.addEventListener("languagechange", () => { if (!element("models-view").hidden) { renderHardware(advice?.hardware); renderAll(); } });
  return {onEvent, onShow};
})();
