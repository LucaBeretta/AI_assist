/* Two small menus: "⋯" for the open chat's own actions, and Appearance (look and Night/Day).
   Every item presses an existing button or calls an existing function, so each action keeps one implementation. */
"use strict";
window.menusui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const menu = el("quick-menu");
  let opener = null;

  function entries() { return [...(menu?.children || [])].filter(child => child.getAttribute?.("role")?.startsWith("menuitem")); }
  function close(refocus = true) {
    if (!menu || menu.hidden) return;
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
    button.addEventListener("click", () => { close(); item.run(); });
    return button;
  }
  function open(button, items) {
    if (!menu || !button) return;
    if (opener === button && !menu.hidden) { close(); return; }
    close(false);
    menu.replaceChildren(...items.map(entry));
    const box = button.getBoundingClientRect?.();
    if (box && menu.style) {
      menu.hidden = false;  // measure after showing
      const width = menu.offsetWidth || 220, height = menu.offsetHeight || 160;
      const below = box.bottom + 4 + height <= window.innerHeight;
      menu.style.left = Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8)) + "px";
      menu.style.top = (below ? box.bottom + 4 : Math.max(8, box.top - height - 4)) + "px";
    }
    menu.hidden = false;
    opener = button;
    button.setAttribute?.("aria-expanded", "true");
    entries()[0]?.focus?.();
  }

  // "⋯": what you can do with the chat that is open. Incognito chats never use memory.
  function chatItems() {
    const chat = window.app?.currentChat?.();
    const items = [{label: t("Rename…"), run: () => el("rename-chat")?.click()}];
    if (chat?.id) {
      items.push({label: t("Chat instructions…"), run: () => window.workspaceui?.openInstructions(chat.id)});
      if (!chat.incognito) items.push({label: t("Memories for this chat…"), run: () => window.workspaceui?.openMemories(chat.id)});
    }
    items.push({separator: true}, {label: t("Delete chat"), kind: "danger", run: () => el("delete-chat")?.click()});
    return items;
  }
  // Appearance: the looks Settings > Look offers, then Night / Day, with the current ones ticked.
  function appearanceItems() {
    const current = window.lookui?.current?.() || "classic";
    const looks = [...(el("setting-look")?.options || [])].map(option => ({
      label: option.textContent || option.value, checked: option.value === current,
      run: () => window.lookui?.setLook?.(option.value)}));
    const theme = document.documentElement?.getAttribute?.("data-theme") === "day" ? "day" : "night";
    const pick = name => () => { if (theme !== name) el("theme")?.click(); };
    return [{heading: t("Look")}, ...looks, {separator: true}, {heading: t("Theme")},
            {label: t("Night"), checked: theme === "night", run: pick("night")},
            {label: t("Day"), checked: theme === "day", run: pick("day")}];
  }

  el("chat-more")?.addEventListener("click", () => open(el("chat-more"), chatItems()));
  el("open-appearance")?.addEventListener("click", () => open(el("open-appearance"), appearanceItems()));
  // A click anywhere else, or Escape, closes the menu; the arrow keys move through it.
  document.addEventListener?.("pointerdown", event => {
    if (!menu || menu.hidden || menu.contains?.(event.target) || opener?.contains?.(event.target)) return;
    close(false);
  }, true);
  menu?.addEventListener("keydown", event => {
    const items = entries(), at = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      items[(at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus?.();
    }
  });
  return {open, close, chatItems, appearanceItems};
})();
