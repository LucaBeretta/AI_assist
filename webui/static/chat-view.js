// @ts-check
/** Chats: the saved-chat list (sidebar) and the conversation (main area). */
import { h, icon, replace } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { relativeTime } from "./format.js";

const SUGGESTIONS = [
  ["Explain an idea", "Explain a complex idea with a useful everyday example."],
  ["Help me write", "Help me turn a rough idea into clear, concise writing."],
  ["Think it through", "Help me think through a decision. Start by asking what I am deciding."],
  ["Learn something", "Help me learn something new. Ask what topic I want to explore."],
];
const CONTEXTS = [["default", "Server default"], ["4096", "4k context"], ["8192", "8k context"], ["16384", "16k context"],
  ["32768", "32k context"], ["65536", "64k context"], ["131072", "128k context"]];

/** @param {any} ctx */
export function createChatSidebar(ctx) {
  let timer = 0;
  const search = /** @type {HTMLInputElement} */ (h("input", {
    class: "input", type: "search", placeholder: "Search chats  ( / )", "aria-label": "Search chats",
    onInput: () => { window.clearTimeout(timer); timer = window.setTimeout(() => ctx.loadChats(search.value.trim()), 180); },
  }));
  const list = h("ul", { class: "list", "aria-labelledby": "recent-heading" });
  const element = h("div", { class: "sidebar-body" },
    h("button", { class: "button button-primary", type: "button", onClick: () => ctx.startNewChat(), title: "New chat (Alt+N)" },
      icon("plus"), "New chat"),
    search,
    h("div", { class: "sidebar-heading", id: "recent-heading" }, "Recent"),
    list);

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (previous && state.chats === previous.chats && state.chat?.id === previous.chat?.id
        && state.chatsLoading === previous.chatsLoading && state.chatsError === previous.chatsError) return;
    if (state.chatsLoading && !state.chats.length) {
      replace(list, [1, 2, 3].map(() => h("li", { class: "list-item", "aria-hidden": "true" }, h("div", { class: "skeleton" }))));
      return;
    }
    if (state.chatsError) { replace(list, h("li", { class: "empty-note" }, `Could not load chats: ${state.chatsError}`)); return; }
    if (!state.chats.length) {
      replace(list, h("li", { class: "empty-note" }, state.chatQuery ? "No matching chats." : "Your conversations will appear here."));
      return;
    }
    replace(list, state.chats.map((/** @type {any} */ chat) => h("li", null,
      h("button", { class: "list-item", type: "button", "aria-current": state.chat?.id === chat.id ? "true" : null,
        onClick: () => ctx.openChat(chat.id) },
        h("span", { class: "list-item-title" }, chat.mode === "dev" ? h("span", { class: "muted" }, "</> ") : null, chat.title),
        h("span", { class: "list-item-meta" }, relativeTime(chat.updated_at), chat.model ? h("span", null, "· " + chat.model) : null)))));
  }

  return { element, render, focusSearch: () => search.focus() };
}

