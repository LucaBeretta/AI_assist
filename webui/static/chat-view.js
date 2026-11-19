// @ts-check
/** Chats: the saved-chat list (sidebar) and the conversation (main area). */
import { h, icon, replace } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { diffLines, relativeTime } from "./format.js";

const SUGGESTIONS = [
  ["Explain an idea", "Explain a complex idea with a useful everyday example."],
  ["Help me write", "Help me turn a rough idea into clear, concise writing."],
  ["Think it through", "Help me think through a decision. Start by asking what I am deciding."],
  ["Learn something", "Help me learn something new. Ask what topic I want to explore."],
];
// Fixes that only the desktop app can do: say where to go instead.
const DESKTOP_FIXES = {
  get_started: "Open the desktop app's Settings > Get started.",
  find_model: "Use Find & download models in the desktop app, or choose another installed model above.",
  documents: "Choose the folder again in the desktop app (Chat options > Use documents from a folder).",
};

/**
 * What happened and what to do, above an error's details.
 * @param {any} ctx @param {any} help
 */
export function errorHelp(ctx, help) {
  if (!help) return null;
  return h("div", { class: "error-help" },
    h("strong", null, help.title), h("p", null, help.help),
    help.action === "refresh"
      ? h("button", { class: "button button-small button-primary", type: "button",
        onClick: () => { ctx.loadModels(); ctx.loadStatus(); ctx.announce("Checking again."); } }, "Check again")
      : DESKTOP_FIXES[/** @type {keyof typeof DESKTOP_FIXES} */ (help.action)]
        ? h("p", { class: "muted" }, DESKTOP_FIXES[/** @type {keyof typeof DESKTOP_FIXES} */ (help.action)]) : null,
    h("span", { class: "muted" }, "Details:"));
}

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
  // The agent's edits and commands wait here for the person's answer.
  const reviews = h("div", { class: "reviews" });
  const body = h("div", { class: "view-body" }, h("div", { class: "conversation" }, messages, reviews));
  /** @type {Map<string, {element: HTMLElement, run: HTMLButtonElement | null, step: number}>} */
  const reviewCards = new Map();
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
    const accepted = state.chat?.mode === "dev" ? await ctx.runAgent(text) : await ctx.send(text);
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

  /** One review: what the agent wants to do, and the buttons to answer. @param {any} review */
  function reviewCard(review) {
    const action = review.action;
    const command = action.kind === "run_command";
    const second = command && review.step === 2;
    const headingId = `review-${review.id}`;
    const heading = h("h2", { id: headingId, tabindex: "-1" }, command
      ? (second ? "Second check: run this command?" : "Run this command?")
      : `${action.exists ? "Change" : "Create"} ${action.path}`);
    const detail = command
      ? (second
        ? h("p", { class: "review-warning" }, "It runs as a separate program with your account, outside the internet gate: "
          + "AI Assist cannot see or stop what it does online. Run it only if you expect what it does.")
        : h("p", { class: "muted" }, `Folder: ${action.cwd}`, action.description ? h("br") : null, action.description || ""))
      : h("p", { class: "muted" }, `File: ${action.destination || action.path}. Review the exact change before applying it.`);
    const shown = command
      ? h("pre", { class: "review-command", tabindex: "0" }, h("code", null, action.command))
      : h("pre", { class: "review-diff", tabindex: "0", "aria-label": `Changes to ${action.path}` }, h("code", null,
        diffLines(action.diff || "").map((line) => h("span", { class: `diff-line ${line.kind}` }, line.text + "\n"))));
    /** One answer at a time: both buttons wait for the server's reply. @param {string} choice */
    const answer = async (choice) => {
      reject.disabled = true;
      primary.disabled = true;
      if (!(await ctx.answerReview(review.id, choice)) && element.isConnected) {
        reject.disabled = false;
        primary.disabled = false;
      }
    };
    const reject = /** @type {HTMLButtonElement} */ (h("button", { class: "button", type: "button",
      onClick: () => answer("reject") }, "Reject"));
    const primary = /** @type {HTMLButtonElement} */ (h("button", { class: "button button-primary", type: "button",
      onClick: () => answer(!command ? "approve" : second ? "approve" : "second_check") },
      command ? (second ? "Run" : "Run command") : "Apply change"));
    const element = h("section", { class: `review${second ? " is-second" : ""}`, "aria-labelledby": headingId },
      h("div", { class: "review-kicker" }, "Waiting for your review"), heading, detail, shown,
      action.diff_shortened ? h("p", { class: "muted" }, "The diff is long; only its beginning is shown.") : null,
      h("div", { class: "card-row review-actions" }, reject, primary));
    return { element, run: second ? primary : null, step: review.step };
  }

  /** Keep the review cards in step with the waiting reviews; count down a command's second check. @param {any} state */
  function renderReviews(state) {
    const open = new Set(state.reviews.map((/** @type {any} */ review) => review.id));
    for (const [id, card] of reviewCards) {
      if (!open.has(id)) { card.element.remove(); reviewCards.delete(id); }
    }
    for (const review of state.reviews) {
      const existing = reviewCards.get(review.id);
      if (!existing || existing.step !== review.step) {
        const card = reviewCard(review);
        if (existing) existing.element.replaceWith(card.element);
        else reviews.appendChild(card.element);
        reviewCards.set(review.id, card);
        body.scrollTop = body.scrollHeight;
        /** @type {HTMLElement | null} */ (card.element.querySelector("h2"))?.focus();
      }
      const card = /** @type {any} */ (reviewCards.get(review.id));
      if (card.run) {
        const left = Math.ceil((review.unlockAt - Date.now()) / 1000);
        card.run.disabled = left > 0;
        card.run.textContent = left > 0 ? `Run (${left})` : "Run";
      }
    }
  }

  /** @param {any} state */
  function emptyState(state) {
    const ready = Boolean(state.selectedModel) && !state.streaming;
    return h("section", { class: "state", "aria-labelledby": "empty-title" },
      h("div", { class: "state-kicker" }, "◇ ai-assist · local workspace"),
      h("h2", { id: "empty-title" }, "What are we working on?"),
      h("p", { class: "muted" }, "A question, a rough idea, a fresh start. Your models run on this computer, and nothing goes online from here."),
      state.modelsError ? h("div", { class: "notice is-amber", role: "alert" }, icon("lock"),
        h("div", null, state.modelsHelp ? errorHelp(ctx, state.modelsHelp) : h("strong", null, "No models answered. "),
          state.modelsError)) : null,
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
    const library = chat?.library_path ? String(chat.library_path).split(/[\\/]/).filter(Boolean).pop() : "";
    const project = developer ? String(chat.project_path || "").split(/[\\/]/).filter(Boolean).pop() : "";
    subtitle.textContent = chat
      ? `${chat.backend || "Ollama"} / ${chat.model || "no model"}${developer ? `  /  developer · ${project}` : ""}`
        + (library ? `  /  documents: ${library}` : "")
      : `${state.status?.engine || "engine"} / ${state.selectedModel || "choose a model"}`;
    const busy = Boolean(state.streaming);
    replace(actions, chat ? [
      developer ? h("button", { class: "button button-small", type: "button", disabled: busy || !state.chatUndo,
        title: state.chatUndo ? "Put back the files the AI's last task changed" : "No AI file changes to undo",
        onClick: () => ctx.undoTask(chat) }, icon("refresh"), "Undo last task") : null,
      developer ? h("button", { class: "button button-small", type: "button", onClick: () => ctx.openProject(chat.id) },
        icon("folder"), "Files") : null,
      developer ? h("button", { class: "button button-small", type: "button", disabled: busy, onClick: () => ctx.newTask(chat.id) },
        icon("plus"), "New task") : null,
      h("button", { class: "icon-button", type: "button", "aria-label": "Rename chat", title: "Rename", onClick: () => ctx.renameChat(chat),
        disabled: busy }, icon("edit")),
      h("button", { class: "icon-button", type: "button", "aria-label": "Delete chat", title: "Delete", onClick: () => ctx.deleteChat(chat),
        disabled: busy }, icon("trash")),
    ] : []);

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
          errorHelp(ctx, state.sendHelp),
          h("div", { class: "message-body" }, state.sendError)));
      }
      body.scrollTop = body.scrollHeight;
    }
    if (state.streaming && state.streaming.chatId === chat?.id) {
      const fresh = !streamNode;
      if (!streamNode) {
        streamNode = h("div", { class: "message-body" });
        messages.appendChild(h("article", { class: "message is-assistant", "aria-busy": "true", "aria-label": "AI Assist, writing" },
          h("div", { class: "message-header" }, h("span", { class: "message-role" }, "◇ ai-assist"),
            h("span", { class: "writing", "aria-hidden": "true" }, h("span"), h("span"), h("span")),
            h("span", { class: "stream-status" }, state.streaming.stopping ? "stopping…" : "writing…")),
          streamNode));
      }
      const status = messages.querySelector(".stream-status");
      if (status) status.textContent = state.streaming.stopping ? "stopping…" : state.streaming.text ? "writing…" : state.streaming.status || "writing…";
      if (developer) {
        if (fresh || state.streaming.steps !== previous?.streaming?.steps) {
          // The agent's latest steps: what it read, changed and ran.
          replace(streamNode, h("ol", { class: "agent-steps", "aria-label": "Agent steps" },
            (state.streaming.steps || []).map((/** @type {any} */ step) => h("li", { class: `agent-step is-${step.type}` },
              h("span", { class: "agent-step-type" }, String(step.type).replace(/_/g, " ")),
              h("span", null, step.command || String(step.text || "").split("\n")[0].slice(0, 200))))));
        }
      } else if (fresh || state.streaming.text !== previous?.streaming?.text) {
        const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 120;
        replace(streamNode, renderMarkdown(state.streaming.text, document));
        if (nearBottom) body.scrollTop = body.scrollHeight;
      }
    }
    if (state.reviews !== previous?.reviews || state.tick !== previous?.tick || chat?.id !== previous?.chat?.id) {
      renderReviews(state.streaming && state.streaming.chatId === chat?.id ? state : { reviews: [] });
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
    const blocked = mismatch;
    replace(notice, mismatch ? h("div", { class: "notice is-amber" }, icon("lock"), h("div", null,
      `This chat was written with ${chat.backend || "Ollama"}. Switch the engine in Settings to continue it.`))
      : developer && !state.streaming ? h("div", { class: "notice" }, icon("lock"), h("div", null,
        h("strong", null, "You approve every step. "), "The agent shows each file change and each command here before "
        + "it happens. Commands run with your account, outside the internet gate."))
        : null);
    const streaming = Boolean(state.streaming);
    textarea.placeholder = developer ? "Describe the change you need…" : "Message your local model…";
    textarea.setAttribute("aria-label", developer ? "Task for the developer agent" : "Message");
    sendButton.disabled = !streaming && (Boolean(blocked) || !state.selectedModel);
    replace(sendButton, streaming ? [icon("stop"), state.streaming.stopping ? "Stopping…" : "Stop"]
      : [icon("send"), developer ? "Run agent" : "Send"]);
    sendButton.setAttribute("aria-label", streaming ? (developer ? "Stop the agent" : "Stop the answer")
      : developer ? "Run the developer agent" : "Send message");
    hint.textContent = streaming ? (developer ? "Stop declines what is waiting; applied changes stay (Undo puts them back)."
      : "Stop keeps the part already written.")
      : state.selectedModel ? "Enter to send · Shift+Enter for a new line" : "Choose a model to start.";
    textarea.disabled = Boolean(blocked);
  }

  return { element, render, focusComposer: () => window.setTimeout(() => textarea.focus(), 0) };
}
