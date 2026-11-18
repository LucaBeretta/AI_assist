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
  }

  function renderMessages() {
    const record = chats.get(selected);
    element("chat-title").textContent = record?.title || "Conversation";
    const nodes = [];
    for (const message of record?.messages || []) {
      const section = document.createElement("section");
      const label = document.createElement("strong");
      label.textContent = message.role;
      const text = document.createElement("pre");
      text.textContent = message.content;
      section.append(label, text);
      nodes.push(section);
    }
    const replies = new Map((record?.unsaved_replies || []).map(reply => [reply.stream_id, reply]));
    for (const stream of streams.values()) {
      if (stream.chat_id === selected && (stream.active || stream.saved === false)) replies.set(stream.id, stream);
    }
    for (const stream of replies.values()) {
      const text = document.createElement("pre");
      text.textContent = (stream.saved === false ? "Not saved — copy this response before closing.\n" : "") + stream.text;
      nodes.push(text);
    }
    element("messages").replaceChildren(...nodes);
    controls();
  }

  function renderChats() {
    const nodes = [...chats.values()].map(record => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = record.title + (activeStream(record.id) ? " (working)" : "");
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
      if (version === selectionVersion) { openingChat = false; renderMessages(); }
    }
  }

  async function reloadChat(id) {
    const version = (loadVersions.get(id) || 0) + 1;
    loadVersions.set(id, version);
    const result = await call("open_chat", id);
    if (loadVersions.get(id) !== version) return;
    chats.set(id, {...result.chat, unsaved_replies: result.unsaved_replies || [], localDraft: chats.get(id)?.localDraft});
  }

  async function refreshChats() {
    const result = await call("list_chats");
    for (const chat of result.chats) chats.set(chat.id, {...chat, localDraft: chats.get(chat.id)?.localDraft});
    renderChats();
  }

  async function newChat() {
    const result = await call("new_chat");
    chats.set(result.chat.id, result.chat);
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

  function onEvent(batch) {
    for (const event of Array.isArray(batch) ? batch : [batch]) {
      if (event.type === "ready") { refreshModels().catch(showError); continue; }
      if (event.type === "status") { status(event.text); continue; }
      if (!event.stream_id) { if (event.type === "error") showError(new Error(event.error)); continue; }
      const stream = streams.get(event.stream_id) || {id: event.stream_id, chat_id: event.chat_id, text: "", active: true};
      streams.set(event.stream_id, stream);
      if (event.type === "token" && stream.active) stream.text += event.text;
      if (["done", "stopped", "error"].includes(event.type)) {
        if (!stream.active) continue;
        stream.active = false;
        stream.saved = event.saved;
        if (event.text !== undefined) stream.text = event.text;
        if (selected === event.chat_id) status(event.type === "error" ? event.error : `${event.type}${event.metrics?.eval_count ? ` · ${event.metrics.eval_count} tokens` : ""}`);
        // Reload the event's chat, not whichever chat happens to be visible.
        reloadChat(event.chat_id).then(() => {
          renderMessages();
          renderChats();
        }).catch(error => { if (selected === event.chat_id) showError(error); });
      }
    }
    renderMessages();
    renderChats();
  }

  element("composer").addEventListener("submit", send);
  element("new-chat").addEventListener("click", () => newChat().catch(showError));
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
