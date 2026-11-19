/* AI Assist web UI controller: talks to webui.app.Api through pywebview and renders the v2 design. */
"use strict";

window.app = (() => {
  const element = id => document.getElementById(id);
  const later = (callback, ms) => (typeof setTimeout === "function" ? setTimeout(callback, ms) : null);
  const streams = new Map();
  const chats = new Map();
  let selected = null;
  let connected = false;
  let engineReady = false;
  let submitting = false;
  let openingChat = false;
  let selectionVersion = 0;
  let editingIndex = null;
  let selectedProject = null;
  let lastState = null;
  let draftTimer = null;
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
    element("attach").disabled = !selected || openingChat || Boolean(active);
  }

  function button(label, className, onClick, aria) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    if (aria) node.setAttribute("aria-label", aria);
    node.addEventListener("click", onClick);
    return node;
  }

  // ---------- conversation ----------
  function renderMessages() {
    const record = chats.get(selected);
    element("chat-title").textContent = record?.title || "Conversation";
    element("incognito-badge").hidden = !record?.incognito;
    const nodes = [];
    const messages = record?.messages || [];
    const messageNode = (message, index) => {
      const section = document.createElement("section");
      section.className = "msg msg-" + message.role + (index === editingIndex ? " is-editing" : "");
      const label = document.createElement("strong");
      label.className = "msg-role";
      label.textContent = message.role;
      const text = document.createElement(message.role === "assistant" ? "div" : "pre");
      if (message.role === "assistant") text.append(window.renderMarkdown(message.content));
      else text.textContent = message.content;
      // Actions sit before the text in the DOM (CSS shows them after) so the text stays the last child.
      section.append(label, messageActions(record, message, index), text);
      return section;
    };
    if (record?.summary) {
      const archive = document.createElement("details");
      archive.className = "compacted";
      const label = document.createElement("summary");
      label.textContent = `Conversation compacted — show ${record.summary.upto} original messages`;
      archive.append(label, ...messages.slice(0, record.summary.upto).map((message, index) => messageNode(message, index)));
      const summary = document.createElement("pre");
      summary.className = "summary-text";
      summary.textContent = record.summary.text;
      nodes.push(archive, summary);
    }
    const offset = record?.summary?.upto || 0;
    nodes.push(...messages.slice(offset).map((message, index) => messageNode(message, offset + index)));
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
    for (const node of container.querySelectorAll?.('.code-actions [data-action="copy"]') || []) node.disabled = false;
    renderInterrupted(record);
    renderAttachments(record);
    if (follow) scrollToEnd();
    updateJump();
    controls();
  }

  function messageActions(record, message, index) {
    const bar = document.createElement("div");
    bar.className = "msg-actions";
    const busy = Boolean(activeStream(selected));
    // A version anchor at index-1 means this message starts a branch: show ‹ n / total ›.
    const version = record?.versions?.[String(index - 1)];
    if (version && version[1] > 1) {
      const nav = document.createElement("span");
      nav.className = "versions";
      const [current, count] = version;
      const prev = button("‹", "icon-btn", () => switchVersion(index - 1, current - 1), "Previous version");
      const next = button("›", "icon-btn", () => switchVersion(index - 1, current + 1), "Next version");
      prev.disabled = busy || current === 0;
      next.disabled = busy || current === count - 1;
      const label = document.createElement("span");
      label.className = "mono";
      label.textContent = `${current + 1} / ${count}`;
      nav.append(prev, label, next);
      bar.append(nav);
    }
    const copy = button("Copy", "icon-btn", () => copyText(message.content, copy), "Copy message");
    bar.append(copy);
    const messages = record?.messages || [];
    if (message.role === "user") {
      const edit = button("Edit", "icon-btn", () => startEditing(index), "Edit and resend");
      edit.disabled = busy;
      bar.append(edit);
    } else if (message.role === "assistant" && index === messages.length - 1 && messages[index - 1]?.role === "user") {
      const again = button("Regenerate", "icon-btn", () => regenerate(), "Regenerate answer");
      again.disabled = busy;
      bar.append(again);
    }
    return bar;
  }

  async function copyText(text, node) {
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
    if (node) {
      const label = node.textContent;
      node.textContent = "Copied";
      later(() => { node.textContent = label; }, 1200);
    }
  }

  // ---------- versions, edit, regenerate ----------
  function startEditing(index) {
    const record = chats.get(selected);
    if (!record || activeStream(selected)) return;
    editingIndex = index;
    element("prompt").value = record.messages[index].content;
    element("edit-hint").hidden = false;
    element("prompt").focus?.();
    renderMessages();
  }

  function cancelEditing() {
    if (editingIndex === null) return;
    editingIndex = null;
    element("edit-hint").hidden = true;
    element("prompt").value = chats.get(selected)?.localDraft ?? "";
    renderMessages();
  }

  function track(chatId, result, operation = "chat") {
    if (!streams.has(result.stream_id)) streams.set(result.stream_id, {id: result.stream_id, chat_id: chatId, text: "", active: true, operation});
  }

  async function regenerate() {
    const chatId = selected;
    if (!chatId || activeStream(chatId)) return;
    try {
      const result = await call("regenerate", chatId);
      track(chatId, result);
      await reloadChat(chatId);
      status("Regenerating…");
      renderMessages(); renderChats();
    } catch (error) { showError(error); }
  }

  async function switchVersion(anchor, n) {
    const chatId = selected;
    try {
      const result = await call("switch_version", chatId, anchor, n);
      chats.set(chatId, {...chats.get(chatId), ...result.chat, versions: result.versions});
      renderMessages();
      refreshUsage(chatId);
    } catch (error) { showError(error); }
  }

  // ---------- interrupted replies and drafts ----------
  function renderInterrupted(record) {
    const last = record?.messages?.at(-1);
    element("interrupted").hidden = !(last && last.partial === true && !activeStream(selected));
  }

  async function resolveInterrupted(keep) {
    const chatId = selected;
    try {
      await call("resolve_interrupted", chatId, keep);
      await reloadChat(chatId);
      renderMessages();
    } catch (error) { showError(error); }
  }

  function scheduleDraft() {
    const chatId = selected;
    if (!chatId || editingIndex !== null || typeof clearTimeout !== "function") return;
    clearTimeout(draftTimer);
    const text = element("prompt").value;
    draftTimer = later(() => { call("save_draft", chatId, text).catch(() => {}); }, 700);
  }

  // ---------- attachments ----------
  function renderAttachments(record) {
    const items = record?.attachments || [];
    element("attachments").hidden = items.length === 0;
    element("attachments").replaceChildren(...items.map(item => {
      const chip = document.createElement("span");
      chip.className = "chip" + (item.kind === "image" ? " chip-image" : "");
      chip.textContent = `${item.name} · ${formatBytes(item.size)}`;
      return chip;
    }));
  }

  function formatBytes(size) {
    if (!Number.isFinite(size)) return "?";
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return Math.round(size / 1024) + " KB";
    return (size / 1024 / 1024).toFixed(1) + " MB";
  }

  function readBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
      reader.onerror = () => reject(reader.error || new Error("Could not read " + file.name));
      reader.readAsDataURL(file);
    });
  }

  async function attachFiles(files) {
    const chatId = selected;
    if (!chatId || !files?.length) return;
    for (const file of files) {
      try {
        status(`Attaching ${file.name}…`);
        await call("attach", chatId, file.name || "pasted.png", await readBase64(file));
        await reloadChat(chatId);
        renderMessages();
        status(`Attached ${file.name}`);
      } catch (error) { showError(error); }
    }
  }

  // ---------- scrolling ----------
  // Follow the stream only while the reader is at the bottom; otherwise offer "Jump to latest".
  function scroller() { return element("conversation"); }
  function nearBottom() {
    const box = scroller();
    if (!box || box.scrollHeight === undefined) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }
  function scrollToEnd() { const box = scroller(); if (box && box.scrollHeight !== undefined) box.scrollTop = box.scrollHeight; }
  function updateJump() { element("jump").hidden = nearBottom(); }

  // ---------- sidebar ----------
  function renderChats() {
    const nodes = [...chats.values()].map(record => {
      const item = document.createElement("li");
      const node = document.createElement("button");
      node.type = "button";
      node.className = "chat-item" + (record.incognito ? " incognito" : "");
      node.setAttribute("aria-current", record.id === selected ? "true" : "false");
      node.textContent = record.title;
      if (activeStream(record.id)) {
        const working = document.createElement("span");
        working.className = "working";
        working.textContent = "working";
        node.append(working);
      }
      node.addEventListener("click", () => openChat(record.id).catch(showError));
      item.append(node);
      return item;
    });
    element("chats").replaceChildren(...nodes);
  }

  async function refreshProjects() {
    if (!window.pywebview?.api?.list_projects) return;
    const result = await call("list_projects");
    const nodes = result.projects.map(project => {
      const item = document.createElement("li");
      const node = button(project.name, "project-item", () => selectProject(project.id === selectedProject ? null : project.id));
      node.setAttribute("aria-pressed", project.id === selectedProject ? "true" : "false");
      item.append(node);
      return item;
    });
    element("projects").replaceChildren(...nodes);
    element("chats-label").textContent = selectedProject
      ? (result.projects.find(project => project.id === selectedProject)?.name || "Project") + " · chats"
      : "Recent";
  }

  async function selectProject(id) {
    selectedProject = id;
    try {
      await refreshProjects();
      await refreshChats();
      if (chats.size) await openChat(chats.keys().next().value);
      else { selected = null; renderMessages(); }
    } catch (error) { showError(error); }
  }

  async function openChat(id) {
    if (!openingChat && selected && chats.has(selected) && editingIndex === null) chats.get(selected).localDraft = element("prompt").value;
    editingIndex = null;
    element("edit-hint").hidden = true;
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
    chats.set(id, {...result.chat, versions: result.versions || {}, unsaved_replies: result.unsaved_replies || [], localDraft: chats.get(id)?.localDraft});
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
    const result = selectedProject ? await call("list_chats", selectedProject) : await call("list_chats");
    const previous = new Map(chats);
    chats.clear();
    for (const chat of result.chats) chats.set(chat.id, {...previous.get(chat.id), ...chat, localDraft: previous.get(chat.id)?.localDraft});
    renderChats();
  }

  // Newest chats go to the top of the sidebar.
  function addChatFirst(record) {
    const rest = [...chats].filter(([id]) => id !== record.id);
    chats.clear();
    chats.set(record.id, record);
    for (const [id, value] of rest) chats.set(id, value);
  }

  async function newChat(incognito = false) {
    const result = selectedProject || incognito ? await call("new_chat", incognito, selectedProject) : await call("new_chat");
    addChatFirst(result.chat);
    renderChats();
    await openChat(result.chat.id);
    if (incognito) status("Incognito chat: not saved, no memory.");
  }

  async function refreshModels() {
    engineReady = false;
    controls();
    const result = await call("state");
    lastState = result;
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
    const editing = editingIndex;
    submitting = true;
    controls();
    try {
      const result = editing !== null
        ? await call("edit_and_resend", chatId, editing, text)
        : await call("send", chatId, text, model, rawContext ? Number(rawContext) : null);
      track(chatId, result);
      if (editing !== null) { editingIndex = null; element("edit-hint").hidden = true; }
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

  // ---------- banner (VRAM and other app-wide notices) ----------
  function showBanner(text, actions = []) {
    element("banner-text").textContent = text;
    element("banner-actions").replaceChildren(...actions);
    element("banner").hidden = false;
  }

  async function unloadModel(name) {
    try {
      status(`Unloading ${name}…`);
      await call("unload_model", name);
    } catch (error) { showError(error); }
  }

  function gigabytes(bytes) { return (bytes / 1024 ** 3).toFixed(1) + " GB"; }

  function appEvent(event) {
    switch (event.type) {
      case "vram_warning":
        showBanner(`${event.models.length} models loaded need ${gigabytes(event.required_bytes)}, more than the ${gigabytes(event.gpu_memory_bytes)} of GPU memory. Responses slow down.`,
          event.models.map(name => button("Unload " + name, "btn", () => unloadModel(name))));
        return true;
      case "model_unloaded":
        status(`${event.model} unloaded from memory.`);
        element("banner").hidden = true;
        return true;
      case "attached":
        if (event.chat_id === selected) reloadChat(event.chat_id).then(renderMessages).catch(showError);
        return true;
      case "project_changed":
      case "project_deleted":
        refreshProjects().catch(showError);
        return true;
      case "checkpoint_restored":
        status(`Restored ${event.restored} files from the task checkpoint.`);
        return true;
      default:
        return false;
    }
  }

  function onEvent(batch) {
    for (const event of Array.isArray(batch) ? batch : [batch]) {
      if (event.type === "ready") { refreshModels().catch(showError); continue; }
      if (event.type === "status") { status(event.text); continue; }
      if (event.type === "model_loading" || event.type === "model_loaded") {
        const text = event.type === "model_loading"
          ? `Loading ${event.model}… ${Math.floor(event.elapsed_seconds)} s`
          : `${event.model} ready in ${event.load_seconds.toFixed(1)} s`;
        if (event.chat_id === selected) status(text);
        if (event.task_id) window.devui?.onEvent({...event, type: "loading", text});
        continue;
      }
      if (appEvent(event)) continue;
      if (window.modelsui?.onEvent(event)) continue;  // downloads and model list changes
      if (event.task_id) { window.devui?.onEvent(event); continue; }  // Developer task events
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

  // ---------- theme ----------
  // Night by default, remembered per window (the settings file takes over once the Api exposes it).
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

  // ---------- dialogs ----------
  function openDialog(id) { const dialog = element(id); if (dialog.showModal) dialog.showModal(); else dialog.open = true; }
  function closeDialog(id) { const dialog = element(id); if (dialog.close) dialog.close(); else dialog.open = false; }

  async function openSettings() {
    try { await refreshModels(); } catch (error) { showError(error); }
    element("setting-theme").value = root?.getAttribute?.("data-theme") || "night";
    element("setting-idle").value = String(lastState?.settings?.idle_unload_minutes ?? 0);
    const loaded = Object.entries(lastState?.loaded || {});
    element("loaded-models").replaceChildren(...(loaded.length ? loaded.map(([name, details]) => {
      const row = document.createElement("div");
      row.className = "setting-row";
      const label = document.createElement("span");
      label.className = "mono";
      label.textContent = `${name}${details?.size ? " · " + gigabytes(details.size) : ""}`;
      row.append(label, button("Unload", "btn", async () => { await unloadModel(name); openSettings(); }));
      return row;
    }) : [Object.assign(document.createElement("p"), {className: "setting-hint", textContent: "No models are loaded right now."})]));
    openDialog("settings-dialog");
  }

  function closeContextMenu() { element("context-menu").open = false; }
  function fitPrompt() {
    const prompt = element("prompt");
    if (!prompt.style) return;
    prompt.style.height = "auto";
    prompt.style.height = prompt.scrollHeight + "px";
  }

  // ---------- wiring ----------
  element("prompt").addEventListener("input", () => { fitPrompt(); scheduleDraft(); });
  element("prompt").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!element("send").disabled) send(event);
    }
  });
  element("prompt").addEventListener("paste", event => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) { event.preventDefault(); attachFiles(files); }
  });
  element("composer").addEventListener("dragover", event => { event.preventDefault(); element("composer").classList?.add("is-dropping"); });
  element("composer").addEventListener("dragleave", () => element("composer").classList?.remove("is-dropping"));
  element("composer").addEventListener("drop", event => {
    event.preventDefault();
    element("composer").classList?.remove("is-dropping");
    attachFiles([...(event.dataTransfer?.files || [])]);
  });
  element("attach").addEventListener("click", () => element("attach-input").click());
  element("attach-input").addEventListener("change", () => {
    const input = element("attach-input");
    attachFiles([...(input.files || [])]).finally(() => { input.value = ""; });
  });

  if (document.addEventListener) {
    document.addEventListener("keydown", event => {
      const ctrl = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (ctrl && event.shiftKey && key === "l") { event.preventDefault(); toggleTheme(); }
      else if (ctrl && event.shiftKey && key === "n") { event.preventDefault(); newChat(true).catch(showError); }
      else if (ctrl && !event.shiftKey && key === "n") { event.preventDefault(); newChat().catch(showError); }
      else if (ctrl && key === ",") { event.preventDefault(); openSettings(); }
      else if (event.key === "Escape") {
        if (element("context-menu").open) closeContextMenu();
        else if (window.devui && !element("dev-view").hidden) { if (window.devui.isRunning()) window.devui.stop(); }
        else if (editingIndex !== null) cancelEditing();
        else if (!element("stop").disabled) element("stop").click();
      }
    });
  }

  element("jump").addEventListener("click", () => { scrollToEnd(); updateJump(); });
  element("conversation").addEventListener("scroll", updateJump);
  element("messages").addEventListener("click", event => {
    const node = event.target?.closest?.('.code-actions [data-action="copy"]');
    if (!node) return;
    const code = node.parentElement?.nextElementSibling;
    if (code) copyText(code.textContent, node);
  });

  element("composer").addEventListener("submit", send);
  element("new-chat").addEventListener("click", () => newChat().catch(showError));
  element("new-incognito").addEventListener("click", () => newChat(true).catch(showError));
  element("cancel-edit").addEventListener("click", cancelEditing);
  element("keep-interrupted").addEventListener("click", () => resolveInterrupted(true));
  element("discard-interrupted").addEventListener("click", () => resolveInterrupted(false));
  element("banner-close").addEventListener("click", () => { element("banner").hidden = true; });
  element("open-settings").addEventListener("click", openSettings);
  element("settings-close").addEventListener("click", () => closeDialog("settings-dialog"));
  element("setting-theme").addEventListener("change", () => setTheme(element("setting-theme").value));
  element("setting-idle").addEventListener("change", async () => {
    try {
      const result = await call("set_idle_unload_minutes", Number(element("setting-idle").value));
      if (lastState) lastState.settings = result.settings;
      status("Saved.");
    } catch (error) { showError(error); }
  });
  element("new-project").addEventListener("click", () => {
    element("project-name").value = "";
    element("project-instructions").value = "";
    element("project-folder").value = "";
    openDialog("project-dialog");
  });
  element("project-cancel").addEventListener("click", () => closeDialog("project-dialog"));
  element("project-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const folder = element("project-folder").value.trim();
      const result = await call("create_project", element("project-name").value.trim(), element("project-instructions").value, folder || null);
      closeDialog("project-dialog");
      await selectProject(result.project.id);
    } catch (error) { showError(error); }
  });
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
      track(chatId, result, "compact");
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
    try { await refreshProjects(); } catch (error) { showError(error); }
    try {
      await refreshChats();
      if (chats.size) await openChat(chats.keys().next().value);
      else await newChat();
    } catch (error) { showError(error); }
    if (lastState?.interrupted?.length) status(`${lastState.interrupted.length} chat(s) have an interrupted reply: open them to keep or discard it.`);
  });
  // ---------- views (Chats / Developer / Models) ----------
  const views = {chat: ["chat-view", "nav-chats"], dev: ["dev-view", "nav-dev"], models: ["models-view", "nav-models"]};
  function showView(name) {
    for (const [key, [view, nav]] of Object.entries(views)) {
      element(view).hidden = key !== name;
      element(nav).setAttribute("aria-current", key === name ? "page" : "false");
    }
    if (name === "dev") window.devui?.onShow?.();
    if (name === "models") window.modelsui?.onShow?.();
  }
  element("nav-chats").addEventListener("click", () => showView("chat"));
  element("nav-dev").addEventListener("click", () => showView("dev"));
  element("nav-models").addEventListener("click", () => showView("models"));
  element("open-models").addEventListener("click", () => showView("models"));

  function selectModel(name) {
    element("model").value = name;
    controls();
    if (selected && window.pywebview?.api?.set_chat_model) call("set_chat_model", selected, name).catch(showError);
  }

  return {onEvent, showView, selectModel, refreshModels: () => refreshModels().catch(showError)};
})();
