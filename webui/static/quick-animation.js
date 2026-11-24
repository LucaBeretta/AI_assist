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
  const mediaType = typeof canvas.captureStream === "function"
    ? ["video/mp4", "video/webm"].find(type => window.MediaRecorder?.isTypeSupported?.(type))
    : undefined;
  let renderer;
  let ready = false;
  let photoVersion = 0;
  let previewFrame = 0;
  let saving = false;

  function readDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error(t("Could not read the file.")));
      reader.readAsDataURL(file);
    });
  }

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
      image.src = await readDataUrl(photo);
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

  async function recordClip() {
    drawFrame(0);
    const stream = canvas.captureStream(30);
    let frame = 0;
    let recorder;
    try {
      recorder = new MediaRecorder(stream, {mimeType: mediaType, videoBitsPerSecond: 3_000_000});
      return await new Promise((resolve, reject) => {
        const parts = [];
        const started = performance.now();
        function tick(now) {
          try {
            const seconds = (now - started) / 1000;
            drawFrame(Math.min(seconds, window.photoShader.duration));
            if (seconds < window.photoShader.duration) {
              frame = requestAnimationFrame(tick);
            } else {
              recorder.stop();
            }
          } catch (error) {
            reject(error);
          }
        }
        recorder.ondataavailable = event => {
          if (event.data.size) parts.push(event.data);
        };
        recorder.onerror = event => reject(event.error || new Error(t("Could not record the clip.")));
        recorder.onstop = () => {
          const type = (recorder.mimeType || mediaType).split(";", 1)[0];
          const clip = new Blob(parts, {type});
          if (clip.size) resolve(clip);
          else reject(new Error(t("Could not record the clip.")));
        };
        recorder.start();
        frame = requestAnimationFrame(tick);
      });
    } finally {
      if (frame) cancelAnimationFrame(frame);
      if (recorder && recorder.state !== "inactive") recorder.stop();
      stream.getTracks().forEach(track => track.stop());
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
      const clip = await recordClip();
      status.textContent = t("Choose where to save the clip.");
      const encoded = (await readDataUrl(clip)).split(",", 2)[1];
      const result = await window.pywebview.api.save_quick_animation(encoded, clip.type);
      if (!result?.ok) throw new Error(result?.error || t("Could not record the clip."));
      status.textContent = result.cancelled ? "" : t("Clip saved.");
    } catch (error) {
      status.textContent = t(error.message);
    } finally {
      saving = false;
      updateControls();
    }
  }

  function selectMode(quick) {
    document.getElementById("generate-quick-mode").setAttribute("aria-pressed", String(quick));
    document.getElementById("generate-wan-mode").setAttribute("aria-pressed", String(!quick));
    document.getElementById("generate-quick-panel").hidden = !quick;
    document.getElementById("generate-wan-panel").hidden = quick;
    if (quick) document.getElementById("photo-video-preview").pause();
    else pause();
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
  document.getElementById("generate-quick-mode").addEventListener("click", () => selectMode(true));
  document.getElementById("generate-wan-mode").addEventListener("click", () => selectMode(false));
  window.quickAnimation = {pause};
})();
