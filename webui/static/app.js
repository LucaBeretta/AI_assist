/* Functional shell only; visual design belongs to the next layer. */
"use strict";

window.app = (() => {
  const element = id => document.getElementById(id);
  const streams = new Map();
  const chats = new Map();
  let selected = null;
  let connected = false;
  let engineReady = false;
  let submitting = false;
  let openingChat = false;
  let selectionVersion = 0;
  const loadVersions = new Map();

  function status(text) { element("status").textContent = text; }

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(result?.error || "Invalid API response");
    return result;
  }

  function activeStream(chatId) {
    return [...streams.values()].find(stream => stream.chat_id === chatId && stream.active);
  }

  function controls() {
    const active = activeStream(selected);
    element("send").disabled = !connected || !engineReady || !selected || openingChat || !element("model").value || submitting || Boolean(active);
    element("prompt").disabled = openingChat;
    element("stop").disabled = !active || Boolean(active.stopping);
    element("compact").disabled = !selected || openingChat || submitting || Boolean(active);
    element("same-environment").disabled = !selected || openingChat;
  }

  function renderMessages() {
    const record = chats.get(selected);
    element("chat-title").textContent = record?.title || "Conversation";
    const nodes = [];
    const messageNode = message => {
      const section = document.createElement("section");
      section.className = "msg msg-" + message.role;
      const label = document.createElement("strong");
      label.className = "msg-role";
      label.textContent = message.role;
      const text = document.createElement(message.role === "assistant" ? "div" : "pre");
      if (message.role === "assistant") text.append(window.renderMarkdown(message.content));
      else text.textContent = message.content;
      // Actions sit before the text in the DOM (CSS shows them after) so the text stays the last child.
      section.append(label, messageActions(message), text);
      return section;
    };
    const messages = record?.messages || [];
    if (record?.summary) {
      const archive = document.createElement("details");
      archive.className = "compacted";
      const label = document.createElement("summary");
      label.textContent = `Conversation compacted — show ${record.summary.upto} original messages`;
      archive.append(label, ...messages.slice(0, record.summary.upto).map(messageNode));
      const summary = document.createElement("pre");
      summary.className = "summary-text";
      summary.textContent = record.summary.text;
      nodes.push(archive, summary);
    }
    nodes.push(...messages.slice(record?.summary?.upto || 0).map(messageNode));
    const replies = new Map((record?.unsaved_replies || []).map(reply => [reply.stream_id, reply]));
    for (const stream of streams.values()) {
      if (stream.chat_id === selected && stream.operation !== "compact" && (stream.active || stream.saved === false)) replies.set(stream.id, stream);
    }
    for (const stream of replies.values()) {
      const text = document.createElement("div");
      text.className = "msg msg-stream" + (stream.active ? " is-active" : "");
      if (stream.saved === false) {
        const warning = document.createElement("p");
        warning.className = "notice";
        warning.textContent = "Not saved — copy this response before closing.";
        text.append(warning);
      }
      text.append(window.renderMarkdown(stream.text));
      nodes.push(text);
    }
    const container = element("messages");
    const follow = nearBottom();
    container.replaceChildren(...nodes);
    // markdown.js renders code toolbars disabled; Copy works here, Save/Preview wait for their Api methods.
    for (const button of container.querySelectorAll?.('.code-actions [data-action="copy"]') || []) button.disabled = false;
    if (follow) scrollToEnd();
    updateJump();
    controls();
  }

  function messageActions(message) {
    const bar = document.createElement("div");
    bar.className = "msg-actions";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "icon-btn";
    copy.setAttribute("aria-label", "Copy message");
    copy.setAttribute("title", "Copy");
    copy.textContent = "Copy";
    copy.addEventListener("click", () => copyText(message.content, copy));
    bar.append(copy);
    return bar;
  }

  async function copyText(text, button) {
    try {
      if (window.navigator?.clipboard?.writeText) await window.navigator.clipboard.writeText(text);
      else throw new Error("no clipboard API");
    } catch (error) {
      // WebView file pages may refuse the async clipboard; fall back to a hidden selection.
      const area = document.createElement("textarea");
      area.value = text;
      area.className = "sr-only";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    if (button) {
      const label = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = label; }, 1200);
    }
  }

  // Follow the stream only while the reader is at the bottom; otherwise offer "Jump to latest".
  function scroller() { return element("conversation"); }
  function nearBottom() {
    const box = scroller();
    if (!box || box.scrollHeight === undefined) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }
  function scrollToEnd() { const box = scroller(); if (box && box.scrollHeight !== undefined) box.scrollTop = box.scrollHeight; }
  function updateJump() { element("jump").hidden = nearBottom(); }

  function renderChats() {
    const nodes = [...chats.values()].map(record => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chat-item";
      button.setAttribute("aria-current", record.id === selected ? "true" : "false");
      button.textContent = record.title;
      if (activeStream(record.id)) {
        const working = document.createElement("span");
        working.className = "working";
        working.textContent = "working";
        button.append(working);
      }
      button.addEventListener("click", () => openChat(record.id).catch(showError));
      item.append(button);
      return item;
    });
    element("chats").replaceChildren(...nodes);
  }

  async function openChat(id) {
    if (!openingChat && selected && chats.has(selected)) chats.get(selected).localDraft = element("prompt").value;
    const version = ++selectionVersion;
    selected = id;
    openingChat = true;
    controls();
    try {
      await reloadChat(id);
      if (version !== selectionVersion) return;
      const record = chats.get(id);
      element("prompt").value = record.localDraft ?? record.draft ?? "";
      if (record.model) element("model").value = record.model;
      element("context").value = record.context_length ?? "";
    } catch (error) {
      if (version !== selectionVersion) return;
      selected = null;
      throw error;
    } finally {
      if (version === selectionVersion) { openingChat = false; renderMessages(); renderChats(); refreshUsage(id); }
    }
  }

  async function reloadChat(id) {
    const version = (loadVersions.get(id) || 0) + 1;
    loadVersions.set(id, version);
    const result = await call("open_chat", id);
    if (loadVersions.get(id) !== version) return;
    chats.set(id, {...result.chat, unsaved_replies: result.unsaved_replies || [], localDraft: chats.get(id)?.localDraft});
  }

  async function refreshUsage(id) {
    try {
      const result = await call("context_usage", id);
      if (selected !== id) return;
      const total = result.total || 0;
      element("context-usage").textContent = `${tokens(result.used)} / ${total ? tokens(total) : "?"}`;
      element("context-meter").value = total ? Math.min(1, result.used / total) : 0;
      element("context-menu").className = "ctx level-" + (result.level || "ok");
    } catch (error) {
      if (selected === id) element("context-usage").textContent = "–";
    }
  }

  function tokens(n) {
    if (!Number.isFinite(n) || n < 1000) return String(n ?? "?");
    if (n % 1024 === 0) return n / 1024 + "k";  // model contexts are powers of two: 16384 → 16k
    const value = (Math.round(n / 100) / 10).toFixed(1);
    return (value.endsWith(".0") ? value.slice(0, -2) : value) + "k";
  }

  async function refreshChats() {
    const result = await call("list_chats");
    for (const chat of result.chats) chats.set(chat.id, {...chat, localDraft: chats.get(chat.id)?.localDraft});
    renderChats();
  }

  // Newest chats go to the top of the sidebar.
  function addChatFirst(record) {
    const rest = [...chats].filter(([id]) => id !== record.id);
    chats.clear();
    chats.set(record.id, record);
    for (const [id, value] of rest) chats.set(id, value);
  }

  async function newChat() {
    const result = await call("new_chat");
    addChatFirst(result.chat);
    renderChats();
    await openChat(result.chat.id);
  }

  async function refreshModels() {
    engineReady = false;
    controls();
    const result = await call("state");
    engineReady = true;
    const previous = element("model").value;
    const options = result.models.map(name => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    });
    element("model").replaceChildren(...options);
    if (result.models.includes(previous)) element("model").value = previous;
    status(`${result.engine}: ${result.host}. ${result.models.length} models available.`);
    controls();
  }

  function showError(error) { status(error.message || String(error)); controls(); }

  async function send(event) {
    event.preventDefault();
    const chatId = selected;
    const text = element("prompt").value;
    if (!chatId || openingChat || submitting || !text.trim() || activeStream(chatId)) return;
    const model = element("model").value;
    const rawContext = element("context").value;
    submitting = true;
    controls();
    try {
      const result = await call("send", chatId, text, model, rawContext ? Number(rawContext) : null);
      if (!streams.has(result.stream_id)) streams.set(result.stream_id, {id: result.stream_id, chat_id: chatId, text: "", active: true});
      chats.get(chatId).localDraft = "";
      await reloadChat(chatId);
      if (selected === chatId) element("prompt").value = "";
      if (selected === chatId && streams.get(result.stream_id).active) status("Generating…");
      renderMessages();
      renderChats();
    } catch (error) { showError(error); }
    finally { submitting = false; controls(); }
  }

  function rate(metrics) {
    const seconds = (metrics.eval_duration || 0) / 1e9;
    return seconds > 0 ? ` · ${(metrics.eval_count / seconds).toFixed(1)} tok/s` : "";
  }

  function onEvent(batch) {
    for (const event of Array.isArray(batch) ? batch : [batch]) {
      if (event.type === "ready") { refreshModels().catch(showError); continue; }
      if (event.type === "status") { status(event.text); continue; }
      if (!event.stream_id) { if (event.type === "error") showError(new Error(event.error)); continue; }
      const stream = streams.get(event.stream_id) || {id: event.stream_id, chat_id: event.chat_id, text: "", active: true, operation:event.operation || "chat"};
      streams.set(event.stream_id, stream);
      if (event.type === "token" && stream.active) stream.text += event.text;
      if (["done", "stopped", "error", "compact_done"].includes(event.type)) {
        if (!stream.active) continue;
        stream.active = false;
        stream.saved = event.saved;
        if (event.text !== undefined) stream.text = event.text;
        if (selected === event.chat_id) status(event.type === "error" ? event.error : `${event.type}${event.metrics?.eval_count ? ` · ${event.metrics.eval_count} tokens${rate(event.metrics)}` : ""}`);
        // Reload the event's chat, not whichever chat happens to be visible.
        reloadChat(event.chat_id).then(() => {
          renderMessages();
          renderChats();
          refreshUsage(event.chat_id);
        }).catch(error => { if (selected === event.chat_id) showError(error); });
      }
    }
    renderMessages();
    renderChats();
  }

  // Theme: Night by default, remembered per window (the settings file takes over once the Api exposes it).
  const root = document.documentElement;
  function setTheme(name) {
    if (!root) return;
    root.setAttribute("data-theme", name);
    try { window.localStorage?.setItem("aiassist-theme", name); } catch (error) { /* storage unavailable */ }
  }
  function toggleTheme() { setTheme(root?.getAttribute?.("data-theme") === "day" ? "night" : "day"); }
  try { const saved = window.localStorage?.getItem("aiassist-theme"); if (saved === "day" || saved === "night") setTheme(saved); }
  catch (error) { /* storage unavailable */ }
  element("theme").addEventListener("click", toggleTheme);

  function closeContextMenu() { element("context-menu").open = false; }
  function fitPrompt() {
    const prompt = element("prompt");
    if (!prompt.style) return;
    prompt.style.height = "auto";
    prompt.style.height = prompt.scrollHeight + "px";
  }
  element("prompt").addEventListener("input", fitPrompt);
  element("prompt").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!element("send").disabled) send(event);
    }
  });
  if (document.addEventListener) {
    document.addEventListener("keydown", event => {
      const ctrl = event.ctrlKey || event.metaKey;
      if (ctrl && event.shiftKey && event.key.toLowerCase() === "l") { event.preventDefault(); toggleTheme(); }
      else if (ctrl && !event.shiftKey && event.key.toLowerCase() === "n") { event.preventDefault(); newChat().catch(showError); }
      else if (event.key === "Escape") {
        if (element("context-menu").open) closeContextMenu();
        else if (!element("stop").disabled) element("stop").click();
      }
    });
  }

  element("jump").addEventListener("click", () => { scrollToEnd(); updateJump(); });
  element("conversation").addEventListener("scroll", updateJump);
  element("messages").addEventListener("click", event => {
    const button = event.target?.closest?.('.code-actions [data-action="copy"]');
    if (!button) return;
    const code = button.parentElement?.nextElementSibling;
    if (code) copyText(code.textContent, button);
  });

  element("composer").addEventListener("submit", send);
  element("new-chat").addEventListener("click", () => newChat().catch(showError));
  element("same-environment").addEventListener("click", async () => {
    closeContextMenu();
    try {
      const result = await call("new_chat_same_environment", selected);
      addChatFirst(result.chat);
      renderChats();
      await openChat(result.chat.id);
    } catch (error) { showError(error); }
  });
  element("compact").addEventListener("click", async () => {
    const chatId = selected;
    if (!chatId || openingChat || submitting || activeStream(chatId)) return;
    closeContextMenu();
    submitting = true; controls();
    try {
      const result = await call("compact", chatId);
      if (!streams.has(result.stream_id)) streams.set(result.stream_id, {id:result.stream_id,chat_id:chatId,text:"",active:true,operation:"compact"});
      if (selected === chatId && streams.get(result.stream_id).active) status("Compacting…");
      renderChats();
    } catch (error) { showError(error); }
    finally { submitting = false; controls(); }
  });
  element("refresh").addEventListener("click", () => refreshModels().catch(showError));
  element("model").addEventListener("change", controls);
  element("stop").addEventListener("click", async () => {
    const stream = activeStream(selected);
    if (!stream) return;
    stream.stopping = true;
    status("Stopping; waiting for the stream to close…");
    controls();
    try { await call("stop", stream.id); }
    catch (error) { stream.stopping = false; showError(error); }
  });
  window.addEventListener("pywebviewready", async () => {
    connected = true;
    try { await refreshModels(); }
    catch (error) { showError(error); }
    try {
      await refreshChats();
      if (chats.size) await openChat(chats.keys().next().value);
      else await newChat();
    } catch (error) { showError(error); }
  });
  return {onEvent};
})();
