/* Browser look: the top bar drives the app's own buttons, so every action keeps one implementation.
   Tabs mirror the sidebar's sections; back and forward walk the sections you visited;
   the address bar shows where you are and opens search (Ctrl K). */
"use strict";
window.browserui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const tabs = [...(document.querySelectorAll?.(".browser-tab") || [])];
  const slug = {"nav-chats": "chats", "nav-dev": "developer", "nav-models": "models", "nav-generate": "generate", "nav-web": "web"};
  const back = [], forward = [];
  let current = null, travelling = false;

  const press = id => el(id)?.click?.();
  function activeNav() {
    return Object.keys(slug).find(id => el(id)?.getAttribute?.("aria-current") === "page") || null;
  }
  function address() {
    const nav = activeNav();
    const place = nav ? slug[nav] : "welcome";
    const title = nav === "nav-chats" ? (el("chat-title")?.textContent || "").trim() : "";
    return "aiassist://" + place + (title ? "/" + title : "");
  }
  function render() {
    const nav = activeNav();
    for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.nav === nav));
    const text = el("browser-address-text");
    if (text) text.textContent = address();
    if (el("browser-back")) el("browser-back").disabled = !back.length;
    if (el("browser-forward")) el("browser-forward").disabled = !forward.length;
  }
  // Record each section change, unless it came from Back or Forward.
  function sectionChanged() {
    const nav = activeNav();
    if (nav && nav !== current) {
      if (current && !travelling) { back.push(current); forward.length = 0; }
      current = nav;
    }
    travelling = false;
    render();
  }
  function go(from, to) {
    const target = from.pop();
    if (!target) return;
    to.push(current);
    travelling = true;
    press(target);
    sectionChanged();
  }

  for (const tab of tabs) tab.addEventListener("click", () => press(tab.dataset.nav));
  // New chat, then show it: the app's New chat button leaves the current section in front.
  el("browser-new-tab")?.addEventListener("click", () => { press("new-chat"); press("nav-chats"); });
  el("browser-back")?.addEventListener("click", () => go(back, forward));
  el("browser-forward")?.addEventListener("click", () => go(forward, back));
  el("browser-reload")?.addEventListener("click", () => press("refresh"));
  el("browser-address")?.addEventListener("click", () => press("open-palette"));
  el("browser-mission")?.addEventListener("click", () => press("open-mission-control"));
  el("browser-settings")?.addEventListener("click", () => press("open-settings"));

  // The app marks the current section with aria-current and names the chat in #chat-title: follow both.
  const Watch = window.MutationObserver;
  if (Watch) {
    const watcher = new Watch(sectionChanged);
    for (const id of Object.keys(slug)) if (el(id)) watcher.observe(el(id), {attributes: true, attributeFilter: ["aria-current"]});
    if (el("chat-title")) watcher.observe(el("chat-title"), {childList: true, characterData: true, subtree: true});
  }
  sectionChanged();
  return {render, address, back: () => go(back, forward), forward: () => go(forward, back), sectionChanged, t};
})();
