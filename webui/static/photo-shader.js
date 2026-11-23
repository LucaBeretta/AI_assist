"use strict";

window.photoShader = (() => {
  const duration = 3;
  const effects = new Map([["zoom", 0], ["pan", 1], ["ripple", 2]]);

  const vertexSource = `#version 300 es
out vec2 photoPosition;

void main() {
  // One triangle covers the viewport without any vertex buffers.
  vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  photoPosition = corner;
  gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
}`;

  const fragmentSource = `#version 300 es
precision highp float;

uniform sampler2D photo;
uniform vec2 cover;
uniform float imageAspect;
uniform float phase;
uniform int effect;
in vec2 photoPosition;
out vec4 color;

void main() {
  vec2 uv = (photoPosition - 0.5) * cover;

  if (effect == 0) {
    float zoom = 1.0 + 0.20 * (0.5 - 0.5 * cos(phase));
    uv /= zoom;
  } else if (effect == 1) {
    // Leave enough image beyond both sides for the camera to pan.
    uv *= 0.8;
    uv.x += 0.1 * cover.x * sin(phase);
  } else {
    float radius = length(uv * vec2(imageAspect, 1.0));
    uv *= 0.96 + 0.025 * sin(radius * 38.0 - phase);
  }

  vec4 pixel = texture(photo, uv + 0.5);
  color = vec4(mix(vec3(0.06), pixel.rgb, pixel.a), 1.0);
}`;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("Could not create a WebGL shader.");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      throw new Error("Could not compile the photo animation shader.");
    }
    return shader;
  }

  function makeProgram(gl) {
    const program = gl.createProgram();
    if (!program) throw new Error("Could not create a WebGL program.");
    let vertex;
    let fragment;
    try {
      vertex = compile(gl, gl.VERTEX_SHADER, vertexSource);
      fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error("Could not link the photo animation shader.");
      }
      return program;
    } catch (error) {
      gl.deleteProgram(program);
      throw error;
    } finally {
      if (vertex) gl.deleteShader(vertex);
      if (fragment) gl.deleteShader(fragment);
    }
  }

  function create(canvas) {
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      depth: false,
      preserveDrawingBuffer: true,
    });
    if (!gl) throw new Error("WebGL 2 is required for Quick animation.");

    const program = makeProgram(gl);
    const texture = gl.createTexture();
    if (!texture) {
      gl.deleteProgram(program);
      throw new Error("Could not create a WebGL texture for the photo.");
    }
    const maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    const uniforms = {
      cover: gl.getUniformLocation(program, "cover"),
      imageAspect: gl.getUniformLocation(program, "imageAspect"),
      phase: gl.getUniformLocation(program, "phase"),
      effect: gl.getUniformLocation(program, "effect"),
    };
    let imageAspect = 0;
    let disposed = false;

    gl.useProgram(program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1i(gl.getUniformLocation(program, "photo"), 0);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);

    function requireContext() {
      if (disposed || gl.isContextLost()) {
        throw new Error("The graphics context was lost. Restart AI Assist and try again.");
      }
    }

    function setImage(image) {
      requireContext();
      const width = image.naturalWidth ?? image.width;
      const height = image.naturalHeight ?? image.height;
      if (!(width > 0 && height > 0)) throw new Error("Could not read the photo dimensions.");
      if (width > maxTextureSize || height > maxTextureSize) {
        throw new Error("This photo is too large for WebGL. Choose a smaller photo.");
      }
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      if (gl.getError() !== gl.NO_ERROR) {
        requireContext();
        throw new Error("Could not load this photo onto the graphics device.");
      }
      imageAspect = width / height;
    }

    function draw(effect, seconds) {
      requireContext();
      if (!imageAspect || !gl.drawingBufferWidth || !gl.drawingBufferHeight) return;
      const canvasAspect = gl.drawingBufferWidth / gl.drawingBufferHeight;
      gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
      gl.uniform2f(uniforms.cover, Math.min(1, canvasAspect / imageAspect), Math.min(1, imageAspect / canvasAspect));
      gl.uniform1f(uniforms.imageAspect, imageAspect);
      gl.uniform1f(uniforms.phase, (seconds % duration) * (2 * Math.PI / duration));
      gl.uniform1i(uniforms.effect, effects.get(effect) ?? 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      gl.deleteTexture(texture);
      gl.deleteProgram(program);
    }

    return {setImage, draw, dispose};
  }

  return {create, duration};
})();
