/* 3D sound for Quick animation: a sound moves around the listener, rendered for headphones (HRTF) on this computer. */
"use strict";

window.spatialSound = (() => {
  const steps = 120;  // position updates per loop
  const maxBytes = 20 * 1024 * 1024;
  const types = ["audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/webm", "audio/flac"];

  // Listener at the origin, facing -z, +x to the right, +y up. Each path returns to its start, so the loop is seamless.
  const paths = {
    around: phase => ({x: 2 * Math.sin(phase), y: 0, z: -2 * Math.cos(phase)}),
    sideways: phase => ({x: 3 * Math.sin(phase), y: 0, z: -1}),
    overhead: phase => ({x: 0, y: 2 * Math.sin(phase), z: -2 * Math.cos(phase)}),
  };

  function position(path, seconds, duration) {
    const move = paths[path];
    if (!move) throw new Error("Unknown sound movement.");
    return move((seconds % duration) / duration * 2 * Math.PI);
  }

  function checkFile(file) {
    if (!file) return null;
    if (file.size > maxBytes || (file.type && !types.includes(file.type))) {
      throw new Error("Choose an audio file (MP3, WAV, OGG, M4A or FLAC) up to 20 MB.");
    }
    return file;
  }

  async function decode(file) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error("This window cannot play 3D sound.");
    const context = new Context();
    try {
      return await context.decodeAudioData(await file.arrayBuffer());
    } catch {
      throw new Error("Could not read this sound file.");
    } finally {
      context.close?.();
    }
  }

  // Plays `buffer` looping along `path`; to the speakers for a preview, or into a recordable track.
  function start(buffer, path, duration, {record = false} = {}) {
    const Context = window.AudioContext || window.webkitAudioContext;
    const context = new Context();
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    const panner = context.createPanner();
    panner.panningModel = "HRTF";
    panner.distanceModel = "inverse";
    panner.refDistance = 1;
    panner.rolloffFactor = 1;
    source.connect(panner);
    const output = record ? context.createMediaStreamDestination() : context.destination;
    panner.connect(output);
    const begin = context.currentTime + 0.05;
    // Schedule every loop of the path ahead of time, so the movement never depends on frame timing.
    const loops = record ? 1 : 200;
    for (let index = 0; index <= steps * loops; index++) {
      const seconds = index / steps * duration;
      const at = position(path, seconds, duration);
      for (const axis of ["x", "y", "z"]) {
        const param = panner["position" + axis.toUpperCase()];
        if (param) {
          if (index === 0) param.setValueAtTime(at[axis], begin);
          else param.linearRampToValueAtTime(at[axis], begin + seconds);
        }
      }
    }
    if (!panner.positionX) panner.setPosition?.(...Object.values(position(path, 0, duration)));
    source.start(begin);
    return {
      tracks: record ? output.stream.getAudioTracks() : [],
      stop() {
        try { source.stop(); } catch { /* already stopped */ }
        context.close?.();
      },
    };
  }

  return {paths: Object.keys(paths), position, checkFile, decode, start};
})();
