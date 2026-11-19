// @ts-check
/**
 * AI Assist web workspace: application shell.
 *
 * One store holds the page state; views subscribe and redraw their own part.
 * Every action goes through `ctx`, which is the only place that calls the API.
 */
import { createApi, ApiError } from "./api.js";
import { h, icon, replace, loadPreference, savePreference } from "./dom.js";
import { createChatSidebar, createChatView } from "./chat-view.js";
import { createProjectsSidebar, createProjectsView } from "./projects-view.js";
import { createModelsSidebar, createModelsView } from "./models-view.js";
import { createSettingsSidebar, createSettingsView } from "./settings-view.js";
import { createInspector } from "./inspector.js";
import { createComputeSidebar, createComputeView } from "./compute-view.js";

/** @param {Record<string, any>} initial */
function createStore(initial) {
  let state = initial;
  /** @type {Set<(state: any, previous: any) => void>} */
  const listeners = new Set();
  return {
    get: () => state,
    /** @param {Record<string, any>} patch */
    set(patch) {
      const previous = state;
      state = { ...state, ...patch };
      listeners.forEach((listener) => listener(state, previous));
    },
    /** @param {(state: any, previous: any) => void} listener */
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}

const api = createApi();
// Below this width the details panel and the navigation are drawers over the content.
const narrow = window.matchMedia("(max-width: 1180px)");
const inspectorPreference = () => loadPreference("aiassist.inspector", "open") === "open";
const store = createStore({
  view: "chats", navOpen: false, inspectorOpen: !narrow.matches && inspectorPreference(),
  theme: loadPreference("aiassist.theme", "system"),
  status: null, statusError: null,
  models: [], modelsError: null, modelsLoading: false, selectedModel: loadPreference("aiassist.model", ""),
  context: "default",
  chats: [], chatsLoading: true, chatsError: null, chatQuery: "",
  chat: null, chatLoading: false, chatError: null,
  streaming: null, sendError: null, lastMetrics: null,
  details: {}, hardware: null, memory: null, log: null, compute: null,
  projects: null, projectsError: null, project: null,
});

const announcer = /** @type {HTMLElement} */ (document.getElementById("announcer"));
const toasts = /** @type {HTMLElement} */ (document.getElementById("toasts"));
const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));

/** Messages for screen readers, without moving focus. @param {string} text */
function announce(text) {
  announcer.textContent = "";
  window.setTimeout(() => { announcer.textContent = text; }, 30);
}

/** @param {string} text @param {"info" | "danger"} [kind] */
function toast(text, kind = "info") {
  const item = h("div", { class: `toast${kind === "danger" ? " is-danger" : ""}` }, text);
  toasts.appendChild(item);
  window.setTimeout(() => item.remove(), kind === "danger" ? 7000 : 3500);
}

/**
 * A modal dialog. Escape and the backdrop cancel; focus returns to where it was.
 * @param {{title: string, body?: string, confirm: string, danger?: boolean, input?: {label: string, value: string, maxLength?: number}}} options
 * @returns {Promise<string | boolean | null>}
 */
function openDialog(options) {
  const returnFocus = /** @type {HTMLElement | null} */ (document.activeElement);
  const input = options.input
    ? h("input", { class: "input", id: "dialog-input", value: options.input.value, maxlength: options.input.maxLength || 200, required: true })
    : null;
  const confirm = h("button", { class: `button ${options.danger ? "button-danger" : "button-primary"}`, value: "confirm" }, options.confirm);
  const form = h("form", { method: "dialog" },
    h("h2", { id: "dialog-title" }, options.title),
    options.body ? h("p", { class: "muted" }, options.body) : null,
    input ? h("label", { class: "field", for: "dialog-input" }, h("span", { class: "field-label" }, options.input?.label || ""), input) : null,
    h("div", { class: "dialog-actions" },
      h("button", { class: "button", value: "cancel", formnovalidate: true }, "Cancel"), confirm));
  replace(dialog, form);
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      const confirmed = dialog.returnValue === "confirm";
      returnFocus?.focus();
      resolve(confirmed ? (input ? /** @type {HTMLInputElement} */ (input).value.trim() || null : true) : (input ? null : false));
    }, { once: true });
    dialog.returnValue = "";
    dialog.showModal();
    (input || confirm).focus();
    if (input) /** @type {HTMLInputElement} */ (input).select();
  });
}

