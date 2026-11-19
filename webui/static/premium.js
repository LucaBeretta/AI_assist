/* Small keyboard and reading features, with plain testable transformations. */
"use strict";
window.premiumui = (() => {
  let zoom = 1, loaded = false, timer = null;
  const expanded = new Set();
  const el = id => document.getElementById(id);
  function splitThinking(source) {
    const answer = [], thinking = [];
    let cursor = 0, depth = 0, fence = null;
    const append = text => (depth ? thinking : answer).push(text);
    const tokens = /(^ {0,3}(`{3,}|~{3,}).*$)|(<\/?think>)/gm;
    for (const match of String(source).matchAll(tokens)) {
      append(source.slice(cursor, match.index)); cursor = match.index + match[0].length;
      if (match[2]) {
        if (!fence) fence = match[2];
        else if (match[2][0] === fence[0] && match[2].length >= fence.length) fence = null;
        append(match[0]);
      } else if (fence) append(match[0]);
      else if (match[0] === "<think>") depth++;
      else if (depth) depth--;
      else append(match[0]);
    }
    append(source.slice(cursor));
    return {answer: answer.join(""), thinking: thinking.join(""), pending: depth > 0};
  }
  function renderReply(source, seconds, identity) {
    const parts = splitThinking(String(source)), result = document.createDocumentFragment();
    if (parts.thinking) {
      const details = document.createElement("details"), summary = document.createElement("summary");
      details.className = "thinking";
      details.open = expanded.has(identity);
      details.addEventListener("toggle", () => { if (identity) { if (details.open) expanded.add(identity); else expanded.delete(identity); } });
      summary.textContent = Number.isFinite(seconds) ? `Thought for ${Math.round(seconds)} s` : parts.pending ? "Thinking…" : "Thought process";
      details.append(summary, window.renderMarkdown(parts.thinking)); result.append(details);
    }
    result.append(window.renderMarkdown(parts.answer)); return result;
  }
  function dateGroup(value, now = new Date()) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "";
    const day = item => Date.UTC(item.getFullYear(), item.getMonth(), item.getDate());
    const days = Math.round((day(now) - day(date)) / 86400000);
    return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Last 7 days" : "Older";
  }
  function groupNodes(records, nodes) {
    const groups = new Map();
    records.forEach((record, index) => {
      const group = dateGroup(record.updated_at || record.created_at);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(nodes[index]);
    });
    const out = [];
    for (const name of ["Today", "Yesterday", "Last 7 days", "Older", ""]) {
      if (!groups.has(name)) continue;
      if (name) { const heading = document.createElement("li"); heading.className = "chat-date"; heading.textContent = name; out.push(heading); }
      out.push(...groups.get(name));
    }
    return out;
  }
  function applyZoom(value) {
    zoom = Math.round(Math.min(1.5, Math.max(.7, value)) * 10) / 10;
    if (document.documentElement?.style) document.documentElement.style.zoom = String(zoom);
  }
  function saveZoom() {
    const save = () => window.pywebview.api.set_web_preferences({zoom}).then(value => {
      if (value.ok === false) el("status").textContent = value.error;
    }).catch(failure => { el("status").textContent = failure.message; });
    if (typeof setTimeout === "function") { if (timer) clearTimeout(timer); timer = setTimeout(save, 250); } else save();
  }
  function onState(state) { if (!loaded) { applyZoom(state.settings?.zoom || 1); loaded = true; } }
  function key(event) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return false;
    if (event.key === "/") {
      event.preventDefault(); const dialog = el("shortcuts-dialog"); if (dialog.showModal) dialog.showModal(); else dialog.open = true; return true;
    }
    if (!["+", "=", "-", "0"].includes(event.key)) return false;
    event.preventDefault(); applyZoom(event.key === "0" ? 1 : zoom + (event.key === "-" ? -.1 : .1)); saveZoom(); return true;
  }
  el("shortcuts-close").addEventListener("click", () => { const dialog = el("shortcuts-dialog"); if (dialog.close) dialog.close(); else dialog.open = false; });
  return {renderReply, splitThinking, dateGroup, groupNodes, key, onState};
})();
