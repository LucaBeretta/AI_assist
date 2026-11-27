/* The Developer tab's "Open in OpenCode" button: opens the chosen folder in OpenCode, locked to local models. */
"use strict";
window.opencodeui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const api = () => window.pywebview?.api;
  async function refresh() {
    if (!api()?.opencode_state) return;
    try {
      // The button stays clickable: OpenCode may be installed while AI Assist is open, and a click checks again.
      const state = await api().opencode_state();
      el("opencode-status").textContent = state?.installed ? "" : t("Install OpenCode from opencode.ai to use this button.");
    } catch (error) { /* the button stays as it is */ }
  }
  async function open() {
    const button = el("opencode-open");
    button.disabled = true;
    try {
      // The model chosen for chats, when it is one on this computer (a plain Ollama tag, not Colibri or a paired computer).
      const chosen = document.getElementById("model")?.value || "";
      const result = await api().open_opencode(el("dev-project").value.trim(), chosen && !chosen.startsWith("[") ? chosen : null);
      el("opencode-status").textContent = result?.ok ? t("OpenCode opened in a new window.") : t(result?.error || "Invalid API response");
    } catch (error) { el("opencode-status").textContent = error.message; }
    finally { button.disabled = false; }
  }
  el("opencode-open")?.addEventListener("click", open);
  if (api()?.opencode_state) refresh(); else window.addEventListener?.("pywebviewready", refresh);
  return {refresh, open};
})();