/** @param {string} text @param {HTMLElement} [button] */
async function copy(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    if (button) {
      const label = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = label; }, 1400);
    }
    announce("Copied to the clipboard.");
  } catch {
    toast("The browser did not allow copying. Select the text and copy it instead.", "danger");
  }
}

/** Report an API failure; a 401 signs the page out. @param {unknown} error @param {string} what */
function failed(error, what) {
  if (error instanceof ApiError && error.status === 401) return;
  const message = error instanceof Error ? error.message : String(error);
  toast(`${what}: ${message}`, "danger");
}

// ------------------------------------------------------------ actions

const ctx = {
  api, store, announce, toast, copy, failed, dialog: openDialog,

  /** @param {string} view */
  navigate(view) {
    store.set({ view, navOpen: false });
    if (view === "projects") ctx.loadProjects();
    if (view === "compute") ctx.loadCompute();
    if (view === "models") ctx.loadModels();
    if (view === "settings") { ctx.loadMemory(); ctx.loadLog(); }
    /** @type {HTMLElement} */ (document.getElementById("main")).focus();
  },

  async loadStatus() {
    try {
      const model = store.get().selectedModel;
      store.set({ status: await api.get(`/api/status?model=${encodeURIComponent(model || "")}`), statusError: null });
    } catch (error) {
      store.set({ statusError: error instanceof Error ? error.message : String(error) });
    }
  },

  async loadModels() {
    store.set({ modelsLoading: true });
    try {
      const result = await api.get("/api/models");
      const names = result.models.map((/** @type {any} */ model) => model.name);
      let selected = store.get().selectedModel;
      if (!names.includes(selected)) selected = names[0] || "";
      store.set({ models: result.models, modelsError: null, selectedModel: selected, modelsLoading: false });
    } catch (error) {
      store.set({ modelsError: error instanceof Error ? error.message : String(error), modelsLoading: false });
    }
  },

  /** @param {string} name */
  selectModel(name) {
    savePreference("aiassist.model", name);
    store.set({ selectedModel: name });
    ctx.loadStatus();
    if (store.get().inspectorOpen) ctx.loadDetails(name);
  },

  /** @param {string} name */
  async loadDetails(name) {
    if (!name || store.get().details[name]) return;
    store.set({ details: { ...store.get().details, [name]: { loading: true } } });
    try {
      const details = await api.get(`/api/models/details?name=${encodeURIComponent(name)}`);
      store.set({ details: { ...store.get().details, [name]: details } });
    } catch (error) {
      store.set({ details: { ...store.get().details, [name]: { error: error instanceof Error ? error.message : String(error) } } });
    }
  },

  async loadCompute() {
    if (store.get().view !== "compute") return;
    try { store.set({ compute: await api.get("/api/compute") }); }
    catch (error) { store.set({ compute: { error: error instanceof Error ? error.message : String(error) } }); }
  },

  async loadHardware() {
    store.set({ hardware: { loading: true } });
    try { store.set({ hardware: await api.get("/api/hardware") }); }
    catch (error) { store.set({ hardware: { error: error instanceof Error ? error.message : String(error) } }); }
  },

  /** @param {string} [query] */
  async loadChats(query = store.get().chatQuery) {
    store.set({ chatQuery: query });
    try {
      const result = await api.get(`/api/chats?query=${encodeURIComponent(query)}`);
      if (store.get().chatQuery === query) store.set({ chats: result.chats, chatsLoading: false, chatsError: null });
    } catch (error) {
      store.set({ chatsLoading: false, chatsError: error instanceof Error ? error.message : String(error) });
    }
  },

  /** @param {string} id */
  async openChat(id) {
    if (store.get().streaming) { toast("Stop the current answer before opening another chat."); return; }
    store.set({ view: "chats", navOpen: false, chatLoading: true, chatError: null, sendError: null });
    try {
      const { chat } = await api.get(`/api/chats/${id}`);
      const names = store.get().models.map((/** @type {any} */ model) => model.name);
      const patch = { chat, chatLoading: false, context: chat.context_length ? String(chat.context_length) : "default" };
      if (chat.model && names.includes(chat.model)) Object.assign(patch, { selectedModel: chat.model });
      store.set(patch);
    } catch (error) {
      store.set({ chatLoading: false, chatError: error instanceof Error ? error.message : String(error) });
    }
  },

  /** A fresh, empty conversation; saved when the first message is sent. */
  startNewChat() {
    if (store.get().streaming) { toast("Stop the current answer before starting a new chat."); return; }
    store.set({ view: "chats", chat: null, navOpen: false, sendError: null, chatError: null, context: "default" });
    chatView.focusComposer();
  },

  /** @param {any} chat */
  async renameChat(chat) {
    const title = await openDialog({ title: "Rename chat", confirm: "Rename", input: { label: "Title", value: chat.title, maxLength: 200 } });
    if (typeof title !== "string") return;
    try {
      const result = await api.request("PATCH", `/api/chats/${chat.id}`, { title });
      store.set({ chat: { ...store.get().chat, title: result.chat.title } });
      ctx.loadChats();
      announce("Chat renamed.");
    } catch (error) { failed(error, "Could not rename the chat"); }
  },

  /** @param {any} chat */
  async deleteChat(chat) {
    const sure = await openDialog({ title: "Delete this chat?", body: `“${chat.title}” will be removed from saved chats and from shared-memory recall. This cannot be undone.`, confirm: "Delete chat", danger: true });
    if (sure !== true) return;
    try {
      await api.request("DELETE", `/api/chats/${chat.id}`);
      store.set({ chat: null });
      ctx.loadChats();
      announce("Chat deleted.");
    } catch (error) { failed(error, "Could not delete the chat"); }
  },

  /**
   * Send a message and stream the answer into the open chat.
   * @param {string} text
   * @returns {Promise<boolean>} whether the message was accepted
   */
  async send(text) {
    const state = store.get();
    if (state.streaming) return false;
    let chat = state.chat;
    store.set({ sendError: null });
    try {
      if (!chat) {
        chat = (await api.request("POST", "/api/chats", { model: state.selectedModel })).chat;
        store.set({ chat });
      }
    } catch (error) { failed(error, "Could not start a chat"); return false; }
    const chatId = chat.id;
    store.set({ streaming: { chatId, requestId: "", text: "", stopping: false } });
    const context = state.context === "default" ? null : Number(state.context);
    /** Merge into the latest streaming state (Stop also updates it). @param {Record<string, any>} patch */
    const update = (patch) => {
      const current = store.get().streaming;
      if (current?.chatId === chatId) store.set({ streaming: { ...current, ...patch } });
    };
    // Tokens arrive faster than the screen redraws: draw at most once per frame.
    let pending = "";
    let frame = 0;
    const flush = () => {
      frame = 0;
      if (pending) { const piece = pending; pending = ""; update({ text: (store.get().streaming?.text || "") + piece }); }
    };
    let accepted = false;
    let finished = false;
    try {
      await api.stream(`/api/chats/${chatId}/messages`, { content: text, model: state.selectedModel, context_length: context }, (event) => {
        if (event.type === "chunk") {
          pending += event.text;
          if (!frame) frame = window.requestAnimationFrame(flush);
          return;
        }
        flush();
        const current = store.get().chat;
        if (event.type === "start") update({ requestId: event.request });
        else if (event.type === "user") {
          accepted = true;
          if (current?.id === chatId) store.set({ chat: { ...current, messages: [...current.messages, event.message] } });
        } else if (event.type === "metrics") store.set({ lastMetrics: event.metrics });
        else if (event.type === "done") {
          finished = true;
          const messages = current?.id === chatId && event.message ? [...current.messages, event.message] : current?.messages;
          if (current?.id === chatId) store.set({ chat: { ...current, ...event.chat, messages } });
          announce(event.stopped ? (event.message ? "Stopped. The partial answer was kept." : "Stopped before the model answered.")
            : event.local ? "Saved to shared memory." : "Answer complete.");
        } else if (event.type === "error") {
          finished = true;
          store.set({ sendError: event.error });
          announce(`The answer failed: ${event.error}`);
        }
      });
      if (!finished) store.set({ sendError: "The connection closed before the answer finished. Your message was saved; send it again when ready." });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        store.set({ sendError: error instanceof Error ? error.message : String(error) });
      }
    }
    if (frame) window.cancelAnimationFrame(frame);
    store.set({ streaming: null });
    ctx.loadChats();
    ctx.loadStatus();
    return accepted;
  },

  async stop() {
    const streaming = store.get().streaming;
    if (!streaming?.requestId || streaming.stopping) return;
    store.set({ streaming: { ...streaming, stopping: true } });
    try { await api.request("POST", `/api/requests/${streaming.requestId}/cancel`, {}); }
    catch (error) { if (!(error instanceof ApiError && error.status === 404)) failed(error, "Could not stop the answer"); }
  },

  /** @param {string} note */
  async addToMemory(note) {
    try {
      store.set({ memory: await api.request("POST", "/api/memory/notes", { note }) });
      toast("Saved to shared memory. Available in other chats.");
    } catch (error) { failed(error, "Could not add to shared memory"); }
  },

  async loadMemory() {
    try { store.set({ memory: await api.get("/api/memory") }); }
    catch (error) { store.set({ memory: { error: error instanceof Error ? error.message : String(error) } }); }
  },

  /** @param {string} notes @param {boolean} enabled */
  async saveMemory(notes, enabled) {
    try {
      store.set({ memory: await api.request("PUT", "/api/memory", { notes, enabled }) });
      announce("Shared memory saved.");
      toast("Shared memory saved. It applies to the next message in any chat.");
      ctx.loadStatus();
    } catch (error) { failed(error, "Could not save shared memory"); }
  },

  async loadLog() {
    try { store.set({ log: await api.get("/api/log") }); }
    catch (error) { store.set({ log: { error: error instanceof Error ? error.message : String(error) } }); }
  },

  /** @param {string} backend */
  async setEngine(backend) {
    try {
      store.set({ status: await api.request("PUT", "/api/settings/engine", { backend }), models: [], details: {} });
      toast(`Engine: ${backend}. New chats use it.`);
      ctx.loadModels();
    } catch (error) { failed(error, "Could not switch engines"); }
  },

  async loadProjects() {
    try { store.set({ projects: (await api.get("/api/projects")).projects, projectsError: null }); }
    catch (error) { store.set({ projectsError: error instanceof Error ? error.message : String(error) }); }
  },

  /** @param {string} chatId */
  async openProject(chatId) {
    store.set({ view: "projects", navOpen: false, project: { chatId, files: null, error: null, filter: "", path: "", content: null, tab: "source", chat: null } });
    try {
      const [files, chat] = await Promise.all([api.get(`/api/projects/${chatId}/files`), api.get(`/api/chats/${chatId}`)]);
      const project = store.get().project;
      if (project?.chatId === chatId) store.set({ project: { ...project, files: files.files, root: files.project_path, chat: chat.chat } });
    } catch (error) {
      const project = store.get().project;
      if (project?.chatId === chatId) store.set({ project: { ...project, error: error instanceof Error ? error.message : String(error) } });
    }
  },

  /** @param {string} path */
  async openProjectFile(path) {
    const project = store.get().project;
    if (!project) return;
    store.set({ project: { ...project, path, content: { loading: true }, tab: "source" } });
    try {
      const file = await api.get(`/api/projects/${project.chatId}/file?path=${encodeURIComponent(path)}`);
      const current = store.get().project;
      if (current?.chatId === project.chatId && current.path === path) store.set({ project: { ...current, content: file } });
    } catch (error) {
      const current = store.get().project;
      if (current?.path === path) store.set({ project: { ...current, content: { error: error instanceof Error ? error.message : String(error) } } });
    }
  },

  /** @param {Record<string, any>} patch */
  updateProject(patch) {
    const project = store.get().project;
    if (project) store.set({ project: { ...project, ...patch } });
  },

  /** @param {string} theme */
  setTheme(theme) {
    savePreference("aiassist.theme", theme);
    store.set({ theme });
  },

  toggleInspector() {
    const open = !store.get().inspectorOpen;
    // The remembered choice is for wide screens, where the panel sits beside the content.
    if (!narrow.matches) savePreference("aiassist.inspector", open ? "open" : "closed");
    store.set({ inspectorOpen: open, navOpen: narrow.matches ? false : store.get().navOpen });
    if (open) ctx.loadDetails(store.get().selectedModel);
  },
};

