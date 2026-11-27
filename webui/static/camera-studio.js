"use strict";

(() => {
  const el = name => document.getElementById(`camera-studio-${name}`);
  const t = message => window.i18n.t(message);
  const canvas = el("canvas");
  const lens = el("lens");
  const orbit = el("orbit");
  const play = el("play");
  const record = el("record");
  const status = el("status");
  const empty = el("empty");
  const mediaType = window.canvasClip.supportedType(canvas);
  let renderer;
  let ready = false;
  let recording = false;
  let elapsed = 0;
  let previewFrame = 0;

  function updateControls() {
    lens.disabled = orbit.disabled = !ready;
    play.disabled = !ready || recording;
    record.disabled = !ready || recording || !mediaType;
  }

  function setPlayLabel(label) {
    play.setAttribute("data-i18n", label);
    play.textContent = t(label);
  }

  function pause() {
    if (!previewFrame) return;
    cancelAnimationFrame(previewFrame);
    previewFrame = 0;
    setPlayLabel("Play scene");
  }

  function draw(seconds) {
    renderer.draw({seconds, focalLength: Number(lens.value), orbit: Number(orbit.value)});
  }

  function sceneFailed(error) {
    pause();
    ready = false;
    status.textContent = t(error.message || "The scene could not be displayed.");
    updateControls();
  }

  function showIfVisible() {
    if (renderer || document.getElementById("generate-view").hidden ||
        document.getElementById("generate-camera-panel").hidden) return;
    try {
      renderer = window.cameraScene.create(canvas);
      draw(0);
      ready = true;
      canvas.hidden = false;
      empty.hidden = true;
      if (!mediaType) status.textContent = t("Saving clips requires a desktop webview with canvas recording.");
      updateControls();
    } catch (error) {
      empty.hidden = true;
      sceneFailed(error);
    }
  }

  function playScene() {
    const started = performance.now() - elapsed * 1000;
    function tick(now) {
      try {
        elapsed = ((now - started) / 1000) % window.cameraScene.duration;
        draw(elapsed);
        previewFrame = requestAnimationFrame(tick);
      } catch (error) {
        sceneFailed(error);
      }
    }
    setPlayLabel("Pause scene");
    previewFrame = requestAnimationFrame(tick);
  }

  function redrawControls() {
    el("angle").textContent = `${orbit.value}°`;
    if (ready && !recording && !previewFrame) {
      try {
        draw(elapsed);
      } catch (error) {
        sceneFailed(error);
      }
    }
  }

  async function saveScene() {
    if (!ready || recording || !mediaType) return;
    const api = window.pywebview?.api;
    if (!api?.save_camera_clip) {
      status.textContent = t("The desktop connection is still starting.");
      return;
    }
    pause();
    recording = true;
    updateControls();
    status.textContent = t("Recording the 4-second clip…");
    try {
      const duration = window.cameraScene.duration;
      const clip = await window.canvasClip.record(canvas, {
        mimeType: mediaType,
        duration,
        draw(seconds) {
          elapsed = seconds % duration;
          draw(elapsed);
        },
      });
      status.textContent = t("Choose where to save the clip.");
      const encoded = (await window.canvasClip.readDataUrl(clip)).split(",", 2)[1];
      const result = await api.save_camera_clip(encoded, clip.type);
      if (!result?.ok) throw new Error(result?.error || t("Could not record the clip."));
      status.textContent = result.cancelled ? "" : t("Clip saved.");
      if (!result.cancelled) {
        if (result.history_error) status.textContent += ` ${t("Recent clips could not be updated.")}`;
        window.recentClips?.refreshIfVisible();
      }
    } catch (error) {
      status.textContent = t(error.message);
    } finally {
      recording = false;
      updateControls();
    }
  }

  lens.addEventListener("change", redrawControls);
  orbit.addEventListener("input", redrawControls);
  play.addEventListener("click", () => {
    if (!ready || recording) return;
    if (previewFrame) pause();
    else playScene();
  });
  record.addEventListener("click", saveScene);
  window.cameraStudio = {showIfVisible, pause};
})();
