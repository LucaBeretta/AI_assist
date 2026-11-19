// @ts-check
/**
 * Markdown for model answers, safe by construction.
 *
 * parseMarkdown() turns text into plain data; renderMarkdown() builds DOM nodes
 * with createElement/createTextNode only. Model output is never parsed as
 * HTML, so it cannot inject markup or scripts. Links are shown as their text
 * followed by the address, not as clickable links: following them would leave
 * this computer outside the internet gate (the desktop app does the same).
 */

const FENCE = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
const RULE = /^\s*(?:-\s*){3,}$|^\s*(?:\*\s*){3,}$|^\s*(?:_\s*){3,}$/;
const LIST_ITEM = /^(\s*)([-+*]|\d{1,9}[.)])\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const TASK = /^\[( |x|X)\]\s+/;
const LINK = /^(!?)\[([^\]\n]*)\]\(([^()\s]+)(?:\s+"[^"\n]*")?\)/;
const MAX_DEPTH = 6;

/** @typedef {{type: "text", text: string} | {type: "code", text: string} | {type: "strong" | "em" | "del", children: Inline[]} | {type: "link", text: string, url: string, image: boolean} | {type: "break"}} Inline */
/** @typedef {{type: "code", language: string, filename: string, code: string, complete: boolean}
 *   | {type: "heading", level: number, inlines: Inline[]}
 *   | {type: "paragraph", lines: Inline[][]}
 *   | {type: "quote", lines: Inline[][]}
 *   | {type: "list", ordered: boolean, items: {inlines: Inline[], indent: number, task: boolean | null, marker: string}[]}
 *   | {type: "table", header: Inline[][], align: string[], rows: Inline[][][]}
 *   | {type: "rule"}} Block */

/** @param {string} info */
function fenceLabel(info) {
  const tokens = info.trim().split(/\s+/).filter(Boolean);
  let language = (tokens[0] || "").toLowerCase();
  let filename = "";
  const named = /(?:^|\s)(?:filename|file|title)\s*=\s*(?:"([^"]+)"|'([^']+)'|(\S+))/i.exec(info);
  if (named) filename = named[1] || named[2] || named[3] || "";
  if (/^(filename|file|title)=/.test(language)) language = "";
  else if (!filename && /[./\\]/.test(language)) { filename = tokens[0]; language = ""; }
  else if (!filename && tokens[1] && /[./\\]/.test(tokens[1])) filename = tokens[1].replace(/^[`"']|[`"']$/g, "");
  return { language, filename };
}

/** @param {string} line */
function splitRow(line) {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  return text.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

/**
 * Split text into blocks. An unclosed final code fence is returned with
 * complete: false, so a streaming answer can be shown as it arrives.
 * @param {string} text
 * @returns {Block[]}
 */
export function parseMarkdown(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  /** @type {Block[]} */
  const blocks = [];
  let i = 0;
  /** @type {Inline[][]} */
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", lines: paragraph });
    paragraph = [];
  };
  while (i < lines.length) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence && !(fence[1][0] === "`" && fence[2].includes("`"))) {
      flush();
      const opening = fence[1];
      const { language, filename } = fenceLabel(fence[2]);
      const code = [];
      let complete = false;
      i += 1;
      while (i < lines.length) {
        const closing = FENCE.exec(lines[i]);
        if (closing && closing[1][0] === opening[0] && closing[1].length >= opening.length && !closing[2].trim()) {
          complete = true;
          i += 1;
          break;
        }
        code.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "code", language, filename, code: code.join("\n") + (code.length ? "\n" : ""), complete });
      continue;
    }
    if (!line.trim()) { flush(); i += 1; continue; }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ type: "heading", level: heading[1].length, inlines: parseInline(heading[2]) });
      i += 1;
      continue;
    }
    if (RULE.test(line)) { flush(); blocks.push({ type: "rule" }); i += 1; continue; }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      flush();
      const header = splitRow(line);
      const align = splitRow(lines[i + 1]).map((cell) =>
        cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "left");
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i]).map(parseInline));
        i += 1;
      }
      blocks.push({ type: "table", header: header.map(parseInline), align, rows });
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flush();
      const first = /** @type {RegExpExecArray} */ (LIST_ITEM.exec(line));
      const ordered = /\d/.test(first[2]);
      const items = [];
      while (i < lines.length) {
        const item = LIST_ITEM.exec(lines[i]);
        if (!item) {
          // A continuation line indented under the previous item joins it.
          if (items.length && lines[i].trim() && /^\s{2,}/.test(lines[i])) {
            items[items.length - 1].inlines.push({ type: "break" }, ...parseInline(lines[i].trim()));
            i += 1;
            continue;
          }
          break;
        }
        // A top-level item of the other kind (bullets vs numbers) starts a new list.
        if (items.length && !item[1].length && /\d/.test(item[2]) !== ordered) break;
        let content = item[3];
        let task = null;
        const box = TASK.exec(content);
        if (box) { task = box[1] !== " "; content = content.slice(box[0].length); }
        const indent = Math.min(4, Math.floor(item[1].replace(/\t/g, "  ").length / 2));
        items.push({ inlines: parseInline(content), indent, task, marker: /\d/.test(item[2]) ? item[2] : "" });
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    const quote = QUOTE.exec(line);
    if (quote) {
      flush();
      const quoted = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        quoted.push(parseInline(/** @type {RegExpExecArray} */ (QUOTE.exec(lines[i]))[1]));
        i += 1;
      }
      blocks.push({ type: "quote", lines: quoted });
      continue;
    }
    paragraph.push(parseInline(line));
    i += 1;
  }
  flush();
  return blocks;
}

