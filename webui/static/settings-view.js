// @ts-check
/** Settings: engine, appearance, shared memory, the connection log, and what this interface cannot do. */
import { h, icon, replace } from "./dom.js";

const SECTIONS = [["settings-engine", "Engine"], ["settings-appearance", "Appearance"], ["settings-memory", "Shared memory"],
  ["settings-log", "Connection log"], ["settings-safety", "Safety"]];

/** @param {any} ctx */
export function createSettingsSidebar(ctx) {
  const element = h("div", { class: "sidebar-body" }, h("div", { class: "sidebar-heading" }, "Settings"),
    h("ul", { class: "list" }, SECTIONS.map(([id, label]) => h("li", null,
      h("button", { class: "list-item", type: "button", onClick: () => {
        ctx.store.set({ navOpen: false });
        const target = document.getElementById(id);
        target?.scrollIntoView({ block: "start" });
        target?.focus();
      } }, h("span", { class: "list-item-title" }, label))))));
  return { element, render: () => {} };
}

/** @param {any} ctx */
export function createSettingsView(ctx) {
  const engine = h("div", { class: "card-row" });
  const appearance = h("div", { class: "card-row" });
  const memory = h("div", { class: "field" });
  const log = h("div");
  const element = h("div", { class: "view-root" },
    h("header", { class: "view-header" }, h("h1", { class: "view-title" }, "Settings")),
    h("div", { class: "view-body" }, h("div", { class: "page" },
      h("section", { class: "card", id: "settings-engine", tabindex: "-1", "aria-labelledby": "engine-title" },
        h("h2", { id: "engine-title" }, "Engine"),
        h("p", { class: "muted" }, "The engine that answers new chats. A chat keeps the engine it was written with."), engine),
      h("section", { class: "card", id: "settings-appearance", tabindex: "-1", "aria-labelledby": "appearance-title" },
        h("h2", { id: "appearance-title" }, "Appearance"), appearance),
      h("section", { class: "card", id: "settings-memory", tabindex: "-1", "aria-labelledby": "memory-title" },
        h("h2", { id: "memory-title" }, "Shared memory"),
        h("p", { class: "muted" }, "Notes shared with every chat, plus short excerpts of related chats. You can also say “Remember that …” in a chat."),
        memory),
      h("section", { class: "card", id: "settings-log", tabindex: "-1", "aria-labelledby": "log-title" },
        h("div", { class: "card-row" }, h("h2", { id: "log-title" }, "Connection log"),
          h("button", { class: "button button-small", type: "button", onClick: () => ctx.loadLog() }, icon("refresh"), "Refresh")),
        h("p", { class: "muted" }, "Every internet request AI Assist made or tried to make, newest last, from the desktop app and from here."),
        log),
      h("section", { class: "card", id: "settings-safety", tabindex: "-1", "aria-labelledby": "safety-title" },
        h("h2", { id: "safety-title" }, "What this workspace cannot do"),
        h("ul", null,
          h("li", null, h("strong", null, "Go online. "), "There is no approval window here, so the internet gate blocks every internet request. Model downloads, cloud models and update checks happen in the desktop app, after two confirmations."),
          h("li", null, h("strong", null, "Change your projects. "), "The developer agent, file edits and commands run in the desktop app, where each one is reviewed first. Project files are read-only here."),
          h("li", null, h("strong", null, "Be reached from other devices. "), "It listens on this computer only, and every request needs this page's session."))),
    )));

  /** @param {string} label @param {boolean} pressed @param {() => void} action */
  const option = (label, pressed, action) => h("button", { type: "button", "aria-pressed": String(pressed), onClick: action }, label);

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "settings") return;
    const fresh = previous?.view !== "settings";
    if (fresh || state.status !== previous.status) {
      const current = state.status?.engine;
      replace(engine, h("div", { class: "segmented", role: "group", "aria-label": "Engine" },
        ["Ollama", "Colibri"].map((name) => option(name, current === name, () => { if (current !== name) ctx.setEngine(name); }))),
        state.status ? h("span", { class: "muted mono" }, state.status.endpoint) : null);
    }
    if (fresh || state.theme !== previous.theme) {
      replace(appearance, h("div", { class: "segmented", role: "group", "aria-label": "Theme" },
        [["system", "System"], ["light", "Day"], ["dark", "Night"]].map(([value, label]) =>
          option(label, state.theme === value, () => ctx.setTheme(value)))));
    }
    if (fresh || state.memory !== previous.memory) {
      const data = state.memory;
      if (!data) replace(memory, h("div", { class: "skeleton" }));
      else if (data.error) replace(memory, h("div", { class: "notice is-danger", role: "alert" }, data.error));
      else {
        const enabled = /** @type {HTMLInputElement} */ (h("input", { type: "checkbox", id: "memory-enabled", checked: data.enabled }));
        const notes = /** @type {HTMLTextAreaElement} */ (h("textarea", { class: "textarea", id: "memory-notes", rows: 8, maxlength: data.max, value: data.notes }));
        const count = h("span", { class: "muted mono" }, `${data.notes.length} / ${data.max}`);
        notes.addEventListener("input", () => { count.textContent = `${notes.value.length} / ${data.max}`; });
        replace(memory,
          h("label", { class: "check", for: "memory-enabled" }, enabled, h("span", null, "Use saved notes and relevant excerpts from other chats")),
          h("label", { class: "field-label", for: "memory-notes" }, "Notes"), notes,
          h("div", { class: "card-row" }, count,
            h("button", { class: "button button-ghost", type: "button", onClick: () => { notes.value = ""; notes.dispatchEvent(new Event("input")); } }, "Clear notes"),
            h("button", { class: "button button-primary", type: "button", onClick: () => ctx.saveMemory(notes.value, enabled.checked) }, "Save")));
      }
    }
    if (fresh || state.log !== previous.log) {
      const data = state.log;
      if (!data) replace(log, h("div", { class: "skeleton" }));
      else if (data.error) replace(log, h("div", { class: "notice is-danger", role: "alert" }, data.error));
      else if (!data.entries.length) replace(log, h("p", { class: "empty-note" }, "No internet requests yet."));
      else {
        replace(log, h("ol", { class: "log", "aria-label": "Internet requests" }, data.entries.map((/** @type {any} */ entry) =>
          h("li", { class: entry.decision.startsWith("blocked") ? "is-blocked" : entry.decision === "allowed" ? "is-allowed" : "" },
            `${entry.time}  ${entry.decision.toUpperCase()}  ${entry.kind}: ${entry.target}`,
            h("div", { class: "muted" }, `why: ${entry.purpose || "not declared"} · asked by ${entry.source}`)))),
          h("p", { class: "muted mono" }, data.path));
      }
    }
  }
  return { element, render };
}
