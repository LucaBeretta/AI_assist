"""The chat's "⋯" menu and the Appearance menu press the app's own buttons and show the current choices."""

from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).parent / "webui" / "static" / "menus.js"

CHECK = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const pressed = [], calls = [];
class Node {
  constructor(id, tag = 'div') { this.id = id; this.tagName = tag; this.attrs = {}; this.listeners = {}; this.children = []; this.hidden = false; this.style = {}; this.textContent = ''; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  replaceChildren(...nodes) { this.children = nodes; }
  click() { pressed.push(this.id); }
  focus() { document.activeElement = this; }
  contains(other) { return other === this || this.children.includes(other); }
}
const nodes = new Map();
for (const id of ['quick-menu', 'chat-more', 'open-appearance', 'rename-chat', 'delete-chat', 'theme', 'setting-look']) nodes.set(id, new Node(id));
nodes.get('quick-menu').hidden = true;
nodes.get('setting-look').options = [{value: 'classic', textContent: 'Classic'}, {value: 'cockpit', textContent: '3D cockpit'}, {value: 'browser', textContent: 'Browser'}];
const root = new Node('html'); root.setAttribute('data-theme', 'night');
const pageListeners = {};
const document = {getElementById: id => nodes.get(id) || null, createElement: tag => new Node(null, tag), documentElement: root,
  addEventListener(type, fn) { pageListeners[type] = fn; }, activeElement: null};
let chat = {id: 'chat-a', incognito: false};
let look = 'cockpit';
const window = {innerWidth: 1280, innerHeight: 800,
  app: {currentChat: () => chat},
  lookui: {current: () => look, setLook: name => { calls.push(['look', name]); look = name; }},
  workspaceui: {openInstructions: id => calls.push(['instructions', id]), openMemories: id => calls.push(['memories', id])}};
vm.runInNewContext(fs.readFileSync(process.argv.at(-1), 'utf8'), {window, document, String, Math});
const menu = nodes.get('quick-menu');
const items = () => menu.children.filter(node => (node.getAttribute('role') || '').startsWith('menuitem'));
const labelled = text => items().find(node => node.textContent.trim().replace(/^✓\s*/, '') === text);

// "⋯" for this chat
nodes.get('chat-more').listeners.click();
assert.equal(menu.hidden, false);
assert.equal(nodes.get('chat-more').getAttribute('aria-expanded'), 'true');
assert.deepEqual(items().map(node => node.textContent), ['Rename…', 'Chat instructions…', 'Memories for this chat…', 'Delete chat']);
labelled('Rename…').listeners.click();
assert.equal(pressed.at(-1), 'rename-chat');
assert.equal(menu.hidden, true);
nodes.get('chat-more').listeners.click();
labelled('Memories for this chat…').listeners.click();
assert.deepEqual(calls.at(-1), ['memories', 'chat-a']);
nodes.get('chat-more').listeners.click();
labelled('Delete chat').listeners.click();
assert.equal(pressed.at(-1), 'delete-chat');
chat = {id: 'chat-b', incognito: true};                // incognito chats never use memory
nodes.get('chat-more').listeners.click();
assert.equal(labelled('Memories for this chat…'), undefined);
nodes.get('chat-more').listeners.click();               // a second click closes it
assert.equal(menu.hidden, true);

// Appearance: the looks from Settings > Look, then Night / Day, with the current ones ticked
nodes.get('open-appearance').listeners.click();
const ticked = items().filter(node => node.getAttribute('aria-checked') === 'true').map(node => node.textContent.replace(/^✓\s*/, ''));
assert.deepEqual(ticked, ['3D cockpit', 'Night']);
labelled('Browser').listeners.click();
assert.deepEqual(calls.at(-1), ['look', 'browser']);
nodes.get('open-appearance').listeners.click();
labelled('Night').listeners.click();                    // already night: nothing to switch
assert.notEqual(pressed.at(-1), 'theme');
nodes.get('open-appearance').listeners.click();
labelled('Day').listeners.click();
assert.equal(pressed.at(-1), 'theme');
// Escape closes the menu only: it must not also reach the app, where it stops an answer or a task
nodes.get('open-appearance').listeners.click();
let stopped = false;
pageListeners.keydown({key: 'Escape', preventDefault() {}, stopPropagation() { stopped = true; }});
assert.equal(menu.hidden, true);
assert.equal(stopped, true);
pageListeners.keydown({key: 'Escape', preventDefault() {}, stopPropagation() { throw new Error('not open'); }});
// Focus moving elsewhere (Tab, Ctrl+K, Ctrl+N) closes it, so its items never act on another chat
nodes.get('chat-more').listeners.click();
pageListeners.focusin({target: new Node('prompt')});
assert.equal(menu.hidden, true);
// Night / Day read the theme when clicked: Ctrl+Shift+L may have changed it while the menu was open
nodes.get('open-appearance').listeners.click();
root.setAttribute('data-theme', 'day');
const before = pressed.length;
labelled('Night').listeners.click();
assert.equal(pressed.length, before + 1);
assert.equal(pressed.at(-1), 'theme');
// With no chat open, Rename and Delete are greyed out instead of doing nothing
chat = null;
nodes.get('chat-more').listeners.click();
assert.equal(labelled('Rename…').disabled, true);
assert.equal(labelled('Delete chat').disabled, true);
assert.equal(labelled('Chat instructions…'), undefined);
nodes.get('chat-more').listeners.click();
// With the app zoomed to 130%, the menu still opens under its button and inside the window
const zoom = 1.3;
nodes.get('chat-more').getBoundingClientRect = () => ({left: 1202, top: 13, right: 1244, bottom: 55});
menu.offsetWidth = 200;
menu.getBoundingClientRect = () => ({width: 200 * zoom, height: 150 * zoom});
chat = {id: 'chat-a', incognito: false};
nodes.get('chat-more').listeners.click();
const left = parseFloat(menu.style.left) * zoom, top = parseFloat(menu.style.top) * zoom;
assert.ok(Math.abs(left + 200 * zoom - 1244) < 1, 'right edge under the button: ' + left);
assert.ok(Math.abs(top - 59) < 1, 'just below the button: ' + top);
process.stdout.write('checked\n');
'''


class MenuTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the menu test")
    def test_chat_and_appearance_menus(self):
        result = subprocess.run(["node", "-e", CHECK, "--", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
