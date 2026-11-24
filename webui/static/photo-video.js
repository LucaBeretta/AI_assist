"use strict";

(() => {
  const el = id => document.getElementById(`photo-video-${id}`);
  const t = message => window.i18n.t(message);
  const preview = el("preview");
  let clipUrl = null;

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result?.ok) throw new Error(result?.error || t("Invalid API response"));
    return result;
  }

  function readPhoto(photo) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",", 2)[1]);
      reader.onerror = () => reject(new Error(t("Could not read the photo.")));
      reader.readAsDataURL(photo);
    });
  }

  async function waitForVideo(job) {
    const deadline = Date.now() + 30 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      const result = await call("photo_video_status", job);
      if (result.ready) return result;
    }
    throw new Error(t("Still waiting for ComfyUI. Check its queue and output folder."));
  }

  el("form").addEventListener("submit", async event => {
    event.preventDefault();
    const generate = el("generate");
    const status = el("status");
    const progress = el("progress");
    if (generate.disabled) return;
    const photo = el("image").files[0];
    const prompt = el("prompt").value.trim();
    if (!photo || photo.size > 10 * 1024 * 1024 || !prompt) {
      status.textContent = t("Choose a photo up to 10 MB and describe the motion.");
      return;
    }

    const portrait = el("orientation").value === "portrait";
    generate.disabled = true;
    progress.hidden = false;
    status.textContent = t("Sending photo to local ComfyUI…");
    try {
      if (!window.pywebview?.api?.start_photo_video) {
        throw new Error(t("The desktop connection is still starting."));
      }
      const {job} = await call("start_photo_video", await readPhoto(photo), prompt, portrait);
      status.textContent = t("Waiting for ComfyUI to render the video…");
      const result = await waitForVideo(job);
      const bytes = Uint8Array.from(atob(result.video), character => character.charCodeAt(0));
      const nextUrl = URL.createObjectURL(new Blob([bytes], {type: "video/mp4"}));
      preview.src = nextUrl;
      if (clipUrl) URL.revokeObjectURL(clipUrl);
      clipUrl = nextUrl;
      el("empty").hidden = true;
      preview.hidden = false;
      status.textContent = `${t("Saved in ComfyUI’s output/ai-assist folder.")} ${result.name}`;
    } catch (error) {
      status.textContent = t(error.message);
    } finally {
      progress.hidden = true;
      generate.disabled = false;
    }
  });

  preview.addEventListener("error", () => {
    el("status").textContent = t("The video could not play here. Open it from ComfyUI’s output/ai-assist folder.");
  });
})();