// ------------------------------------------------------------ shell

const app = /** @type {HTMLElement} */ (document.getElementById("app"));
const sidebar = /** @type {HTMLElement} */ (document.getElementById("sidebar"));
const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const inspectorElement = /** @type {HTMLElement} */ (document.getElementById("inspector"));
const chips = /** @type {HTMLElement} */ (document.getElementById("status-chips"));
const navToggle = /** @type {HTMLButtonElement} */ (document.getElementById("nav-toggle"));
const themeToggle = /** @type {HTMLButtonElement} */ (document.getElementById("theme-toggle"));
const inspectorToggle = /** @type {HTMLButtonElement} */ (document.getElementById("inspector-toggle"));
const scrim = /** @type {HTMLElement} */ (document.getElementById("scrim"));

const SECTIONS = [["chats", "Chats", "chat"], ["projects", "Projects", "folder"], ["models", "Models", "cube"], ["compute", "Compute", "cpu"], ["settings", "Settings", "gear"]];
const sectionButtons = SECTIONS.map(([view, label, glyph]) =>
  h("button", { class: "nav-section", type: "button", "data-view": view, onClick: () => ctx.navigate(view) },
    icon(/** @type {any} */ (glyph)), label));
const sidebarSlot = h("div", { class: "sidebar-slot" });
sidebar.append(h("div", { class: "nav-sections" }, sectionButtons), sidebarSlot);

