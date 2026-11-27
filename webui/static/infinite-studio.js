"use strict";

// Generate → Infinite: describe a clip, a local model writes an Infinite patch, the Infinite app renders it.
window.infiniteui = (() => {
  const el = id => document.getElementById(`infinite-${id}`);
  const t = (message, values) => window.i18n.t(message, values);
  const panel = document.getElementById("generate-infinite-panel");
  const modeButton = document.getElementById("generate-infinite-mode");
  const preview = el("preview");
  let installed = null;
  let job = null;
  let clip = null;
  let clipUrl = null;
  let saving = false;

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result?.ok) throw new Error(result?.error || t("Invalid API response"));
    return result;
  }

  function stageText(event) {
    switch (event.stage) {
      case "catalogue": return t("Reading Infinite’s list of nodes. The first time takes about 20 seconds…");
      case "writing": return t("The model is writing the patch…");
      case "fixing": return t("Infinite found a problem. The model is fixing the patch ({v0} of {v1})…", {v0: event.attempt, v1: event.rounds});
      case "checking": return t("Infinite is checking the patch…");
      case "rendering": return t("Infinite is making the {v0}-second clip…", {v0: event.seconds});
      default: return "";
    }
  }

  // The chat's own Ollama models; Colibri and paired computers are not used here.
  function fillModels() {
    const select = el("model");
    const previous = select.value;
    const chat = window.app?.modelChoice?.();
    // Placeholders such as "No running model available" are disabled options; other engines' values start with "[".
    const names = [...(document.getElementById("model")?.options || [])]
      .filter(option => !option.disabled).map(option => option.value).filter(value => value && !value.startsWith("["));
    select.replaceChildren(...names.map(name => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    }));
    const wanted = names.includes(previous) ? previous : chat?.backend === "Ollama" && names.includes(chat.name) ? chat.name : names[0];
    if (wanted) select.value = wanted;
  }

  function updateControls() {
    const busy = job !== null;
    el("generate").disabled = busy || installed === false || !el("model").value;
    el("stop").hidden = !busy;
    el("progress").hidden = !busy;
    el("prompt").disabled = busy;
    el("model").disabled = busy;
    el("length").disabled = busy;
    el("save").disabled = saving || !clip;
    el("save-patch").disabled = saving || !clip;
  }

  async function refresh() {
    fillModels();
    try {
      if (!window.pywebview?.api?.infinite_state) throw new Error(t("The desktop connection is still starting."));
      const state = await call("infinite_state");
      installed = state.installed;
      el("missing").hidden = installed;
      if (!job && !el("model").value) el("status").textContent = t("Download a model in Models first.");
    } catch (error) {
      el("status").textContent = t(error.message);
    }
    updateControls();
  }

  function pause() {
    preview.pause?.();
  }

  function show() {
    for (const button of document.querySelectorAll(".generate-modes button")) {
      button.setAttribute("aria-pressed", String(button === modeButton));
    }
    for (const page of document.querySelectorAll(".generate-page")) page.hidden = page !== panel;
    window.quickAnimation?.pause?.();
    document.getElementById("photo-video-preview")?.pause?.();
    window.cameraStudio?.pause?.();
    window.recentClips?.pause?.();
    void refresh();
  }

  function hide() {
    modeButton.setAttribute("aria-pressed", "false");
    panel.hidden = true;
    pause();
  }

  async function showClip(result) {
    clip = result.clip;
    const {video} = await call("infinite_clip", clip);
    const bytes = Uint8Array.from(atob(video), character => character.charCodeAt(0));
    const nextUrl = URL.createObjectURL(new Blob([bytes], {type: "video/mp4"}));
    preview.src = nextUrl;
    if (clipUrl) URL.revokeObjectURL(clipUrl);
    clipUrl = nextUrl;
    el("empty").hidden = true;
    preview.hidden = false;
    el("actions").hidden = false;
    el("patch").textContent = result.patch;
    el("patch-details").hidden = false;
    el("credit").textContent = t("Made with {v0}", {v0: result.credit});
    el("credit").hidden = false;
    const notes = [t("Your clip is ready.")];
    if (!result.sound) notes.push(t("It has no sound."));
    if (result.warnings?.length) notes.push(t("Infinite says: {v0}", {v0: result.warnings[0]}));
    el("status").textContent = notes.join(" ");
  }

  function onEvent(event) {
    if (event.operation !== "infinite" || !["job_progress", "job_done", "job_error", "job_stopped"].includes(event.type)) return false;
    if (job && job !== true && event.job_id !== job) return true;
    if (event.type === "job_progress") {
      const text = stageText(event);
      if (text) el("status").textContent = text;
      return true;
    }
    job = null;
    updateControls();
    if (event.type === "job_done") {
      showClip(event.result).catch(error => { el("status").textContent = t(error.message); }).finally(updateControls);
    } else {
      el("status").textContent = event.type === "job_stopped" ? t("Operation stopped.")
        : window.i18n?.gate?.(event.error) || t(event.error || "Operation stopped.");
    }
    return true;
  }

  el("form").addEventListener("submit", async event => {
    event.preventDefault();
    if (job) return;
    const description = el("prompt").value.trim();
    if (!description) {
      el("status").textContent = t("Describe the clip first.");
      return;
    }
    job = true;  // the real job ID arrives with the reply
    updateControls();
    el("status").textContent = t("Starting…");
    try {
      if (!window.pywebview?.api?.make_infinite_clip) throw new Error(t("The desktop connection is still starting."));
      const started = await call("make_infinite_clip", description, el("model").value, Number(el("length").value));
      if (job === true) job = started.job_id;
    } catch (error) {
      job = null;
      el("status").textContent = t(error.message);
    }
    updateControls();
  });

  el("stop").addEventListener("click", () => {
    if (typeof job === "string") call("cancel_job", job).catch(error => { el("status").textContent = t(error.message); });
  });

  async function save(method, done) {
    if (!clip || saving) return;
    saving = true;
    updateControls();
    try {
      const result = await call(method, clip);
      el("status").textContent = result.cancelled ? "" : t(done);
    } catch (error) {
      el("status").textContent = t(error.message);
    } finally {
      saving = false;
      updateControls();
    }
  }
  el("save").addEventListener("click", () => save("save_infinite_clip", "Clip saved."));
  el("save-patch").addEventListener("click", () => save("save_infinite_patch", "Patch saved. Open it in Infinite to change it."));

  preview.addEventListener("error", () => {
    if (clip) el("status").textContent = t("The clip could not play here. Save it and open it in another app.");
  });

  modeButton.addEventListener("click", show);
  for (const button of document.querySelectorAll(".generate-modes button")) {
    if (button !== modeButton) button.addEventListener("click", hide);
  }
  // Leaving Generate pauses the clip, as it does for the other Generate previews.
  const view = document.getElementById("generate-view");
  if (view && typeof MutationObserver === "function") {
    new MutationObserver(() => { if (view.hidden) pause(); }).observe(view, {attributes: true, attributeFilter: ["hidden"]});
  }
  updateControls();
  return {onEvent, refresh, pause};
})();