/** @param {any} ctx */
export function createChatView(ctx) {
  const title = h("h1", { class: "view-title" }, "New chat");
  const subtitle = h("div", { class: "view-subtitle" });
  const actions = h("div", { class: "card-row" });
  const header = h("header", { class: "view-header" }, h("div", { class: "view-heading" }, title, subtitle), actions);
  const messages = h("div", { class: "messages", role: "log", "aria-label": "Conversation", "aria-live": "off" });
  const body = h("div", { class: "view-body" }, h("div", { class: "conversation" }, messages));
  const textarea = /** @type {HTMLTextAreaElement} */ (h("textarea", {
    id: "composer-input", rows: 2, placeholder: "Message your local model…", "aria-label": "Message",
    "aria-describedby": "composer-hint",
  }));
  const modelSelect = /** @type {HTMLSelectElement} */ (h("select", { class: "select", "aria-label": "Model",
    onChange: () => ctx.selectModel(modelSelect.value) }));
  const contextSelect = /** @type {HTMLSelectElement} */ (h("select", { class: "select", "aria-label": "Context size",
    onChange: () => ctx.store.set({ context: contextSelect.value }) },
    CONTEXTS.map(([value, label]) => h("option", { value }, label))));
  const hint = h("span", { class: "composer-hint", id: "composer-hint" }, "Enter to send · Shift+Enter for a new line");
  const sendButton = /** @type {HTMLButtonElement} */ (h("button", { class: "button button-primary", type: "submit" }));
  const notice = h("div", { class: "composer-notice" });
  const form = h("form", { class: "composer", onSubmit: (/** @type {Event} */ event) => { event.preventDefault(); submit(); } },
    textarea, h("div", { class: "composer-toolbar" }, hint, modelSelect, contextSelect, sendButton));
  const composerArea = h("div", { class: "composer-area" }, notice, form);
  const element = h("div", { class: "view-root" }, header, body, composerArea);

  /** @type {HTMLElement | null} */
  let streamNode = null;
  let renderedKey = "";

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit(); }
  });
  textarea.addEventListener("input", grow);

  function grow() {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, window.innerHeight * 0.4)}px`;
  }

  async function submit() {
    const state = ctx.store.get();
    if (state.streaming) { ctx.stop(); return; }
    const text = textarea.value.trim();
    if (!text || sendButton.disabled) return;
    textarea.value = "";
    grow();
    const accepted = await ctx.send(text);
    // A message the server never accepted goes back into the composer.
    if (!accepted && !textarea.value) { textarea.value = text; grow(); }
    textarea.focus();
  }

  /** @param {string} role @param {string} content */
  function messageNode(role, content) {
    const assistant = role === "assistant";
    const bodyNode = h("div", { class: "message-body" });
    if (assistant) bodyNode.appendChild(renderMarkdown(content, document, { onCopy: (code, button) => ctx.copy(code, button) }));
    else { bodyNode.classList.add("is-plain"); bodyNode.textContent = content; }
    const copy = h("button", { class: "button button-ghost button-small", type: "button" }, "Copy");
    copy.addEventListener("click", () => ctx.copy(content, copy));
    return h("article", { class: `message is-${assistant ? "assistant" : "user"}`, "aria-label": assistant ? "AI Assist" : "You" },
      h("div", { class: "message-header" }, h("span", { class: "message-role" }, assistant ? "◇ ai-assist" : "> you"),
        h("div", { class: "message-actions" }, copy,
          h("button", { class: "button button-ghost button-small", type: "button", onClick: () => ctx.addToMemory(content),
            title: "Save this message as a shared note for every chat" }, "Add to memory"))),
      bodyNode);
  }

  /** @param {any} state */
  function emptyState(state) {
    const ready = Boolean(state.selectedModel) && !state.streaming;
    return h("section", { class: "state", "aria-labelledby": "empty-title" },
      h("div", { class: "state-kicker" }, "◇ ai-assist · local workspace"),
      h("h2", { id: "empty-title" }, "What are we working on?"),
      h("p", { class: "muted" }, "A question, a rough idea, a fresh start. Your models run on this computer, and nothing goes online from here."),
      state.modelsError ? h("div", { class: "notice is-amber", role: "alert" }, icon("lock"),
        h("div", null, h("strong", null, "No models answered. "), state.modelsError)) : null,
      h("div", { class: "suggestions" }, SUGGESTIONS.map(([label, prompt]) =>
        h("button", { class: "suggestion", type: "button", disabled: !ready,
          onClick: () => { textarea.value = prompt; grow(); textarea.focus(); } },
          h("strong", null, label), h("span", null, prompt)))));
  }

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "chats") return;
    const chat = state.chat;
    const developer = chat?.mode === "dev";
    const mismatch = chat && chat.messages.length && (chat.backend || "Ollama") !== state.status?.engine && state.status;

    // Header
    title.textContent = chat ? chat.title : "New chat";
    subtitle.textContent = chat
      ? `${chat.backend || "Ollama"} / ${chat.model || "no model"}${developer ? "  /  developer chat (read-only here)" : ""}`
      : `${state.status?.engine || "engine"} / ${state.selectedModel || "choose a model"}`;
    replace(actions, chat && !developer ? [
      h("button", { class: "icon-button", type: "button", "aria-label": "Rename chat", title: "Rename", onClick: () => ctx.renameChat(chat),
        disabled: Boolean(state.streaming) }, icon("edit")),
      h("button", { class: "icon-button", type: "button", "aria-label": "Delete chat", title: "Delete", onClick: () => ctx.deleteChat(chat),
        disabled: Boolean(state.streaming) }, icon("trash")),
    ] : developer ? [h("button", { class: "button button-small", type: "button", onClick: () => ctx.openProject(chat.id) },
      icon("folder"), "Open project")] : []);

    // Messages: rebuilt when the chat or its message count changes; streaming text updates in place.
    const key = [chat?.id || "new", chat?.messages.length || 0, state.chatLoading, state.chatError || "", state.sendError || "",
      Boolean(state.modelsError), Boolean(state.selectedModel), Boolean(state.streaming)].join("\u0000");
    if (key !== renderedKey) {
      renderedKey = key;
      streamNode = null;
      if (state.chatLoading) replace(messages, h("div", { class: "skeleton", "aria-label": "Loading the chat" }));
      else if (state.chatError) replace(messages, h("div", { class: "notice is-danger", role: "alert" }, state.chatError));
      else if (!chat || !chat.messages.length) {
        replace(messages, state.streaming || state.sendError ? [] : emptyState(state));
      } else {
        replace(messages, chat.messages.filter((/** @type {any} */ m) => m.role !== "system")
          .map((/** @type {any} */ m) => messageNode(m.role, m.content)));
      }
      if (state.sendError) {
        messages.appendChild(h("div", { class: "message is-error", role: "alert" },
          h("div", { class: "message-header" }, h("span", { class: "message-role" }, "request failed")),
          h("div", { class: "message-body" }, state.sendError)));
      }
      body.scrollTop = body.scrollHeight;
    }
    if (state.streaming && state.streaming.chatId === chat?.id) {
      if (!streamNode) {
        streamNode = h("div", { class: "message-body" });
        messages.appendChild(h("article", { class: "message is-assistant", "aria-busy": "true", "aria-label": "AI Assist, writing" },
          h("div", { class: "message-header" }, h("span", { class: "message-role" }, "◇ ai-assist"),
            h("span", { class: "writing", "aria-hidden": "true" }, h("span"), h("span"), h("span")),
            h("span", null, state.streaming.stopping ? "stopping…" : "writing…")),
          streamNode));
      }
      if (state.streaming.text !== previous?.streaming?.text) {
        const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 120;
        replace(streamNode, renderMarkdown(state.streaming.text, document));
        if (nearBottom) body.scrollTop = body.scrollHeight;
      }
    }
    // Composer
    if (state.models !== previous?.models || state.selectedModel !== previous?.selectedModel) {
      replace(modelSelect, state.models.length
        ? state.models.map((/** @type {any} */ m) => h("option", { value: m.name }, m.name + (m.cloud ? " (cloud)" : "")))
        : h("option", { value: "" }, state.modelsLoading ? "Loading models…" : "No models"));
      modelSelect.value = state.selectedModel;
    }
    contextSelect.value = state.context;
    const colibri = state.status?.engine === "Colibri";
    contextSelect.disabled = colibri || Boolean(state.streaming);
    contextSelect.title = colibri ? "Colibri's context is set when its model is installed" : "Context for this chat's next answer";
    modelSelect.disabled = Boolean(state.streaming) || !state.models.length;
    const blocked = developer || mismatch;
    form.hidden = Boolean(developer);
    replace(notice, developer
      ? h("div", { class: "notice" }, icon("lock"), h("div", null, h("strong", null, "Read-only here. "),
        "Developer chats run in the desktop app, where every edit and command is reviewed before it happens. ",
        h("button", { class: "button button-small", type: "button", onClick: () => ctx.openProject(chat.id) }, "View its project")))
      : mismatch ? h("div", { class: "notice is-amber" }, icon("lock"), h("div", null,
        `This chat was written with ${chat.backend || "Ollama"}. Switch the engine in Settings to continue it.`))
        : null);
    const streaming = Boolean(state.streaming);
    sendButton.disabled = !streaming && (Boolean(blocked) || !state.selectedModel);
    replace(sendButton, streaming ? [icon("stop"), state.streaming.stopping ? "Stopping…" : "Stop"] : [icon("send"), "Send"]);
    sendButton.setAttribute("aria-label", streaming ? "Stop the answer" : "Send message");
    hint.textContent = streaming ? "Stop keeps the part already written." : state.selectedModel
      ? "Enter to send · Shift+Enter for a new line" : "Choose a model to start.";
    textarea.disabled = Boolean(blocked);
  }

  return { element, render, focusComposer: () => window.setTimeout(() => textarea.focus(), 0) };
}
