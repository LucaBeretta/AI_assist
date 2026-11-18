/* Small offline renderer: untrusted input only becomes text nodes and safe attributes. */
"use strict";

(function () {
  const node = (tag, text) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  };
  const plain = text => document.createTextNode(text);
  const listMarker = line => /^( *)([-+*]|\d+[.)])\s+(.*)$/.exec(line);
  const fence = line => /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  const heading = line => /^ {0,3}(#{1,6})\s+(.*)$/.exec(line);
  const rule = line => /^(?:-{3,}|\*{3,}|_{3,})$/.test(line.trim().replace(/ /g, ""));
  const quote = line => /^ {0,3}> ?(.*)$/.exec(line);

  function inline(text, depth = 0, allowLinks = true) {
    const out = document.createDocumentFragment();
    if (depth > 16) { out.append(plain(text)); return out; }
    let buffer = "";
    const flush = () => { if (buffer) { out.append(plain(buffer)); buffer = ""; } };
    for (let i = 0; i < text.length;) {
      if (text[i] === "\\" && i + 1 < text.length) { buffer += text[i + 1]; i += 2; continue; }
      if (text[i] === "`") {
        const ticks = /^`+/.exec(text.slice(i))[0];
        const end = text.indexOf(ticks, i + ticks.length);
        if (end !== -1) {
          flush(); out.append(node("code", text.slice(i + ticks.length, end))); i = end + ticks.length; continue;
        }
        buffer += ticks; i += ticks.length; continue;
      }
      if (allowLinks && text[i] === "[") {
        const labelEnd = text.indexOf("](", i + 1);
        if (labelEnd !== -1) {
          let end = labelEnd + 2, balance = 1;
          for (; end < text.length && balance; end++) {
            if (text[end] === "\\") { end++; continue; }
            if (text[end] === "(") balance++;
            if (text[end] === ")") balance--;
          }
          if (!balance) {
            let href = text.slice(labelEnd + 2, end - 1).trim();
            if (href.startsWith("<") && href.endsWith(">")) href = href.slice(1, -1);
            flush();
            if (/^(https?:\/\/|mailto:)/i.test(href) && !/[\u0000-\u0020\u007f]/.test(href)) {
              const link = node("a");
              link.setAttribute("href", href);
              link.setAttribute("target", "_blank");
              link.setAttribute("rel", "noopener noreferrer");
              link.append(inline(text.slice(i + 1, labelEnd), depth + 1, false));
              out.append(link);
            } else out.append(plain(text.slice(i, end)));
            i = end; continue;
          }
          allowLinks = false;
        } else allowLinks = false;
      }
      const marker = text.startsWith("**", i) ? "**" : text.startsWith("__", i) ? "__" : "*_".includes(text[i]) ? text[i] : null;
      if (marker && !(marker.includes("_") && /\w/.test(text[i - 1] || ""))) {
        let end = text.indexOf(marker, i + marker.length);
        while (end !== -1 && text[end - 1] === "\\") end = text.indexOf(marker, end + marker.length);
        if (end > i + marker.length) {
          flush();
          const emphasis = node(marker.length === 2 ? "strong" : "em");
          emphasis.append(inline(text.slice(i + marker.length, end), depth + 1, allowLinks));
          out.append(emphasis); i = end + marker.length; continue;
        }
      }
      buffer += text[i++];
    }
    flush();
    return out;
  }

  const aliases = {py:"python", js:"javascript", ts:"javascript", typescript:"javascript", sh:"bash", shell:"bash", ps1:"powershell", ps:"powershell", htm:"html"};
  const keywords = {
    python:"and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield",
    javascript:"async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while yield interface type implements public private readonly",
    json:"true false null",
    bash:"if then else elif fi for in do done while case esac function return export local echo printf",
    powershell:"param function filter begin process end if else elseif foreach for while do switch return break continue try catch finally throw trap true false null",
    css:"important inherit initial unset none auto block flex grid relative absolute fixed solid",
    html:""
  };

  function highlight(code, language) {
    const out = document.createDocumentFragment();
    const lang = Object.prototype.hasOwnProperty.call(aliases, language) ? aliases[language] : language;
    if (!Object.prototype.hasOwnProperty.call(keywords, lang) || code.length > 200000) { out.append(plain(code)); return out; }
    const words = new Set(keywords[lang].split(" "));
    const token = (text, type) => out.append(type ? Object.assign(node("span", text), {className:"tok-" + type}) : plain(text));
    for (let i = 0; i < code.length;) {
      const rest = code.slice(i);
      let match, type;
      const hashComments = ["python", "bash", "powershell"].includes(lang);
      const slashComments = lang === "javascript";
      if ((hashComments && rest[0] === "#") || (slashComments && rest.startsWith("//"))) {
        match = /^[^\n]*/.exec(rest); type = "com";
      } else if ((["javascript", "css"].includes(lang) && rest.startsWith("/*")) || (lang === "html" && rest.startsWith("<!--"))) {
        const close = lang === "html" ? "-->" : "*/";
        const end = code.indexOf(close, i + 2);
        match = [end === -1 ? rest : code.slice(i, end + close.length)]; type = "com";
      } else if (/^["'`]/.test(rest)) {
        match = /^(?:"(?:\\[\s\S]|[^"\\])*"?|'(?:\\[\s\S]|[^'\\])*'?|`(?:\\[\s\S]|[^`\\])*`?)/.exec(rest); type = "str";
      } else if (lang === "html" && (match = /^<\/?[A-Za-z][^<>]*>/.exec(rest))) {
        type = "kw";
      } else if ((match = /^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest))) {
        type = "num";
      } else if ((match = /^[A-Za-z_$][\w$-]*/.exec(rest))) {
        type = words.has(lang === "powershell" ? match[0].toLowerCase() : match[0]) ? "kw" : null;
      } else match = [code[i]];
      token(match[0], type);
      i += match[0].length;
    }
    return out;
  }

  function codeBlock(text, info) {
    const wrapper = node("div");
    const actions = node("div"); actions.className = "code-actions";
    const language = (/^[\w+-]{1,40}$/.test(info) ? info.toLowerCase() : "text");
    for (const [action, label] of [["copy", "Copy"], ["save", "Save as…"], ...(["html", "htm"].includes(language) ? [["preview", "Preview"]] : [])]) {
      const button = node("button", label); button.type = "button"; button.disabled = true;
      button.setAttribute("data-action", action); actions.append(button);
    }
    const pre = node("pre"), code = node("code");
    code.className = "lang-" + language;
    code.append(highlight(text, language)); pre.append(code); wrapper.append(actions, pre);
    return wrapper;
  }

  function cells(line) {
    const result = []; let current = "", ticks = false;
    const source = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    for (let i = 0; i < source.length; i++) {
      if (source[i] === "\\" && source[i + 1] === "|") { current += "|"; i++; continue; }
      if (source[i] === "`") ticks = !ticks;
      if (source[i] === "|" && !ticks) { result.push(current.trim()); current = ""; }
      else current += source[i];
    }
    result.push(current.trim()); return result;
  }

  function tableStart(lines, i) {
    if (i + 1 >= lines.length || !lines[i].includes("|")) return false;
    const header = cells(lines[i]), divider = cells(lines[i + 1]);
    return header.length === divider.length && divider.every(value => /^:?-{3,}:?$/.test(value));
  }

  function blocks(lines, depth = 0) {
    const out = document.createDocumentFragment();
    if (depth > 32) { out.append(node("p", lines.join("\n"))); return out; }
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const open = fence(line);
      if (open) {
        const body = []; i++;
        while (i < lines.length) {
          const close = /^ {0,3}([`~]+)\s*$/.exec(lines[i]);
          if (close && close[1][0] === open[1][0] && close[1].length >= open[1].length && [...close[1]].every(c => c === open[1][0])) { i++; break; }
          body.push(lines[i++]);
        }
        out.append(codeBlock(body.join("\n"), open[2].trim().split(/\s+/)[0])); continue;
      }
      const title = heading(line);
      if (title) { const h = node("h" + title[1].length); h.append(inline(title[2].replace(/\s+#+\s*$/, ""))); out.append(h); i++; continue; }
      if (rule(line)) { out.append(node("hr")); i++; continue; }
      if (quote(line)) {
        const body = [];
        while (i < lines.length && quote(lines[i])) body.push(quote(lines[i++])[1]);
        const q = node("blockquote"); q.append(blocks(body, depth + 1)); out.append(q); continue;
      }
      const marker = listMarker(line);
      if (marker) {
        const base = marker[1].length, ordered = /^\d/.test(marker[2]);
        const list = node(ordered ? "ol" : "ul");
        while (i < lines.length) {
          const item = listMarker(lines[i]);
          if (!item || item[1].length !== base || /^\d/.test(item[2]) !== ordered) break;
          const body = [item[3]]; i++;
          while (i < lines.length) {
            if (lines[i].trim() && (/^ */.exec(lines[i])[0].length <= base)) break;
            body.push(lines[i].slice(Math.min(base + 2, /^ */.exec(lines[i])[0].length))); i++;
          }
          const li = node("li"); li.append(blocks(body, depth + 1)); list.append(li);
        }
        out.append(list); continue;
      }
      if (tableStart(lines, i)) {
        const header = cells(lines[i]), table = node("table"), head = node("thead"), row = node("tr");
        for (const value of header) { const th = node("th"); th.append(inline(value)); row.append(th); }
        head.append(row); table.append(head); i += 2;
        const body = node("tbody");
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          const values = cells(lines[i++]), tr = node("tr");
          for (let col = 0; col < header.length; col++) { const td = node("td"); td.append(inline(values[col] || "")); tr.append(td); }
          body.append(tr);
        }
        table.append(body); out.append(table); continue;
      }
      const paragraph = [line]; i++;
      while (i < lines.length && lines[i].trim() && !fence(lines[i]) && !heading(lines[i]) && !rule(lines[i]) && !quote(lines[i]) && !listMarker(lines[i]) && !tableStart(lines, i)) paragraph.push(lines[i++]);
      const p = node("p"); p.append(inline(paragraph.join("\n"))); out.append(p);
    }
    return out;
  }

  window.renderMarkdown = function renderMarkdown(text) {
    return blocks(String(text).replace(/\r\n?/g, "\n").split("\n"));
  };
})();
