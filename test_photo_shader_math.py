"""Check the Quick animation shader maths across photo shapes and every frame of the loop.

The real photo-shader.js runs under Node with a recording WebGL double. The test reads the
uniforms it sets and the constants in its fragment shader, then evaluates the same formulas.
"""

from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).parent / "webui" / "static" / "photo-shader.js"

CHECK = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sources = [];
const pixelStore = {};
let uniforms = {};
const gl = {
  VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, MAX_TEXTURE_SIZE: 5, NO_ERROR: 0,
  UNPACK_FLIP_Y_WEBGL: 'flip', UNPACK_PREMULTIPLY_ALPHA_WEBGL: 'premultiply', TEXTURE_2D: 6, TEXTURE0: 7,
  TEXTURE_MIN_FILTER: 8, TEXTURE_MAG_FILTER: 9, TEXTURE_WRAP_S: 10, TEXTURE_WRAP_T: 11, LINEAR: 12,
  CLAMP_TO_EDGE: 13, RGBA: 14, UNSIGNED_BYTE: 15, TRIANGLES: 16,
  drawingBufferWidth: 0, drawingBufferHeight: 0,
  createShader: () => ({}), shaderSource: (_, source) => sources.push(source), compileShader() {},
  getShaderParameter: () => true, deleteShader() {}, createProgram: () => ({}), attachShader() {},
  linkProgram() {}, getProgramParameter: () => true, deleteProgram() {}, createTexture: () => ({}),
  deleteTexture() {}, getParameter: () => 16384, getUniformLocation: (_, name) => name, useProgram() {},
  activeTexture() {}, bindTexture() {}, uniform1i: (name, value) => { uniforms[name] = value; },
  uniform1f: (name, value) => { uniforms[name] = value; }, uniform2f: (name, x, y) => { uniforms[name] = [x, y]; },
  texParameteri() {}, pixelStorei: (name, value) => { pixelStore[name] = value; }, isContextLost: () => false,
  texImage2D() {}, getError: () => 0, viewport() {}, drawArrays() {},
};
const window = {};
vm.runInNewContext(fs.readFileSync(process.argv.at(-1), 'utf8'), {window, Map, Math, Error});
const renderer = window.photoShader.create({getContext: () => gl});
const fragment = sources[1];
const number = pattern => { const match = fragment.match(pattern); assert(match, 'shader constant not found: ' + pattern); return Number(match[1]); };
const zoomAmount = number(/float zoom = 1\.0 \+ ([\d.]+) \*/);
const panScale = number(/uv \*= ([\d.]+);\s*uv\.x \+=/);
const panReach = number(/uv\.x \+= ([\d.]+) \* cover\.x/);
const rippleBase = number(/uv \*= ([\d.]+) \+ [\d.]+ \* sin\(radius/);
const rippleAmount = number(/uv \*= [\d.]+ \+ ([\d.]+) \* sin\(radius/);
const rippleRings = number(/sin\(radius \* ([\d.]+) - phase\)/);

function sample(effect, position, cover, aspect, phase) {
  let u = (position[0] - 0.5) * cover[0], v = (position[1] - 0.5) * cover[1];
  if (effect === 'zoom') {
    const zoom = 1 + zoomAmount * (0.5 - 0.5 * Math.cos(phase)); u /= zoom; v /= zoom;
  } else if (effect === 'pan') {
    u *= panScale; v *= panScale; u += panReach * cover[0] * Math.sin(phase);
  } else {
    const scale = rippleBase + rippleAmount * Math.sin(Math.hypot(u * aspect, v) * rippleRings - phase);
    u *= scale; v *= scale;
  }
  return [u + 0.5, v + 0.5];
}

// 1. Photo stays inside the picture: every shape, every frame, every edge of the screen.
const edges = [];
for (let i = 0; i <= 20; i++) edges.push([i / 20, 0], [i / 20, 1], [0, i / 20], [1, i / 20]);
let samples = 0;
for (const photoAspect of [0.2, 0.5, 0.75, 1, 4 / 3, 16 / 9, 3, 6]) {
  for (const [width, height] of [[400, 1600], [720, 1280], [800, 800], [1280, 720], [1600, 400]]) {
    renderer.setImage({width: Math.round(400 * photoAspect), height: 400});
    gl.drawingBufferWidth = width; gl.drawingBufferHeight = height;
    for (const effect of ['zoom', 'pan', 'ripple']) {
      for (let frame = 0; frame <= 90; frame++) {
        uniforms = {};
        renderer.draw(effect, frame / 30);
        const cover = uniforms.cover;
        // Cover never stretches: the screen shows a window of the photo with the screen's own shape.
        assert(Math.abs(cover[0] * uniforms.imageAspect / cover[1] - width / height) < 1e-9 * (width / height));
        assert(Math.max(...cover) === 1);
        for (const position of edges) {
          const [u, v] = sample(effect, position, cover, uniforms.imageAspect, uniforms.phase);
          assert(u >= -1e-9 && u <= 1 + 1e-9 && v >= -1e-9 && v <= 1 + 1e-9,
            `${effect} reads outside the photo: aspect ${photoAspect}, screen ${width}x${height}, frame ${frame}`);
          samples++;
        }
      }
    }
  }
}

// 2. The loop is seamless: the last frame of the clip is the first one again.
uniforms = {}; renderer.draw('ripple', 0); const first = uniforms.phase;
uniforms = {}; renderer.draw('ripple', window.photoShader.duration); const last = uniforms.phase;
assert.equal(first, last);
uniforms = {}; renderer.draw('ripple', window.photoShader.duration - 1e-6);
assert(Math.abs(uniforms.phase - 2 * Math.PI) < 1e-5);

// 3. See-through photos are premultiplied, and the shader composites them that way.
assert.equal(pixelStore.premultiply, true);
assert.match(fragment, /pixel\.rgb \+ vec3\(0\.06\) \* \(1\.0 - pixel\.a\)/);
process.stdout.write(`checked ${samples} samples\n`);
'''


class PhotoShaderMathTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the shader maths test")
    def test_every_frame_stays_inside_the_photo_and_loops_seamlessly(self):
        result = subprocess.run(["node", "-e", CHECK, "--", str(SCRIPT)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
