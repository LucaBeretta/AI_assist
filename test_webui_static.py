"""Exercise the bare frontend with a small DOM/API double, without a WebView."""

from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.value = ''; this.textContent = ''; this.children = []; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
}
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement() { return new Element(); }
};
const chat = {id:'chat-a', title:'Chat A', model:'fake:7b', messages:[]};
const saved = { ...chat, messages:[] };
const calls = [];
const window = {listeners:{}, addEventListener(type, callback) {this.listeners[type] = callback;}};
window.pywebview = {api: {
  state: async () => ({ok:true, engine:'Ollama', host:'localhost', models:['fake:7b']}),
  list_chats: async () => ({ok:true, chats:[saved]}),
  open_chat: async () => ({ok:true, chat:structuredClone(saved)}),
  send: async (...args) => { calls.push(args); saved.messages.push({role:'user',content:args[1]}); return {ok:true,stream_id:'stream-a'}; },
  stop: async id => {calls.push(['stop',id]); return {ok:true};}
}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document, window, Map, Error, String, Number, Boolean, Array});
(async () => {
  await window.listeners.pywebviewready();
  elements.get('model').value = 'fake:7b';
  const hostile = '<img src=x onerror=alert(1)>';
  elements.get('prompt').value = 'Hi';
  await elements.get('composer').listeners.submit({preventDefault(){}});
  assert.equal(calls[0][0], 'chat-a');
  window.app.onEvent([{type:'token',chat_id:'chat-a',stream_id:'stream-a',text:hostile}]);
  assert.equal(elements.get('messages').children.at(-1).textContent, hostile);
  assert.equal(elements.get('stop').disabled, false);
  await elements.get('stop').listeners.click();
  assert.deepEqual(calls[1], ['stop','stream-a']);
  saved.messages.push({role:'assistant',content:hostile});
  window.app.onEvent([{type:'stopped',chat_id:'chat-a',stream_id:'stream-a',text:hostile,metrics:{}}]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.get('stop').disabled, true);
  assert.equal(elements.get('messages').children.at(-1).children.at(-1).textContent, hostile);
  process.stdout.write('Frontend model/send/token/stop checks passed\n');
})().catch(error => { console.error(error); process.exitCode=1; });
'''


class StaticTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_model_selection_streamed_text_and_stop(self):
        script = Path(__file__).parent / "webui" / "static" / "app.js"
        result = subprocess.run([shutil.which("node"), "-e", HARNESS, str(script)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