/**
 * @param {string} text
 * @param {number} [depth]
 * @returns {Inline[]}
 */
export function parseInline(text, depth = 0) {
  /** @type {Inline[]} */
  const out = [];
  let plain = "";
  const push = () => { if (plain) out.push({ type: "text", text: plain }); plain = ""; };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === "\\" && i + 1 < text.length && /[\\`*_~[\]()#+\-.!|>]/.test(text[i + 1])) {
      plain += text[i + 1];
      i += 2;
      continue;
    }
    if (ch === "`") {
      const run = (/^`+/.exec(text.slice(i)) || ["`"])[0];
      const end = text.indexOf(run, i + run.length);
      if (end !== -1) {
        push();
        out.push({ type: "code", text: text.slice(i + run.length, end).replace(/^ (.*) $/, "$1") });
        i = end + run.length;
        continue;
      }
      plain += run;
      i += run.length;
      continue;
    }
    if (ch === "[" || (ch === "!" && text[i + 1] === "[")) {
      const link = LINK.exec(text.slice(i));
      if (link) {
        push();
        out.push({ type: "link", text: link[2], url: link[3], image: link[1] === "!" });
        i += link[0].length;
        continue;
      }
    }
    if (depth < MAX_DEPTH) {
      const pair = text.startsWith("**", i) ? "**" : text.startsWith("__", i) ? "__" : text.startsWith("~~", i) ? "~~" : null;
      if (pair) {
        const end = text.indexOf(pair, i + 2);
        if (end > i + 2) {
          push();
          out.push({ type: pair === "~~" ? "del" : "strong", children: parseInline(text.slice(i + 2, end), depth + 1) });
          i = end + 2;
          continue;
        }
      }
      if ((ch === "*" || ch === "_") && text[i + 1] && text[i + 1] !== " " && text[i + 1] !== ch) {
        // "_" only at word boundaries, so snake_case names stay intact.
        const boundary = ch === "*" || i === 0 || !/[\p{L}\p{N}]/u.test(text[i - 1]);
        let end = text.indexOf(ch, i + 1);
        while (end !== -1 && ch === "_" && end + 1 < text.length && /[\p{L}\p{N}]/u.test(text[end + 1])) {
          end = text.indexOf(ch, end + 1);
        }
        if (boundary && end > i + 1 && text[end - 1] !== " ") {
          push();
          out.push({ type: "em", children: parseInline(text.slice(i + 1, end), depth + 1) });
          i = end + 1;
          continue;
        }
      }
    }
    plain += ch;
    i += 1;
  }
  push();
  return out;
}

/**
 * @typedef {{createElement(tag: string): any, createTextNode(text: string): any, createDocumentFragment(): any}} DocumentLike
 * @typedef {{onCopy?: (code: string, button: any) => void, onUse?: (code: string) => void}} RenderOptions
 */

/**
 * @param {DocumentLike} doc
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 */
function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.appendChild(doc.createTextNode(text));
  return node;
}

/**
 * @param {DocumentLike} doc
 * @param {any} parent
 * @param {Inline[]} inlines
 */
