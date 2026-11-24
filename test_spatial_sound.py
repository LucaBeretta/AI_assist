"""3D sound for Quick animation: seamless paths, headphone (HRTF) rendering and a recordable track."""

from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).parent / "webui" / "static" / "spatial-sound.js"

CHECK = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const contexts = [];
class Param { constructor() { this.events = []; } setValueAtTime(v, t) { this.events.push([v, t]); } linearRampToValueAtTime(v, t) { this.events.push([v, t]); } }
class FakeContext {
  constructor() { this.currentTime = 1; this.destination = {kind: 'speakers'}; this.closed = false; contexts.push(this); }
  createBufferSource() { return this.source = {connect: node => { this.source.to = node; }, start: at => { this.source.started = at; }, stop: () => { this.source.stopped = true; }}; }
  createPanner() { return this.panner = {positionX: new Param(), positionY: new Param(), positionZ: new Param(), connect: node => { this.panner.to = node; }}; }
  createMediaStreamDestination() { return {kind: 'recording', stream: {getAudioTracks: () => [{kind: 'audio'}]}}; }
  close() { this.closed = true; }
}
const window = {AudioContext: FakeContext};
vm.runInNewContext(fs.readFileSync(process.argv.at(-1), 'utf8'), {window, Math, Error, Object});
const sound = window.spatialSound;
const near = (a, b) => ['x', 'y', 'z'].every(k => Math.abs(a[k] - b[k]) < 1e-9);

for (const path of sound.paths) {
  // Seamless: the end of the 3-second loop is its start, and every path starts in front of the listener.
  assert(near(sound.position(path, 0, 3), sound.position(path, 3, 3)), path);
  assert(sound.position(path, 0, 3).z < 0, path);
}
assert(sound.position('sideways', 0.75, 3).x > 2.9 && sound.position('sideways', 2.25, 3).x < -2.9);
assert(sound.position('overhead', 0.75, 3).y > 1.9);
assert.throws(() => sound.position('teleport', 0, 3));

// Recording: rendered for headphones, routed into a track for the clip, never to the speakers.
const clip = sound.start({}, 'around', 3, {record: true});
const context = contexts.at(-1);
assert.equal(context.panner.panningModel, 'HRTF');
assert.equal(context.panner.to.kind, 'recording');
assert.equal(clip.tracks.length, 1);
assert.equal(context.source.loop, true);
const xs = context.panner.positionX.events;
assert.equal(xs.length, 121);
assert(Math.abs(xs.at(-1)[1] - xs[0][1] - 3) < 1e-9);  // one loop covers exactly the 3-second clip
clip.stop();
assert(context.source.stopped && context.closed);

// Preview: plays to the speakers and keeps looping the path.
const preview = sound.start({}, 'sideways', 3);
assert.equal(contexts.at(-1).panner.to.kind, 'speakers');
assert.equal(preview.tracks.length, 0);
assert(contexts.at(-1).panner.positionX.events.length > 121 * 10);

// Files: audio up to 20 MB only; no file means no sound.
assert.equal(sound.checkFile(undefined), null);
assert.throws(() => sound.checkFile({size: 21 * 1024 * 1024, type: 'audio/wav'}));
assert.throws(() => sound.checkFile({size: 10, type: 'application/x-msdownload'}));
assert.equal(sound.checkFile({size: 10, type: 'audio/mpeg'}).type, 'audio/mpeg');
process.stdout.write('checked\n');
'''


class SpatialSoundTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the 3D sound test")
    def test_paths_loop_and_recording_uses_headphone_rendering(self):
        result = subprocess.run(["node", "-e", CHECK, "--", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
