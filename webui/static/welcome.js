/* A skippable first conversation, using the same model download events as Models. */
"use strict";
window.welcomeui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const el = id => document.getElementById(id);
  let state = null, shown = false, advice = null, pull = null, pendingModel = null, cancelRequested = false;
  const finished = new Set();
  const call = async (method, ...args) => {
    const value = await window.pywebview.api[method](...args);
    if (!value || value.ok === false) throw new Error(window.i18n?.gate?.(value?.error) || value?.error || t("Invalid API response"));
    return value;
  };
  const installed = () => state?.ollama?.models || (state?.engine === "Ollama" ? state.models || [] : []);
  const gb = bytes => (bytes / 1024 ** 3).toFixed(1) + " GB";
  const downloadGb = bytes => (bytes / 1000 ** 3).toFixed(1) + " GB";  // decimal, as ollama.com lists it
  function render() {
    const name = el("welcome-model").value;
    const ready = installed().includes(name);
    el("welcome-start").disabled = !name || Boolean(pendingModel);
    el("welcome-start").hidden = ready;
    el("welcome-open").hidden = !ready; el("welcome-open").disabled = Boolean(pendingModel);
    el("welcome-stop").hidden = !pendingModel; el("welcome-download").hidden = !pendingModel;
    el("welcome-model").disabled = Boolean(pendingModel);
    const card = (advice?.recommendations || []).find(item => item.name === name);
    el("welcome-reason").textContent = card ? [card.purpose, card.fit, card.reason, card.size_bytes ? downloadGb(card.size_bytes) + t(" download") : ""].filter(Boolean).join(" · ") : t("Already installed on this computer.");
  }
  async function load(retry = false) {
    el("welcome-status").textContent = t("Reading hardware and recommendations…");
    el("welcome-retry").hidden = true;
    try {
      advice = await call("recommend_models", retry === true);
      const hw = advice.hardware, gpu = (hw.gpus || []).find(item => item.supported !== false);
      el("welcome-hardware").textContent = [gpu?.name || "CPU", gpu?.total_bytes ? gb(gpu.total_bytes) + " VRAM" : "", hw.ram_total_bytes ? gb(hw.ram_total_bytes) + " RAM" : "", hw.cpu].filter(Boolean).join(" · ");
      const names = [...new Set([...installed(), ...(advice.recommendations || []).map(item => item.name)])];
      el("welcome-model").replaceChildren(...names.map(name => {
        const option = document.createElement("option"); option.value = name; option.textContent = name; return option;
      }));
      el("welcome-model").value = names[0] || "";
      el("welcome-status").textContent = names.length ? t("Choose a model to continue. You can change it later.") : t("No suitable recommendation is available. Open Models to choose one manually.");
      render();
    } catch (failure) { el("welcome-status").textContent = failure.message; el("welcome-retry").hidden = false; }
  }
  // ---------- your 3D character (built by Blender on this computer) ----------
  const characterSelects = ["skin", "hair_style", "height", "build", "accessory"];
  const characterColours = ["hair_color", "top_color"];
  const optionLabel = value => t(value.charAt(0).toUpperCase() + value.slice(1));
  let characterBuilding = false;
  function showPortrait(url) {
    const image = el("character-portrait");
    image.hidden = !url;
    if (url) { image.src = url; image.alt = t("Your 3D character"); }
  }
  async function loadCharacter() {
    if (!window.pywebview.api.character_state) return;
    el("welcome-character").hidden = false;
    try {
      const info = await call("character_state");
      for (const field of characterSelects) {
        el("character-" + field).replaceChildren(...(info.options[field] || []).map(value => {
          const option = document.createElement("option"); option.value = value; option.textContent = optionLabel(value); return option;
        }));
      }
      const saved = info.choices || {};
      el("character-name").value = saved.name || "";
      for (const field of [...characterSelects, ...characterColours]) if (saved[field]) el("character-" + field).value = saved[field];
      if (!saved.skin) el("character-skin").value = "light";
      if (!saved.height) el("character-height").value = "medium";
      if (!saved.build) el("character-build").value = "average";
      if (!saved.hair_style) el("character-hair_style").value = "short";
      showPortrait(info.portrait);
      characterBuilding = Boolean(info.building);
      el("character-form").hidden = !info.blender;
      el("character-create").hidden = !info.blender;
      el("character-create").disabled = characterBuilding;
      el("character-status").textContent = !info.blender ? t("Install Blender from blender.org to make your character. You can do it later.")
        : characterBuilding ? t("Blender is building your character…") : "";
    } catch (failure) { el("character-status").textContent = failure.message; }
  }
  async function buildCharacter() {
    if (characterBuilding) return;
    const choices = {name: el("character-name").value};
    for (const field of [...characterSelects, ...characterColours]) choices[field] = el("character-" + field).value;
    characterBuilding = true; el("character-create").disabled = true;
    el("character-status").textContent = t("Blender is building your character… This takes a minute or two.");
    try { await call("build_character", choices); }
    catch (failure) { characterBuilding = false; el("character-create").disabled = false; el("character-status").textContent = failure.message; }
  }
  function onCharacterEvent(event) {
    if (event.operation !== "character" || !["job_done", "job_error", "job_stopped"].includes(event.type)) return false;
    characterBuilding = false; el("character-create").disabled = false;
    if (event.type === "job_done") {
      showPortrait(event.result?.portrait);
      el("character-status").textContent = event.result?.files?.avatar
        ? t("Your character is ready. Its files for Unreal Engine (.glb) and VRM apps (.vrm) are in {v0}", {v0: event.result.folder})
        : t("Your character is ready.");
    }
    else el("character-status").textContent = event.error || t("Operation stopped.");
    return true;
  }
  // A computer without its own models can use a paired computer's shared AI instead.
  async function sharedModelsReady() {
    if (!window.pywebview.api.shared_ai_models) return false;
    try { return (await call("shared_ai_models")).computers.some(item => item.state === "ready" && item.models.length); }
    catch (error) { return false; }
  }
  async function onReady(value) {
    state = value;
    if (shown || !window.pywebview.api.set_web_preferences) return;
    const available = installed().length + (state?.colibri?.models?.length || 0);
    // Without an engine on this computer the app has no state; the saved preferences still say whether the welcome was done.
    let settings = state?.settings;
    if (!settings && window.pywebview.api.web_preferences) {
      try { settings = (await call("web_preferences")).settings; } catch (error) { settings = null; }
    }
    if (settings?.first_run_complete && (available || await sharedModelsReady())) return;
    shown = true; window.app.showView("welcome"); loadCharacter(); await load();
  }
  async function start() {
    const name = el("welcome-model").value;
    if (!name || pendingModel) return;
    pendingModel = name; cancelRequested = false; el("welcome-status").textContent = t("Starting the download…"); render();
    try {
      const result = await call("pull_model", name);
      if (finished.has(result.pull_id)) return;
      pull = result.pull_id;
      if (cancelRequested) await call("stop_pull", pull);
    } catch (failure) { pendingModel = null; pull = null; el("welcome-status").textContent = failure.message; render(); }
  }
  function onEvent(event) {
    if (onCharacterEvent(event)) return;
    if (!pendingModel || event.model !== pendingModel || (pull && pull !== event.pull_id)) return;
    if (event.type === "pull_progress") {
      pull = event.pull_id;
      el("welcome-progress").max = event.total || 1; el("welcome-progress").value = event.completed || 0;
      el("welcome-progress-text").textContent = event.total ? `${Math.floor(event.completed / event.total * 100)}% · ${event.status}` : event.status;
    } else if (["pull_done", "pull_error", "pull_stopped"].includes(event.type)) {
      finished.add(event.pull_id); pendingModel = null; pull = null;
      if (event.type === "pull_done") {
        const names = [...installed(), event.model]; state = {...state, ollama: {models: names}};
        window.app.refreshModels(); el("welcome-status").textContent = t("The model is ready. Open your first chat.");
      } else el("welcome-status").textContent = event.error || t("Download stopped. You can resume it later.");
      render();
    }
  }
  async function complete(openChat) {
    try {
      if (pendingModel) { cancelRequested = true; if (pull) await call("stop_pull", pull); }
      await call("set_web_preferences", {first_run_complete: true});
      if (openChat) { await window.app.refreshModels(); await window.app.selectModel(el("welcome-model").value); }
      window.app.showView("chat");
    } catch (failure) { el("welcome-status").textContent = failure.message; }
  }
  el("welcome-model").addEventListener("change", render);
  el("welcome-start").addEventListener("click", start);
  el("welcome-retry").addEventListener("click", () => load(true));
  el("welcome-stop").addEventListener("click", async () => { cancelRequested = true; if (pull) await call("stop_pull", pull).catch(failure => { el("welcome-status").textContent = failure.message; }); });
  el("character-create").addEventListener("click", buildCharacter);
  el("welcome-skip").addEventListener("click", () => complete(false));
  el("welcome-open").addEventListener("click", () => complete(true));
  return {onReady, onEvent, loadCharacter};
})();
