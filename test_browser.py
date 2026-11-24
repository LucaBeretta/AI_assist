"""The in-app reading browser: addresses, the HTML allowlist, the gate's session and the API."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.request

import network_gate
from webui import browser
from webui.browser import BrowserError, BrowserSession, _Page, address_url, checked_url, plain_text


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16


def parse(html, url="https://example.com/docs/index.html"):
    page = _Page(url)
    page.feed(html)
    page.close()
    return page


def walk(node):
    yield node
    if isinstance(node, list):
        for child in node[2]:
            yield from walk(child)


def elements(node):
    return [item for item in walk(node) if isinstance(item, list)]


def text_of(node):
    return "".join(item for item in walk(node) if isinstance(item, str))


def response(url, body, kind="text/html", status=200, charset="utf-8"):
    data = body.encode("utf-8") if isinstance(body, str) else body
    return {"url": url, "status": status, "data": data, "cut": False, "type": kind, "charset": charset}


class FakeWeb:
    """Stands in for browser.fetch, and records where and how each request was made."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, limit, accept, timeout, language="en"):
        task = network_gate._current_task()
        self.calls.append({"url": url, "thread": threading.current_thread().name, "task": task,
                           "hosts": task.hosts if task else None, "language": language})
        page = self.pages.get(url)
        if page is None:
            raise OSError("not found")
        return page(url) if callable(page) else page


