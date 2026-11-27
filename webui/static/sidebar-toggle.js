/* Narrow windows (760 px or less) have no room for the sidebar. A menu button opens it over the
   page, so the sections, the chat list and Settings stay one click away there too. */
"use strict";
window.sidebarui = (() => {
  const button = document.getElementById("open-sidebar");
  const app = document.querySelector?.(".app");
  const sidebar = document.querySelector?.(".sidebar");
  if (!button || !app || !sidebar) return null;
  const isOpen = () => Boolean(app.classList?.contains("sidebar-open"));
  function set(open) {
    app.classList?.toggle("sidebar-open", open);
    button.setAttribute("aria-expanded", String(open));
  }
  button.addEventListener("click", () => set(!isOpen()));
  // Choosing something in the sidebar closes it (the choice still happens), and so does a click elsewhere.
  sidebar.addEventListener("click", event => { if (event.target?.closest?.("button, a, li")) set(false); });
  document.addEventListener?.("pointerdown", event => {
    if (isOpen() && !sidebar.contains?.(event.target) && !button.contains?.(event.target)) set(false);
  }, true);
  // Escape closes it, and only that: it must not also stop an answer or a Developer task.
  document.addEventListener?.("keydown", event => {
    if (event.key !== "Escape" || !isOpen()) return;
    event.preventDefault(); event.stopPropagation();
    set(false); button.focus?.();
  }, true);
  window.matchMedia?.("(max-width: 760px)")?.addEventListener?.("change", () => set(false));
  return {isOpen, set};
})();
