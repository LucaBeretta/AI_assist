/* A skippable first conversation, using the same model download events as Models. */
"use strict";
window.welcomeui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const el = id => document.getElementById(id);
  let state = null, shown = false, advice = null, pull = null, pendingModel = null, cancelRequested = false;
  const finished = new Set();
  const call = async (method, ...args) => {
    const value = await window.pywebview.api[method](...args);
    if (!value || value.ok === false) throw new Error(value?.error || t("Invalid API response"));
    return value;
  };
  const installed = () => state?.ollama?.models || (state?.engine === "Ollama" ? state.models || [] : []);
  const gb = bytes => (bytes / 1024 ** 3).toFixed(1) + " GB";
  function render() {
    const name = el("welcome-model").value;
    const ready = installed().includes(name);
    el("welcome-start").disabled = !name || Boolean(pendingModel);
    el("welcome-start").hidden = ready;
    el("welcome-open").hidden = !ready; el("welcome-open").disabled = Boolean(pendingModel);
    el("welcome-stop").hidden = !pendingModel; el("welcome-download").hidden = !pendingModel;
    el("welcome-model").disabled = Boolean(pendingModel);
    const card = (advice?.recommendations || []).find(item => item.name === name);
    el("welcome-reason").textContent = card ? [card.purpose, card.fit, card.reason, card.size_bytes ? gb(card.size_bytes) + t(" download") : ""].filter(Boolean).join(" · ") : t("Already installed on this computer.");
  }
  async function load() {
    el("welcome-status").textContent = t("Reading hardware and recommendations…");
    el("welcome-retry").hidden = true;
    try {
      advice = await call("recommend_models");
      const hw = advice.hardware, gpu = (hw.gpus || []).find(item => item.supported !== false);
      el("welcome-hardware").textContent = [gpu?.name || "CPU", gpu?.total_bytes ? gb(gpu.total_bytes) + " VRAM" : "", hw.ram_total_bytes ? gb(hw.ram_total_bytes) + " RAM" : "", hw.cpu].filter(Boolean).join(" · ");
      const names = [...new Set([...installed(), ...(advice.recommendations || []).map(item => item.name)])];
      el("welcome-model").replaceChildren(...names.map(name => {
        const option = document.createElement("option"); option.value = name; option.textContent = name; return option;
      }));
      el("welcome-model").value = names[0] || "";
      el("welcome-status").textContent = names.length ? t("Choose a model to continue. You can change it later.") : t("No suitable recommendation is available. Open Models to choose one manually.");
      render();
    } catch (failure) { el("welcome-status").textContent = failure.message; el("welcome-retry").hidden = false; }
  }
  async function onReady(value) {
    state = value;
    if (shown || !window.pywebview.api.set_web_preferences) return;
    const available = installed().length + (state?.colibri?.models?.length || 0);
    if (state?.settings?.first_run_complete && available) return;
    shown = true; window.app.showView("welcome"); await load();
  }
  async function start() {
    const name = el("welcome-model").value;
    if (!name || pendingModel) return;
    pendingModel = name; cancelRequested = false; el("welcome-status").textContent = t("Starting the download…"); render();
    try {
      const result = await call("pull_model", name);
      if (finished.has(result.pull_id)) return;
      pull = result.pull_id;
      if (cancelRequested) await call("stop_pull", pull);
    } catch (failure) { pendingModel = null; pull = null; el("welcome-status").textContent = failure.message; render(); }
  }
  function onEvent(event) {
    if (!pendingModel || event.model !== pendingModel || (pull && pull !== event.pull_id)) return;
    if (event.type === "pull_progress") {
      pull = event.pull_id;
      el("welcome-progress").max = event.total || 1; el("welcome-progress").value = event.completed || 0;
      el("welcome-progress-text").textContent = event.total ? `${Math.floor(event.completed / event.total * 100)}% · ${event.status}` : event.status;
    } else if (["pull_done", "pull_error", "pull_stopped"].includes(event.type)) {
      finished.add(event.pull_id); pendingModel = null; pull = null;
      if (event.type === "pull_done") {
        const names = [...installed(), event.model]; state = {...state, ollama: {models: names}};
        window.app.refreshModels(); el("welcome-status").textContent = t("The model is ready. Open your first chat.");
      } else el("welcome-status").textContent = event.error || t("Download stopped. You can resume it later.");
      render();
    }
  }
  async function complete(openChat) {
    try {
      if (pendingModel) { cancelRequested = true; if (pull) await call("stop_pull", pull); }
      await call("set_web_preferences", {first_run_complete: true});
      if (openChat) { await window.app.refreshModels(); await window.app.selectModel(el("welcome-model").value); }
      window.app.showView("chat");
    } catch (failure) { el("welcome-status").textContent = failure.message; }
  }
  el("welcome-model").addEventListener("change", render);
  el("welcome-start").addEventListener("click", start);
  el("welcome-retry").addEventListener("click", load);
  el("welcome-stop").addEventListener("click", async () => { cancelRequested = true; if (pull) await call("stop_pull", pull).catch(failure => { el("welcome-status").textContent = failure.message; }); });
  el("welcome-skip").addEventListener("click", () => complete(false));
  el("welcome-open").addEventListener("click", () => complete(true));
  return {onReady, onEvent};
})();
