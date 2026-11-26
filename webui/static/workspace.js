/* Shared memory, saved prompts and status controls over the D5 API. */
"use strict";
window.workspaceui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const el = id => document.getElementById(id);
  const node = (tag, text, cls = "") => {
    const item = document.createElement(tag); item.className = cls;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const call = async (method, ...args) => {
    const value = await window.pywebview.api[method](...args);
    if (!value || value.ok === false) throw new Error(window.i18n?.gate?.(value?.error) || value?.error || t("Invalid API response"));
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
    el("memory-state").textContent = result.enabled ? t("Shared memory is included in replies. Each reply shows the notes it used.") : t("Off. Shared memory is not sent to the model.");
    el("memory-list").replaceChildren(...result.memories.map(item => {
      const row = node("div", undefined, "mem-row");
      row.append(node("span", item.text), button(t("Edit"), () => editText(t("Edit memory"), item.text, 4000,
        async text => { await call("edit_memory", item.id, text); await renderMemories(); })),
        button(t("Forget"), async () => { await call("forget_memory", item.id); await renderMemories(); }));
      return row;
    }));
  }
  el("memory-enabled").addEventListener("change", async () => {
    try { await call("set_memory_enabled", el("memory-enabled").checked); await renderMemories(); }
    catch (failure) { error(failure); await renderMemories().catch(error); }
  });
  el("memory-add").addEventListener("click", () => editText(t("Add memory"), "", 4000,
    async text => { await call("add_memory", text); await renderMemories(); }));

  async function openInstructions(id) {
    try {
      const {chat} = await call("open_chat", id);
      editText(t("Chat instructions"), chat.environment.instructions, 1000000,
        async text => { await call("set_chat_instructions", id, text); await window.app?.refreshChat(id); });
    } catch (failure) { error(failure); }
  }
  async function openMemories(id) {
    try {
      const [{chat}, result] = await Promise.all([call("open_chat", id), call("list_memories")]);
      if (chat.incognito) throw new Error(t("Incognito chats never use shared memory."));
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
      if (!el("chat-memory-all").checked && !ids.length) throw new Error(t("Choose a memory or select all memories."));
      await call("set_chat_memories", memoryChat, ids); await window.app?.refreshChat(memoryChat); close("chat-memory-dialog");
    } catch (failure) { el("chat-memory-error").textContent = failure.message; }
  });

  async function loadPrompts() {
    prompts = (await call("list_prompts")).prompts;
    el("saved-prompts-list").replaceChildren(...prompts.map(item => {
      const row = node("div", undefined, "setting-row");
      row.append(node("span", item.name), button(t("Edit"), () => {
        el("saved-prompt-name").value = item.name; el("saved-prompt-text").value = item.text;
      }), button(t("Delete"), async () => { await call("delete_prompt", item.name); await loadPrompts(); }));
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
    el("status").textContent = value.backend && value.engine_state ? value.backend.toLowerCase() + " " + t(value.engine_state) : value.engine;
    el("status-model").textContent = value.model_name || "";
    el("status-placement").textContent = value.gpu_fraction == null ? (value.backend === "Colibri" ? "ram + ssd" : "")
      : `${Math.round(value.gpu_fraction * 100)}% gpu · ${Math.round(value.cpu_fraction * 100)}% cpu`;
    el("status-vram").textContent = value.vram || "";
    el("status-speed").textContent = value.speed || "";
    el("status-agent").textContent = value.agent ? t("Agent step {v0} / {v1}", {v0: value.agent_step, v1: value.agent_max_steps}) : "";
    el("status-privacy").textContent = window.securityui ? window.securityui.privacyText(value.privacy) : t(value.privacy_kind || value.privacy || "");
    el("status-privacy").className = "privacy" + (value.privacy_role === "amber" || value.privacy_kind && value.privacy_kind !== "local only" ? " is-remote" : "");
    window.app?.syncContext?.(value);
  }
  function onEvent(event) {
    if (event.type === "memories_changed" && el("settings-dialog").open) renderMemories().catch(error);
    if (event.type === "prompts_changed") loadPrompts().then(renderPromptMenu).catch(error);
  }
  function openSettings() {
    el("setting-language").value = window.i18n?.preference || "system";
    renderMemories().catch(error); loadPrompts().catch(error);
  }
  function onState(state) {
    window.i18n?.setLanguage(state.settings?.language || "system");
    if (!prompts.length && window.pywebview.api.list_prompts) loadPrompts().catch(error);
  }
  el("setting-language").addEventListener("change", async () => {
    try { await call("set_web_preferences", {language: el("setting-language").value}); window.i18n.setLanguage(el("setting-language").value); }
    catch (failure) { error(failure); }
  });
  window.addEventListener("languagechange", () => { if (el("settings-dialog").open) openSettings(); });
  return {openSettings, openMemories, openInstructions, renderStatus, promptKey, onEvent, onState};
})();