const chatSidebar = createChatSidebar(ctx);
const chatView = createChatView(ctx);
const projectsSidebar = createProjectsSidebar(ctx);
const projectsView = createProjectsView(ctx);
const modelsSidebar = createModelsSidebar(ctx);
const modelsView = createModelsView(ctx);
const computeSidebar = createComputeSidebar(ctx);
const computeView = createComputeView(ctx);
const settingsSidebar = createSettingsSidebar(ctx);
const settingsView = createSettingsView(ctx);
const inspector = createInspector(ctx);
const views = {
  chats: [chatSidebar, chatView], projects: [projectsSidebar, projectsView],
  models: [modelsSidebar, modelsView], compute: [computeSidebar, computeView], settings: [settingsSidebar, settingsView],
};
replace(navToggle, icon("menu"));
replace(inspectorToggle, icon("panel"));
inspectorElement.appendChild(inspector.element);

let shownView = "";
store.subscribe((state, previous) => {
  if (state.view !== shownView) {
    shownView = state.view;
    const [side, view] = views[/** @type {keyof typeof views} */ (state.view)];
    replace(sidebarSlot, side.element);
    replace(main, view.element);
    sectionButtons.forEach((button) => {
      if (button.dataset.view === state.view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  }
  for (const [side, view] of Object.values(views)) { side.render(state, previous); view.render(state, previous); }
  inspector.render(state, previous);
  renderShell(state, previous);
});

/** @param {any} state @param {any} previous */
function renderShell(state, previous) {
  app.classList.toggle("nav-open", state.navOpen);
  app.classList.toggle("inspector-open", state.inspectorOpen);
  scrim.hidden = !(state.navOpen || state.inspectorOpen);
  navToggle.setAttribute("aria-expanded", String(state.navOpen));
  navToggle.setAttribute("aria-label", state.navOpen ? "Close navigation" : "Open navigation");
  inspectorToggle.setAttribute("aria-expanded", String(state.inspectorOpen));
  inspectorToggle.setAttribute("aria-label", state.inspectorOpen ? "Hide details panel" : "Show details panel");
  if (state.theme !== previous?.theme || !previous) {
    if (state.theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", state.theme);
    const dark = state.theme === "dark" || (state.theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    replace(themeToggle, icon(dark ? "sun" : "moon"));
    themeToggle.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme");
  }
  if (state.status !== previous?.status || state.statusError !== previous?.statusError || !previous) {
    const status = state.status;
    if (state.statusError && !status) {
      replace(chips, h("span", { class: "chip is-danger is-primary" }, "AI Assist is not answering"));
    } else if (status) {
      const engine = `${status.engine.toLowerCase()} · ${status.endpoint_local ? "this computer" : "remote server"}`;
      replace(chips,
        h("button", { class: `chip is-primary is-${status.privacy.role === "success" ? "success" : "amber"}`, type: "button",
          title: `${status.privacy.text}. Open the connection log.`, onClick: () => ctx.navigate("settings") },
          h("span", { class: "chip-text" }, status.privacy.text)),
        h("span", { class: `chip ${status.endpoint_local ? "is-success" : "is-amber"}`, title: status.endpoint },
          h("span", { class: "chip-text" }, engine)),
        h("span", { class: "chip is-muted", title: "Edits, commands and internet approvals run in the desktop app" },
          h("span", { class: "chip-text" }, "agent: desktop app")));
    }
  }
}

navToggle.addEventListener("click", () => {
  const open = !store.get().navOpen;
  store.set({ navOpen: open, inspectorOpen: open && narrow.matches ? false : store.get().inspectorOpen });
});
inspectorToggle.addEventListener("click", () => ctx.toggleInspector());
// Crossing into the narrow layout closes the drawers; widening restores the panel as remembered.
narrow.addEventListener("change", () => store.set({ navOpen: false, inspectorOpen: !narrow.matches && inspectorPreference() }));
themeToggle.addEventListener("click", () => {
  const state = store.get();
  const dark = state.theme === "dark" || (state.theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  ctx.setTheme(dark ? "light" : "dark");
});
scrim.addEventListener("click", () => store.set({ navOpen: false, inspectorOpen: !narrow.matches && store.get().inspectorOpen }));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => store.set({ theme: store.get().theme }));
document.addEventListener("keydown", (event) => {
  const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, select, [contenteditable]");
  if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === "n") {
    event.preventDefault();
    ctx.startNewChat();
  } else if (event.key === "Escape" && (store.get().navOpen || (store.get().inspectorOpen && narrow.matches)) && !dialog.open) {
    store.set({ navOpen: false, inspectorOpen: !narrow.matches && store.get().inspectorOpen });
  } else if (event.key === "/" && !typing && !dialog.open) {
    event.preventDefault();
    ctx.navigate("chats");
    chatSidebar.focusSearch();
  }
});

// ------------------------------------------------------------ start

const signIn = /** @type {HTMLElement} */ (document.getElementById("sign-in"));

/** @param {string} [message] */
function showSignIn(message) {
  signIn.hidden = false;
  app.setAttribute("inert", "");
  if (message) /** @type {HTMLElement} */ (document.getElementById("sign-in-message")).textContent = message;
}

api.onExpired(() => showSignIn("This page's session ended, usually because AI Assist was restarted. Open a new link from its window."));

async function start() {
  const launch = new URLSearchParams(window.location.hash.slice(1)).get("launch");
  if (launch) {
    // The code works once; take it out of the address bar and history at once.
    window.history.replaceState(null, "", "/");
    try { await api.signIn(launch); }
    catch (error) { showSignIn(error instanceof Error ? error.message : String(error)); return; }
  }
  if (!api.hasSession()) { showSignIn(); return; }
  store.set({ view: "chats" });
  await Promise.all([ctx.loadStatus(), ctx.loadChats(""), ctx.loadModels()]);
  if (store.get().inspectorOpen) ctx.loadDetails(store.get().selectedModel);
  window.setInterval(() => { if (!document.hidden) ctx.loadStatus(); }, 15000);
  window.setInterval(() => { if (!document.hidden && store.get().view === "compute") ctx.loadCompute(); }, 5000);
}

start();
