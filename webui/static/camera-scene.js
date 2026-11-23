"use strict";

window.cameraScene = (() => {
  const duration = 4;
  const fullTurn = Math.PI * 2;
  const sensorWidth = 36;
  const background = [0.055, 0.077, 0.096];

  const vertexSource = `#version 300 es
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 normal;

uniform mat4 view;
uniform mat4 projection;
uniform vec3 translation;
uniform vec3 scale;
uniform vec2 heading;

out vec3 worldPosition;
out vec3 worldNormal;

vec3 rotateY(vec3 point) {
  return vec3(
    heading.x * point.x + heading.y * point.z,
    point.y,
    heading.x * point.z - heading.y * point.x
  );
}

void main() {
  worldPosition = rotateY(position * scale) + translation;
  worldNormal = rotateY(normal / scale);
  gl_Position = projection * view * vec4(worldPosition, 1.0);
}`;

  const fragmentSource = `#version 300 es
precision highp float;

uniform vec3 baseColor;
uniform vec3 camera;
uniform vec3 subject;
uniform vec3 background;
uniform int material;

in vec3 worldPosition;
in vec3 worldNormal;
out vec4 color;

float line(float distanceToLine) {
  return 1.0 - smoothstep(0.0, max(fwidth(distanceToLine), 0.001), distanceToLine);
}

void main() {
  vec3 normal = normalize(worldNormal);
  vec3 eye = normalize(camera - worldPosition);
  vec3 key = normalize(vec3(-0.55, 0.82, 0.45));
  vec3 rim = normalize(vec3(0.7, 0.4, -0.8));
  float diffuse = max(dot(normal, key), 0.0);
  float edgeLight = max(dot(normal, rim), 0.0);
  float specular = pow(max(dot(normal, normalize(key + eye)), 0.0), 32.0);
  vec3 result = baseColor * (0.40 + 0.65 * diffuse) +
                vec3(0.04, 0.12, 0.18) * edgeLight;
  float radius = length(worldPosition.xz);

  if (material == 1) {
    // Ground marks make perspective changes legible throughout the orbit.
    vec2 cells = abs(fract(worldPosition.xz + 0.5) - 0.5);
    float grid = max(line(cells.x), line(cells.y)) * (1.0 - smoothstep(4.0, 13.0, radius));
    result += vec3(0.10, 0.15, 0.17) * grid * 0.45;
    float foot = 1.0 - smoothstep(2.28, 3.12, length(worldPosition.xz - vec2(0.15, -0.12)));
    result *= 1.0 - 0.64 * foot;
  } else if (material == 2 && normal.y > 0.5) {
    float ring = max(line(abs(radius - 2.02)), line(abs(radius - 1.20)) * 0.55);
    float angle = atan(worldPosition.z, worldPosition.x);
    float ticks = (1.0 - smoothstep(0.08, 0.16, abs(fract(angle * 7.6394) - 0.5))) *
                  step(2.09, radius) * (1.0 - step(2.19, radius));
    result += vec3(0.13, 0.27, 0.31) * (ring + ticks * 0.65);
    vec2 shadowPoint = worldPosition.xz - subject.xz - vec2(0.12, -0.08);
    float shadow = exp(-4.5 * dot(shadowPoint, shadowPoint));
    result *= 1.0 - 0.53 * shadow;
  } else if (material == 3) {
    float sheen = pow(1.0 - max(dot(eye, normal), 0.0), 3.0);
    result += vec3(0.48, 0.30, 0.14) * specular + vec3(0.09, 0.13, 0.16) * sheen;
  }

  result = mix(result, background, smoothstep(8.0, 29.0, length(camera - worldPosition)));
  color = vec4(result, 1.0);
}`;

  function subtract(a, b) {
    return a.map((value, index) => value - b[index]);
  }

  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }

  function unit(vector) {
    const length = Math.hypot(...vector);
    return vector.map(value => value / length);
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function viewMatrix(eye, target) {
    const back = unit(subtract(eye, target));
    const right = unit(cross([0, 1, 0], back));
    const up = cross(back, right);
    return new Float32Array([
      right[0], up[0], back[0], 0,
      right[1], up[1], back[1], 0,
      right[2], up[2], back[2], 0,
      -dot(right, eye), -dot(up, eye), -dot(back, eye), 1,
    ]);
  }

  function projectionMatrix(focalLength, aspect) {
    const horizontalScale = 2 * focalLength / sensorWidth;
    const near = 0.1;
    const far = 64;
    return new Float32Array([
      horizontalScale, 0, 0, 0,
      0, horizontalScale * aspect, 0, 0,
      0, 0, (far + near) / (near - far), -1,
      0, 0, 2 * far * near / (near - far), 0,
    ]);
  }

  function triangle(vertices, a, b, c) {
    const normal = unit(cross(subtract(b, a), subtract(c, a)));
    vertices.push(...a, ...normal, ...b, ...normal, ...c, ...normal);
  }

  function quad(vertices, a, b, c, d) {
    triangle(vertices, a, b, c);
    triangle(vertices, a, c, d);
  }

  function box() {
    const vertices = [];
    const a = [-0.5, -0.5, 0.5], b = [0.5, -0.5, 0.5];
    const c = [0.5, 0.5, 0.5], d = [-0.5, 0.5, 0.5];
    const e = [-0.5, -0.5, -0.5], f = [0.5, -0.5, -0.5];
    const g = [0.5, 0.5, -0.5], h = [-0.5, 0.5, -0.5];
    quad(vertices, a, b, c, d);
    quad(vertices, f, e, h, g);
    quad(vertices, b, f, g, c);
    quad(vertices, e, a, d, h);
    quad(vertices, d, c, g, h);
    quad(vertices, e, f, b, a);
    return vertices;
  }

  function lathe(sides, profile) {
    const vertices = [];
    const rings = profile.map(([y, radius]) => Array.from({length: sides}, (_, index) => {
      const angle = fullTurn * index / sides;
      return [Math.cos(angle) * radius, y, Math.sin(angle) * radius];
    }));
    const base = rings[0];
    const top = rings.at(-1);
    for (let index = 0; index < sides; index++) {
      const next = (index + 1) % sides;
      triangle(vertices, [0, base[0][1], 0], base[index], base[next]);
      triangle(vertices, [0, top[0][1], 0], top[next], top[index]);
      for (let ring = 0; ring < rings.length - 1; ring++) {
        quad(vertices, rings[ring][index], rings[ring + 1][index],
          rings[ring + 1][next], rings[ring][next]);
      }
    }
    return vertices;
  }

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("Could not create a 3D camera shader.");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      throw new Error("Could not compile a 3D camera shader.");
    }
    return shader;
  }

  function makeProgram(gl) {
    const program = gl.createProgram();
    if (!program) throw new Error("Could not create the 3D camera renderer.");
    let vertex;
    let fragment;
    try {
      vertex = compile(gl, gl.VERTEX_SHADER, vertexSource);
      fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error("Could not link the 3D camera shaders.");
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
    const gl = canvas.getContext("webgl2", {alpha: false, depth: true, preserveDrawingBuffer: true});
    if (!gl) throw new Error("WebGL 2 is required for 3D Camera.");

    const program = makeProgram(gl);
    const meshes = [];
    let disposed = false;

    function dispose() {
      if (disposed) return;
      disposed = true;
      for (const {vao, buffer} of meshes) {
        gl.deleteVertexArray(vao);
        gl.deleteBuffer(buffer);
      }
      gl.deleteProgram(program);
    }

    function upload(vertices) {
      const vao = gl.createVertexArray();
      const buffer = gl.createBuffer();
      if (!vao || !buffer) {
        if (vao) gl.deleteVertexArray(vao);
        if (buffer) gl.deleteBuffer(buffer);
        throw new Error("Could not allocate the 3D camera scene.");
      }
      const mesh = {vao, buffer, count: vertices.length / 6};
      meshes.push(mesh);
      gl.bindVertexArray(vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);
      return mesh;
    }

    let block;
    let stage;
    let stone;
    try {
      block = upload(box());
      stage = upload(lathe(96, [[-0.5, 0.96], [-0.3, 1], [0.3, 1], [0.5, 0.96]]));
      stone = upload(lathe(8, [[-0.65, 0.18], [-0.41, 0.48], [0.22, 0.59], [0.59, 0.31], [0.68, 0.17]]));
    } catch (error) {
      dispose();
      throw error;
    }

    const locations = Object.fromEntries([
      "view", "projection", "translation", "scale", "heading", "baseColor",
      "camera", "subject", "background", "material",
    ].map(name => [name, gl.getUniformLocation(program, name)]));

    const props = [
      {position: [-2.85, 0.45, -1.85], size: [0.50, 1.1, 0.50], color: [0.17, 0.33, 0.35], angle: 0.35},
      {position: [-3.42, 0.19, -1.30], size: [0.58, 0.58, 0.58], color: [0.28, 0.47, 0.47], angle: 0.35},
      {position: [2.85, 0.27, -1.70], size: [0.72, 0.74, 0.58], color: [0.30, 0.29, 0.30], angle: -0.28},
      {position: [3.42, 0.03, 0.65], size: [0.40, 0.26, 0.75], color: [0.42, 0.30, 0.22], angle: -0.55},
      {position: [-2.84, 0.01, 1.77], size: [0.50, 0.22, 0.50], color: [0.29, 0.36, 0.40], angle: 0.55},
    ];

    function drawMesh(mesh, position, scale, color, material = 0, angle = 0) {
      gl.uniform3fv(locations.translation, position);
      gl.uniform3fv(locations.scale, scale);
      gl.uniform2f(locations.heading, Math.cos(angle), Math.sin(angle));
      gl.uniform3fv(locations.baseColor, color);
      gl.uniform1i(locations.material, material);
      gl.bindVertexArray(mesh.vao);
      gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
    }

    function draw({seconds = 0, focalLength = 24, orbit = 0} = {}) {
      if (disposed || gl.isContextLost()) throw new Error("The graphics context was lost. Restart AI Assist and try again.");
      const width = gl.drawingBufferWidth;
      const height = gl.drawingBufferHeight;
      if (!width || !height) return;
      const phase = fullTurn * ((Number.isFinite(seconds) ? seconds : 0) % duration) / duration;
      const azimuth = (Number.isFinite(orbit) ? orbit : 0) * Math.PI / 180;
      const camera = [6.3 * Math.sin(azimuth), 3.1, 6.3 * Math.cos(azimuth)];
      const subject = [Math.sin(phase) * 0.94, 1.05 + 0.13 * Math.cos(phase), Math.cos(phase) * 0.51];
      const lens = focalLength === 50 ? 50 : 24;

      gl.viewport(0, 0, width, height);
      gl.clearColor(...background, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.useProgram(program);
      gl.uniformMatrix4fv(locations.view, false, viewMatrix(camera, [0, 0.87, 0]));
      gl.uniformMatrix4fv(locations.projection, false, projectionMatrix(lens, width / height));
      gl.uniform3fv(locations.camera, camera);
      gl.uniform3fv(locations.subject, subject);
      gl.uniform3fv(locations.background, background);

      drawMesh(block, [0, -0.20, 0], [64, 0.2, 64], [0.13, 0.18, 0.22], 1);
      drawMesh(stage, [0, 0.01, 0], [2.45, 0.22, 2.45], [0.10, 0.17, 0.22]);
      drawMesh(stage, [0, 0.13, 0], [2.42, 0.06, 2.42], [0.20, 0.45, 0.46]);
      drawMesh(stage, [0, 0.22, 0], [2.38, 0.14, 2.38], [0.17, 0.26, 0.29], 2);
      for (const prop of props) drawMesh(block, prop.position, prop.size, prop.color, 0, prop.angle);
      drawMesh(stone, subject, [1, 1, 1], [0.93, 0.49, 0.20], 3, phase + 0.26);
    }

    return {draw, dispose};
  }

  return {create, duration};
})();
