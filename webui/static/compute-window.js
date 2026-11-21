/* Separate Compute window for a second screen. Polls the shared read-only snapshot; no other API use. */
"use strict";
window.computewindow = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
  const api = () => window.pywebview?.api;
  let fullscreen = false, timer = null;
  try { const saved = window.localStorage?.getItem("aiassist-theme"); if (saved === "day" || saved === "night") document.documentElement?.setAttribute("data-theme", saved); }
  catch (error) { /* storage unavailable */ }
  async function refresh() {
    if (document.hidden === true || !api()?.compute_state) return;
    try { const result = await api().compute_state(); if (result.ok !== false && result.data) window.computeui.render(result.data); }
    catch (error) { el("compute-note").textContent = error.message; }
  }
  function label() { el("compute-fullscreen").textContent = t(fullscreen ? "Exit full screen" : "Full screen"); }
  async function toggleFullscreen() {
    try {
      const result = await api().toggle_compute_fullscreen();
      if (result.ok === false) throw new Error(result.error);
      fullscreen = result.fullscreen === true; label();
    } catch (error) { el("compute-note").textContent = error.message; }
  }
  async function start() {
    try { const prefs = await api().compute_window_preferences(); if (prefs.ok !== false) window.i18n?.setLanguage(prefs.language || "system"); }
    catch (error) { /* keep the system language */ }
    await window.computeui.onVisible(true);
    await loadNetwork();
    if (timer === null) timer = setInterval(refresh, 2000);
  }
  // Cube faces repeat the headline readings; unknown values stay a dash rather than a guess.
  const percent = value => typeof value === "number" && Number.isFinite(value) ? Math.round(value) + "%" : "–";
  window.computeFaces = data => {
    const raw = data?.snapshot || {}, values = data?.values || {};
    el("cube-cpu").textContent = percent(raw.cpu?.percent);
    const total = raw.ram?.total_bytes, free = raw.ram?.available_bytes;
    const known = typeof total === "number" && total > 0 && typeof free === "number";
    el("cube-ram").textContent = known ? percent(100 * (total - free) / total) : "–";
    el("cube-ram-detail").textContent = known ? ((total - free) / 2 ** 30).toFixed(1) + " / " + (total / 2 ** 30).toFixed(0) + " GiB" : "";
    el("cube-gpu").textContent = percent(raw.gpus?.[0]?.percent);
    el("cube-gpu-detail").textContent = String(values.gpus?.[0]?.name || "").replace(/\s*\(.*\)\s*$/, "");
    el("cube-model").textContent = values.loaded?.[0] || t("No loaded models");
  };
  // ---------- Network: devices on the local network, found only when Scan is clicked ----------
  const ICONS = {
    computer: "M4 5h16v10H4zM2 19h20", phone: "M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2",
    tablet: "M6 2h12a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2", tv: "M3 5h18v12H3zM8 21h8",
    printer: "M7 9V3h10v6M5 9h14v7H5zM7 14h10v7H7z", speaker: "M7 2h10v20H7zM12 15a2 2 0 1 0 0-.01M12 6h.01",
    router: "M3 14h18v5H3zM7 14l2-6M17 14l-2-6M7 16.5h.01M11 16.5h.01", device: "M12 3l8 4.5v9L12 21l-8-4.5v-9z"
  };
  const KIND_LABELS = {computer: "Computer", phone: "Phone or tablet", tablet: "Tablet", tv: "TV or media player", printer: "Printer", speaker: "Speaker", router: "Router", device: "Device"};
  let paired = [], scanning = false;
  function svg(kind) {
    const ns = "http://www.w3.org/2000/svg", icon = document.createElementNS(ns, "svg"), path = document.createElementNS(ns, "path");
    icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("aria-hidden", "true");
    path.setAttribute("d", ICONS[kind] || ICONS.device); icon.append(path); return icon;
  }
  function node(tag, className, text) {
    const item = document.createElement(tag); if (className) item.className = className;
    if (text !== undefined) item.textContent = text; return item;
  }
  function deviceRow(device) {
    const row = node("div", "net-row" + (device.ai ? " ai" : ""));
    const icon = node("span", "net-icon"); icon.title = t(KIND_LABELS[device.kind] || "Device"); icon.append(svg(device.kind));
    const text = node("div", "net-text");
    text.append(node("p", "net-name", device.name || (device.router ? t("Router") : t(KIND_LABELS[device.kind] || "Device"))), node("p", "net-sub", device.address));
    const tags = node("div", "net-tags");
    if (device.this) tags.append(node("span", "net-tag", t("This computer")));
    if (device.router && device.name) tags.append(node("span", "net-tag", t("Router")));
    if (device.ollama) tags.append(node("span", "net-tag ai", "Ollama " + device.ollama));
    if (device.ai_assist) tags.append(node("span", "net-tag ai", t("AI Assist · unverified")));
    if (paired.some(row => String(row.endpoint || "").split(":")[0] === device.address)) tags.append(node("span", "net-tag ai", t("Paired")));
    row.append(icon, text, tags);
    return row;
  }
  function renderNetwork(scan) {
    const list = el("net-list");
    if (!scan) { list.replaceChildren(); return; }
    const devices = scan.devices || [], ai = devices.filter(d => d.ai), other = devices.filter(d => !d.ai);
    const parts = [node("p", "net-group", t("AI computers"))];
    parts.push(...(ai.length ? ai.map(deviceRow) : [node("p", "setting-hint", t("No AI computers found."))]));
    if (other.length) parts.push(node("p", "net-group", t("Other devices ({v0})", {v0: other.length})), ...other.map(deviceRow));
    list.replaceChildren(...parts);
    el("net-meta").textContent = [scan.network, scan.time ? new Date(scan.time * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""].filter(Boolean).join(" · ");
    el("net-note").textContent = scan.note ? t(scan.note) : "";
  }
  async function scanNetwork() {
    if (scanning || !api()?.scan_network) return;
    scanning = true; el("net-scan").disabled = true; el("net-scan").textContent = t("Scanning…");
    el("net-note").textContent = t("Looking for devices on this network…");
    try {
      const result = await api().scan_network();
      if (result.ok === false) throw new Error(window.i18n?.gate?.(result.error) || result.error);
      renderNetwork(result.scan);
    } catch (error) { el("net-note").textContent = error.message; }
    finally { scanning = false; el("net-scan").disabled = false; el("net-scan").textContent = t("Scan"); }
  }
  async function loadNetwork() {
    try { const result = await api()?.network_state?.(); if (result?.ok !== false && result?.scan) renderNetwork(result.scan); }
    catch (error) { /* keep the hint */ }
  }
  // Paired computers that share usage: live CPU, RAM and GPU, refreshed with every Compute reading.
  const PEER_STATES = {"live": "Live", "not sharing usage": "Not sharing usage", "offline": "Offline", "rejected": "Rejected", "paired · live telemetry unavailable": "Paired"};
  function meterRow(label, percent, detail) {
    const known = typeof percent === "number" && Number.isFinite(percent);
    const row = node("div", "peer-meter");
    const bar = node("span", "peer-bar"), fill = node("span", "peer-fill");
    fill.style.width = (known ? Math.max(2, Math.min(100, percent)) : 0) + "%";
    bar.append(fill);
    row.append(node("span", "peer-label", label), bar, node("span", "peer-value", known ? Math.round(percent) + "%" : "–"));
    if (detail) row.title = detail;
    return row;
  }
  const gib = bytes => (bytes / 2 ** 30).toFixed(1);
  function peerCard(peer) {
    const usage = peer.usage, live = peer.state === "live" && usage;
    const card = node("div", "peer-card" + (live ? " live" : ""));
    const head = node("div", "peer-head");
    const name = node("p", "net-name", (usage && usage.name) || peer.id);
    const state = node("span", "net-tag" + (live ? " ai" : ""), t(PEER_STATES[peer.state] || "Paired"));
    head.append(name, state);
    card.append(head, node("p", "net-sub", peer.endpoint + (live && usage.loaded.length ? " · " + usage.loaded.join(", ") : "")));
    if (live) {
      const ram = usage.ram, gpu = usage.gpus[0];
      const ramPercent = ram.total_bytes ? 100 * (ram.total_bytes - ram.available_bytes) / ram.total_bytes : null;
      card.append(meterRow("CPU", usage.cpu.percent, usage.cpu.name),
        meterRow("RAM", ramPercent, ram.total_bytes ? gib(ram.total_bytes - ram.available_bytes) + " / " + gib(ram.total_bytes) + " GiB" : ""),
        meterRow("GPU", gpu ? gpu.percent : null, gpu ? gpu.name + (gpu.total_bytes ? " · " + gib(gpu.used_bytes) + " / " + gib(gpu.total_bytes) + " GiB" : "") : ""));
    }
    return card;
  }
  function renderPaired() {
    const box = el("net-paired");
    box.replaceChildren(...(paired.length ? [node("p", "net-group", t("Paired computers")), ...paired.map(peerCard)] : []));
  }
  el("net-scan").addEventListener("click", scanNetwork);
  const facesBefore = window.computeFaces;
  window.computeFaces = data => { paired = Array.isArray(data?.peers) ? data.peers : []; renderPaired(); facesBefore(data); };
  el("compute-fullscreen").addEventListener("click", toggleFullscreen);
  document.addEventListener?.("keydown", event => {
    if (event.key === "F11" || (event.key === "Escape" && fullscreen)) { event.preventDefault?.(); toggleFullscreen(); }
    else if (event.key.toLowerCase?.() === "r" && (event.ctrlKey || event.metaKey)) { event.preventDefault?.(); refresh(); }
  });
  window.addEventListener("languagechange", label);
  window.addEventListener("pywebviewready", start);
  return {start, refresh, toggleFullscreen, scanNetwork, renderNetwork};
})();