class AddressTests(unittest.TestCase):
    def test_addresses_and_searches(self):
        self.assertEqual(address_url("example.com"), "https://example.com/")
        self.assertEqual(address_url("  it.wikipedia.org/wiki/Roma "), "https://it.wikipedia.org/wiki/Roma")
        self.assertEqual(address_url("HTTP://Example.com/a?b=1#c"), "http://Example.com/a?b=1#c")
        self.assertEqual(address_url("localhost:8080/x"), "http://localhost:8080/x")
        self.assertEqual(address_url("192.168.1.1"), "http://192.168.1.1/")
        self.assertEqual(address_url("[::1]:8080"), "http://[::1]:8080/")
        for words in ("hello world", "3.5", "ai", "what is a.b"):
            self.assertTrue(address_url(words).startswith("https://en.wikipedia.org/w/index.php?search="), words)
        self.assertEqual(address_url("lago di como", "it"), "https://it.wikipedia.org/w/index.php?search=lago+di+como")

    def test_other_schemes_passwords_and_empty_input_are_refused(self):
        for text in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "ftp://example.com/",
                     "http://user:secret@example.com/", "", "   ", "x" * 2001):
            with self.assertRaises(BrowserError, msg=text):
                address_url(text)

    def test_pages_on_the_internet_never_open_this_computer_or_the_local_network(self):
        for url in ("http://127.0.0.1:11434/api/tags", "http://localhost/", "http://192.168.1.1/", "http://[::1]/",
                    "http://10.0.0.8/admin"):
            with self.assertRaises(BrowserError, msg=url):
                checked_url(url, from_internet=True)
            self.assertTrue(checked_url(url))  # typed by the person: allowed
        self.assertEqual(checked_url("https://example.org/a", from_internet=True), "https://example.org/a")

    def test_redirects_from_the_internet_to_the_local_network_are_refused(self):
        handler = browser._Redirects()
        request = urllib.request.Request("https://example.com/start")
        with self.assertRaises(BrowserError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://192.168.1.1/")
        with self.assertRaises(BrowserError):
            handler.redirect_request(request, None, 302, "Found", {}, "javascript:alert(1)")
        moved = handler.redirect_request(request, None, 302, "Found", {}, "/next")
        self.assertEqual(moved.full_url, "https://example.com/next")
        local = urllib.request.Request("http://192.168.1.1/")
        self.assertEqual(handler.redirect_request(local, None, 302, "Found", {}, "http://192.168.1.1/login").full_url,
                         "http://192.168.1.1/login")


class AllowlistTests(unittest.TestCase):
    def test_active_and_hidden_content_never_reaches_the_tree(self):
        page = parse("""<html><head><title>T</title><style>a{}</style><script>alert(1)</script>
            <link rel=stylesheet href=x.css></head><body onload="x()">
            <p onclick="steal()" style="color:red" class="c">Hi <a href="javascript:alert(1)">js</a>
            <a href="data:text/html,x">data</a> <a href="vbscript:x">vb</a></p>
            <iframe src="https://evil.example"><p>framed</p></iframe><svg><script>x()</script><text>svg</text></svg>
            <template><p>template</p></template><object data=x><p>object</p></object><noscript><p>shown</p></noscript>
            <div hidden>hidden</div><div aria-hidden="true">aria</div><div style="display: none">css</div>
            <video src=v.mp4>video</video><textarea>area</textarea><select><option>opt</option></select>
            <button onclick="x()">click</button></body></html>""")
        text = text_of(page.root)
        for word in ("alert", "framed", "svg", "template", "object", "hidden", "aria", "css", "video", "area",
                     "opt", "click", "steal"):
            self.assertNotIn(word, text)
        self.assertIn("shown", text)
        allowed = {"href", "hash", "title", "id", "dir", "src", "alt", "width", "height", "colspan", "rowspan",
                   "start", "open", "action", "type", "name", "value", "placeholder"}
        for tag, attrs, _ in elements(page.root):
            self.assertTrue(tag in browser._KEEP or tag in ("form", "input", "button"), tag)
            self.assertLessEqual(set(attrs), allowed, tag)
        self.assertEqual([attrs for tag, attrs, _ in elements(page.root) if tag == "a"], [{}, {}, {}])

    def test_links_resolve_against_the_page_and_its_base(self):
        page = parse('<base href="/guide/"><a href="intro.html">a</a><a href="//cdn.example.org/x">b</a>'
                     '<a href="mailto:a@example.com">c</a><a href="https://u:p@example.net/">d</a><a name="top">top</a>')
        links = [attrs for tag, attrs, _ in elements(page.root) if tag == "a"]
        self.assertEqual(links[0]["href"], "https://example.com/guide/intro.html")
        self.assertEqual(links[1]["href"], "https://cdn.example.org/x")
        self.assertEqual(links[2:4], [{}, {}])
        self.assertEqual(links[4], {"id": "top"})

    def test_same_page_links_carry_their_fragment(self):
        page = parse('<a href="#history">h</a><a href="index.html#b">b</a><a href="other.html#c">c</a>')
        links = [attrs for tag, attrs, _ in elements(page.root) if tag == "a"]
        self.assertEqual(links[0]["hash"], "history")
        self.assertEqual(links[1]["hash"], "b")
        self.assertNotIn("hash", links[2])

    def test_missing_end_tags_close_like_a_browser(self):
        page = parse("<ul><li>a<li>b</ul><p>one<p>two<div>three</div><table><tr><td>1<td>2<tr><td>3</table>")
        top = page.root[2]
        self.assertEqual([child[0] for child in top if isinstance(child, list)], ["ul", "p", "p", "div", "table"])
        self.assertEqual([text_of(item) for item in top[0][2]], ["a", "b"])
        table = top[-1]
        self.assertEqual([[text_of(cell) for cell in row[2]] for row in table[2]], [["1", "2"], ["3"]])

    def test_only_get_forms_with_text_fields_are_kept(self):
        page = parse('<form action="/w/index.php"><input name=search placeholder=Find><input type=hidden name=t '
                     'value=x><input type=password name=p><input type=submit value=Go></form>'
                     '<form method=post action=/login><input name=user><button>Sign in</button></form>')
        forms = [item for item in elements(page.root) if item[0] == "form"]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0][1], {"action": "https://example.com/w/index.php"})
        fields = [item[1] for item in elements(forms[0]) if item[0] == "input"]
        self.assertEqual([field["name"] for field in fields], ["search", "t"])
        self.assertEqual(text_of(forms[0]), "Go")
        self.assertNotIn("Sign in", text_of(page.root))

    def test_images_tracking_pixels_lazy_sources_and_inline_data(self):
        inline = "data:image/png;base64," + base64.b64encode(PNG).decode()
        page = parse(f'<img src="a.png" alt=" A  photo "><img src="t.gif" width=1 height=1>'
                     f'<img src="data:image/gif;base64,R0lGOD" data-src="/lazy.jpg"><img src="{inline}">'
                     '<img src="data:image/svg+xml;base64,PHN2Zz4="><img srcset="s.png 1x, l.png 2x">')
        images = [attrs for tag, attrs, _ in elements(page.root) if tag == "img"]
        self.assertEqual([image["src"] for image in images],
                         ["https://example.com/docs/a.png", "https://example.com/lazy.jpg", inline,
                          "https://example.com/docs/s.png"])
        self.assertEqual(images[0]["alt"], "A photo")
        self.assertEqual(page.images, ["https://example.com/docs/a.png", "https://example.com/lazy.jpg",
                                       "https://example.com/docs/s.png"])

    def test_reading_view_finds_the_article_text_under_the_page_heading(self):
        page = parse('<title>Lake - Encyclopedia</title><svg><title>icon label</title></svg>'
                     '<main><h1 id="first">Lake</h1><div class="tools"><ul><li>Deutsch<li>Italiano</ul></div>'
                     '<div class="body"><table><tr><td>Infobox</table><p>' + "water " * 40 + "</p><p>"
                     + "shore " * 40 + '</p><aside>Related</aside><form action=/s><input name=q></form></div></main>')
        self.assertEqual(page.title, "Lake - Encyclopedia")
        reading = page.reading_root()
        self.assertEqual(reading[2][0], ["h1", {}, ["Lake"]])
        text = text_of(reading)
        self.assertIn("Infobox", text)
        for word in ("Deutsch", "Related", "icon label"):
            self.assertNotIn(word, text)
        self.assertEqual([item[0] for item in elements(reading) if item[0] == "form"], [])

    def test_reading_view_of_a_results_list_skips_the_site_menus(self):
        menu = "".join(f'<li><a href="/p{n}">Page {n}</a></li>' for n in range(30))
        results = "".join(f'<li><a href="/r{n}">Result {n}</a><div>{"snippet words " * 8}</div></li>' for n in range(5))
        page = parse(f'<div id=menu><ul>{menu}</ul></div><main><h1>Search results</h1><div class=results><ul>{results}'
                     '</ul></div></main><footer><p>' + "Legal text. " * 20 + "</p></footer>")
        reading = page.reading_root()
        self.assertEqual(reading[2][0], ["h1", {}, ["Search results"]])
        self.assertIn("Result 4", text_of(reading))
        self.assertNotIn("Page 1", text_of(reading))
        self.assertNotIn("Legal", text_of(reading))

    def test_title_refresh_and_reading_view(self):
        page = parse('<title> Hello &amp; welcome </title><meta http-equiv="refresh" content="0; url=/next">'
                     '<nav><a href="/">Home</a></nav><main><nav>Contents</nav><h1>Article</h1><p>'
                     + "text " * 60 + "</p></main><footer>Footer</footer>")
        self.assertEqual(page.title, "Hello & welcome")
        self.assertEqual(page.refresh, "https://example.com/next")
        reading = page.reading_root()
        self.assertIn("Article", text_of(reading))
        self.assertNotIn("Contents", text_of(reading))
        self.assertNotIn("Footer", text_of(reading))
        self.assertIsNone(parse("<main><p>short</p></main>").reading_root())

    def test_huge_or_deep_pages_stop_safely(self):
        page = parse("<p>x</p>" * (browser.MAX_NODES + 50))
        self.assertTrue(page.full)
        self.assertLessEqual(len(elements(page.root)), browser.MAX_NODES + 1)
        deep = parse("<div>" * 500 + "deep" + "</div>" * 500)
        self.assertIn("deep", text_of(deep.root))
        json.dumps(deep.root)

    def test_plain_text_for_the_ai(self):
        page = parse("<h1>T</h1><p>a<br>b</p><ul><li>x<li>y</ul><img src=p.png alt=Photo>"
                     "<table><tr><td>1<td>2</table><form action=/s><input name=q><button>Go</button></form>")
        self.assertEqual(plain_text(page.root, 1000), "# T\n\na\nb\n\n- x\n- y\n\n[Photo]\n| 1 | 2")


