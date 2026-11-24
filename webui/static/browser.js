/* Browser view: tabs, history and a reading view of web pages.
   Python fetches every page through the internet gate and sends back a tree of allowlisted elements.
   This file never gives the window a web address to load: links keep theirs in data-href, images
   arrive as data, and forms are read here and opened as addresses. */
"use strict";

window.browserui = (() => {
  const el = id => document.getElementById(id);
  const t = (message, vars) => window.i18n?.t?.(message, vars) ?? message;
  const gate = text => window.i18n?.gate?.(text) || text;
  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(gate(result?.error) || t("Invalid API response"));
    return result;
  }

  const TAGS = new Set(`a abbr address article aside b bdi blockquote br button caption cite code dd del details dfn
    div dl dt em figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr i img input ins kbd li main mark nav ol p pre
    q s samp section small span strong sub summary sup table tbody td tfoot th thead time tr u ul var`.split(/\s+/));
  const IMAGE_DATA = /^data:image\/(?:png|jpeg|gif|webp|avif|bmp|x-icon);base64,[A-Za-z0-9+/=\s]+$/;
  const KEEP_PAGES = 12;  // older history entries keep their address and reload when visited

  const tabs = [];
  let active = null;
  let nextTab = 1;
  let shadow = null;
  let page = null;
  let reading = true;
  let scoped = false;

  // ---------- tabs and history ----------
  function makeTab() {
    const tab = {id: nextTab++, entries: [], index: -1, loading: false, pending: "", token: 0, error: "", failed: null};
    tabs.push(tab);
    return tab;
  }
  const entryOf = tab => tab?.entries[tab.index] || null;
  function remember() {
    const entry = entryOf(active);
    if (entry && !el("browser-page").hidden) entry.scroll = el("browser-viewport").scrollTop;
  }
  function select(tab) {
    if (tab !== active) remember();
    active = tab;
    render(true);
  }
  function newTab(address) {
    const tab = makeTab();
    select(tab);
    if (address) go(tab, address); else focusAddress();
    return tab;
  }
  function closeTab(tab) {
    const index = tabs.indexOf(tab);
    if (index < 0) return;
    tab.token++;
    tabs.splice(index, 1);
    if (!tabs.length) {
      // The last tab closed: Python drops its pages and every site approval with them.
      call("browser_forget").catch(() => {});
      newTab();
      return;
    }
    if (tab === active) { active = null; select(tabs[Math.min(index, tabs.length - 1)]); }
    else renderTabs();
  }
  function trim(tab) {
    tab.entries.forEach((entry, position) => {
      if (Math.abs(position - tab.index) > KEEP_PAGES) { entry.page = null; entry.images = new Map(); }
    });
  }

  function fragmentOf(address) {
    const at = String(address).indexOf("#");
    return at > 0 && /^https?:\/\//i.test(address) ? String(address).slice(at + 1) : "";
  }

  async function go(tab, address, {typed = true, replace = false, referrer = null} = {}) {
    address = String(address || "").trim();
    if (!address) return;
    const token = ++tab.token;
    const source = entryOf(tab)?.page;
    if (tab === active) remember();
    Object.assign(tab, {loading: true, pending: address, error: "", failed: null});
    render();
    try {
      const result = await call("browser_open", address, {
        typed, reading, referrer: typed ? null : (referrer || source?.id || null),
        language: window.i18n?.language || "en",
      });
      if (tab.token !== token) return;  // stopped, or another address replaced this one
      const entry = {page: result.page, url: result.page.url, title: result.page.title, scroll: 0,
                     images: new Map(), fragment: fragmentOf(address), shownOther: false};
      if (replace && tab.index >= 0) tab.entries[tab.index] = entry;
      else { tab.entries.splice(tab.index + 1); tab.entries.push(entry); tab.index = tab.entries.length - 1; }
      trim(tab);
      Object.assign(tab, {loading: false, pending: ""});
      if (tab === active) render(true);
      else renderTabs();
      loadImages(tab, entry, "same");
    } catch (error) {
      if (tab.token !== token) return;
      Object.assign(tab, {loading: false, pending: "", error: error.message || String(error), failed: {address, typed, referrer}});
      render(true);
    }
  }
  function stop(tab) {
    if (!tab?.loading) return;
    tab.token++;
    Object.assign(tab, {loading: false, pending: ""});
    render();
  }
  function move(tab, step) {
    if (!tab) return;
    const index = tab.index + step;
    if (index < 0 || index >= tab.entries.length) return;
    remember();
    tab.token++;
    Object.assign(tab, {index, loading: false, pending: "", error: "", failed: null});
    const entry = entryOf(tab);
    if (entry.page) render(true);
    else go(tab, entry.url, {replace: true});
  }
  function reload(tab) {
    const entry = entryOf(tab);
    if (tab?.error && tab.failed) go(tab, tab.failed.address, tab.failed);
    else if (entry) go(tab, entry.url + (entry.fragment ? "#" + entry.fragment : ""), {replace: true});
  }

  // ---------- images ----------
  async function loadImages(tab, entry, which) {
    const count = entry.page?.images?.[which] || 0;
    if (!count || entry.imagesLoading) return;
    entry.imagesLoading = which;
    if (tab === active) renderBar();
    try {
      const result = await call("browser_images", entry.page.id, which);
      for (const [url, data] of Object.entries(result.images || {})) if (IMAGE_DATA.test(data)) entry.images.set(url, data);
      if (which === "other") entry.shownOther = true;
    } catch (error) {
      if (which === "other") entry.notice = error.message || String(error);
    } finally {
      entry.imagesLoading = null;
      if (tab === active && entryOf(tab) === entry) { fillImages(entry); renderBar(); }
    }
  }
  function fillImages(entry) {
    for (const holder of [...(shadow?.querySelectorAll?.(".image-placeholder") || [])]) {
      if (entry.images.has(holder.dataset.src)) {
        holder.replaceWith(image({src: holder.dataset.src, alt: holder.dataset.alt,
                                  width: Number(holder.dataset.width) || 0, height: Number(holder.dataset.height) || 0}, entry));
      }
    }
  }
  function image(attrs, entry) {
    const source = String(attrs.src || "");
    const data = source.startsWith("data:") ? source : entry.images.get(source);
    if (data && IMAGE_DATA.test(data)) {
      const picture = document.createElement("img");
      picture.src = data;
      picture.alt = attrs.alt || "";
      picture.loading = "lazy";
      if (attrs.width > 0) picture.width = attrs.width;
      if (attrs.height > 0) picture.height = attrs.height;
      return picture;
    }
    const holder = document.createElement("span");
    holder.className = "image-placeholder";
    holder.dataset.src = source;
    holder.dataset.alt = attrs.alt || "";
    if (attrs.width) holder.dataset.width = String(attrs.width);
    if (attrs.height) holder.dataset.height = String(attrs.height);
    holder.textContent = attrs.alt || "";
    return holder;
  }

  // ---------- the page ----------
  function build(node, entry, depth = 0) {
    if (typeof node === "string") return document.createTextNode(node);
    if (!Array.isArray(node) || depth > 80) return document.createTextNode("");
    const [tag, attrs = {}, children = []] = node;
    const list = Array.isArray(children) ? children : [];
    if (tag === "img") return image(attrs, entry);
    let item;
    if (!TAGS.has(tag)) item = document.createDocumentFragment();
    else if (tag === "a") {
      item = document.createElement("a");
      if (typeof attrs.href === "string" && /^https?:\/\//i.test(attrs.href)) {
        item.dataset.href = attrs.href;
        if (attrs.hash) item.dataset.hash = String(attrs.hash);
        item.tabIndex = 0;
        item.setAttribute("role", "link");
      }
    } else if (tag === "input") {
      item = document.createElement("input");
      item.type = attrs.type === "hidden" ? "hidden" : attrs.type === "search" ? "search" : "text";
      item.name = String(attrs.name || "");
      item.value = String(attrs.value || "");
      if (attrs.placeholder) item.placeholder = String(attrs.placeholder);
    } else if (tag === "button") {
      item = document.createElement("button");
      item.type = "submit";
      if (attrs.name) { item.name = String(attrs.name); item.value = String(attrs.value || ""); }
    } else if (tag === "form") {
      item = document.createElement("form");
      item.dataset.action = String(attrs.action || "");
      item.noValidate = true;
    } else item = document.createElement(tag);
    if (item.nodeType === 1) {
      if (typeof attrs.id === "string" && scoped) item.id = attrs.id;  // ids stay inside the page's shadow root
      if (["ltr", "rtl", "auto"].includes(attrs.dir)) item.dir = attrs.dir;
      if (typeof attrs.title === "string") item.title = attrs.title;
      if (Number.isInteger(attrs.colspan)) item.colSpan = attrs.colspan;
      if (Number.isInteger(attrs.rowspan)) item.rowSpan = attrs.rowspan;
      if (tag === "ol" && Number.isInteger(attrs.start)) item.start = attrs.start;
      if (tag === "details" && attrs.open === true) item.open = true;
    }
    for (const child of list) item.append(build(child, entry, depth + 1));
    return item;
  }

  function setupPage() {
    if (shadow) return;
    const host = el("browser-page");
    scoped = Boolean(host.attachShadow);
    shadow = scoped ? host.attachShadow({mode: "open"}) : host;
    const sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = "browser-page.css";
    page = document.createElement("article");
    page.className = "page";
    shadow.append(sheet, page);
    const linkOf = target => target?.closest?.("a[data-href]") || null;
    shadow.addEventListener("click", event => {
      const link = linkOf(event.target);
      if (!link) return;
      event.preventDefault();
      follow(link, event.metaKey || event.ctrlKey);
    });
    shadow.addEventListener("auxclick", event => {
      const link = linkOf(event.target);
      if (link && event.button === 1) { event.preventDefault(); follow(link, true); }
    });
    shadow.addEventListener("keydown", event => {
      const link = linkOf(event.target);
      if (link && event.key === "Enter") { event.preventDefault(); follow(link, event.metaKey || event.ctrlKey); }
    });
    shadow.addEventListener("submit", event => { event.preventDefault(); submit(event.target, event.submitter); });
    shadow.addEventListener("mouseover", event => hover(linkOf(event.target)?.dataset.href || ""));
    host.addEventListener("mouseleave", () => hover(""));
  }
  function follow(link, inNewTab) {
    const href = link.dataset.href;
    const source = entryOf(active)?.page;
    if (link.dataset.hash && !inNewTab) { scrollTo(link.dataset.hash); return; }
    if (inNewTab) {
      const tab = makeTab();
      renderTabs();
      go(tab, href, {typed: false, referrer: source?.id});
    } else go(active, href, {typed: false});
  }
  function submit(form, submitter) {
    let url;
    try { url = new URL(form.dataset.action); } catch { return; }
    if (!/^https?:$/.test(url.protocol)) return;
    let data;
    try { data = new FormData(form, submitter); } catch { data = new FormData(form); }
    url.search = "";
    for (const [name, value] of data) if (name && typeof value === "string") url.searchParams.append(name, value);
    go(active, url.href, {typed: false});
  }
  function scrollTo(fragment) {
    if (!fragment || !shadow) return;
    let decoded = fragment;
    try { decoded = decodeURIComponent(fragment); } catch { /* keep it as written */ }
    const target = shadow.getElementById?.(decoded) || shadow.getElementById?.(fragment);
    target?.scrollIntoView?.({block: "start"});
  }
  function hover(href) {
    el("browser-hover").textContent = href;
    el("browser-hover").hidden = !href;
  }

  // ---------- rendering ----------
  function renderTabs() {
    const strip = el("browser-tabs");
    strip.replaceChildren(...tabs.map(tab => {
      const item = document.createElement("div");
      item.className = "browser-tab" + (tab.loading ? " loading" : "");
      item.setAttribute("role", "tab");
      item.setAttribute("aria-selected", tab === active ? "true" : "false");
      const title = document.createElement("button");
      title.type = "button";
      title.className = "browser-tab-title";
      const entry = entryOf(tab);
      title.textContent = tab.loading && !entry ? t("Loading…") : entry?.title || t("New tab");
      title.title = entry?.url || "";
      title.addEventListener("click", () => select(tab));
      const close = document.createElement("button");
      close.type = "button";
      close.className = "browser-tab-close";
      close.textContent = "×";
      close.setAttribute("aria-label", t("Close tab"));
      close.title = t("Close tab");
      close.addEventListener("click", () => closeTab(tab));
      item.append(title, close);
      return item;
    }));
  }
  function renderBar() {
    const entry = entryOf(active);
    const messages = [];
    let images = false;
    if (entry?.page && !active.error) {
      const info = entry.page;
      if (info.status >= 400) messages.push(t("The site answered {v0}.", {v0: info.status}));
      if (info.cut) messages.push(t("This page is very long; only the first part is shown."));
      if (entry.imagesLoading === "other") messages.push(t("Loading images from other sites…"));
      else if (entry.notice) messages.push(entry.notice);
      else if (info.images?.other && !entry.shownOther) {
        const hosts = info.images.other_hosts.join(", ");
        messages.push(info.images.other === 1 ? t("1 image from another site is hidden: {v0}.", {v0: hosts})
          : t("{v0} images from other sites are hidden: {v1}.", {v0: info.images.other, v1: hosts}));
        images = true;
      }
    }
    el("browser-bar-text").textContent = messages.join(" ");
    el("browser-show-images").hidden = !images;
    el("browser-bar").hidden = !messages.length;
  }
  function render(showPage = false) {
    if (!active) return;
    const entry = entryOf(active);
    const info = entry?.page;
    renderTabs();
    el("browser-back").disabled = active.index <= 0;
    el("browser-forward").disabled = active.index >= active.entries.length - 1;
    const loading = active.loading;
    el("browser-reload").setAttribute("aria-label", loading ? t("Stop") : t("Reload"));
    el("browser-reload").title = loading ? t("Stop") : t("Reload");
    el("browser-reload").classList?.toggle?.("is-stop", loading);
    el("browser-progress").hidden = !loading;
    const address = el("browser-address");
    if (document.activeElement !== address || showPage) {
      address.value = loading ? active.pending : active.error ? active.failed?.address || ""
        : info ? info.url + (entry.fragment ? "#" + entry.fragment : "") : "";
    }
    const lock = el("browser-lock");
    lock.hidden = !info;
    lock.classList?.toggle?.("insecure", Boolean(info && !info.secure));
    lock.title = info ? (info.secure ? t("Encrypted connection (https)") : t("Not encrypted (http): the network can read this page")) : "";
    el("browser-reading").disabled = !info?.can_read;
    el("browser-reading").setAttribute("aria-pressed", info?.reading ? "true" : "false");
    el("browser-ask").disabled = !info;
    renderBar();
    if (!showPage) return;
    el("browser-home").hidden = Boolean(info || active.error || active.loading);
    el("browser-error").hidden = !active.error;
    el("browser-error-text").textContent = active.error;
    el("browser-page").hidden = !info || Boolean(active.error);
    hover("");
    if (!info || active.error) return;
    setupPage();
    page.className = "page" + (info.reading ? " reading" : " full");
    const children = Array.isArray(info.tree?.[2]) ? info.tree[2] : [];
    const fragment = document.createDocumentFragment();
    for (const child of children) fragment.append(build(child, entry));
    page.replaceChildren(fragment);
    const viewport = el("browser-viewport");
    if (entry.scroll) viewport.scrollTop = entry.scroll;
    else if (entry.fragment) scrollTo(entry.fragment);
    else viewport.scrollTop = 0;
  }

  // ---------- toolbar ----------
  function focusAddress() {
    const address = el("browser-address");
    address.focus?.();
    address.select?.();
  }
  async function toggleReading() {
    const entry = entryOf(active);
    if (!entry?.page?.can_read) return;
    try {
      const result = await call("browser_view", entry.page.id, !entry.page.reading);
      reading = result.page.reading;
      entry.page = result.page;
      entry.scroll = 0;
      render(true);
    } catch (error) { active.error = error.message || String(error); render(true); }
  }
  function fileName(title) {
    const name = String(title || "").replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 80);
    return name.replace(/[. ]+$/, "") || t("Web page");
  }
  async function ask() {
    const entry = entryOf(active);
    if (!entry?.page || !window.app?.newChat || !window.app?.attachFiles) return;
    const button = el("browser-ask");
    button.disabled = true;
    try {
      const result = await call("browser_text", entry.page.id);
      await window.app.newChat();
      await window.app.attachFiles([new File([result.text], fileName(result.title) + ".txt", {type: "text/plain"})]);
      window.app.showView("chat");
      window.app.setPrompt(t("Summarize this page in a few short points."));
    } catch (error) {
      entry.notice = error.message || String(error);
      renderBar();
    } finally { button.disabled = !entryOf(active)?.page; }
  }

  el("browser-toolbar").addEventListener("submit", event => {
    event.preventDefault();
    const value = el("browser-address").value.trim();
    if (!value) return;
    if (!active) newTab(value); else go(active, value);
    el("browser-address").blur?.();
  });
  el("browser-address").addEventListener("focus", () => el("browser-address").select?.());
  el("browser-address").addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); render(); el("browser-address").blur?.(); }
  });
  el("browser-back").addEventListener("click", () => move(active, -1));
  el("browser-forward").addEventListener("click", () => move(active, 1));
  el("browser-reload").addEventListener("click", () => active?.loading ? stop(active) : reload(active));
  el("browser-retry").addEventListener("click", () => reload(active));
  el("browser-new-tab").addEventListener("click", () => newTab());
  el("browser-reading").addEventListener("click", () => toggleReading());
  el("browser-ask").addEventListener("click", () => ask());
  el("browser-show-images").addEventListener("click", () => {
    const entry = entryOf(active);
    if (entry) loadImages(active, entry, "other");
  });
  document.addEventListener("keydown", event => {
    if (el("browser-view").hidden || event.defaultPrevented) return;
    const ctrl = event.ctrlKey || event.metaKey;
    const key = String(event.key || "").toLowerCase();
    const typing = /^(input|textarea|select)$/i.test(document.activeElement?.tagName || "");
    if (ctrl && !event.shiftKey && key === "l") { event.preventDefault(); focusAddress(); }
    else if (ctrl && !event.shiftKey && key === "t") { event.preventDefault(); newTab(); }
    else if (event.altKey && !typing && event.key === "ArrowLeft") { event.preventDefault(); move(active, -1); }
    else if (event.altKey && !typing && event.key === "ArrowRight") { event.preventDefault(); move(active, 1); }
  });
  window.addEventListener("languagechange", () => { if (active) render(); });

  function onShow() {
    if (!active) newTab();
    else render();
  }

  function open(address) {
    if (!active) newTab();
    return go(active, address);
  }

  return {onShow, open};
})();
