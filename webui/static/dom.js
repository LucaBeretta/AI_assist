// @ts-check
/**
 * A tiny element builder. Text always becomes text nodes; there is no way to
 * pass HTML, and event handlers are attached with addEventListener (the page's
 * Content-Security-Policy forbids inline handlers anyway).
 */

/**
 * h("button", {class: "x", onClick: fn, "aria-label": "Close"}, "text", child)
 * @param {string} tag
 * @param {Record<string, any> | null} [attributes]
 * @param {...any} children strings, nodes, arrays, or null/false (skipped)
 * @returns {HTMLElement}
 */
export function h(tag, attributes, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") /** @type {any} */ (node).value = value;
    else if (key === "checked") /** @type {any} */ (node).checked = Boolean(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}

/** @param {Node} node @param {any[]} children */
export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  return node;
}

/** Replace a node's children. @param {Node} node @param {...any} children */
export function replace(node, ...children) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return append(node, children);
}

const ICONS = {
  menu: "M4 6h16M4 12h16M4 18h16",
  plus: "M12 5v14M5 12h14",
  chat: "M5 5h14v10H9l-4 4z",
  folder: "M3 7h6l2 2h10v10H3z",
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .2-1.2-1.6.4-2-3-1-1 1.8h-2L10 2.6 7 3.6l.4 2L6.2 7.2 4.2 7l-1 3 2 1v2l-2 1 1 3 2-.2 1.2 1.6-.4 2 3 1 1-1.8h2l1 1.8 3-1-.4-2 1.2-1.6 2 .2 1-3-2-1z",
  panel: "M4 5h16v14H4zM15 5v14",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  send: "M4 12l16-8-6 16-2-7z",
  stop: "M7 7h10v10H7z",
  close: "M6 6l12 12M18 6L6 18",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z",
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  copy: "M9 9h11v11H9zM5 15V4h11",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  memory: "M5 4h14v16H5zM9 8h6M9 12h6M9 16h3",
};

/**
 * An inline SVG icon, decorative unless given a label.
 * @param {keyof typeof ICONS} name
 * @param {string} [label]
 */
export function icon(name, label) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); }
  else svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name] || "");
  svg.appendChild(path);
  return svg;
}

/** Read a per-browser preference; storage can be unavailable (private windows). @param {string} key */
export function loadPreference(key, fallback = "") {
  try { return window.localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

/** @param {string} key @param {string} value */
export function savePreference(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* Preferences are a convenience only. */ }
}