class SessionTests(unittest.TestCase):
    def session(self, pages):
        web = FakeWeb(pages)
        return BrowserSession(fetch=web), web

    def test_pages_are_fetched_by_one_worker_inside_one_gate_task(self):
        html = '<title>Home</title><a href="/two">two</a><img src="/a.png"><img src="https://img.example.net/b.png">'
        session, web = self.session({"https://example.com/": response("https://example.com/", html),
                                     "https://example.com/two": response("https://example.com/two", "<p>two</p>")})
        first = session.open("example.com")
        second = session.open("https://example.com/two", typed=False, referrer=first["id"])
        self.assertEqual(first["title"], "Home")
        self.assertEqual(second["tree"], ["div", {}, [["p", {}, ["two"]]]])
        self.assertEqual({call["thread"] for call in web.calls}, {"webui-browser"})
        self.assertIsNotNone(web.calls[0]["task"])
        self.assertIs(web.calls[0]["task"], web.calls[1]["task"])  # one approval per site for the session
        self.assertEqual(web.calls[0]["task"].purpose, browser.PURPOSE)
        self.assertEqual(first["images"], {"same": 1, "other": 1, "other_hosts": ["img.example.net"]})
        session.forget()
        session.open("example.com")
        self.assertIsNot(web.calls[-1]["task"], web.calls[0]["task"])  # closing every tab ended the approvals

    def test_links_from_internet_pages_cannot_reach_the_local_network(self):
        session, web = self.session({
            "https://example.com/": response("https://example.com/", '<img src="http://192.168.1.1/cam.jpg">'),
            "http://192.168.1.1/": response("http://192.168.1.1/", '<a href="/settings">s</a>'),
            "http://192.168.1.1/settings": response("http://192.168.1.1/settings", "<p>ok</p>")})
        page = session.open("example.com")
        self.assertEqual(page["images"]["same"] + page["images"]["other"], 0)
        with self.assertRaises(BrowserError):
            session.open("http://192.168.1.1/", typed=False, referrer=page["id"])
        with self.assertRaises(BrowserError):
            session.open("http://192.168.1.1/", typed=False, referrer="unknown")
        router = session.open("192.168.1.1")  # typed by the person
        self.assertEqual(session.open("http://192.168.1.1/settings", typed=False, referrer=router["id"])["tree"],
                         ["div", {}, [["p", {}, ["ok"]]]])
        self.assertEqual(len(web.calls), 3)

    def test_refresh_pages_are_followed_and_other_content_types(self):
        session, _ = self.session({
            "https://example.com/": response("https://example.com/", '<meta http-equiv=refresh content="0;url=/home">'),
            "https://example.com/home": response("https://example.com/home", "<p>home</p>"),
            "https://example.com/notes.txt": response("https://example.com/notes.txt", "a <b>", kind="text/plain"),
            "https://example.com/p.png": response("https://example.com/p.png", PNG, kind="image/png"),
            "https://example.com/f.pdf": response("https://example.com/f.pdf", b"%PDF-1.7", kind="application/pdf"),
            "https://example.com/gone": response("https://example.com/gone", b"", kind="", status=404)})
        self.assertEqual(session.open("example.com")["url"], "https://example.com/home")
        self.assertEqual(session.open("example.com/notes.txt")["tree"], ["div", {}, [["pre", {}, ["a <b>"]]]])
        image = session.open("example.com/p.png")["tree"][2][0]
        self.assertTrue(image[1]["src"].startswith("data:image/png;base64,"))
        with self.assertRaisesRegex(BrowserError, "application/pdf"):
            session.open("example.com/f.pdf")
        with self.assertRaisesRegex(BrowserError, "404"):
            session.open("example.com/gone")

    def test_images_come_back_as_data_and_other_sites_ask_once_together(self):
        html = ('<img src="/a.png"><img src="/fake.png"><img src="https://one.example.net/b.gif">'
                '<img src="https://two.example.org/c.png">')
        session, web = self.session({
            "https://example.com/": response("https://example.com/", html),
            "https://example.com/a.png": response("https://example.com/a.png", PNG, kind="image/png"),
            "https://example.com/fake.png": response("https://example.com/fake.png", "<html>", kind="image/png"),
            "https://one.example.net/b.gif": response("https://one.example.net/b.gif", GIF, kind="image/gif"),
            "https://two.example.org/c.png": response("https://two.example.org/c.png", PNG, kind="image/png")})
        page = session.open("example.com")
        same = session.images(page["id"], "same")
        self.assertEqual(list(same["images"]), ["https://example.com/a.png"])
        self.assertEqual(same["failed"], 1)
        other = session.images(page["id"], "other")
        self.assertEqual(sorted(other["images"]), ["https://one.example.net/b.gif", "https://two.example.org/c.png"])
        self.assertTrue(other["images"]["https://one.example.net/b.gif"].startswith("data:image/gif;base64,"))
        self.assertEqual(web.calls[-1]["hosts"], frozenset({"one.example.net", "two.example.org"}))
        with self.assertRaises(BrowserError):
            session.images("missing", "same")

    def test_reading_view_toggle_and_text_within_the_attachment_limit(self):
        body = "<nav>Menu</nav><article><h1>Story</h1><p>" + "è" * 4000 + "</p></article>"
        session, _ = self.session({"https://example.com/": response("https://example.com/", body)})
        page = session.open("example.com")
        self.assertTrue(page["reading"] and page["can_read"])
        self.assertNotIn("Menu", text_of(page["tree"]))
        self.assertIn("Menu", text_of(session.view(page["id"], False)["tree"]))
        text = session.text(page["id"], 2000)
        self.assertLessEqual(len(text["text"].encode("utf-8")), 2000)
        self.assertTrue(text["text"].startswith("example.com\nhttps://example.com/\n\n# Story"))
        self.assertTrue(text["text"].endswith("[The page continues; the rest was cut to fit.]"))


