/* Master switch between the classic look and the 3D cockpit.
   Saved in AI Assist's settings file (webui.settings), with a per-window copy so the page opens in the right look. */
"use strict";
window.lookui = (() => {
  const root = document.documentElement;
  const looks = ["classic", "cockpit"];
  const el = id => document.getElementById(id);
  const api = () => window.pywebview?.api;
  function show(name) {
    const look = looks.includes(name) ? name : "classic";
    root?.setAttribute?.("data-look", look);
    try { window.localStorage?.setItem("aiassist-look", look); } catch (error) { /* storage unavailable */ }
    el("look")?.setAttribute?.("aria-pressed", String(look === "cockpit"));
    const choice = el("setting-look");
    if (choice) choice.value = look;
    return look;
  }
  async function setLook(name) {
    const look = show(name);
    try { await api()?.set_web_preferences?.({look}); } catch (error) { /* the look still changes for this window */ }
    return look;
  }
  const current = () => root?.getAttribute?.("data-look") === "cockpit" ? "cockpit" : "classic";
  const toggle = () => setLook(current() === "cockpit" ? "classic" : "cockpit");
  async function loadSaved() {
    try {
      const result = await api()?.web_preferences?.();
      if (result?.ok && result.settings?.look) show(result.settings.look);
    } catch (error) { /* keep the window's copy */ }
  }
  let saved = null;
  try { saved = window.localStorage?.getItem("aiassist-look"); } catch (error) { /* storage unavailable */ }
  show(saved);
  if (api()?.web_preferences) loadSaved();
  else window.addEventListener?.("pywebviewready", loadSaved);
  el("look")?.addEventListener("click", toggle);
  el("setting-look")?.addEventListener("change", () => setLook(el("setting-look").value));
  return {setLook, toggle, current, loadSaved};
})();