function appendInlines(doc, parent, inlines) {
  for (const item of inlines) {
    if (item.type === "text") parent.appendChild(doc.createTextNode(item.text));
    else if (item.type === "code") parent.appendChild(el(doc, "code", "inline-code", item.text));
    else if (item.type === "break") parent.appendChild(doc.createElement("br"));
    else if (item.type === "link") {
      const span = el(doc, "span", "md-link", item.image ? `[image: ${item.text || "untitled"}]` : item.text || item.url);
      if (item.url !== item.text) span.appendChild(el(doc, "span", "md-link-url", ` (${item.url})`));
      parent.appendChild(span);
    } else {
      const node = doc.createElement(item.type === "strong" ? "strong" : item.type === "em" ? "em" : "del");
      appendInlines(doc, node, item.children);
      parent.appendChild(node);
    }
  }
}

/**
 * @param {DocumentLike} doc
 * @param {any} parent
 * @param {Inline[][]} lines
 */
function appendLines(doc, parent, lines) {
  lines.forEach((line, index) => {
    if (index) parent.appendChild(doc.createElement("br"));
    appendInlines(doc, parent, line);
  });
}

/**
 * Render Markdown into a DocumentFragment.
 * @param {string} text
 * @param {DocumentLike} doc
 * @param {RenderOptions} [options]
 */
export function renderMarkdown(text, doc, options = {}) {
  const fragment = doc.createDocumentFragment();
  for (const block of parseMarkdown(text)) {
    if (block.type === "code") {
      const figure = el(doc, "figure", "code-block" + (block.complete ? "" : " is-incomplete"));
      const header = el(doc, "figcaption", "code-header");
      header.appendChild(el(doc, "span", "code-language", block.filename || block.language || "code"));
      if (!block.complete) header.appendChild(el(doc, "span", "code-state", "writing…"));
      if (options.onCopy && block.complete) {
        const copy = el(doc, "button", "button button-ghost button-small", "Copy");
        copy.setAttribute("type", "button");
        copy.setAttribute("aria-label", `Copy ${block.filename || block.language || "code"}`);
        copy.addEventListener("click", () => options.onCopy?.(block.code, copy));
        header.appendChild(copy);
      }
      figure.appendChild(header);
      const pre = el(doc, "pre", "code-body");
      pre.setAttribute("tabindex", "0");
      const code = el(doc, "code", block.language ? `language-${block.language.replace(/[^a-z0-9_+-]/g, "")}` : "", block.code);
      pre.appendChild(code);
      figure.appendChild(pre);
      fragment.appendChild(figure);
    } else if (block.type === "heading") {
      const heading = el(doc, `h${Math.min(6, block.level + 1)}`, "md-heading");
      appendInlines(doc, heading, block.inlines);
      fragment.appendChild(heading);
    } else if (block.type === "paragraph") {
      const p = el(doc, "p");
      appendLines(doc, p, block.lines);
      fragment.appendChild(p);
    } else if (block.type === "quote") {
      const quote = el(doc, "blockquote");
      appendLines(doc, quote, block.lines);
      fragment.appendChild(quote);
    } else if (block.type === "rule") {
      fragment.appendChild(doc.createElement("hr"));
    } else if (block.type === "list") {
      const list = el(doc, block.ordered ? "ol" : "ul", "md-list");
      for (const item of block.items) {
        const li = el(doc, "li", item.indent ? `indent-${item.indent}` : "");
        if (item.marker) li.setAttribute("data-marker", item.marker);
        if (item.task !== null) {
          li.className = (li.className + " task").trim();
          li.appendChild(el(doc, "span", "task-box", item.task ? "☑ " : "☐ "));
        }
        appendInlines(doc, li, item.inlines);
        list.appendChild(li);
      }
      fragment.appendChild(list);
    } else if (block.type === "table") {
      const wrap = el(doc, "div", "table-wrap");
      wrap.setAttribute("tabindex", "0");
      const table = doc.createElement("table");
      const head = doc.createElement("thead");
      const headRow = doc.createElement("tr");
      block.header.forEach((cell, index) => {
        const th = el(doc, "th", `align-${block.align[index] || "left"}`);
        th.setAttribute("scope", "col");
        appendInlines(doc, th, cell);
        headRow.appendChild(th);
      });
      head.appendChild(headRow);
      table.appendChild(head);
      const body = doc.createElement("tbody");
      for (const row of block.rows) {
        const tr = doc.createElement("tr");
        block.header.forEach((_, index) => {
          const td = el(doc, "td", `align-${block.align[index] || "left"}`);
          appendInlines(doc, td, row[index] || []);
          tr.appendChild(td);
        });
        body.appendChild(tr);
      }
      table.appendChild(body);
      wrap.appendChild(table);
      fragment.appendChild(wrap);
    }
  }
  return fragment;
}

/** Plain text of an answer's code blocks, for copy and export. @param {string} text */
export function codeBlocks(text) {
  return parseMarkdown(text).filter((block) => block.type === "code");
}
