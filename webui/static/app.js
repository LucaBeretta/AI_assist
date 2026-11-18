/* Functional shell only; visual design belongs to the next layer. */
"use strict";

window.app = (() => {
  const element = id => document.getElementById(id);
  const streams = new Map();
  const chats = new Map();
  let selected = null;
  let connected = false;
  let submitting = false;

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
    element("send").disabled = !connected || !selected || !element("model").value || submitting || Boolean(active);
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
    const stream = activeStream(selected);
    if (stream) {
      const text = document.createElement("pre");
      text.textContent = stream.text;
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
    if (selected && chats.has(selected)) chats.get(selected).localDraft = element("prompt").value;
    selected = id;
    const result = await call("open_chat", id);
    if (selected !== id) return;
    const draft = chats.get(id)?.localDraft;
    chats.set(id, {...result.chat, localDraft: draft});
    element("prompt").value = draft ?? result.chat.draft ?? "";
    if (result.chat.model) element("model").value = result.chat.model;
    element("context").value = result.chat.context_length ?? "";
    renderMessages();
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
    const result = await call("state");
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
    if (!chatId || !text.trim() || activeStream(chatId)) return;
    const model = element("model").value;
    const rawContext = element("context").value;
    submitting = true;
    controls();
    try {
      const result = await call("send", chatId, text, model, rawContext ? Number(rawContext) : null);
      if (!streams.has(result.stream_id)) streams.set(result.stream_id, {id: result.stream_id, chat_id: chatId, text: "", active: true});
      const latest = await call("open_chat", chatId);
      chats.set(chatId, {...latest.chat, localDraft: ""});
      if (selected === chatId) element("prompt").value = "";
      status("Generating…");
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
      if (event.type === "token") stream.text += event.text;
      if (["done", "stopped", "error"].includes(event.type)) {
        stream.active = false;
        status(event.type === "error" ? event.error : `${event.type}${event.metrics?.eval_count ? ` · ${event.metrics.eval_count} tokens` : ""}`);
        // Reload the event's chat, not whichever chat happens to be visible.
        call("open_chat", event.chat_id).then(result => {
          chats.set(event.chat_id, {...result.chat, localDraft: chats.get(event.chat_id)?.localDraft});
          renderMessages();
          renderChats();
        }).catch(showError);
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
    try {
      await refreshModels();
      await refreshChats();
      if (chats.size) await openChat(chats.keys().next().value);
      else await newChat();
    } catch (error) { showError(error); }
  });
  return {onEvent};
})();
