// @ts-check
/** The details panel: the selected model, the last answer, privacy, memory and this computer. */
import { h, icon, replace } from "./dom.js";
import { formatBytes, metricsRows, shortTokens } from "./format.js";

/** @param {[string, any][]} rows */
function facts(rows) {
  return h("dl", { class: "facts" }, rows.filter(([, value]) => value !== null && value !== undefined && value !== "")
    .flatMap(([label, value]) => [h("dt", null, label), h("dd", null, String(value))]));
}

/** @param {any} ctx */
export function createInspector(ctx) {
  const model = h("section", { class: "inspector-section", "aria-labelledby": "inspector-model" });
  const runtime = h("section", { class: "inspector-section", "aria-labelledby": "inspector-runtime" });
  const privacy = h("section", { class: "inspector-section", "aria-labelledby": "inspector-privacy" });
  const memory = h("section", { class: "inspector-section", "aria-labelledby": "inspector-memory" });
  const hardware = h("section", { class: "inspector-section", "aria-labelledby": "inspector-hardware" });
  const element = h("div", null, model, runtime, privacy, memory, hardware);

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (!state.inspectorOpen) return;
    const opened = !previous?.inspectorOpen;
    if (opened || state.selectedModel !== previous.selectedModel || state.details !== previous.details || state.models !== previous.models) {
      const name = state.selectedModel;
      const details = state.details[name];
      const running = state.models.find((/** @type {any} */ item) => item.name === name);
      const capabilities = details?.capabilities || [];
      replace(model, h("h2", { id: "inspector-model" }, "Model"),
        !name ? h("p", { class: "muted" }, "No model selected.") : [
          h("div", { class: "mono" }, name),
          details?.loading ? h("div", { class: "skeleton" }) : details?.error ? h("p", { class: "muted" }, details.error) : null,
          capabilities.length ? h("div", { class: "chips" }, capabilities.map((/** @type {string} */ c) => h("span", { class: "badge is-accent" }, c))) : null,
          facts([
            ["Context max", details?.context_length ? shortTokens(details.context_length) + " tokens" : details && !details.loading && !details.error ? "not advertised" : ""],
            ["Family", details?.family], ["Size", details?.parameter_size], ["Quantization", details?.quantization],
            ["Loaded", running ? (running.running ? `yes${running.placement ? " · " + running.placement : ""}` : "no") : ""],
            ["GPU memory", running?.size_vram ? formatBytes(running.size_vram) : ""],
          ]),
          !details && name ? h("button", { class: "button button-small", type: "button", onClick: () => ctx.loadDetails(name) }, "Load details") : null,
        ]);
    }
    if (opened || state.lastMetrics !== previous.lastMetrics || state.context !== previous.context) {
      const rows = metricsRows(state.lastMetrics);
      const used = state.lastMetrics && typeof state.lastMetrics.prompt_eval_count === "number"
        ? state.lastMetrics.prompt_eval_count + (state.lastMetrics.eval_count || 0) : null;
      const total = state.context !== "default" ? Number(state.context) : null;
      replace(runtime, h("h2", { id: "inspector-runtime" }, "Last answer"),
        rows.length ? facts(rows) : h("p", { class: "muted" }, "Timings appear after an answer."),
        used && total ? h("div", { class: "field" }, h("span", { class: "field-label" }, `Context ${shortTokens(used)} / ${shortTokens(total)}`),
          h("div", { class: "meter", role: "img", "aria-label": `${Math.round(Math.min(1, used / total) * 100)} percent of the context used` },
            h("span", { class: "meter-fill", "data-fill": String(Math.min(100, Math.round(used / total * 100))) }))) : null);
      const fill = /** @type {HTMLElement | null} */ (runtime.querySelector(".meter-fill"));
      if (fill) fill.style.width = `${fill.dataset.fill}%`;
    }
    if (opened || state.status !== previous.status) {
      const status = state.status;
      replace(privacy, h("h2", { id: "inspector-privacy" }, "Privacy"), status ? [
        h("span", { class: `chip ${status.privacy.role === "success" ? "is-success" : "is-amber"}` }, status.privacy.text),
        facts([["Engine", status.engine], ["Server", status.endpoint], ["Server is", status.endpoint_local ? "this computer" : "another computer"],
          ["Internet gate", status.gate.installed ? "on" : "off"], ["Internet approvals", "desktop app only"],
          ["Agent", "you approve each edit and command"]]),
      ] : h("div", { class: "skeleton" }));
      replace(memory, h("h2", { id: "inspector-memory" }, "Shared memory"),
        status ? h("p", { class: "muted" }, !status.memory.available ? "Unavailable." : status.memory.enabled
          ? "On: saved notes and related chats go with each message." : "Off: messages go without saved notes.") : null,
        h("button", { class: "button button-small", type: "button", onClick: () => { ctx.navigate("settings");
          window.setTimeout(() => document.getElementById("settings-memory")?.focus(), 50); } }, icon("memory"), "Edit notes"));
    }
    if (opened || state.hardware !== previous.hardware) {
      const report = state.hardware;
      replace(hardware, h("h2", { id: "inspector-hardware" }, "This computer"),
        !report ? h("button", { class: "button button-small", type: "button", onClick: () => ctx.loadHardware() }, "Scan hardware")
          : report.loading ? [h("div", { class: "skeleton" }), h("p", { class: "muted" }, "Reading CPU, memory and GPUs…")]
            : report.error ? h("p", { class: "muted" }, report.error) : [
              facts([["CPU", report.cpu], ["Threads", report.logical_processors],
                ["RAM", report.ram_total_bytes ? `${formatBytes(report.ram_available_bytes)} free of ${formatBytes(report.ram_total_bytes)}` : "unknown"],
                ["Model disk", report.disk_free_bytes ? `${formatBytes(report.disk_free_bytes)} free` : ""],
                ["Ollama", report.ollama_version || (report.ollama_running ? "running" : "not reachable")]]),
              ...(report.gpus || []).map((/** @type {any} */ gpu) => facts([["GPU", gpu.name],
                ["VRAM", gpu.total_bytes ? `${formatBytes(gpu.free_bytes)} free of ${formatBytes(gpu.total_bytes)}` : "unknown"]])),
              h("button", { class: "button button-ghost button-small", type: "button", onClick: () => ctx.loadHardware() }, icon("refresh"), "Scan again"),
            ]);
    }
  }
  return { element, render };
}
