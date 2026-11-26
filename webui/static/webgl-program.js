"use strict";

/* Compiles and links one WebGL program, for Quick animation (photo-shader.js) and 3D Camera
   (camera-scene.js). Each passes its own messages for a failed step: {shader, compile, program, link}. */
window.webglProgram = (() => {
  function compile(gl, type, source, errors) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error(errors.shader);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      throw new Error(errors.compile);
    }
    return shader;
  }

  function create(gl, vertexSource, fragmentSource, errors) {
    const program = gl.createProgram();
    if (!program) throw new Error(errors.program);
    let vertex;
    let fragment;
    try {
      vertex = compile(gl, gl.VERTEX_SHADER, vertexSource, errors);
      fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource, errors);
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(errors.link);
      return program;
    } catch (error) {
      gl.deleteProgram(program);
      throw error;
    } finally {
      if (vertex) gl.deleteShader(vertex);
      if (fragment) gl.deleteShader(fragment);
    }
  }

  return {create};
})();
