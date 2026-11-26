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
const document = {getElementById: id => nodes.get(id) || null, createElement: tag => new Node(null, tag), documentElement: root, addEventListener() {}, activeElement: null};
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
nodes.get('open-appearance').listeners.click();
menu.listeners.keydown({key: 'Escape', preventDefault() {}});
assert.equal(menu.hidden, true);
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
