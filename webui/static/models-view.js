// @ts-check
/** Models: what the current engine serves, where each model runs, and which one new chats use. */
import { h, icon, replace } from "./dom.js";
import { formatBytes } from "./format.js";

/** @param {any} ctx */
export function createModelsSidebar(ctx) {
  const facts = h("dl", { class: "facts" });
  const element = h("div", { class: "sidebar-body" }, h("div", { class: "sidebar-heading" }, "Engine"), facts,
    h("p", { class: "muted" }, "Downloads go online, so they happen in the desktop app (Find & download models), after two confirmations."));
  /** @param {any} state */
  function render(state) {
    if (state.view !== "models") return;
    const status = state.status;
    replace(facts, status ? [
      h("dt", null, "Engine"), h("dd", null, status.engine),
      h("dt", null, "Server"), h("dd", null, status.endpoint),
      h("dt", null, "Where"), h("dd", null, status.endpoint_local ? "this computer" : "another computer"),
    ] : [h("dt", null, "Engine"), h("dd", null, "checking…")]);
  }
  return { element, render };
}

/** @param {any} ctx */
export function createModelsView(ctx) {
  const list = h("div", { class: "model-list", "aria-live": "polite" });
  const element = h("div", { class: "view-root" },
    h("header", { class: "view-header" }, h("div", { class: "view-heading" }, h("h1", { class: "view-title" }, "Models"),
      h("div", { class: "view-subtitle" }, "Installed on the current engine")),
      h("button", { class: "button button-small", type: "button", onClick: () => ctx.loadModels() }, icon("refresh"), "Refresh")),
    h("div", { class: "view-body" }, h("div", { class: "page" }, list)));

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "models") return;
    if (previous?.view === "models" && state.models === previous.models && state.selectedModel === previous.selectedModel
        && state.modelsError === previous.modelsError && state.modelsLoading === previous.modelsLoading) return;
    if (state.modelsError) {
      replace(list, h("div", { class: "notice is-amber", role: "alert" }, icon("cube"), h("div", null,
        h("strong", null, "The engine did not answer. "), state.modelsError,
        h("p", { class: "muted" }, "Start Ollama (or Colibri), then choose Refresh."))));
      return;
    }
    if (state.modelsLoading && !state.models.length) { replace(list, [1, 2, 3].map(() => h("div", { class: "skeleton" }))); return; }
    if (!state.models.length) {
      replace(list, h("section", { class: "state" }, h("h2", null, "No models yet"),
        h("p", { class: "muted" }, "Install one with the desktop app's Find & download models, or run “ollama pull qwen3:8b” in a terminal.")));
      return;
    }
    replace(list, state.models.map((/** @type {any} */ model) => {
      const selected = model.name === state.selectedModel;
      return h("div", { class: `model-row${selected ? " is-selected" : ""}` },
        h("span", { class: "model-name" }, model.name),
        model.cloud ? h("span", { class: "badge is-amber", title: "Runs on Ollama's servers; blocked here" }, "cloud") : null,
        model.running ? h("span", { class: "badge is-accent" }, `loaded${model.placement ? " · " + model.placement : ""}`) : h("span", { class: "badge" }, "not loaded"),
        model.size_vram ? h("span", { class: "muted mono" }, `vram ${formatBytes(model.size_vram)}`) : null,
        h("button", { class: "button button-small", type: "button", "aria-pressed": String(selected),
          onClick: () => { ctx.selectModel(model.name); ctx.announce(`${model.name} is used for new messages.`); } },
          selected ? "In use" : "Use"),
        h("button", { class: "button button-ghost button-small", type: "button",
          onClick: () => { ctx.selectModel(model.name); if (!ctx.store.get().inspectorOpen) ctx.toggleInspector(); else ctx.loadDetails(model.name); } },
          "Details"));
    }));
  }
  return { element, render };
}
