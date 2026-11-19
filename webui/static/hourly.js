/* Quiet-hour reports contain plain local data; preview does not enable the microphone. */
"use strict";
window.hourlyui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
  let report = null;
  const call = async (name, ...args) => { const result = await window.pywebview.api[name](...args); if (result.ok === false) throw new Error(window.i18n?.gate?.(result.error) || result.error); return result; };
  function state(value) {
    el("hourly-enabled").checked = value.enabled;
    el("hourly-status").textContent = value.message || (value.enabled ? t("Listening for quiet locally; no audio is saved or sent.") : t("Off by default. Preview does not turn on quiet detection."));
    if (value.report) render(value.report);
  }
  function render(value) {
    report = value;
    el("hourly-work").textContent = t("{v0} chats updated in the last hour", {v0: value.threads}) + (value.topics.length ? " · " + value.topics.join(" / ") : "");
    el("hourly-device").textContent = value.system + "\n" + value.ollama;
    el("hourly-updates").textContent = t(value.updates);
    el("hourly-reflection").textContent = value.reflection;
    el("hourly-reflection-title").textContent = value.reflected_by_model ? t("Local model reflection") : t("Next step");
    el("hourly-message").textContent = "";
  }
  function show() { const dialog = el("hourly-dialog"); if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.open = true; } }
  async function openSettings() {
    try { state(await call("hourly_state")); } catch (error) { el("hourly-status").textContent = error.message; }
  }
  async function preview() {
    show(); el("hourly-message").textContent = t("Gathering your local check-in…");
    try { const value = await call("preview_hourly_checkin"); if (value.report && !value.collecting) render(value.report); }
    catch (error) { el("hourly-message").textContent = error.message; }
  }
  function onEvent(event) {
    if (event.type === "hourly_report") { render(event.report); show(); return true; }
    if (event.type === "hourly_update") { render(event.report); return true; }
    if (event.type === "hourly_state") { state(event); return true; }
    return false;
  }
  el("hourly-enabled").addEventListener("change", async () => {
    try { state(await call("set_hourly_checkin", el("hourly-enabled").checked)); }
    catch (error) { el("hourly-status").textContent = error.message; }
  });
  el("hourly-preview").addEventListener("click", preview);
  el("hourly-close").addEventListener("click", () => { const dialog = el("hourly-dialog"); if (dialog.close) dialog.close(); else dialog.open = false; });
  el("hourly-check-updates").addEventListener("click", () => el("check-updates").click());
  for (const [id, value] of [["hourly-useful", "useful"], ["hourly-not-useful", "not_useful"]]) el(id).addEventListener("click", async () => {
    try { await call("rate_hourly_checkin", value); el("hourly-message").textContent = t("Feedback saved on this device."); }
    catch (error) { el("hourly-message").textContent = error.message; }
  });
  window.addEventListener("languagechange", () => { if (report) render(report); });
  return {openSettings, onEvent};
})();