class ApiTests(unittest.TestCase):
    def test_errors_come_back_as_plain_json(self):
        with tempfile.TemporaryDirectory() as folder:
            api = browser.BrowserApi()
            api._store = SimpleNamespace(directory=Path(folder))
            api._browser_session = BrowserSession(fetch=FakeWeb({
                "https://example.com/": response("https://example.com/", "<title>Hi</title><p>hello</p>")}))
            self.assertEqual(api.browser_open("javascript:alert(1)"),
                             {"ok": False, "error": "The browser opens web addresses (http and https) only."})
            opened = api.browser_open("example.com", {"typed": True, "language": "it"})
            self.assertTrue(opened["ok"])
            self.assertEqual(api.browser_text(opened["page"]["id"])["text"], "Hi\nhttps://example.com/\n\nhello")
            self.assertEqual(api.browser_images("nope", "same")["ok"], False)
            self.assertEqual(api.browser_forget(), {"ok": True})
            self.assertFalse(api.browser_view(opened["page"]["id"])["ok"])


FRONTEND = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Node {
  constructor(tag, type = 1) {
    Object.assign(this, {tagName: tag.toUpperCase(), localName: tag, nodeType: type, children: [], listeners: {},
                         attributes: {}, dataset: {}, hidden: false, value: '', _text: ''});
    this.classList = {toggle: () => {}};
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(text) { this._text = String(text); this.children = []; }
  append(...items) { for (const item of items) this.children.push(...(item.nodeType === 11 ? item.children.splice(0) : [item])); }
  replaceChildren(...items) { this._text = ''; this.children = []; this.append(...items); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  attachShadow() { this.shadow = new Node('#shadow', 11); this.shadow.nodeType = 1; return this.shadow; }
  querySelectorAll() { return []; }
  focus() {} select() {} blur() {}
  *walk() { yield this; for (const child of this.children) yield* child.walk(); }
}
const nodes = new Map();
const document = {
  activeElement: null,
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Node('div')); return nodes.get(id); },
  createElement: tag => new Node(tag),
  createTextNode: text => { const node = new Node('#text', 3); node._text = String(text); return node; },
  createDocumentFragment: () => new Node('#fragment', 11),
  addEventListener() {},
};
const hostile = ['div', {}, [
  ['script', {}, ['alert(1)']], ['iframe', {src: 'https://evil.example'}, []],
  ['a', {href: 'javascript:alert(1)'}, ['js']], ['a', {href: 'https://example.org/next', onclick: 'x()'}, ['ok']],
  ['img', {src: 'https://tracker.example/pixel.png', alt: 'pic'}, []],
  ['img', {src: 'data:image/svg+xml;base64,PHN2Zz4=', alt: 'svg'}, []],
  ['p', {style: 'color:red', id: 'para', onmouseover: 'x()'}, ['text']]]];
