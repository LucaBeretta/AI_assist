/* Two small menus: "⋯" for the open chat's own actions, and Appearance (look and Night/Day).
   Every item presses an existing button or calls an existing function, so each action keeps one implementation. */
"use strict";
window.menusui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const menu = el("quick-menu");
  let opener = null;

  const isOpen = () => Boolean(menu && !menu.hidden);
  function entries() { return [...(menu?.children || [])].filter(child => child.getAttribute?.("role")?.startsWith("menuitem") && !child.disabled); }
  function close(refocus = true) {
    if (!isOpen()) return;
    menu.hidden = true;
    opener?.setAttribute?.("aria-expanded", "false");
    if (refocus) opener?.focus?.();
    opener = null;
  }
  function entry(item) {
    if (item.separator) { const line = document.createElement("hr"); line.className = "quick-menu-line"; return line; }
    if (item.heading) { const head = document.createElement("div"); head.className = "quick-menu-head"; head.textContent = item.heading; return head; }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "popup-item" + (item.kind ? " " + item.kind : "");
    const choice = typeof item.checked === "boolean";
    button.setAttribute("role", choice ? "menuitemradio" : "menuitem");
    if (choice) button.setAttribute("aria-checked", String(item.checked));
    button.textContent = (choice ? (item.checked ? "✓ " : "   ") : "") + item.label;
    button.disabled = Boolean(item.disabled);
    button.addEventListener("click", () => { close(); item.run(); });
    return button;
  }
  // Next to its button, inside the window. Rectangles are on-screen pixels; with the app's zoom
  // (Ctrl + / -) the menu's own CSS pixels are larger or smaller, so convert before placing it.
  function place(button) {
    const box = button.getBoundingClientRect?.();
    if (!box || !menu.style) return;
    menu.style.left = "0px"; menu.style.top = "0px";
    const size = menu.getBoundingClientRect?.() || {};
    const width = size.width || 220, height = size.height || 160;
    const scale = size.width && menu.offsetWidth ? size.width / menu.offsetWidth : 1;
    const below = box.bottom + 4 + height <= window.innerHeight;
    const left = Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8));
    const top = below ? box.bottom + 4 : Math.max(8, box.top - height - 4);
    menu.style.left = left / scale + "px";
    menu.style.top = top / scale + "px";
  }
  function open(button, items) {
    if (!menu || !button) return;
    if (opener === button && isOpen()) { close(); return; }
    close(false);
    menu.replaceChildren(...items.map(entry));
    menu.hidden = false;  // measure after showing
    place(button);
    opener = button;
    button.setAttribute?.("aria-expanded", "true");
    entries()[0]?.focus?.();
  }

  // "⋯": what you can do with the chat that is open. Incognito chats never use memory.
  function chatItems() {
    const chat = window.app?.currentChat?.();
    const none = !chat?.id;  // nothing to rename or delete
    const items = [{label: t("Rename…"), disabled: none, run: () => el("rename-chat")?.click()}];
    if (!none) {
      items.push({label: t("Chat instructions…"), run: () => window.workspaceui?.openInstructions(chat.id)});
      if (!chat.incognito) items.push({label: t("Memories for this chat…"), run: () => window.workspaceui?.openMemories(chat.id)});
    }
    items.push({separator: true}, {label: t("Delete chat"), kind: "danger", disabled: none, run: () => el("delete-chat")?.click()});
    return items;
  }
  // Appearance: the looks Settings > Look offers, then Night / Day, with the current ones ticked.
  const theme = () => document.documentElement?.getAttribute?.("data-theme") === "day" ? "day" : "night";
  function appearanceItems() {
    const current = window.lookui?.current?.() || "classic";
    const looks = [...(el("setting-look")?.options || [])].map(option => ({
      label: option.textContent || option.value, checked: option.value === current,
      run: () => window.lookui?.setLook?.(option.value)}));
    // Read the theme when clicked: Ctrl+Shift+L may have changed it while the menu was open.
    const pick = name => () => { if (theme() !== name) el("theme")?.click(); };
    return [{heading: t("Look")}, ...looks, {separator: true}, {heading: t("Theme")},
            {label: t("Night"), checked: theme() === "night", run: pick("night")},
            {label: t("Day"), checked: theme() === "day", run: pick("day")}];
  }

  el("chat-more")?.addEventListener("click", () => open(el("chat-more"), chatItems()));
  el("open-appearance")?.addEventListener("click", () => open(el("open-appearance"), appearanceItems()));
  const inside = target => Boolean(menu?.contains?.(target) || opener?.contains?.(target));
  // A click elsewhere, or the focus moving elsewhere (Tab, a shortcut, another screen), closes the menu.
  document.addEventListener?.("pointerdown", event => { if (isOpen() && !inside(event.target)) close(false); }, true);
  document.addEventListener?.("focusin", event => { if (isOpen() && !inside(event.target)) close(false); }, true);
  // Escape closes the menu and does nothing else: it must not also stop an answer, a task or an edit.
  document.addEventListener?.("keydown", event => {
    if (event.key !== "Escape" || !isOpen()) return;
    event.preventDefault(); event.stopPropagation();
    close();
  }, true);
  menu?.addEventListener("keydown", event => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = entries(), at = items.indexOf(document.activeElement);
    items[(at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus?.();
  });
  return {open, close, chatItems, appearanceItems};
})();
