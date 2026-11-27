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

# The web reader's bar (#web-toolbar) lives in the top bar in this look, so Web has one address bar.
READER = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const observers = [];
class Node {
  constructor(id) { Object.assign(this, {id, attrs: {}, listeners: {}, dataset: {}, children: [], parentNode: null, textContent: '', disabled: false}); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { this.listeners.click?.(); }
  insertBefore(child, before) {
    if (before && before.parentNode !== this) throw new Error('NotFoundError');
    if (document.activeElement && child.contains(document.activeElement)) document.activeElement = null;  // a move drops focus
    child.parentNode?.children.splice(child.parentNode.children.indexOf(child), 1);
    this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, child);
    child.parentNode = this;
    return child;
  }
  append(child) { this.insertBefore(child, null); }
  contains(other) { for (let node = other; node; node = node.parentNode) if (node === this) return true; return false; }
  focus() { document.activeElement = this; }
}
const nodes = new Map();
const add = (id, parent) => { const node = new Node(id); nodes.set(id, node); parent?.append(node); return node; };
const document = {documentElement: new Node('html'), activeElement: null, getElementById: id => nodes.get(id) || null, querySelectorAll: () => []};
const chrome = add('browser-chrome');
const bar = add('browser-toolbar', chrome);
for (const id of ['browser-back', 'browser-forward', 'browser-reload', 'browser-address', 'browser-mission', 'browser-settings']) add(id, bar);
const view = add('web-view');
add('web-tabs', view);
const reader = add('web-toolbar', view);
const address = add('web-address', reader);
add('web-progress', view);
add('web-viewport', view);
const navs = ['nav-chats', 'nav-web'];
for (const id of navs) add(id).setAttribute('aria-current', id === 'nav-chats' ? 'page' : 'false');
for (const nav of navs) nodes.get(nav).listeners.click = () => {
  for (const id of navs) nodes.get(id).setAttribute('aria-current', id === nav ? 'page' : 'false');
  observers.forEach(o => o());
};
class MutationObserver { constructor(fn) { observers.push(fn); } observe() {} }
const look = name => { document.documentElement.setAttribute('data-look', name); observers.forEach(o => o()); };
document.documentElement.setAttribute('data-look', 'classic');
vm.runInNewContext(fs.readFileSync(process.argv.at(-1), 'utf8'), {window: {MutationObserver}, document, String, Object});
const ids = parent => parent.children.map(child => child.id);

// The other looks keep the reader's bar in Web, above its progress line.
assert.deepEqual(ids(view), ['web-tabs', 'web-toolbar', 'web-progress', 'web-viewport']);
assert.equal(chrome.getAttribute('data-section'), 'chats');
// The browser look moves it into the top bar, before Mission Control; browser.css shows it only in Web.
look('browser');
assert.deepEqual(ids(bar), ['browser-back', 'browser-forward', 'browser-reload', 'browser-address', 'web-toolbar',
                            'browser-mission', 'browser-settings']);
assert.deepEqual(ids(view), ['web-tabs', 'web-progress', 'web-viewport']);
nodes.get('nav-web').click();
assert.equal(chrome.getAttribute('data-section'), 'web');
assert.equal(reader.parentNode, bar);
// Changing the look while typing an address: the bar goes back to Web and the address keeps the cursor.
address.focus();
look('classic');
assert.deepEqual(ids(view), ['web-tabs', 'web-toolbar', 'web-progress', 'web-viewport']);
assert.equal(document.activeElement, address);
look('cockpit');
assert.equal(reader.parentNode, view);
look('browser');
assert.equal(reader.parentNode, bar);
assert.equal(document.activeElement, address);
process.stdout.write('checked\n');
'''


class BrowserLookTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the browser look test")
    def test_tabs_history_address_and_toolbar(self):
        result = subprocess.run(["node", "-e", CHECK, "--", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the browser look test")
    def test_the_web_reader_bar_joins_the_top_bar(self):
        result = subprocess.run(["node", "-e", READER, "--", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("checked", result.stdout)

    def test_web_shows_one_address_bar(self):
        css = (SCRIPT.parent / "browser.css").read_text(encoding="utf-8")
        rule = '.browser-chrome:not([data-section="web"]) .web-toolbar { display: none; }'
        self.assertIn(rule, css)
        hidden_in_web = css.split(rule)[0].rsplit("}", 1)[-1]  # the selectors that share the rule
        for control in ("#browser-back", "#browser-forward", "#browser-reload", "#browser-address"):
            self.assertIn(f'.browser-chrome[data-section="web"] {control},', hidden_in_web)
        page = (SCRIPT.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div id="browser-toolbar" class="browser-toolbar">', page)
        self.assertLess(page.index('id="web-toolbar"'), page.index('id="web-progress"'))

    def test_the_browser_bar_hides_in_the_other_looks(self):
        css = (SCRIPT.parent / "browser.css").read_text(encoding="utf-8")
        self.assertIn(':root:not([data-look="browser"]) .browser-chrome { display: none; }', css)
        page = (SCRIPT.parent / "index.html").read_text(encoding="utf-8")
        for target in ("nav-chats", "nav-dev", "nav-models", "nav-generate", "nav-web"):
            self.assertIn(f'data-nav="{target}"', page)


if __name__ == "__main__":
    unittest.main()
