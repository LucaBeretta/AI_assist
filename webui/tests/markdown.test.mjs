// Run with: node --test webui/tests
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseMarkdown, parseInline, renderMarkdown } from "../static/markdown.js";

/** A minimal document: records what the renderer builds; it has no HTML parser at all. */
function fakeDocument() {
  const make = (tag) => ({
    tagName: tag, children: [], attributes: {}, className: "", listeners: {},
    appendChild(child) { this.children.push(child); return child; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    get text() { return this.children.map((c) => (typeof c.data === "string" ? c.data : c.text)).join(""); },
  });
  return {
    createElement: make,
    createTextNode: (data) => ({ data }),
    createDocumentFragment: () => make("#fragment"),
  };
}

const find = (node, tag) => (node.tagName === tag ? [node] : []).concat(...(node.children || []).map((c) => find(c, tag)));

test("code fences keep their exact contents, language and filename", () => {
  const [block] = parseMarkdown("```python title=app.py\nprint('<b>')\n  x = 1\n```");
  assert.deepEqual(block, { type: "code", language: "python", filename: "app.py", code: "print('<b>')\n  x = 1\n", complete: true });
});

test("an unfinished fence is shown as incomplete while streaming", () => {
  const [block] = parseMarkdown("~~~js\nlet a");
  assert.equal(block.complete, false);
  assert.equal(block.code, "let a\n");
});

test("a longer closing fence closes; an inner shorter one does not", () => {
  const blocks = parseMarkdown("````\n```\ninner\n```\n````\nafter");
  assert.equal(blocks[0].code, "```\ninner\n```\n");
  assert.equal(blocks[1].type, "paragraph");
});

test("headings, rules, quotes, lists and task lists", () => {
  const blocks = parseMarkdown("# Title\n\n---\n> quoted\n\n- a\n  - b\n1. one\n- [x] done\n- [ ] todo");
  assert.deepEqual(blocks.map((b) => b.type), ["heading", "rule", "quote", "list", "list", "list"]);
  assert.equal(blocks[3].items[1].indent, 1);
  assert.equal(blocks[4].ordered, true);
  assert.deepEqual(blocks[5].items.map((item) => item.task), [true, false]);
});

test("tables with alignment", () => {
  const [table] = parseMarkdown("| a | b |\n|:--|--:|\n| 1 | 2 |\n| x \\| y | z |");
  assert.equal(table.type, "table");
  assert.deepEqual(table.align, ["left", "right"]);
  assert.equal(table.rows.length, 2);
  assert.deepEqual(table.rows[1][0], [{ type: "text", text: "x | y" }]);
});

test("inline emphasis, code and links; snake_case stays intact", () => {
  assert.deepEqual(parseInline("**bold** and `co*de*`"), [
    { type: "strong", children: [{ type: "text", text: "bold" }] }, { type: "text", text: " and " }, { type: "code", text: "co*de*" }]);
  assert.deepEqual(parseInline("call my_long_name now"), [{ type: "text", text: "call my_long_name now" }]);
  assert.deepEqual(parseInline("[docs](https://example.com) ![logo](x.png)"), [
    { type: "link", text: "docs", url: "https://example.com", image: false }, { type: "text", text: " " },
    { type: "link", text: "logo", url: "x.png", image: true }]);
  assert.deepEqual(parseInline("\\*not em\\*"), [{ type: "text", text: "*not em*" }]);
});

test("deeply nested emphasis is bounded", () => {
  const text = "**".repeat(200) + "x" + "**".repeat(200);
  assert.doesNotThrow(() => parseInline(text));
});

test("HTML and scripts in answers stay text", () => {
  const doc = fakeDocument();
  const fragment = renderMarkdown("<img src=x onerror=alert(1)>\n\n<script>alert(1)</script>", doc);
  const tags = find(fragment, "img").concat(find(fragment, "script"));
  assert.equal(tags.length, 0);
  assert.equal(fragment.children[0].text, "<img src=x onerror=alert(1)>");
});

test("links are shown with their address, never as clickable links", () => {
  const doc = fakeDocument();
  const fragment = renderMarkdown("See [the site](https://example.com/x).", doc);
  assert.equal(find(fragment, "a").length, 0);
  assert.equal(fragment.children[0].text, "See the site (https://example.com/x).");
});

test("complete code blocks get a copy button; incomplete ones do not", () => {
  const doc = fakeDocument();
  const copied = [];
  const fragment = renderMarkdown("```sh\nls\n```\n```py\nopen", doc, { onCopy: (code) => copied.push(code) });
  const buttons = find(fragment, "button");
  assert.equal(buttons.length, 1);
  buttons[0].listeners.click();
  assert.deepEqual(copied, ["ls\n"]);
  assert.match(fragment.children[1].className, /is-incomplete/);
});

test("answer headings never outrank the page heading", () => {
  const fragment = renderMarkdown("# Top\n###### Small", fakeDocument());
  assert.deepEqual(fragment.children.map((c) => c.tagName), ["h2", "h6"]);
});
