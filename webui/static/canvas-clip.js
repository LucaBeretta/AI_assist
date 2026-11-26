"use strict";

window.canvasClip = (() => {
  function supportedType(canvas) {
    if (typeof canvas.captureStream !== "function") return undefined;
    return ["video/mp4", "video/webm"].find(type => window.MediaRecorder?.isTypeSupported?.(type));
  }

  function readDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read the file."));
      reader.readAsDataURL(file);
    });
  }

  // `audio`, when given, starts the sound to record with the picture and
  // returns {tracks, stop}, as Quick animation's 3D sound does.
  async function record(canvas, {mimeType, duration, draw, audio}) {
    draw(0);
    const stream = canvas.captureStream(30);
    let sound = null;
    let frame = 0;
    let recorder;
    try {
      sound = audio ? audio() : null;
      sound?.tracks.forEach(track => stream.addTrack(track));
      recorder = new window.MediaRecorder(stream, {mimeType, videoBitsPerSecond: 3_000_000,
                                                   ...(sound ? {audioBitsPerSecond: 192_000} : {})});
      return await new Promise((resolve, reject) => {
        const parts = [];
        recorder.ondataavailable = event => {
          if (event.data.size) parts.push(event.data);
        };
        recorder.onerror = event => reject(event.error || new Error("Could not record the clip."));
        recorder.onstop = () => {
          const type = (recorder.mimeType || mimeType).split(";", 1)[0];
          const clip = new Blob(parts, {type});
          if (clip.size) resolve(clip);
          else reject(new Error("Could not record the clip."));
        };
        recorder.start();
        const started = performance.now();
        function tick(now) {
          try {
            const seconds = (now - started) / 1000;
            draw(Math.min(seconds, duration));
            if (seconds < duration) frame = requestAnimationFrame(tick);
            else recorder.stop();
          } catch (error) {
            reject(error);
          }
        }
        frame = requestAnimationFrame(tick);
      });
    } finally {
      if (frame) cancelAnimationFrame(frame);
      try {
        if (recorder && recorder.state !== "inactive") recorder.stop();
      } finally {
        stream.getTracks().forEach(track => track.stop());
        sound?.stop();
      }
    }
  }

  return {supportedType, readDataUrl, record};
})();
