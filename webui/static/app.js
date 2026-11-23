/* AI Assist web UI controller: talks to webui.app.Api through pywebview and renders the v2 design. */
"use strict";

window.app = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const element = id => document.getElementById(id);
  const later = (callback, ms) => (typeof setTimeout === "function" ? setTimeout(callback, ms) : null);
  const streams = new Map();
  const chats = new Map();
  const drafts = new Map();
  const streamNodes = new Map();
  const chatLoads = new Map();
  let openedChat = null;
  let selected = null;
  let connected = false;
  let engineReady = false;
  let submitting = false;
  let openingChat = false;
  let selectionVersion = 0;
  let editingIndex = null;
  let selectedProject = null;
  let lastState = null;
  let sidebarVersion = 0;

  function status(text) { element("status").textContent = text; }

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(window.i18n?.gate?.(result?.error) || result?.error || t("Invalid API response"));
    return result;
  }

  function activeStream(chatId) {
    return [...streams.values()].find(stream => stream.chat_id === chatId && stream.active);
  }

  function currentChat() {
    return openedChat?.id === selected ? openedChat : null;
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
    const record = currentChat();
    element("chat-title").textContent = record ? chatTitle(record) : t("Conversation");
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
      if (message.role === "assistant") text.append(window.premiumui?.renderReply(message.content, message.thinking_seconds, selected + ":" + index) || window.renderMarkdown(message.content));
      else text.textContent = message.content;
      // Actions sit before the text in the DOM (CSS shows them after) so the text stays the last child.
      section.append(label, messageActions(record, message, index), text);
      return section;
    };
    if (record?.summary) {
      const archive = document.createElement("details");
      archive.className = "compacted";
      const label = document.createElement("summary");
      label.textContent = t("Conversation compacted — show {v0} original messages", {v0: (record.summary.upto)});
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
    streamNodes.clear();
    for (const [id, stream] of replies) {
      const node = document.createElement("div");
      fillStreamReply(node, stream);
      streamNodes.set(id, node);
      nodes.push(node);
    }
    const container = element("messages");
    const follow = nearBottom();
    container.replaceChildren(...nodes);
    enableCodeActions(container);
    renderInterrupted(record);
    renderAttachments(record);
    if (follow) scrollToEnd();
    updateJump();
    controls();
  }

  function enableCodeActions(container) {
    for (const node of container.querySelectorAll?.(".code-actions [data-action]") || []) node.disabled = false;
  }

  function fillStreamReply(node, stream) {
    node.className = "msg msg-stream" + (stream.active ? " is-active" : "");
    const children = [];
    if (stream.saved === false) {
      const warning = document.createElement("p");
      warning.className = "notice";
      warning.textContent = t("Not saved — copy this response before closing.");
      children.push(warning);
    }
    children.push(window.premiumui?.renderReply(stream.text, undefined, stream.id || stream.stream_id) || window.renderMarkdown(stream.text));
    node.replaceChildren(...children);
  }

  function updateLiveReply(stream) {
    if (openingChat || stream.chat_id !== selected || stream.operation === "compact") return;
    const follow = nearBottom();
    let node = streamNodes.get(stream.id);
    if (!node) {
      node = document.createElement("div");
      streamNodes.set(stream.id, node);
      element("messages").append(node);
    }
    fillStreamReply(node, stream);
    enableCodeActions(node);
    if (follow) scrollToEnd();
    updateJump();
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
      const prev = button("‹", "icon-btn", () => switchVersion(index - 1, current - 1), t("Previous version"));
      const next = button("›", "icon-btn", () => switchVersion(index - 1, current + 1), t("Next version"));
      prev.disabled = busy || current === 0;
      next.disabled = busy || current === count - 1;
      const label = document.createElement("span");
      label.className = "mono";
      label.textContent = `${current + 1} / ${count}`;
      nav.append(prev, label, next);
      bar.append(nav);
    }
    const copy = button(t("Copy"), "icon-btn", () => copyText(message.content, copy), t("Copy message"));
    bar.append(copy);
    const messages = record?.messages || [];
    if (message.role === "user") {
      const edit = button(t("Edit"), "icon-btn", () => startEditing(index), t("Edit and resend"));
      edit.disabled = busy;
      bar.append(edit);
    } else if (message.role === "assistant" && index === messages.length - 1 && messages[index - 1]?.role === "user") {
      const again = button(t("Regenerate"), "icon-btn", () => regenerate(), t("Regenerate answer"));
      again.disabled = busy;
      bar.append(again);
    }
    const used = message.role === "assistant" && index === messages.length - 1 ? record?.last_response?.memories_used || [] : [];
    if (used.length) bar.append(memoriesUsed(used));
    if (message.role === "assistant") {
      const feedback = button(t("Give feedback"), "icon-btn", () => window.parityui?.openFeedback(selected, index));
      feedback.disabled = busy; bar.append(feedback);
    }
    return bar;
  }

  // "Used 2 memories": what the model was given from shared memory, with a way to forget each one.
  function memoriesUsed(items) {
    const box = document.createElement("details");
    box.className = "mem-used";
    const summary = document.createElement("summary");
    summary.textContent = t("Memories used: {v0}", {v0: (items.length)});
    const list = document.createElement("div");
    list.className = "mem-list";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "mem-row";
      const text = document.createElement("span");
      text.textContent = item.text;
      const forget = button(t("Forget"), "btn ghost", async () => {
        try { await call("forget_memory", item.id); forget.disabled = true; forget.textContent = t("Forgotten"); status(t("Memory removed; it will not be used again.")); }
        catch (error) { showError(error); }
      });
      row.append(text, forget);
      list.append(row);
    }
    box.append(summary, list);
    return box;
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
      node.textContent = t("Copied");
      later(() => { node.textContent = label; }, 1200);
    }
  }

  // ---------- versions, edit, regenerate ----------
  function startEditing(index) {
    const record = currentChat();
    if (!record || activeStream(selected)) return;
    if (editingIndex === null) rememberDraft(selected, element("prompt").value);
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
    element("prompt").value = draftText(selected);
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
      if (selected === chatId) await reloadChat(chatId);
      if (selected === chatId && !openingChat) {
        if (streams.get(result.stream_id).active) status(t("Regenerating…"));
        renderMessages();
      }
      renderChats();
    } catch (error) { showError(error); }
  }

  async function switchVersion(anchor, n) {
    const chatId = selected;
    try {
      const result = await call("switch_version", chatId, anchor, n);
      if (selected === chatId) {
        openedChat = {...currentChat(), ...result.chat, versions: result.versions};
        renderMessages();
      }
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

  function draftText(chatId) {
    return drafts.get(chatId)?.text ?? currentChat()?.draft ?? "";
  }

  function rememberDraft(chatId, text) {
    let draft = drafts.get(chatId);
    if (!draft) {
      draft = {text: undefined, version: 0, timer: null, pending: null, paused: false, dirty: false};
      drafts.set(chatId, draft);
    }
    if (draft.text !== text) {
      draft.text = text;
      draft.version++;
      draft.dirty = true;
    }
    return draft;
  }

  function cancelDraftTimer(draft) {
    if (draft.timer !== null) clearTimeout(draft.timer);
    draft.timer = null;
  }

  function queueDraftSave(chatId, draft) {
    if (!draft.dirty || draft.paused || typeof clearTimeout !== "function") return;
    cancelDraftTimer(draft);
    const {version, text} = draft;
    draft.timer = later(() => {
      draft.timer = null;
      draft.pending = Promise.resolve(draft.pending).then(async () => {
        if (drafts.get(chatId) !== draft || draft.paused || draft.version !== version) return;
        await call("save_draft", chatId, text);
        if (draft.version === version) draft.dirty = false;
      }).catch(() => {});
    }, 700);
  }

  function scheduleDraft() {
    if (!selected || editingIndex !== null) return;
    queueDraftSave(selected, rememberDraft(selected, element("prompt").value));
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
      reader.onerror = () => reject(reader.error || new Error(t("Could not read ") + file.name));
      reader.readAsDataURL(file);
    });
  }

  async function attachFiles(files) {
    const chatId = selected;
    if (!chatId || !files?.length) return;
    for (const file of files) {
      try {
        status(t("Attaching {v0}…", {v0: (file.name)}));
        await call("attach", chatId, file.name || "pasted.png", await readBase64(file));
        await reloadChat(chatId);
        renderMessages();
        status(t("Attached {v0}", {v0: (file.name)}));
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
      node.textContent = chatTitle(record);
      if (activeStream(record.id)) {
        const working = document.createElement("span");
        working.className = "working";
        working.textContent = t("working");
        node.append(working);
      }
      node.addEventListener("click", () => openChat(record.id).catch(showError));
      node.addEventListener("contextmenu", event => showMenu(event, [
        [t("Open"), () => { showView("chat"); openChat(record.id).catch(showError); }],
        [t("Rename…"), () => startRename(record.id)],
        [t("Memories for this chat…"), () => window.workspaceui?.openMemories(record.id)],
        [t("Chat instructions…"), () => window.workspaceui?.openInstructions(record.id)],
        [t("Delete"), () => deleteChat(record.id), "danger"],
      ]));
      item.append(node);
      return item;
    });
    element("chats").replaceChildren(...(window.premiumui ? window.premiumui.groupNodes([...chats.values()], nodes) : nodes));
  }

  const projectsById = new Map();
  let editingProject = null;

  function envLabel(environment) {
    if (!environment?.model) return t("No model set");
    return `${environment.model} · ${environment.context_length ? tokens(environment.context_length) + " ctx" : "auto ctx"}`;
  }

  function renderProjectBar() {
    const project = projectsById.get(selectedProject);
    element("project-bar").hidden = !project;
    if (!project) return;
    element("project-bar-name").textContent = project.name;
    const parts = [envLabel(project.default_environment)];
    if (project.folder) parts.push(project.folder);
    if (project.instructions) parts.push(t("Instructions set"));
    element("project-bar-meta").textContent = parts.join(" · ");
  }

  function currentEnvironment(base) {
    const context = Number(element("context").value) || null;
    const choice = modelChoice();
    return {backend: "Ollama", instructions: "", pinned_paths: [], memory_ids: [], ...(base || {}),
            backend: choice.backend, model: choice.name, context_length: context};
  }

  function openProjectDialog(project) {
    editingProject = project || null;
    element("project-title").textContent = project ? t("Edit project") : t("New project");
    element("project-submit").textContent = project ? t("Save") : t("Create project");
    element("project-delete").hidden = !project;
    element("project-name").value = project?.name || "";
    element("project-instructions").value = project?.instructions || "";
    element("project-folder").value = project?.folder || "";
    element("project-use-env").checked = !project || !project.default_environment?.model;
    element("project-env-label").textContent = "(" + envLabel(currentEnvironment()) + ")";
    openDialog("project-dialog");
  }

  async function browseFolder(input) {
    try {
      const result = await call("choose_folder");
      if (!result.cancelled) { element(input).value = result.path; element(input).dispatchEvent?.(new Event("change")); }
    } catch (error) { showError(error); }
  }

  async function refreshProjects() {
    if (!window.pywebview?.api?.list_projects) return;
    const result = await call("list_projects");
    projectsById.clear();
    for (const project of result.projects) projectsById.set(project.id, project);
    if (selectedProject && !projectsById.has(selectedProject)) selectedProject = null;
    renderProjectBar();
    const nodes = result.projects.map(project => {
      const item = document.createElement("li");
      const node = button(project.name, "project-item", () => selectProject(project.id === selectedProject ? null : project.id));
      node.setAttribute("aria-pressed", project.id === selectedProject ? "true" : "false");
      node.addEventListener("contextmenu", event => showMenu(event, [
        [t("Open"), () => selectProject(project.id)],
        [t("New chat in project"), async () => { await selectProject(project.id); newChat().catch(showError); }],
        [t("Edit project…"), () => openProjectDialog(project)],
        [t("Delete project"), () => deleteProject(project), "danger"],
      ]));
      item.append(node);
      return item;
    });
    element("projects").replaceChildren(...nodes);
    element("chats-label").textContent = selectedProject
      ? (result.projects.find(project => project.id === selectedProject)?.name || t("Project")) + " · " + t("Chats")
      : t("Recent");
  }

  async function selectProject(id) {
    selectedProject = id;
    try {
      await refreshProjects();
      await refreshChats();
      if (selectedProject !== id) return;
      if (chats.size) await openChat(chats.keys().next().value);
      else {
        if (!openingChat && currentChat() && editingIndex === null) rememberDraft(selected, element("prompt").value);
        selectionVersion++;
        selected = openedChat = null;
        openingChat = false;
        editingIndex = null;
        element("edit-hint").hidden = true;
        element("prompt").value = "";
        renderMessages();
      }
    } catch (error) { showError(error); }
  }

  async function openChat(id) {
    if (!openingChat && currentChat() && editingIndex === null) rememberDraft(selected, element("prompt").value);
    editingIndex = null;
    element("edit-hint").hidden = true;
    const version = ++selectionVersion;
    selected = id;
    openingChat = true;
    controls();
    try {
      await reloadChat(id);
      if (version !== selectionVersion) return;
      const record = currentChat();
      if (!record) return;
      element("prompt").value = draftText(id);
      if (record.model) element("model").value = modelValue(record.model, record.backend || record.environment?.backend);
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
    const request = call("open_chat", id);
    chatLoads.set(id, request);
    try {
      const result = await request;
      if (chatLoads.get(id) !== request) {
        await chatLoads.get(id);
        return;
      }
      if (selected === id) openedChat = {...result.chat, versions: result.versions || {}, unsaved_replies: result.unsaved_replies || []};
      if (chats.has(id)) chats.set(id, sidebarChat(result.chat));
    } finally {
      if (chatLoads.get(id) === request) chatLoads.delete(id);
    }
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

  // Stored titles stay in English ("New chat" is the engine's default); show it translated.
  function chatTitle(record) { return record.title === "New chat" ? t("New chat") : record.title; }

  // The status bar polls the engine, so it learns the loaded context after the chat was opened.
  function syncContext(value) {
    if (!value || value.chat_id !== selected || !value.context_total) return;
    const used = value.context_used || 0, total = value.context_total;
    element("context-usage").textContent = `${tokens(used)} / ${tokens(total)}`;
    element("context-meter").value = Math.min(1, used / total);
    element("context-menu").className = "ctx level-" + (used * 100 >= total * 95 ? "full" : used * 100 >= total * 80 ? "warn" : "ok");
  }

  function tokens(n) {
    if (!Number.isFinite(n) || n < 1000) return String(n ?? "?");
    if (n % 1024 === 0) return n / 1024 + "k";  // model contexts are powers of two: 16384 → 16k
    const value = (Math.round(n / 100) / 10).toFixed(1);
    return (value.endsWith(".0") ? value.slice(0, -2) : value) + "k";
  }

  async function refreshChats() {
    const project = selectedProject;
    const version = ++sidebarVersion;
    const result = project ? await call("list_chats", project) : await call("list_chats");
    if (version !== sidebarVersion || selectedProject !== project) return;
    chats.clear();
    for (const chat of result.chats) chats.set(chat.id, sidebarChat(chat));
    renderChats();
  }

  function sidebarChat({id, title, updated_at, incognito}) {
    return {id, title, updated_at, incognito};
  }

  // Newest chats go to the top of the sidebar.
  function addChatFirst(record) {
    sidebarVersion++;
    const rest = [...chats].filter(([id]) => id !== record.id);
    chats.clear();
    chats.set(record.id, sidebarChat(record));
    for (const [id, value] of rest) chats.set(id, value);
  }

  async function newChat(incognito = false) {
    const result = selectedProject || incognito ? await call("new_chat", incognito, selectedProject) : await call("new_chat");
    addChatFirst(result.chat);
    renderChats();
    await openChat(result.chat.id);
    if (incognito) status(t("Incognito chat: not saved, no memory."));
  }

  async function refreshModels() {
    engineReady = false;
    controls();
    const result = await call("state");
    lastState = result;
    engineReady = true;
    const previous = element("model").value;
    const groups = [["Ollama", result.ollama?.models || (result.engine === "Ollama" ? result.models : []), true],
                    ["Colibri", result.colibri?.models || [], Boolean(result.colibri?.running)]];
    const values = [];
    element("model").replaceChildren(...groups.map(([backend, names, ready]) => {
      const group = document.createElement("optgroup");
      group.label = backend;
      group.disabled = !ready || !names.length;
      if (!names.length) {
        const option = document.createElement("option");
        option.disabled = true;
        option.textContent = backend === "Colibri" && !result.colibri?.installed ? t("Not installed or no server available") : t("No running model available");
        group.append(option);
      }
      for (const name of names) {
        const option = document.createElement("option");
        option.value = modelValue(name, backend); option.textContent = name;
        values.push(option.value); group.append(option);
      }
      return group;
    }));
    element("model").value = values.includes(previous) ? previous : values[0] || "";
    window.workspaceui?.onState(result);
    window.premiumui?.onState(result);
    window.parityui?.onState(result);
    status(t("{v0}: {v1}. {v2} models available.", {v0: (result.engine), v1: (result.host), v2: (result.models.length)}));
    controls();
  }

  function showError(error) { status(error.message || String(error)); controls(); }

  function modelValue(name, backend = "Ollama") { return backend === "Colibri" ? JSON.stringify([backend, name]) : name; }
  function modelChoice() {
    const value = element("model").value;
    if (value.startsWith("[")) {
      try { const pair = JSON.parse(value); if (pair[0] === "Colibri" && typeof pair[1] === "string") return {backend: pair[0], name: pair[1]}; } catch (error) { /* ordinary model tag */ }
    }
    return {backend: "Ollama", name: value};
  }

  async function send(event) {
    event.preventDefault();
    const chatId = selected;
    const text = element("prompt").value;
    if (!chatId || openingChat || submitting || !text.trim() || activeStream(chatId)) return;
    const choice = modelChoice(), model = choice.name;
    const rawContext = element("context").value;
    const editing = editingIndex;
    const draft = editing === null ? rememberDraft(chatId, text) : null;
    const submittedVersion = draft?.version;
    submitting = true;
    if (draft) {
      draft.paused = true;
      cancelDraftTimer(draft);
    }
    controls();
    try {
      if (draft?.pending) await draft.pending;
      const result = editing !== null
        ? await call("edit_and_resend", chatId, editing, text)
        : await call("send", chatId, text, model, rawContext ? Number(rawContext) : null, choice.backend);
      track(chatId, result);
      if (draft && draft.version === submittedVersion) {
        draft.text = "";
        draft.dirty = false;
        if (selected === chatId && !openingChat && editingIndex === null) element("prompt").value = "";
      }
      if (selected === chatId && editing !== null && editingIndex === editing) {
        editingIndex = null;
        element("edit-hint").hidden = true;
        element("prompt").value = draftText(chatId);
      }
      if (selected === chatId) await reloadChat(chatId);
      else await refreshChats();
      if (selected === chatId && streams.get(result.stream_id).active) status(t("Generating…"));
      if (selected === chatId && !openingChat) renderMessages();
      renderChats();
    } catch (error) { showError(error); }
    finally {
      if (draft && drafts.get(chatId) === draft) {
        draft.paused = false;
        queueDraftSave(chatId, draft);
      }
      submitting = false;
      controls();
    }
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
      status(t("Unloading {v0}…", {v0: (name)}));
      await call("unload_model", name);
    } catch (error) { showError(error); }
  }

  function gigabytes(bytes) { return (bytes / 1024 ** 3).toFixed(1) + " GB"; }

  function appEvent(event) {
    switch (event.type) {
      case "vram_warning":
        showBanner(t("{v0} models loaded need {v1}, more than the {v2} of GPU memory. Responses slow down.", {v0: (event.models.length), v1: (gigabytes(event.required_bytes)), v2: (gigabytes(event.gpu_memory_bytes))}),
          event.models.map(name => button(t("Unload ") + name, "btn", () => unloadModel(name))));
        return true;
      case "model_unloaded":
        status(t("{v0} unloaded from memory.", {v0: (event.model)}));
        element("banner").hidden = true;
        return true;
      case "attached":
        if (event.chat_id === selected) window.app.refreshChat(event.chat_id).catch(showError);
        return true;
      case "project_changed":
      case "project_deleted":
        refreshProjects().catch(showError);
        return true;
      case "chat_changed":
        refreshChats().then(() => window.app.refreshChat(event.chat_id)).catch(showError);
        return true;
      case "chat_deleted":
        sidebarVersion++;
        chats.delete(event.chat_id);
        chatLoads.delete(event.chat_id);
        if (drafts.has(event.chat_id)) cancelDraftTimer(drafts.get(event.chat_id));
        drafts.delete(event.chat_id);
        renderChats();
        if (event.chat_id === selected) {
          selected = null;
          (chats.size ? openChat(chats.keys().next().value) : newChat()).catch(showError);
        }
        return true;
      case "memories_changed":
        return true;
      case "checkpoint_restored":
        status(t("Restored {v0} files from the task checkpoint.", {v0: (event.restored)}));
        return true;
      default:
        return false;
    }
  }

  function onEvent(batch) {
    const liveReplies = new Set();
    let sidebarChanged = false;
    for (const event of Array.isArray(batch) ? batch : [batch]) {
      if (event.type === "ready") { refreshModels().catch(showError); continue; }
      // Agent progress belongs to the Developer panel, not to the engine label in the status bar.
      if (event.type === "status" && event.task_id && !event.engine) { window.devui?.onEvent(event); continue; }
      if (event.type === "status") {
        if (event.engine) void window.devui?.syncTask?.();
        if (event.engine && (!event.chat_id || event.chat_id === selected)) window.workspaceui?.renderStatus(event);
        else if (event.text) status(event.text);
        continue;
      }
      window.workspaceui?.onEvent(event);
      window.welcomeui?.onEvent(event);
      if (window.sharingui?.onEvent(event)) continue;
      if (window.securityui?.onEvent(event)) continue;
      if (window.computeui?.onEvent(event)) continue;
      if (window.hourlyui?.onEvent(event)) continue;
      if (window.parityui?.onEvent(event)) continue;
      if (event.type === "model_loading" || event.type === "model_loaded") {
        const text = event.type === "model_loading"
          ? t("Loading {v0}… {v1} s", {v0: (event.model), v1: (Math.floor(event.elapsed_seconds))})
          : t("{v0} ready in {v1} s", {v0: (event.model), v1: (event.load_seconds.toFixed(1))});
        if (event.chat_id === selected) status(text);
        if (event.task_id) window.devui?.onEvent({...event, type: "loading", text});
        continue;
      }
      if (appEvent(event)) continue;
      if (window.modelsui?.onEvent(event)) continue;  // downloads and model list changes
      if (event.task_id) { window.devui?.onEvent(event); continue; }  // Developer task events
      if (!event.stream_id) { if (event.type === "error") showError(new Error(window.i18n?.gate?.(event.error) || event.error)); continue; }
      let stream = streams.get(event.stream_id);
      if (!stream) {
        stream = {id: event.stream_id, chat_id: event.chat_id, text: "", active: true, operation: event.operation || "chat"};
        streams.set(event.stream_id, stream);
        sidebarChanged = true;
      }
      if (event.type === "token" && stream.active) {
        stream.text += event.text;
        liveReplies.add(stream);
      }
      if (["done", "stopped", "error", "compact_done"].includes(event.type)) {
        if (!stream.active) continue;
        stream.active = false;
        stream.saved = event.saved;
        sidebarChanged = true;
        if (stream.saved === false) {
          if (event.text !== undefined) stream.text = event.text;
          liveReplies.add(stream);
        } else {
          stream.text = "";
          liveReplies.delete(stream);
        }
        if (selected === event.chat_id) status(event.type === "error" ? event.error : `${t(event.type)}${event.metrics?.eval_count ? ` · ${event.metrics.eval_count} tokens${rate(event.metrics)}` : ""}`);
        if (selected === event.chat_id) {
          window.app.refreshChat(event.chat_id).then(() => {
            if (selected === event.chat_id) {
              renderChats();
              refreshUsage(event.chat_id);
            }
          }).catch(error => { if (selected === event.chat_id) showError(error); });
        } else {
          refreshChats().catch(showError);
        }
      }
    }
    for (const stream of liveReplies) updateLiveReply(stream);
    if (sidebarChanged) {
      renderChats();
      renderInterrupted(currentChat());
      controls();
    }
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

  // Settings categories: each tab shows the sections tagged with its data-pane; the choice lasts the session.
  let settingsPane = "general";
  function showSettingsPane(name) {
    const tabs = [...(document.querySelectorAll?.(".settings-tab") || [])];
    if (!tabs.some(tab => tab.dataset.pane === name)) return;
    settingsPane = name;
    for (const tab of tabs) {
      const active = tab.dataset.pane === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    for (const section of document.querySelectorAll?.(".settings-panes > .setting-group") || []) section.hidden = section.dataset.pane !== name;
    const panes = document.querySelector?.(".settings-panes");
    if (panes) panes.scrollTop = 0;
  }
  for (const tab of document.querySelectorAll?.(".settings-tab") || []) {
    tab.addEventListener("click", () => showSettingsPane(tab.dataset.pane));
    tab.addEventListener("keydown", event => {
      const tabs = [...document.querySelectorAll(".settings-tab")], index = tabs.indexOf(tab);
      const step = {ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      const next = tabs[(index + step + tabs.length) % tabs.length];
      showSettingsPane(next.dataset.pane); next.focus();
    });
  }
  // Windows-only controls (firewall lock, microphone check-ins) are hidden on other systems.
  if (root?.dataset) root.dataset.platform = /Windows/i.test(window.navigator?.userAgent || "") ? "windows" : /Mac/i.test(window.navigator?.userAgent || "") ? "mac" : "other";

  async function openSettings() {
    showSettingsPane(settingsPane);
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
      row.append(label, button(t("Unload"), "btn", async () => { await unloadModel(name); openSettings(); }));
      return row;
    }) : [Object.assign(document.createElement("p"), {className: "setting-hint", textContent: t("No models are loaded right now.")})]));
    window.workspaceui?.openSettings();
    window.parityui?.openSettings();
    window.securityui?.openSettings();
    window.sharingui?.openSettings();
    window.hourlyui?.openSettings();
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
    if (window.workspaceui?.promptKey(event)) return;
    if (event.key === "ArrowUp" && !element("prompt").value && !event.isComposing && !activeStream(selected)) {
      const messages = currentChat()?.messages || [];
      for (let index = messages.length - 1; index >= 0; index--) {
        if (messages[index].role === "user") { event.preventDefault(); startEditing(index); return; }
      }
    }
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
      if (window.premiumui?.key(event)) return;
      const ctrl = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (ctrl && event.shiftKey && key === "l") { event.preventDefault(); toggleTheme(); }
      else if (ctrl && event.shiftKey && key === "n") { event.preventDefault(); newChat(true).catch(showError); }
      else if (ctrl && !event.shiftKey && key === "n") { event.preventDefault(); newChat().catch(showError); }
      else if (ctrl && key === ",") { event.preventDefault(); openSettings(); }
      else if (ctrl && key === "k") { event.preventDefault(); openPalette(); }
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
    const node = event.target?.closest?.('.code-actions [data-action]');
    if (!node) return;
    const code = node.parentElement?.nextElementSibling;
    if (!code) return;
    if (node.getAttribute("data-action") === "preview") {
      call("preview_code", code.textContent).then(() => status(t("Preview opened in a separate window."))).catch(showError);
    } else if (node.getAttribute("data-action") === "save") {
      const language = code.firstChild?.className?.replace("lang-", "") || "txt";
      const extension = ({python: "py", javascript: "js", typescript: "ts", text: "txt"})[language] || language;
      call("save_code", code.textContent, "code." + extension.replace(/[^a-z0-9]/gi, "")).then(result => { if (!result.cancelled) status(t("Code saved.")); }).catch(showError);
    } else copyText(code.textContent, node);
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
      status(t("Saved."));
    } catch (error) { showError(error); }
  });
  element("new-project").addEventListener("click", () => openProjectDialog(null));
  element("project-edit").addEventListener("click", () => openProjectDialog(projectsById.get(selectedProject)));
  element("project-new-chat").addEventListener("click", () => newChat().catch(showError));
  element("project-close").addEventListener("click", () => selectProject(null));
  element("project-browse").addEventListener("click", () => browseFolder("project-folder"));
  element("project-cancel").addEventListener("click", () => closeDialog("project-dialog"));
  element("project-delete").addEventListener("click", () => { if (editingProject) deleteProject(editingProject); });
  element("project-form").addEventListener("submit", async event => {
    event.preventDefault();
    const folder = element("project-folder").value.trim() || null;
    const name = element("project-name").value.trim();
    const instructions = element("project-instructions").value;
    const useEnv = element("project-use-env").checked;
    try {
      let id;
      if (editingProject) {
        const changes = {name, instructions, folder};
        if (useEnv) changes.default_environment = currentEnvironment(editingProject.default_environment);
        id = (await call("update_project", editingProject.id, changes)).project.id;
      } else {
        id = (await call("create_project", name, instructions, folder, null, useEnv ? currentEnvironment() : null)).project.id;
      }
      closeDialog("project-dialog");
      await selectProject(id);
      status(editingProject ? t("Project saved.") : t("Project created: new chats here start with its setup."));
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
      if (selected === chatId && streams.get(result.stream_id).active) status(t("Compacting…"));
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
    status(t("Stopping; waiting for the stream to close…"));
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
    if (lastState?.interrupted?.length) status(t("{v0} chats have an interrupted reply: open them to keep or discard it.", {v0: lastState.interrupted.length}));
    if (window.pywebview.api.status_snapshot) call("status_snapshot").then(value => window.workspaceui?.renderStatus(value)).catch(showError);
    window.welcomeui?.onReady(lastState);
  });
  // ---------- right-click menu ----------
  function showMenu(event, items) {
    event.preventDefault();
    const menu = element("popup-menu");
    menu.replaceChildren(...items.map(([label, run, kind]) => {
      const item = button(label, "popup-item" + (kind ? " " + kind : ""), () => { hideMenu(); run(); });
      item.setAttribute("role", "menuitem");
      return item;
    }));
    menu.hidden = false;
    const width = menu.offsetWidth || 200, height = menu.offsetHeight || 120;
    menu.style.left = Math.min(event.clientX, window.innerWidth - width - 8) + "px";
    menu.style.top = Math.min(event.clientY, window.innerHeight - height - 8) + "px";
    menu.firstChild?.focus?.();
  }
  function hideMenu() { element("popup-menu").hidden = true; }
  if (document.addEventListener) {
    document.addEventListener("click", event => { if (!element("popup-menu").contains?.(event.target)) hideMenu(); });
    document.addEventListener("keydown", event => { if (event.key === "Escape") hideMenu(); }, true);
    window.addEventListener("blur", hideMenu);
  }

  // ---------- rename / delete / export ----------
  let renameTarget = null;
  function startRename(id) {
    const record = chats.get(id);
    if (!record) return;
    renameTarget = id;
    element("rename-input").value = chatTitle(record);
    openDialog("rename-dialog");
    element("rename-input").select?.();
  }
  async function deleteChat(id) {
    const record = chats.get(id);
    if (!record) return;
    if (window.confirm && !window.confirm(t("Delete \"{v0}\"? This removes the chat and its attachments from this computer.", {v0: (chatTitle(record))}))) return;
    try {
      const result = await call("delete_chat", id);
      status(result.warnings?.length ? result.warnings.join(" ") : t("Chat deleted."));
    } catch (error) { showError(error); }
  }
  async function deleteProject(project) {
    if (window.confirm && !window.confirm(t("Delete the project \"{v0}\"? Its chats are kept and move back to Recent.", {v0: (project.name)}))) return;
    try {
      await call("delete_project", project.id);
      closeDialog("project-dialog");
      if (selectedProject === project.id) await selectProject(null); else await refreshProjects();
    } catch (error) { showError(error); }
  }
  element("rename-chat").addEventListener("click", () => startRename(selected));
  element("rename-cancel").addEventListener("click", () => closeDialog("rename-dialog"));
  element("rename-form").addEventListener("submit", async event => {
    event.preventDefault();
    const id = renameTarget;
    try {
      const result = await call("rename_chat", id, element("rename-input").value);
      closeDialog("rename-dialog");
      if (chats.has(id)) chats.set(id, sidebarChat(result.chat));
      if (selected === id && currentChat()) {
        openedChat.title = result.chat.title;
        element("chat-title").textContent = chatTitle(openedChat);
      }
      renderChats();
    } catch (error) { showError(error); }
  });
  element("delete-chat").addEventListener("click", () => deleteChat(selected));
  element("export-chats").addEventListener("click", async () => {
    try {
      const result = await call("export_chats");
      element("export-result").textContent = result.cancelled ? "" : t("Saved {v0} chats to {v1}", {v0: (result.count), v1: (result.path)});
    } catch (error) { element("export-result").textContent = error.message; }
  });

  // ---------- Ctrl+K palette: search chats and run commands ----------
  const commands = () => [
    [t("New chat"), t("Ctrl+N"), () => newChat().catch(showError)],
    [t("New incognito chat"), t("Ctrl+Shift+N"), () => newChat(true).catch(showError)],
    [t("Chats"), "", () => showView("chat")],
    [t("Developer"), "", () => showView("dev")],
    [t("Models"), "", () => showView("models")],
    [t("Generate"), "", () => showView("generate")],
    [t("Mission Control"), "", () => openMissionControl()],
    [t("Settings"), t("Ctrl+,"), () => openSettings()],
    [t("Switch theme"), t("Ctrl+Shift+L"), () => toggleTheme()],
  ];
  let paletteItems = [];
  let paletteIndex = 0;
  let paletteTimer = null;

  function openPalette() {
    element("palette-input").value = "";
    openDialog("palette");
    renderPalette([]);
    element("palette-input").focus?.();
  }

  function renderPalette(results) {
    const query = element("palette-input").value.trim().toLowerCase();
    const matching = commands().filter(([label]) => !query || label.toLowerCase().includes(query));
    paletteItems = [];
    const nodes = [];
    const group = text => { const li = document.createElement("li"); li.className = "palette-group"; li.textContent = text; nodes.push(li); };
    const item = (title, snippet, run) => {
      const li = document.createElement("li");
      const node = button("", "palette-item", () => { closeDialog("palette"); run(); });
      const t = document.createElement("span"); t.className = "palette-title"; t.textContent = title;
      node.append(t);
      if (snippet) { const s = document.createElement("span"); s.className = "palette-snippet"; s.textContent = snippet; node.append(s); }
      li.append(node);
      nodes.push(li);
      paletteItems.push(node);
    };
    if (results.length) { group(t("Chats")); for (const chat of results.slice(0, 30)) item(chatTitle(chat), chat.snippet, () => { showView("chat"); openChat(chat.id).catch(showError); }); }
    if (matching.length) { group(t("Commands")); for (const [label, keys, run] of matching) item(label, keys, run); }
    element("palette-list").replaceChildren(...nodes);
    paletteIndex = 0;
    highlightPalette();
  }

  function highlightPalette() { paletteItems.forEach((node, index) => node.setAttribute("aria-selected", index === paletteIndex ? "true" : "false")); }

  element("palette-input").addEventListener("input", () => {
    if (typeof clearTimeout === "function") clearTimeout(paletteTimer);
    const query = element("palette-input").value.trim();
    if (!query) { renderPalette([]); return; }
    paletteTimer = later(async () => {
      try { renderPalette((await call("search_chats", query)).chats); } catch (error) { renderPalette([]); }
    }, 150);
  });
  element("palette-input").addEventListener("keydown", event => {
    if (event.key === "ArrowDown") { event.preventDefault(); paletteIndex = Math.min(paletteItems.length - 1, paletteIndex + 1); highlightPalette(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); highlightPalette(); }
    else if (event.key === "Enter") { event.preventDefault(); paletteItems[paletteIndex]?.click(); }
  });
  element("open-palette").addEventListener("click", openPalette);

  // ---------- workspace sections ----------
  const views = {
    chat: ["chat-view", "nav-chats"],
    dev: ["dev-view", "nav-dev"],
    models: ["models-view", "nav-models"],
    welcome: ["welcome-view", null],
    capabilities: ["capabilities-view", "nav-capabilities"],
    generate: ["generate-view", "nav-generate"],
  };
  function showView(name) {
    for (const [key, [view, nav]] of Object.entries(views)) {
      element(view).hidden = key !== name;
      if (nav) element(nav).setAttribute("aria-current", key === name ? "page" : "false");
    }
    if (name !== "generate") {
      element("photo-video-preview").pause?.();
      window.quickAnimation?.pause();
    }
    if (name === "dev") window.devui?.onShow?.();
    if (name === "models") window.modelsui?.onShow?.();
    if (name === "capabilities") window.parityui?.capabilities();
  }
  element("nav-chats").addEventListener("click", () => showView("chat"));
  element("nav-dev").addEventListener("click", () => showView("dev"));
  element("nav-models").addEventListener("click", () => showView("models"));
  element("nav-capabilities").addEventListener("click", () => showView("capabilities"));
  element("nav-generate").addEventListener("click", () => showView("generate"));
  // Mission Control opens the live Compute window (for a second screen); it is not a view in this window.
  const openMissionControl = () => call("open_compute_window").catch(showError);
  element("open-mission-control").addEventListener("click", openMissionControl);
  element("open-models").addEventListener("click", () => showView("models"));

  async function selectModel(name, backend = "Ollama") {
    element("model").value = modelValue(name, backend);
    controls();
    if (selected && window.pywebview?.api?.set_chat_model) {
      try { await call("set_chat_model", selected, name, backend); await reloadChat(selected); }
      catch (error) { showError(error); throw error; }
    }
  }

  function setPrompt(text) { element("prompt").value = text; fitPrompt(); scheduleDraft(); element("prompt").focus?.(); }
  window.addEventListener("languagechange", () => { renderMessages(); renderChats(); renderProjectBar(); });
  return {onEvent, openSettings, showView, syncContext, selectModel, modelChoice, setPrompt,
          currentChat, refreshChat: async id => {
            if (selected !== id) return;
            await reloadChat(id);
            if (selected === id && !openingChat) renderMessages();
          },
          refreshModels: () => refreshModels().catch(showError)};
})();
