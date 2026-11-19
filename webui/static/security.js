/* Internet approvals are authoritative in Python; the page only presents choices. */
"use strict";
window.securityui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
  let approval = null, timer = null, deadline = 0, logs = [], refreshTimer = null;
  const call = async (name, ...args) => { const value = await window.pywebview.api[name](...args); if (!value || value.ok === false) throw new Error(value?.error || t("Invalid API response")); return value; };
  function countdown() {
    if (!approval) return;
    const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    el("internet-allow").disabled = approval.stage === 2 && remaining > 0;
    el("internet-allow").textContent = approval.stage === 1 ? t("Continue to allow…") : remaining ? t("Allow ({v0})", {v0: remaining}) : t("Allow");
  }
  function show(value) {
    approval = value;
    const request = value.request;
    el("internet-title").textContent = value.stage === 1 ? t("AI Assist wants to use the internet") : t("Second check: allow {v0}?", {v0: request.target});
    for (const key of ["kind", "target", "purpose", "sends", "source", "scope"]) el("internet-" + key).textContent = request[key] || t("Not declared. Block unless you expected it.");
    el("internet-error").textContent = "";
    deadline = Date.now() + value.remaining_seconds * 1000;
    if (timer !== null) window.clearInterval?.(timer);
    timer = window.setInterval?.(countdown, 100) ?? null;
    countdown();
    const dialog = el("internet-dialog");
    if (!dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.open = true; }
    el("internet-block").focus?.();
  }
  async function respond(action) {
    if (!approval) return;
    const id = approval.approval_id;
    try { await call("respond_internet", id, action); }
    catch (error) { if (approval?.approval_id === id) el("internet-error").textContent = error.message; }
  }
  function hide(id) {
    if (approval?.approval_id !== id) return;
    approval = null;
    if (timer !== null) window.clearInterval?.(timer);
    timer = null;
    const dialog = el("internet-dialog"); if (dialog.close) dialog.close(); else dialog.open = false;
  }
  function renderLog() {
    el("connection-log").replaceChildren(...logs.slice(-100).reverse().map(entry => {
      const item = document.createElement("li");
      item.textContent = [entry.time, entry.decision, entry.kind, entry.target, entry.purpose, entry.sends].filter(Boolean).join(" · ");
      return item;
    }));
  }
  function render(state) {
    logs = state.connections || logs; renderLog();
    el("privacy-gate").textContent = state.gate.installed ? t("Internet locked; external requests need approval.") : t("Internet gate is not active.");
    const summary = state.firewall;
    el("firewall-status").textContent = state.busy ? t("Working…") : summary ? [t(summary.state), summary.message || summary.error || ""].filter(Boolean).join(" · ") : t("Checking firewall…");
    for (const id of ["firewall-on", "firewall-off", "firewall-relock", "firewall-lift"]) el(id).disabled = Boolean(state.busy) || summary?.state === "unavailable";
    if (state.approval) show(state.approval);
  }
  async function openSettings() {
    try {
      const state = await call("privacy_state"); render(state);
      if (refreshTimer !== null) window.clearTimeout?.(refreshTimer);
      if (state.busy) refreshTimer = window.setTimeout?.(() => { if (el("settings-dialog").open) openSettings(); }, 500) ?? null;
    }
    catch (error) { el("privacy-gate").textContent = error.message; }
  }
  async function changeFirewall(action, minutes = null) {
    try { await call("change_firewall", action, minutes); el("firewall-status").textContent = t("Working…"); }
    catch (error) { el("firewall-status").textContent = error.message; }
  }
  function privacyText(text) {
    return String(text || "").split(" · ").map(part => part.startsWith("online now: ") ? t("Online now: {v0}", {v0: part.slice(12)}) : t(part)).join(" · ");
  }
  function onEvent(event) {
    if (event.type === "internet_approval") { show(event); return true; }
    if (event.type === "internet_closed") { hide(event.approval_id); return true; }
    if (event.type === "connection") { logs.push(event.entry); logs = logs.slice(-200); renderLog(); return true; }
    if (event.operation === "firewall" && event.type.startsWith("job_")) {
      if (event.error) el("firewall-status").textContent = event.error;
      if (event.type !== "job_progress") openSettings();
      return true;
    }
    return false;
  }
  el("internet-block").addEventListener("click", () => respond("block"));
  el("internet-allow").addEventListener("click", () => respond(approval?.stage === 1 ? "continue" : "allow"));
  el("internet-dialog").addEventListener("cancel", event => { event.preventDefault(); respond("block"); });
  el("internet-dialog").addEventListener("close", () => { if (approval) respond("block"); });
  el("internet-dialog").addEventListener("keydown", event => {
    if (["Enter", "Escape"].includes(event.key)) { event.preventDefault(); event.stopPropagation?.(); respond("block"); }
  });
  el("privacy-refresh").addEventListener("click", openSettings);
  el("firewall-on").addEventListener("click", () => changeFirewall("on"));
  el("firewall-off").addEventListener("click", () => changeFirewall("off"));
  el("firewall-relock").addEventListener("click", () => changeFirewall("relock"));
  el("firewall-lift").addEventListener("click", () => changeFirewall("lift", Number(el("firewall-minutes").value)));
  window.addEventListener("languagechange", () => { if (approval) show({...approval, remaining_seconds: Math.max(0, (deadline - Date.now()) / 1000)}); });
  return {onEvent, openSettings, privacyText};
})();
