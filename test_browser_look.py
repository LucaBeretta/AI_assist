"""The browser look's top bar: tabs, back and forward, the address bar and the toolbar drive the app's own buttons."""

from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).parent / "webui" / "static" / "browser.js"

CHECK = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const pressed = [];
const observers = [];
class Node {
  constructor(id, attrs = {}) { this.id = id; this.attrs = {...attrs}; this.listeners = {}; this.dataset = {}; this.disabled = false; this.textContent = ''; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { pressed.push(this.id); this.listeners.click?.(); }
}
const nodes = new Map();
const add = (id, attrs) => { const node = new Node(id, attrs); nodes.set(id, node); return node; };
const navs = ['nav-chats', 'nav-dev', 'nav-models', 'nav-generate'];
for (const id of navs) add(id, {'aria-current': id === 'nav-chats' ? 'page' : 'false'});
for (const id of ['new-chat', 'refresh', 'open-palette', 'open-mission-control', 'open-settings', 'chat-title',
                  'browser-new-tab', 'browser-back', 'browser-forward', 'browser-reload', 'browser-address',
                  'browser-address-text', 'browser-mission', 'browser-settings']) add(id);
nodes.get('chat-title').textContent = 'Trip plan for Lisbon';
const tabs = navs.map(nav => { const tab = new Node('tab-' + nav); tab.dataset.nav = nav; return tab; });
// The app's own section buttons: a click makes that section current, as showView does.
const show = nav => () => { for (const id of navs) nodes.get(id).setAttribute('aria-current', id === nav ? 'page' : 'false'); observers.forEach(o => o()); };
for (const id of navs) nodes.get(id).listeners.click = show(id);
const document = {getElementById: id => nodes.get(id) || null, querySelectorAll: selector => selector === '.browser-tab' ? tabs : []};
class MutationObserver { constructor(fn) { observers.push(fn); } observe() {} }
const window = {MutationObserver};
vm.runInNewContext(fs.readFileSync(process.argv.at(-1), 'utf8'), {window, document, String, Object});
const text = () => nodes.get('browser-address-text').textContent;
const selected = () => tabs.filter(tab => tab.getAttribute('aria-selected') === 'true').map(tab => tab.dataset.nav);

assert.equal(text(), 'aiassist://chats/Trip plan for Lisbon');
assert.deepEqual(selected(), ['nav-chats']);
assert.equal(nodes.get('browser-back').disabled, true);
assert.equal(nodes.get('browser-forward').disabled, true);

tabs[1].listeners.click();                       // Developer tab
assert.equal(pressed.at(-1), 'nav-dev');
assert.equal(text(), 'aiassist://developer');
assert.deepEqual(selected(), ['nav-dev']);
tabs[2].listeners.click();                       // Models tab
assert.equal(text(), 'aiassist://models');
assert.equal(nodes.get('browser-back').disabled, false);

nodes.get('browser-back').listeners.click();     // back to Developer
assert.equal(pressed.at(-1), 'nav-dev');
assert.equal(text(), 'aiassist://developer');
assert.equal(nodes.get('browser-forward').disabled, false);
nodes.get('browser-back').listeners.click();     // back to Chats
assert.equal(text(), 'aiassist://chats/Trip plan for Lisbon');
assert.equal(nodes.get('browser-back').disabled, true);
nodes.get('browser-forward').listeners.click();  // forward to Developer
assert.equal(text(), 'aiassist://developer');
tabs[3].listeners.click();                       // a new visit clears the forward list
assert.equal(nodes.get('browser-forward').disabled, true);

tabs[2].listeners.click();                       // on Models, "+" makes a new chat and shows it
nodes.get('browser-new-tab').listeners.click();
assert.deepEqual(pressed.slice(-2), ['new-chat', 'nav-chats']);
assert.deepEqual(selected(), ['nav-chats']);
for (const [button, target] of [['browser-new-tab', 'nav-chats'], ['browser-reload', 'refresh'], ['browser-address', 'open-palette'],
                                ['browser-mission', 'open-mission-control'], ['browser-settings', 'open-settings']]) {
  nodes.get(button).listeners.click();
  assert.equal(pressed.at(-1), target, button);
}
process.stdout.write('checked\n');
'''


class BrowserLookTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the browser look test")
    def test_tabs_history_address_and_toolbar(self):
        result = subprocess.run(["node", "-e", CHECK, "--", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)

    def test_the_browser_bar_hides_in_the_other_looks(self):
        css = (SCRIPT.parent / "browser.css").read_text(encoding="utf-8")
        self.assertIn(':root:not([data-look="browser"]) .browser-chrome { display: none; }', css)
        page = (SCRIPT.parent / "index.html").read_text(encoding="utf-8")
        for target in ("nav-chats", "nav-dev", "nav-models", "nav-generate"):
            self.assertIn(f'data-nav="{target}"', page)


if __name__ == "__main__":
    unittest.main()
