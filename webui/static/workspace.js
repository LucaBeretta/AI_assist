/* Shared memory, saved prompts and status controls over the D5 API. */
"use strict";
window.workspaceui = (() => {
  const el = id => document.getElementById(id);
  const node = (tag, text, cls = "") => {
    const item = document.createElement(tag); item.className = cls;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const call = async (method, ...args) => {
    const value = await window.pywebview.api[method](...args);
    if (!value || value.ok === false) throw new Error(value?.error || "Invalid API response");
    return value;
  };
  const show = id => { const dialog = el(id); if (dialog.showModal) dialog.showModal(); else dialog.open = true; };
  const close = id => { const dialog = el(id); if (dialog.close) dialog.close(); else dialog.open = false; };
  const error = value => { el("status").textContent = value.message || String(value); };
  function button(text, action) {
    const item = node("button", text, "btn ghost"); item.type = "button";
    item.addEventListener("click", () => Promise.resolve().then(action).catch(error)); return item;
  }
  let submitText = null, memoryChat = null, memoryChecks = [], prompts = [], promptIndex = 0, promptMatches = [];
  function editText(title, text, max, submit) {
    el("text-dialog-title").textContent = title;
    el("text-dialog-label").textContent = title;
    el("text-dialog-input").value = text; el("text-dialog-input").maxLength = max;
    el("text-dialog-error").textContent = ""; submitText = submit;
    show("text-dialog"); el("text-dialog-input").focus?.();
  }
  el("text-dialog-form").addEventListener("submit", async event => {
    event.preventDefault();
    try { await submitText(el("text-dialog-input").value); close("text-dialog"); }
    catch (failure) { el("text-dialog-error").textContent = failure.message; }
  });
  el("text-dialog-cancel").addEventListener("click", () => close("text-dialog"));

  async function renderMemories() {
    const result = await call("list_memories");
    el("memory-enabled").checked = result.enabled;
    el("memory-state").textContent = result.enabled ? "Shared memory is included in replies. Each reply shows the notes it used." : "Off. Shared memory is not sent to the model.";
    el("memory-list").replaceChildren(...result.memories.map(item => {
      const row = node("div", undefined, "mem-row");
      row.append(node("span", item.text), button("Edit", () => editText("Edit memory", item.text, 4000,
        async text => { await call("edit_memory", item.id, text); await renderMemories(); })),
        button("Forget", async () => { await call("forget_memory", item.id); await renderMemories(); }));
      return row;
    }));
  }
  el("memory-enabled").addEventListener("change", async () => {
    try { await call("set_memory_enabled", el("memory-enabled").checked); await renderMemories(); }
    catch (failure) { error(failure); await renderMemories().catch(error); }
  });
  el("memory-add").addEventListener("click", () => editText("Add memory", "", 4000,
    async text => { await call("add_memory", text); await renderMemories(); }));

  async function openInstructions(id) {
    try {
      const {chat} = await call("open_chat", id);
      editText("Chat instructions", chat.environment.instructions, 1000000,
        async text => { await call("set_chat_instructions", id, text); await window.app?.refreshChat(id); });
    } catch (failure) { error(failure); }
  }
  async function openMemories(id) {
    try {
      const [{chat}, result] = await Promise.all([call("open_chat", id), call("list_memories")]);
      if (chat.incognito) throw new Error("Incognito chats never use shared memory.");
      memoryChat = id;
      el("chat-memory-all").checked = !chat.environment.memory_ids.length;
      memoryChecks = result.memories.map(item => {
        const label = node("label", undefined, "check-row"), check = node("input");
        check.type = "checkbox"; check.value = item.id; check.checked = chat.environment.memory_ids.includes(item.id);
        label.append(check, node("span", item.text)); return {id: item.id, check, label};
      });
      el("chat-memory-list").replaceChildren(...memoryChecks.map(item => item.label));
      updateChecks(); el("chat-memory-error").textContent = ""; show("chat-memory-dialog");
    } catch (failure) { error(failure); }
  }
  function updateChecks() { for (const item of memoryChecks) item.check.disabled = el("chat-memory-all").checked; }
  el("chat-memory-all").addEventListener("change", updateChecks);
  el("chat-memory-cancel").addEventListener("click", () => close("chat-memory-dialog"));
  el("chat-memory-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const ids = el("chat-memory-all").checked ? [] : memoryChecks.filter(item => item.check.checked).map(item => item.id);
      if (!el("chat-memory-all").checked && !ids.length) throw new Error("Choose a memory or select all memories.");
      await call("set_chat_memories", memoryChat, ids); await window.app?.refreshChat(memoryChat); close("chat-memory-dialog");
    } catch (failure) { el("chat-memory-error").textContent = failure.message; }
  });

  async function loadPrompts() {
    prompts = (await call("list_prompts")).prompts;
    el("saved-prompts-list").replaceChildren(...prompts.map(item => {
      const row = node("div", undefined, "setting-row");
      row.append(node("span", item.name), button("Edit", () => {
        el("saved-prompt-name").value = item.name; el("saved-prompt-text").value = item.text;
      }), button("Delete", async () => { await call("delete_prompt", item.name); await loadPrompts(); }));
      return row;
    }));
  }
  el("saved-prompt-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await call("save_prompt", el("saved-prompt-name").value, el("saved-prompt-text").value);
      el("saved-prompt-name").value = ""; el("saved-prompt-text").value = ""; await loadPrompts();
    } catch (failure) { error(failure); }
  });
  function insertPrompt(item) {
    window.app?.setPrompt(item.text); el("saved-prompt-menu").hidden = true; promptMatches = [];
  }
  function renderPromptMenu() {
    const value = el("prompt").value;
    promptMatches = value.startsWith("/") && !value.includes("\n") ? prompts.filter(item => item.name.toLowerCase().includes(value.slice(1).toLowerCase())).slice(0, 8) : [];
    promptIndex = Math.min(promptIndex, Math.max(0, promptMatches.length - 1));
    el("saved-prompt-menu").hidden = !promptMatches.length;
    el("saved-prompt-menu").replaceChildren(...promptMatches.map((item, index) => {
      const entry = button("/" + item.name, () => insertPrompt(item));
      entry.setAttribute("role", "option"); entry.setAttribute("aria-selected", String(index === promptIndex)); return entry;
    }));
  }
  el("prompt").addEventListener("input", renderPromptMenu);
  el("prompt").addEventListener("focus", () => { loadPrompts().then(renderPromptMenu).catch(error); });
  function promptKey(event) {
    if (!promptMatches.length || el("saved-prompt-menu").hidden || event.isComposing) return false;
    if (event.key === "Enter") { event.preventDefault(); insertPrompt(promptMatches[promptIndex]); return true; }
    if (event.key === "Escape") { event.preventDefault(); el("saved-prompt-menu").hidden = true; return true; }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); promptIndex = (promptIndex + (event.key === "ArrowDown" ? 1 : -1) + promptMatches.length) % promptMatches.length;
      renderPromptMenu(); return true;
    }
    return false;
  }
  function renderStatus(value) {
    if (value.ok === false) return;
    el("status").textContent = value.engine;
    el("status-model").textContent = value.model_name || "";
    el("status-placement").textContent = value.gpu_fraction == null ? (value.backend === "Colibri" ? "ram + ssd" : "")
      : `${Math.round(value.gpu_fraction * 100)}% gpu · ${Math.round(value.cpu_fraction * 100)}% cpu`;
    el("status-vram").textContent = value.vram || "";
    el("status-context").textContent = value.context || ""; el("status-meter").value = value.fraction || 0;
    el("status-speed").textContent = value.speed || ""; el("status-agent").textContent = value.agent || "";
    el("status-privacy").textContent = value.privacy_kind || value.privacy;
    el("status-privacy").className = "privacy" + (value.privacy_kind && value.privacy_kind !== "local only" ? " is-remote" : "");
    el("status-meter").className = value.fraction >= .95 ? "level-full" : value.fraction >= .8 ? "level-warn" : "";
  }
  function onEvent(event) {
    if (event.type === "memories_changed" && el("settings-dialog").open) renderMemories().catch(error);
    if (event.type === "prompts_changed") loadPrompts().then(renderPromptMenu).catch(error);
  }
  function openSettings() { renderMemories().catch(error); loadPrompts().catch(error); }
  function onState() { if (!prompts.length && window.pywebview.api.list_prompts) loadPrompts().catch(error); }
  return {openSettings, openMemories, openInstructions, renderStatus, promptKey, onEvent, onState};
})();
