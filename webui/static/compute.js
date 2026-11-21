/* Fixed-grid local telemetry. Visibility starts polling; no discovery or commands. */
"use strict";
window.computeui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
  let visible = false, last = null;
  // The separate Compute window reports its own visibility, so it never pauses the main view.
  const source = document.documentElement?.dataset?.computeSource === "window" ? "window" : "main";
  function meter(id, percent) {
    const known = typeof percent === "number" && Number.isFinite(percent) && percent >= 0 && percent <= 100;
    el(id).hidden = !known; if (known) el(id).value = percent;
  }
  function list(id, values, empty) {
    el(id).replaceChildren(...(values.length ? values.slice(0, 8) : [t(empty)]).map(value => {
      const li = document.createElement("li"); li.textContent = value; li.title = value; return li;
    }));
    if (values.length > 8) { const li = document.createElement("li"); li.textContent = t("{v0} more", {v0: values.length - 8}); el(id).append(li); }
  }
  function render(data) {
    last = data;
    if (!data) { el("compute-note").textContent = t("Reading local telemetry…"); return; }
    const values = data.values, raw = data.snapshot;
    el("compute-note").textContent = data.error || [values.device, values.system, raw.time ? new Date(raw.time * 1000).toLocaleTimeString() : ""].filter(Boolean).join(" · ");
    el("compute-cpu-name").textContent = values.cpu;
    el("compute-cpu").textContent = t(values.cpu_usage); meter("compute-cpu-meter", raw.cpu?.percent);
    const gpu = values.gpus[0];
    el("compute-gpu-name").textContent = gpu?.name || t("No GPU reading");
    el("compute-gpu").textContent = gpu ? t(gpu.percent) + " · " + gpu.memory : t("unavailable"); meter("compute-gpu-meter", raw.gpus?.[0]?.percent);
    el("compute-ram").textContent = values.ram;
    const total = raw.ram?.total_bytes, free = raw.ram?.available_bytes;
    meter("compute-ram-meter", typeof total === "number" && total > 0 && typeof free === "number" ? 100 * (total - free) / total : null);
    list("compute-loaded", values.loaded, "No loaded models");
    list("compute-installed", values.installed, "No installed models");
    el("compute-model-note").textContent = values.models_available ? t("Local Ollama only") : t(values.model_note || "unavailable");
    list("compute-peers", data.peers.map(row => row.id + " · " + t(row.state)), "No paired devices");
    if (typeof window.computeFaces === "function") window.computeFaces(data);
  }
  async function onVisible(value) {
    visible = value;
    if (!window.pywebview?.api?.set_compute_active) return;
    try {
      const shown = value && document.hidden !== true;
      const result = await (source === "main" ? window.pywebview.api.set_compute_active(shown) : window.pywebview.api.set_compute_active(shown, source));
      if (result.ok === false) throw new Error(window.i18n?.gate?.(result.error) || result.error);
      if (visible) render(result.data);
    } catch (error) { el("compute-note").textContent = error.message; }
  }
  function onEvent(event) { if (event.type !== "compute") return false; if (visible) render(event.data); return true; }
  document.addEventListener?.("visibilitychange", () => onVisible(visible));
  window.addEventListener("languagechange", () => { if (visible) render(last); });
  return {onVisible, onEvent, render};
})();
