/* Master switch between the looks: classic, the 3D cockpit and the 3D viewport (viewport3d.js).
   Saved in AI Assist's settings file (webui.settings), with a per-window copy so the page opens in the right look. */
"use strict";
window.lookui = (() => {
  const root = document.documentElement;
  const looks = ["classic", "cockpit", "viewport"];
  const el = id => document.getElementById(id);
  const api = () => window.pywebview?.api;
  function show(name) {
    const look = looks.includes(name) ? name : "classic";
    root?.setAttribute?.("data-look", look);
    try { window.localStorage?.setItem("aiassist-look", look); } catch (error) { /* storage unavailable */ }
    el("look")?.setAttribute?.("aria-pressed", String(look !== "classic"));
    const choice = el("setting-look");
    if (choice) choice.value = look;
    return look;
  }
  async function setLook(name) {
    const look = show(name);
    try { await api()?.set_web_preferences?.({look}); } catch (error) { /* the look still changes for this window */ }
    return look;
  }
  const current = () => looks.includes(root?.getAttribute?.("data-look")) ? root.getAttribute("data-look") : "classic";
  // The cube steps through the looks in order, then back to classic.
  const toggle = () => setLook(looks[(looks.indexOf(current()) + 1) % looks.length]);
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
