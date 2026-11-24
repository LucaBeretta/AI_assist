/* Manual pairing only: reading settings never enables the LAN service. */
"use strict";
window.sharingui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key;
  let host = null, timer = null;
  const source = document.documentElement?.dataset?.computeSource === "window" ? ["compute"] : [];
  const pairedMessage = source.length ? "Device paired securely. Look for Live under Paired computers." :
    "Device paired securely. Open Compute to check live usage.";
  const call = async (name, ...args) => { const value = await window.pywebview.api[name](...args); if (!value || value.ok === false) throw new Error(window.i18n?.gate?.(value?.error) || value?.error || t("Invalid API response")); return value; };
  function countdown() {
    const seconds = host ? Math.max(0, Math.ceil(host.expires_at - Date.now() / 1000)) : 0;
    el("sharing-pin").textContent = seconds ? `${host.pin} · ${seconds} s` : t("PIN expired. Generate a new one.");
  }
  function render(state) {
    el("sharing-enabled").checked = Boolean(state.enabled);
    if (state.enabled) el("sharing-usage").checked = Boolean(state.usage);
    el("sharing-details").hidden = !state.enabled;
    el("sharing-new-pin").disabled = !state.enabled;
    host = state.host;
    if (timer !== null) window.clearInterval?.(timer);
    timer = null;
    if (host) {
      el("sharing-endpoint").textContent = `${host.address}:${host.port}`;
      el("sharing-code").textContent = host.verification_code;
      countdown(); timer = window.setInterval?.(countdown, 1000) ?? null;
    }
    el("sharing-peers").replaceChildren(...Object.entries(state.peers || {}).map(([id, peer]) => {
      const item = document.createElement("li"); item.textContent = `${id} · ${peer.address}:${peer.port}`; return item;
    }));
  }
  async function openSettings() {
    try { render(await call("sharing_state")); }
    catch (error) { el("sharing-status").textContent = error.message; }
  }
  async function run(name, ...args) {
    el("sharing-enabled").disabled = true;
    el("sharing-usage").disabled = true;
    el("sharing-new-pin").disabled = true;
    el("pair-submit").disabled = true;
    el("sharing-status").textContent = t("Working…");
    try { await call(name, ...args, ...source); }
    catch (error) { el("sharing-status").textContent = error.message; await openSettings(); unlock(); }
  }
  function unlock() { el("sharing-enabled").disabled = false; el("sharing-usage").disabled = false; el("pair-submit").disabled = false; }
  function onEvent(event) {
    if (event.operation !== "sharing" || !event.type.startsWith("job_")) return false;
    if (event.type !== "job_progress") {
      const message = event.type === "job_stopped" ? "Stopped" :
        event.type === "job_done" && event.result?.peer_id ? pairedMessage : "Done";
      el("sharing-status").textContent = event.error || t(message);
      openSettings(); unlock();
    }
    return true;
  }
  el("sharing-enabled").addEventListener("change", () => run("set_sharing", el("sharing-enabled").checked, el("sharing-address").value.trim(), el("sharing-usage").checked));
  // Changing the usage choice while pairing is on restarts the service with the new choice.
  el("sharing-usage").addEventListener("change", () => { if (el("sharing-enabled").checked) run("set_sharing", true, el("sharing-address").value.trim(), el("sharing-usage").checked); });
  el("sharing-new-pin").addEventListener("click", () => run("new_pairing_pin"));
  el("pair-address").addEventListener("paste", event => {
    const endpoint = event.clipboardData?.getData("text").trim();
    const match = endpoint?.match(/^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$/);
    if (!match) return;
    event.preventDefault();
    el("pair-address").value = match[1];
    el("pair-port").value = match[2];
  });
  el("pair-form").addEventListener("submit", event => {
    event.preventDefault();
    const pin = el("pair-pin").value.trim(); el("pair-pin").value = "";
    run("pair_device", el("pair-address").value.trim(), Number(el("pair-port").value), el("pair-code").value.trim(), pin);
  });
  return {openSettings, onEvent};
})();