const calls = [];
const window = {addEventListener() {}, i18n: {t: (text, vars = {}) => text.replace(/\{(\w+)\}/g, (_, key) => vars[key]), language: 'it'},
  pywebview: {api: {
    browser_open: async (...args) => { calls.push(args); return {ok: true, page: {id: 'p1', url: 'https://example.com/', host: 'example.com',
      title: 'Example', secure: true, status: 200, cut: false, tree: hostile, reading: false, can_read: false,
      images: {same: 0, other: 0, other_hosts: []}}}; },
  }}};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {window, document, URL, FormData: class {}, File: class {}, Map, Set, Number, String, Array, Object, Boolean, Error});
(async () => {
  await window.browserui.open('example.com');
  assert.equal(JSON.stringify(calls[0]), JSON.stringify(['example.com', {typed: true, reading: true, referrer: null, language: 'it'}]));
  const page = nodes.get('browser-page').shadow;
  const [sheet, ...all] = [...page.walk()].slice(1);
  assert.equal(sheet.localName + ' ' + sheet.href, 'link browser-page.css');
  assert.equal(all.filter(node => ['script', 'iframe'].includes(node.localName)).length, 0);
  assert.ok(!all.some(node => 'href' in node || 'onclick' in node || node.attributes.style || node.attributes.onmouseover));
  const links = all.filter(node => node.localName === 'a');
  assert.deepEqual(links.map(link => link.dataset.href), [undefined, 'https://example.org/next']);
  assert.ok(!all.some(node => node.localName === 'img'));  // neither image is data the window may show
  assert.deepEqual(all.filter(node => node.className === 'image-placeholder').map(node => node.textContent), ['pic', 'svg']);
  assert.equal(all.find(node => node.localName === 'p').id, 'para');
  assert.equal(nodes.get('browser-address').value, 'https://example.com/');
  assert.equal(nodes.get('browser-lock').hidden, false);
  process.stdout.write('browser frontend ok\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


class FrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the frontend test")
    def test_pages_render_from_the_allowlist_without_web_addresses(self):
        with tempfile.TemporaryDirectory() as folder:
            harness = Path(folder) / "harness.js"
            harness.write_text(FRONTEND, encoding="utf-8")
            script = Path(__file__).parent / "webui" / "static" / "browser.js"
            result = subprocess.run(["node", str(harness), str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("browser frontend ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
