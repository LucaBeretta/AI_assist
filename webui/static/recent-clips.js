"use strict";

(() => {
  const el = id => document.getElementById(`recent-clips-${id}`);
  const t = message => window.i18n.t(message);
  const list = el("list");
  const preview = el("preview");
  const status = el("status");
  const buttons = new Map();
  const sources = {quick: "Quick animation", wan: "AI video", camera: "3D Camera", infinite: "Infinite"};
  let selected = null;
  let clipUrl = null;
  let loadVersion = 0;
  let refreshVersion = 0;

  function visible() {
    return !document.getElementById("generate-view").hidden &&
      !document.getElementById("generate-recent-panel").hidden;
  }

  async function call(method, ...args) {
    const api = window.pywebview?.api;
    if (!api?.[method]) throw new Error(t("The desktop connection is still starting."));
    const result = await api[method](...args);
    if (!result?.ok) throw new Error(result?.error || t("Invalid API response"));
    return result;
  }

  function pause() {
    if (clipUrl) preview.pause();
  }

  function clearPreview() {
    if (clipUrl) {
      pause();
      preview.removeAttribute("src");
      URL.revokeObjectURL(clipUrl);
      clipUrl = null;
    }
    preview.hidden = true;
    el("preview-empty").hidden = false;
    status.textContent = "";
  }

  function updateSelection() {
    for (const [id, button] of buttons) {
      button.setAttribute("aria-pressed", String(id === selected?.id));
    }
    el("actions").hidden = !selected;
    el("reuse").hidden = !selected?.prompt;
  }

  async function selectClip(clip) {
    const version = ++loadVersion;
    clearPreview();
    selected = clip;
    updateSelection();
    el("preview-empty").hidden = true;
    status.textContent = t("Loading clip…");
    try {
      const result = await call("load_recent_clip", clip.id);
      if (version !== loadVersion) return;
      const bytes = Uint8Array.from(atob(result.video), character => character.charCodeAt(0));
      clipUrl = URL.createObjectURL(new Blob([bytes], {type: result.mime_type}));
      preview.src = clipUrl;
      preview.hidden = false;
      el("preview-empty").hidden = true;
      status.textContent = "";
      if (visible()) preview.play().catch(() => {});
    } catch (error) {
      if (version === loadVersion) {
        el("preview-empty").hidden = false;
        status.textContent = t(error.message);
      }
    }
  }

  function clipRow(clip) {
    const entry = document.createElement("li");
    const button = document.createElement("button");
    button.className = "recent-clip";
    button.type = "button";
    button.disabled = !clip.available;
    button.addEventListener("click", () => selectClip(clip));

    const title = document.createElement("span");
    title.className = "recent-clip-title";
    title.textContent = clip.prompt || clip.name;
    const detail = document.createElement("span");
    detail.className = "recent-clip-detail";
    const source = t(sources[clip.kind]);
    const date = new Date(clip.created_at).toLocaleString(document.documentElement.lang || undefined,
      {dateStyle: "medium", timeStyle: "short"});
    detail.textContent = [source, date, ...(clip.available ? [] : [t("File missing")])].join(" · ");
    button.append(title, detail);
    buttons.set(clip.id, button);
    entry.append(button);
    return entry;
  }

  async function refreshIfVisible() {
    if (!visible()) return;
    const version = ++refreshVersion;
    try {
      const {clips} = await call("list_recent_clips");
      if (version !== refreshVersion || !visible()) return;
      if (selected && !clips.some(clip => clip.id === selected.id &&
          clip.available && clip.created_at === selected.created_at)) {
        loadVersion++;
        selected = null;
        clearPreview();
      }
      buttons.clear();
      list.replaceChildren(...clips.map(clipRow));
      el("empty").hidden = clips.length > 0;
      updateSelection();
    } catch (error) {
      if (version === refreshVersion && visible()) status.textContent = t(error.message);
    }
  }

  el("open").addEventListener("click", async () => {
    if (!selected) return;
    try {
      await call("open_recent_clip", selected.id);
    } catch (error) {
      status.textContent = t(error.message);
    }
  });
  el("reuse").addEventListener("click", () => {
    if (!selected?.prompt) return;
    document.getElementById("generate-wan-mode").click();
    const prompt = document.getElementById("photo-video-prompt");
    prompt.value = selected.prompt;
    document.getElementById("photo-video-orientation").value = selected.portrait ? "portrait" : "landscape";
    prompt.focus();
  });
  preview.addEventListener("error", () => {
    if (clipUrl) status.textContent = t("This clip could not play here.");
  });
  window.recentClips = {pause, refreshIfVisible};
})();
