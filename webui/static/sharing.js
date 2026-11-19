/* Manual pairing only: reading settings never enables the LAN service. */
"use strict";
window.sharingui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key;
  let host = null, timer = null;
  const call = async (name, ...args) => { const value = await window.pywebview.api[name](...args); if (!value || value.ok === false) throw new Error(value?.error || t("Invalid API response")); return value; };
  function countdown() {
    const seconds = host ? Math.max(0, Math.ceil(host.expires_at - Date.now() / 1000)) : 0;
    el("sharing-pin").textContent = seconds ? `${host.pin} · ${seconds} s` : t("PIN expired. Generate a new one.");
  }
  function render(state) {
    el("sharing-enabled").checked = Boolean(state.enabled);
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
    el("sharing-new-pin").disabled = true;
    el("pair-submit").disabled = true;
    try { await call(name, ...args); el("sharing-status").textContent = t("Working…"); }
    catch (error) { el("sharing-status").textContent = error.message; await openSettings(); unlock(); }
  }
  function unlock() { el("sharing-enabled").disabled = false; el("pair-submit").disabled = false; }
  function onEvent(event) {
    if (event.operation !== "sharing" || !event.type.startsWith("job_")) return false;
    if (event.type !== "job_progress") {
      el("sharing-status").textContent = event.error || t(event.type === "job_stopped" ? "Stopped" : "Done");
      openSettings(); unlock();
    }
    return true;
  }
  el("sharing-enabled").addEventListener("change", () => run("set_sharing", el("sharing-enabled").checked, el("sharing-address").value.trim()));
  el("sharing-new-pin").addEventListener("click", () => run("new_pairing_pin"));
  el("pair-form").addEventListener("submit", event => {
    event.preventDefault();
    const pin = el("pair-pin").value.trim(); el("pair-pin").value = "";
    run("pair_device", el("pair-address").value.trim(), Number(el("pair-port").value), el("pair-code").value.trim(), pin);
  });
  return {openSettings, onEvent};
})();
