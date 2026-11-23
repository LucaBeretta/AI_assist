"use strict";

(() => {
  const el = name => document.getElementById(`quick-animation-${name}`);
  const t = message => window.i18n.t(message);
  const canvas = el("canvas");
  const effect = el("effect");
  const input = el("image");
  const play = el("play");
  const save = el("save");
  const status = el("status");
  const empty = el("empty");
  const mediaType = window.canvasClip.supportedType(canvas);
  let renderer;
  let ready = false;
  let photoVersion = 0;
  let previewFrame = 0;
  let saving = false;

  function setPlayLabel(label) {
    play.setAttribute("data-i18n", label);
    play.textContent = t(label);
  }

  function pause() {
    if (!previewFrame) return;
    cancelAnimationFrame(previewFrame);
    previewFrame = 0;
    setPlayLabel("Play preview");
  }

  function updateControls() {
    input.disabled = effect.disabled = saving;
    play.disabled = !ready || saving;
    save.disabled = !ready || saving || !mediaType;
  }

  function drawFrame(seconds) {
    renderer.draw(effect.value, seconds);
  }

  function startPreview() {
    const started = performance.now();
    function tick(now) {
      try {
        drawFrame((now - started) / 1000);
        previewFrame = requestAnimationFrame(tick);
      } catch (error) {
        pause();
        status.textContent = t(error.message);
      }
    }
    setPlayLabel("Pause preview");
    previewFrame = requestAnimationFrame(tick);
  }

  async function loadPhoto() {
    const version = ++photoVersion;
    const photo = input.files[0];
    pause();
    ready = false;
    canvas.hidden = true;
    empty.hidden = false;
    status.textContent = "";
    updateControls();
    if (!photo) return;
    if (photo.size > 10 * 1024 * 1024 || !["image/jpeg", "image/png"].includes(photo.type)) {
      status.textContent = t("Choose a JPEG or PNG up to 10 MB.");
      return;
    }
    try {
      const image = new Image();
      image.src = await window.canvasClip.readDataUrl(photo);
      await image.decode();
      if (version !== photoVersion) return;
      const scale = Math.min(1, 720 / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.max(2, 2 * Math.round(image.naturalWidth * scale / 2));
      canvas.height = Math.max(2, 2 * Math.round(image.naturalHeight * scale / 2));
      const source = document.createElement("canvas");
      source.width = canvas.width;
      source.height = canvas.height;
      source.getContext("2d").drawImage(image, 0, 0, source.width, source.height);
      renderer ??= window.photoShader.create(canvas);
      renderer.setImage(source);
      drawFrame(0);
      ready = true;
      canvas.hidden = false;
      empty.hidden = true;
      if (!mediaType) {
        status.textContent = t("Saving clips requires a desktop webview with canvas recording.");
      }
    } catch (error) {
      if (version === photoVersion) status.textContent = t(error.message || "Could not read the photo.");
    } finally {
      if (version === photoVersion) updateControls();
    }
  }

  async function saveClip() {
    if (!ready || saving || !mediaType) return;
    if (!window.pywebview?.api?.save_quick_animation) {
      status.textContent = t("The desktop connection is still starting.");
      return;
    }
    pause();
    saving = true;
    updateControls();
    status.textContent = t("Recording the 3-second clip…");
    try {
      const clip = await window.canvasClip.record(canvas, {
        mimeType: mediaType, duration: window.photoShader.duration, draw: drawFrame,
      });
      status.textContent = t("Choose where to save the clip.");
      const encoded = (await window.canvasClip.readDataUrl(clip)).split(",", 2)[1];
      const result = await window.pywebview.api.save_quick_animation(encoded, clip.type);
      if (!result?.ok) throw new Error(result?.error || t("Could not record the clip."));
      status.textContent = result.cancelled ? "" : t("Clip saved.");
      if (!result.cancelled) {
        if (result.history_error) status.textContent += ` ${t("Recent clips could not be updated.")}`;
        window.recentClips?.refreshIfVisible();
      }
    } catch (error) {
      status.textContent = t(error.message);
    } finally {
      saving = false;
      updateControls();
    }
  }

  input.addEventListener("change", loadPhoto);
  effect.addEventListener("change", () => {
    if (ready && !previewFrame) {
      try { drawFrame(0); }
      catch (error) { status.textContent = t(error.message); }
    }
  });
  play.addEventListener("click", () => {
    if (!ready || saving) return;
    if (previewFrame) pause();
    else startPreview();
  });
  save.addEventListener("click", saveClip);
  window.quickAnimation = {pause};
})();
