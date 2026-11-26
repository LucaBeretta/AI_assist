/* Tk feature adapters. Every long operation is a cancellable API job, never a UI worker. */
"use strict";
window.parityui = (() => {
  const el = id => document.getElementById(id);
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : key.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const node = (tag, text, cls = "") => { const item = document.createElement(tag); item.className = cls; if (text != null) item.textContent = text; return item; };
  const call = async (name, ...args) => { const result = await window.pywebview.api[name](...args); if (!result || result.ok === false) throw new Error(window.i18n?.gate?.(result?.error) || result?.error || t("Invalid API response")); return result; };
  const show = id => { const dialog = el(id); if (dialog.showModal) dialog.showModal(); else dialog.open = true; };
  const close = id => { const dialog = el(id); if (dialog.close) dialog.close(); else dialog.open = false; };
  const jobs = new Map(), terminal = new Set();
  let feedback = null, catalog = [], hybrid = null, qualityDirty = false, colibriState = null;
  const updateOperations = ["updates", "install_update", "rollback_update"];
  function offerInstall(available) {
    el("install-update").hidden = el("hourly-install").hidden = !available;
  }
  function outputFor(operation) { return updateOperations.includes(operation) ? "update-result" : operation === "deploy" ? "deploy-output" : "colibri-output"; }
  function updateControls() {
    const busy = [...jobs.keys()].some(name => name.startsWith("colibri_") && name !== "colibri_catalog");
    for (const id of ["colibri-start", "colibri-stop", "colibri-install", "colibri-apply-context", "colibri-browse"]) el(id).disabled = busy;
    el("colibri-cancel").disabled = !busy;
    const updating = updateOperations.some(name => jobs.has(name));
    for (const id of ["check-updates", "install-update", "rollback-update", "restart-update", "hourly-install", "hourly-check-updates"]) el(id).disabled = updating;
    el("cancel-update").hidden = !jobs.has("install_update") || jobs.get("install_update").cancellable === false;
    el("deploy-submit").disabled = jobs.has("deploy"); el("deploy-stop").disabled = !jobs.has("deploy");
  }
  async function start(method, args, operation) {
    if (jobs.has(operation)) return;
    if (operation === "updates") offerInstall(false);
    jobs.set(operation, {id: null, stopping: false}); updateControls();
    el(outputFor(operation)).textContent = t("Working…");
    if (operation === "deploy") el("deploy-result").replaceChildren();
    try {
      const result = await call(method, ...args);
      if (!terminal.has(result.job_id)) {
        const job = jobs.get(operation); if (job) { job.id = result.job_id; if (job.stopping) await call("cancel_job", job.id); }
      }
    } catch (failure) { jobs.delete(operation); el(outputFor(operation)).textContent = failure.message; updateControls(); }
  }
  async function cancel(operation) {
    const job = jobs.get(operation); if (!job) return;
    job.stopping = true; el(outputFor(operation)).textContent = t("Stopping…");
    if (job.id) { try { await call("cancel_job", job.id); } catch (failure) { el(outputFor(operation)).textContent = failure.message; } }
  }
  function safeLink(url, label) {
    try {
      const value = new URL(url);
      if (value.protocol !== "https:" || value.username || value.password) throw new Error("invalid link");
      const link = node("a", label || url); link.href = value.href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
    } catch (failure) { return node("span", label || String(url)); }
  }
  function renderCatalog() {
    const selected = el("colibri-model").value;
    el("colibri-model").replaceChildren(...catalog.map(row => {
      const option = node("option", row.name); option.value = row.id; option.disabled = !row.fits || row.kind !== "text"; return option;
    }));
    el("colibri-model").value = catalog.some(row => row.id === selected && row.fits) ? selected : catalog.find(row => row.fits && row.kind === "text")?.id || "";
    modelDetails();
  }
  function modelDetails() {
    const row = catalog.find(item => item.id === el("colibri-model").value);
    el("colibri-model-details").textContent = row ? t("Download {v0} GB · RAM {v1} GB minimum · {v2}", {v0: row.download_gb ?? "?", v1: row.ram_min_gb ?? "?", v2: row.reason || row.summary || ""}) : t("Choose a supported model from the catalog.");
  }
  function onEvent(event) {
    if (!["job_progress", "job_done", "job_error", "job_stopped"].includes(event.type)) return false;
    const job = jobs.get(event.operation);
    if (!job || (job.id && job.id !== event.job_id)) return true;
    job.id = event.job_id;
    const output = el(outputFor(event.operation));
    if (event.type === "job_progress") {
      if (event.text) output.textContent = event.operation === "deploy" ? (output.textContent + "\n" + event.text).slice(-32000) : event.text;
      if (event.operation === "install_update") {
        const bar = el("update-progress");
        bar.hidden = event.cancellable === false;
        if (bar.hidden) job.cancellable = false;
        el("cancel-update").hidden = bar.hidden;
        if (event.total) { bar.max = event.total; bar.value = event.completed; }
        output.textContent = bar.hidden ? t("Installing update…") : t("Downloaded {v0} KB", {v0: Math.ceil((event.completed || 0) / 1024)});
        return true;
      }
      if (event.total) { el("colibri-progress").hidden = false; el("colibri-progress").max = event.total; el("colibri-progress").value = event.completed || 0; }
      return true;
    }
    if (updateOperations.includes(event.operation)) el("update-progress").hidden = true;
    terminal.add(event.job_id); jobs.delete(event.operation); updateControls();
    if (event.operation.startsWith("colibri_")) el("colibri-progress").hidden = true;
    if (event.type !== "job_done") output.textContent = event.error ? (window.i18n?.gate?.(event.error) || t(event.error)) : t("Operation stopped.");
    else if (event.operation === "updates") {
      const result = event.result;
      offerInstall(result.can_install);
      output.textContent = [result.differences.length ? t("Changed files: {v0}", {v0: result.differences.length}) : t("This copy matches upstream."), result.reason ? t(result.reason) : ""].filter(Boolean).join(" ");
    } else if (event.operation === "install_update" || event.operation === "rollback_update") {
      offerInstall(false);
      el("restart-update").hidden = false;
      el("rollback-update").hidden = !event.result.can_rollback;
      output.textContent = t(event.operation === "install_update" ? "Update installed." : "Previous version restored.");
      if (event.result.command) output.append(node("p", t("Before restarting, run this command:"), "setting-hint"), node("code", event.result.command));
    } else if (event.operation === "colibri_catalog") {
      catalog = event.result.models || []; renderCatalog(); output.textContent = t("Catalog ready.");
    } else if (event.operation.startsWith("colibri_")) {
      output.textContent = t("Operation finished."); el("colibri-progress").hidden = true; window.app?.refreshModels();
    } else if (event.operation === "deploy") output.textContent += "\n" + t("Deployment finished.");
    if (event.result?.url && event.operation === "deploy") {
      const value = event.result;
      el("deploy-result").replaceChildren(safeLink(value.url), node("p", value.domain_error || (value.domain_assigned ? t("Domain assigned.") : "")));
    }
    return true;
  }
  function openFeedback(chatId, index) {
    feedback = {chatId, index}; el("feedback-text").value = ""; el("feedback-criteria").value = ""; el("feedback-error").textContent = ""; show("feedback-dialog");
  }
  el("feedback-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await call("prepare_feedback", feedback.chatId, feedback.index, el("feedback-text").value, el("feedback-criteria").value);
      if (window.app.currentChat()?.id !== feedback.chatId) throw new Error(t("Open the original chat before preparing this follow-up."));
      window.app.setPrompt(result.text); window.app.showView("chat"); close("feedback-dialog");
    } catch (failure) { el("feedback-error").textContent = failure.message; }
  });
  el("feedback-cancel").addEventListener("click", () => close("feedback-dialog"));
  async function capabilities() {
    const box = el("capabilities-result"), selected = window.app.modelChoice();
    if (!selected.name) { box.textContent = t("Choose a model in Chats first."); return; }
    // A paired computer's model answers chats with text only, over the local network.
    if (String(selected.backend).startsWith("peer:")) {
      box.replaceChildren(node("div", selected.name, "model-row mono"),
        node("p", t("Runs on a paired computer: it answers chats with text. Pictures and Developer tasks need a model on this computer."), "model-row"));
      return;
    }
    box.textContent = t("Working…");
    try {
      const result = await call("model_capabilities", selected.name, selected.backend), data = result.metadata;
      box.replaceChildren(node("div", selected.backend + " · " + selected.name, "model-row mono"),
        ...["tools", "vision", "thinking"].map(name => {
          const row = node("div", undefined, "model-row"); row.append(node("span", t(name)), node("span", (data.capabilities || []).includes(name) ? t("Reported") : t("Not reported"), "pill")); return row;
        }), node("p", t("Advertised context: {v0}", {v0: data.context_length || "?"}), "model-row mono"));
    } catch (failure) { box.textContent = failure.message; }
  }
  function onState(state) {
    hybrid = state.hybrid || {enabled: false, review_model: "", manage_memory: true};
    if (!qualityDirty) el("dev-quality").checked = hybrid.enabled;
    colibriState = state.colibri || {};
    renderColibriState();
  }
  function renderColibriState() {
    if (!colibriState) return;
    el("colibri-folder").value = colibriState.folder || "";
    el("colibri-state").textContent = t(colibriState.state || "offline") + (colibriState.context_length ? " · " + t("Context length") + ": " + colibriState.context_length : "");
    renderReviewerChoices();
  }
  // Quality review is optional: offer the models Colibri is serving now and say what to do when there are none.
  function renderReviewerChoices() {
    const models = Array.isArray(colibriState?.models) ? colibriState.models : [];
    el("hybrid-model-options").replaceChildren(...models.map(name => { const option = document.createElement("option"); option.value = name; return option; }));
    el("hybrid-model").placeholder = models.length ? t("Choose a Colibri model") : t("No Colibri model available");
    el("hybrid-model-hint").textContent = models.length
      ? t("Choose one of the models Colibri is serving now.")
      : colibriState?.installed
        ? t("Start Colibri in Models to list its models. Until then, leave quality review off.")
        : t("Optional. Colibri is a second AI engine that you install separately. Without it, leave quality review off and Ollama works as usual.");
  }
  async function openSettings() {
    try {
      const state = await call("update_status");
      el("rollback-update").hidden = !state.can_rollback;
      el("restart-update").hidden = !state.restart_ready;
    } catch (error) { el("update-result").textContent = error.message; }
    // The hint must also show when the engine state never arrived (for example, Ollama offline).
    renderReviewerChoices();
    if (!hybrid) return;
    el("hybrid-enabled").checked = hybrid.enabled; el("hybrid-model").value = hybrid.review_model; el("hybrid-memory").checked = hybrid.manage_memory;
  }
  el("hybrid-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await call("set_hybrid_settings", el("hybrid-enabled").checked, el("hybrid-model").value, el("hybrid-memory").checked);
      hybrid = result.hybrid; qualityDirty = false; el("dev-quality").checked = hybrid.enabled; el("hybrid-status").textContent = t("Saved.");
    } catch (failure) { el("hybrid-status").textContent = failure.message; }
  });
  el("dev-quality").addEventListener("change", () => { qualityDirty = true; });
  el("check-updates").addEventListener("click", () => start("check_updates", [], "updates"));
  el("install-update").addEventListener("click", () => start("install_update", [], "install_update"));
  el("hourly-install").addEventListener("click", async () => {
    close("hourly-dialog");
    if (!el("settings-dialog").open) await window.app.openSettings();
    await start("install_update", [], "install_update");
  });
  el("cancel-update").addEventListener("click", () => cancel("install_update"));
  el("rollback-update").addEventListener("click", () => start("rollback_update", [], "rollback_update"));
  el("restart-update").addEventListener("click", async () => {
    try { await call("restart_app"); } catch (error) { el("update-result").textContent = error.message; }
  });
  el("create-shortcut").addEventListener("click", async () => {
    try { await call("create_shortcut"); el("shortcut-result").textContent = t("Desktop shortcut created."); }
    catch (error) { el("shortcut-result").textContent = error.message; }
  });
  el("capabilities-refresh").addEventListener("click", capabilities);
  el("colibri-browse").addEventListener("click", async () => {
    try { const chosen = await call("choose_folder"); if (!chosen.cancelled) { await call("set_colibri_folder", chosen.path); el("colibri-folder").value = chosen.path; } }
    catch (failure) { el("colibri-output").textContent = failure.message; }
  });
  el("colibri-catalog").addEventListener("click", () => start("colibri_catalog", [], "colibri_catalog"));
  el("colibri-start").addEventListener("click", () => start("start_colibri", [], "colibri_start"));
  el("colibri-stop").addEventListener("click", () => start("stop_colibri", [], "colibri_stop"));
  el("colibri-model").addEventListener("change", modelDetails);
  el("colibri-install-form").addEventListener("submit", event => { event.preventDefault(); if (el("colibri-model").value) start("install_colibri", [el("colibri-model").value, Number(el("colibri-context").value) || 0], "colibri_install"); });
  el("colibri-apply-context").addEventListener("click", () => start("set_colibri_context", [Number(el("colibri-context").value) || 0], "colibri_context"));
  el("colibri-cancel").addEventListener("click", () => { for (const name of jobs.keys()) if (name.startsWith("colibri_")) cancel(name); });
  el("deploy-open").addEventListener("click", () => { el("deploy-folder").value = el("dev-project").value; el("deploy-confirm").checked = false; show("deploy-dialog"); });
  el("deploy-close").addEventListener("click", () => close("deploy-dialog"));
  el("deploy-stop").addEventListener("click", () => cancel("deploy"));
  el("deploy-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!el("deploy-confirm").checked) { el("deploy-output").textContent = t("Confirm the upload before deploying."); return; }
    start("deploy_vercel", [el("deploy-folder").value, el("deploy-project").value, el("deploy-scope").value, el("deploy-domain").value], "deploy");
  });
  window.addEventListener("languagechange", () => { if (!el("capabilities-view").hidden) capabilities(); modelDetails(); renderColibriState(); });
  updateControls();
  return {onEvent, onState, openSettings, openFeedback, capabilities};
})();
