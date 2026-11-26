/* Developer view: runs agent tasks through Api.run_agent and renders plan/action approvals, activity and checkpoints. */
"use strict";

window.devui = (() => {
  const t = (text, vars = {}) => window.i18n ? window.i18n.t(text, vars) : text.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ""));

  const element = id => document.getElementById(id);
  let task = null;
  let starting = null;
  let syncing = false;
  let chatId = null;        // Developer chat used for the current project

  async function call(method, ...args) {
    const result = await window.pywebview.api[method](...args);
    if (!result || result.ok === false) throw new Error(window.i18n?.gate?.(result?.error) || result?.error || t("Invalid API response"));
    return result;
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function btn(label, className, onClick) {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", onClick);
    return item;
  }

  function status(text) { element("dev-status").textContent = text; }
  function remember(key, value) { try { window.localStorage?.setItem(key, value); } catch (error) { /* storage unavailable */ } }
  function recall(key) { try { return window.localStorage?.getItem(key); } catch (error) { return null; } }

  function thread() { return element("dev-thread"); }
  function append(item) {
    thread().append(item);
    const box = element("dev-scroll");
    box.scrollTop = box.scrollHeight;
  }

  // ---------- activity (collapsible list of steps) ----------
  function ensureActivity() {
    if (task.activity) return task.activity;
    const details = node("details", "activity");
    details.open = true;
    const summary = node("summary", "activity-summary", t("Working…"));
    const list = node("ol", "activity-list");
    details.append(summary, list);
    append(details);
    task.activity = {details, summary, list, reads: 0, edits: 0, commands: 0, steps: 0};
    return task.activity;
  }

  function summarize() {
    const a = task.activity;
    if (!a) return;
    const parts = [t("Steps: {v0}", {v0: (a.steps)})];
    if (a.reads) parts.push(t("Read: {v0}", {v0: (a.reads)}));
    if (a.edits) parts.push(t("Changed: {v0}", {v0: (a.edits)}));
    if (a.commands) parts.push(t("Commands: {v0}", {v0: (a.commands)}));
    a.summary.textContent = parts.join(" · ") + (task.running ? " · working" : "");
  }

  function activityLine(kind, text) {
    const a = ensureActivity();
    const item = node("li", "activity-item " + kind);
    item.append(node("span", "activity-dot"), node("span", "activity-text", text));
    a.list.append(item);
    return item;
  }

  // ---------- diff ----------
  function diffBlock(diff) {
    const pre = node("pre", "diff");
    let added = 0, removed = 0;
    for (const line of String(diff || "").split("\n")) {
      if (line.startsWith("+++") || line.startsWith("---")) continue;
      const kind = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
      if (kind === "add") added++;
      if (kind === "del") removed++;
      pre.append(node("span", "diff-line " + kind, line || " "));
    }
    return {pre, added, removed};
  }

  // ---------- approvals ----------
  function planCard(event) {
    const card = node("section", "dev-card plan-card");
    card.append(node("div", "dev-card-head", t("Proposed plan · nothing has been changed yet")));
    const area = node("textarea", "plan-text");
    area.value = event.text || "";
    area.rows = Math.min(14, Math.max(4, area.value.split("\n").length + 1));
    area.setAttribute("aria-label", t("Plan (you can edit it before approving)"));
    const actions = node("div", "dev-card-actions");
    const original = area.value;
    const decide = async (decision) => {
      for (const item of actions.querySelectorAll("button")) item.disabled = true;
      try {
        const edited = area.value.trim() !== original.trim();
        if (decision === "approve" && edited) await call("approve_plan", event.task_id, "edit", area.value);
        else await call("approve_plan", event.task_id, decision);
        card.classList.add("is-decided");
        actions.replaceChildren(node("span", "dev-decided", decision === "cancel" ? t("Plan cancelled") : edited ? t("Edited plan approved") : t("Plan approved")));
      } catch (error) {
        status(error.message);
        for (const item of actions.querySelectorAll("button")) item.disabled = false;
      }
    };
    actions.append(btn(t("Cancel task"), "btn ghost", () => decide("cancel")), node("span", "grow"),
                   node("span", "dev-hint", t("Edit the text to change the plan.")),
                   btn(t("Approve plan"), "btn primary", () => decide("approve")));
    card.append(area, actions);
    append(card);
    area.focus?.();
  }

  function actionCard(event) {
    const action = event.action || {};
    const card = node("section", "dev-card action-card");
    const head = node("div", "dev-card-head");
    let blocks = [];
    if (action.kind === "write_file") {
      const {pre, added, removed} = diffBlock(action.diff);
      head.append(node("span", "mono strong", action.path), node("span", "grow"),
                  node("span", "dev-hint", action.exists ? "edit" : "new file"),
                  node("span", "diff-count add", "+" + added), node("span", "diff-count del", "−" + removed));
      card.append(head);
      // Several blocks on an existing file: review them one by one (Api applies only the checked ones).
      if (action.exists && (action.hunks || []).length > 1) {
        for (const [index, hunk] of action.hunks.entries()) {
          const bar = node("label", "hunk-bar");
          const box = node("input");
          box.type = "checkbox";
          box.checked = true;
          box.addEventListener("change", updateApplyLabel);
          bar.append(box, node("span", "", t("Block {v0}", {v0: (index + 1)})), node("span", "mono dev-hint", hunk.header));
          const {pre: lines} = diffBlock(hunk.header + "\n" + hunk.lines.join(""));
          lines.firstChild?.remove?.();
          card.append(bar, lines);
          blocks.push({id: hunk.id, box});
        }
      } else {
        card.append(pre);
      }
      if (action.destination && !action.destination.replaceAll("\\", "/").includes("/" + action.path.replaceAll("\\", "/"))) {
        card.append(node("p", "dev-warning", t("Destination: ") + action.destination));
      }
    } else if (action.kind === "run_command") {
      head.append(node("span", "tag", "COMMAND"), node("code", "mono strong", action.command));
      card.append(head);
      if (action.description) card.append(node("p", "dev-hint pad", action.description));
      card.append(node("p", "dev-hint pad mono", `in ${action.cwd} · timeout ${action.timeout_seconds}s`));
    } else if (action.kind === "blender_run") {
      head.append(node("span", "tag", "BLENDER"), node("span", "strong", t("Run in the open Blender")));
      card.append(head);
      if (action.description) card.append(node("p", "dev-hint pad", action.description));
      card.append(node("pre", "diff", action.code || ""));
    } else {
      head.append(node("span", "strong", action.kind || t("Action")));
      card.append(head, node("pre", "diff", JSON.stringify(action, null, 2)));
    }
    const actions = node("div", "dev-card-actions");
    const decide = async (approved) => {
      for (const item of actions.querySelectorAll("button")) item.disabled = true;
      const chosen = blocks.filter(item => item.box.checked).map(item => item.id);
      const partial = approved && blocks.length && chosen.length < blocks.length;
      try {
        if (partial) await call("approve_action", event.task_id, event.approval_id, true, chosen);
        else await call("approve_action", event.task_id, event.approval_id, approved);
        card.classList.add("is-decided");
        for (const item of blocks) item.box.disabled = true;
        actions.replaceChildren(node("span", "dev-decided " + (approved ? "ok" : "no"),
          !approved ? t("Rejected") : partial ? t("Applied {v0} of {v1} blocks", {v0: (chosen.length), v1: (blocks.length)}) : t("Approved")));
      } catch (error) {
        status(error.message);
        for (const item of actions.querySelectorAll("button")) item.disabled = false;
      }
    };
    const runs = action.kind === "run_command" || action.kind === "blender_run";
    const yes = btn(runs ? t("Run") : t("Apply"), "btn primary", () => decide(true));
    function updateApplyLabel() {
      const chosen = blocks.filter(item => item.box.checked).length;
      yes.textContent = chosen === blocks.length ? t("Apply all") : t("Apply {v0} of {v1}", {v0: (chosen), v1: (blocks.length)});
      yes.disabled = chosen === 0;
    }
    if (blocks.length) updateApplyLabel();
    actions.append(btn(t("Reject"), "btn ghost", () => decide(false)), node("span", "grow"),
                   node("span", "dev-hint", action.kind === "write_file" ? t("The original is kept: you can restore it from Checkpoints.")
                     : action.kind === "blender_run" ? t("Runs inside Blender, not sandboxed. Ctrl+Z in Blender undoes it.")
                     : t("Runs on this computer, not sandboxed.")),
                   yes);
    card.append(actions);
    append(card);
    if (window.Notification?.permission === "granted" && document.hidden) new Notification(t("AI Assist needs your review"), {body: action.path || action.command || action.description || ""});
  }

  function commandResult(text) {
    const box = node("details", "command-result");
    const firstLine = String(text).split("\n")[0];
    const failed = /exited with code [1-9]/.test(firstLine);
    const summary = node("summary", failed ? "fail" : "ok", firstLine || t("Command finished"));
    box.append(summary, node("pre", "mono", text));
    append(box);
  }

  // ---------- events ----------
  function onEvent(event) {
    if (starting && event.chat_id === starting.chatId) {
      starting.events.push(event);
      return;
    }
    if (!task?.running || event.task_id !== task.id) return;
    if (event.type === "plan_approval" || event.type === "approval") {
      if (task.approvals.has(event.approval_id)) return;
      task.approvals.add(event.approval_id);
    }
    switch (event.type) {
      case "status": { const text = window.i18n?.gate?.(event.text) || event.text; status(text); activityLine("info", text); break; }
      case "hybrid_stage": case "hybrid_warning": status(event.text); activityLine("info", event.text); break;
      case "hybrid_review": activityLine("info", event.text); break;
      case "loading": status(event.text); break;
      case "metrics": status(event.text); element("dev-step").textContent = (event.text.match(/Step \d+\/\d+/) || [""])[0]; break;
      case "tool_start": {
        const a = ensureActivity();
        a.steps = Math.max(a.steps, event.step || 0);
        if (event.tool === "read_file" || event.tool === "list_files") a.reads++;
        if (event.tool === "run_command") a.commands++;
        task.current = activityLine("running", event.text);
        summarize();
        break;
      }
      case "tool_result":
        if (task.current) { task.current.className = "activity-item done"; task.current = null; }
        if (event.tool === "run_command") commandResult(event.text);
        break;
      case "changed": ensureActivity().edits++; activityLine("changed", event.text); summarize(); break;
      case "checkpoint": activityLine("info", event.text); loadCheckpoints(); break;
      case "plan_approval": planCard(event); status(t("Waiting for your plan approval")); break;
      case "approval": actionCard(event); status(t("Waiting for your review")); break;
      case "plan_approved": activityLine("done", t("Plan approved")); break;
      case "assistant":
        if (event.text && !event.final) { const p = node("div", "dev-note"); p.append(window.renderMarkdown(event.text)); append(p); }
        break;
      case "agent_done": case "agent_stopped": case "agent_error": finish(event); break;
      default: break;
    }
  }

  async function syncTask() {
    if (!task?.running || syncing || !window.pywebview?.api?.developer_task_state) return;
    const id = task.id;
    syncing = true;
    try {
      const snapshot = await call("developer_task_state", id);
      if (task.id !== id || !task.running) return;
      for (const approval of snapshot.approvals) onEvent(approval);
      if (snapshot.result) onEvent(snapshot.result);
    } catch (error) {
      // The next status tick can recover from a transient bridge failure.
    } finally {
      syncing = false;
    }
  }

  function finish(event) {
    task.running = false;
    summarize();
    if (task.activity) task.activity.details.open = false;
    const answer = node("div", "dev-answer" + (event.type === "agent_error" ? " is-error" : ""));
    answer.append(window.renderMarkdown(event.type === "agent_error" ? t("**Error:** ") + event.error : event.text || ""));
    append(answer);
    status(event.type === "agent_done" ? t("Task finished") : event.type === "agent_stopped" ? t("Task stopped") : t("Task failed"));
    element("dev-step").textContent = "";
    controls();
    loadCheckpoints();
    loadFiles();
    loadPins();
    if (event.type === "agent_done" && task.chatId && !window.app?.currentChat()?.incognito) window.feedbackui?.developer(task.chatId, task.id);
  }

  function controls() {
    const running = Boolean(task?.running);
    const busy = running || Boolean(starting);
    element("dev-run").disabled = busy;
    element("dev-stop").disabled = !running;
    element("dev-project").disabled = busy;
    element("dev-browse").disabled = busy;
  }

  // ---------- run / stop ----------
  async function run(event) {
    event?.preventDefault?.();
    if (starting || task?.running) return;
    const text = element("dev-task").value.trim();
    const project = element("dev-project").value.trim();
    if (!text) return;
    if (!project) { status(t("Choose a project folder first.")); element("dev-project").focus?.(); return; }
    const choice = window.app?.modelChoice() || {name: element("model").value, backend: "Ollama"};
    if (choice.backend !== "Ollama") { status(t("Developer currently requires an Ollama model.")); return; }
    const model = choice.name;
    starting = {chatId: null, events: []};
    controls();
    try {
      if (!chatId) {
        const created = await call("new_chat");
        chatId = created.chat.id;
      }
      await call("set_chat_model", chatId, model, "Ollama");
      starting.chatId = chatId;
      remember("aiassist-dev-project", project);
      const steps = Number(document.querySelector('input[name="dev-steps"]:checked')?.value || 20);
      const context = Number(element("context").value) || 16384;
      const planFirst = element("dev-plan").checked;
      const result = await call("run_agent", chatId, text, project, planFirst, steps, Math.max(2048, context), Boolean(element("dev-quality").checked));
      const events = starting.events;
      starting = null;
      task = {id: result.task_id, chatId, running: true, activity: null, current: null, approvals: new Set()};
      const bubble = node("div", "dev-task-bubble");
      bubble.append(node("pre", "", text));
      append(bubble);
      element("dev-task").value = "";
      status(planFirst ? t("Planning (read-only)…") : t("Working…"));
      for (const item of events) onEvent(item);
      void syncTask();
    } catch (error) { status(error.message); }
    finally { starting = null; controls(); }
  }

  async function stop() {
    if (!task?.running) return;
    status(t("Stopping…"));
    try { await call("stop_task", task.id); } catch (error) { status(error.message); }
  }

  // ---------- checkpoints ----------
  async function loadCheckpoints() {
    const project = element("dev-project").value.trim();
    const list = element("dev-checkpoints");
    if (!project) { list.replaceChildren(node("p", "dev-hint", t("Choose a project folder to see its checkpoints."))); return; }
    try {
      const result = await call("list_checkpoints", project);
      if (element("dev-project").value.trim() !== project) return;
      const rows = (result.warnings || []).map(warning => node("p", "dev-warning", warning));
      if (!result.checkpoints.length) rows.push(node("p", "dev-hint", t("No checkpoints yet. One is saved before the first change of each task.")));
      for (const item of result.checkpoints) {
        const row = node("div", "checkpoint");
        const files = Object.keys(item.files || {});
        const when = new Date(item.created_at);
        const info = node("div", "checkpoint-info");
        info.append(node("span", "strong", isNaN(when) ? item.created_at : when.toLocaleString()),
                    node("span", "dev-hint mono", files.slice(0, 3).join(", ") + (files.length > 3 ? ` +${files.length - 3}` : "")));
        row.append(info, btn(t("Restore"), "btn", async () => {
          const message = t("Restore {v0} file(s) to how they were before this task?\\n\\nLater changes to these files will be overwritten and files created by the task will be deleted.", {v0: (files.length)});
          if (window.confirm && !window.confirm(message)) return;
          try { const done = await call("restore_checkpoint", item.task_id); status(t("Restored {v0} file(s).", {v0: (done.restored)})); }
          catch (error) { status(error.message); }
        }));
        rows.push(row);
      }
      list.replaceChildren(...rows);
    } catch (error) { list.replaceChildren(node("p", "dev-hint", error.message)); }
  }

  // ---------- files, pins, preview ----------
  let files = [];
  let pinned = [];   // [{path, size_bytes, estimated_tokens, error}]

  function tokensLabel(n) { return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k tok" : (n || 0) + " tok"; }

  async function loadFiles() {
    const project = element("dev-project").value.trim();
    const list = element("dev-files");
    if (!project) { files = []; list.replaceChildren(node("p", "dev-hint", t("Choose a project folder to list its files."))); return; }
    try {
      files = (await call("list_project_files", project)).files;
      renderFiles();
    } catch (error) { files = []; list.replaceChildren(node("p", "dev-hint", error.message)); }
  }

  async function loadPins() {
    if (!chatId) { pinned = []; renderPins(); return; }
    try { pinned = (await call("open_chat", chatId)).pinned_files || []; } catch (error) { pinned = []; }
    renderPins();
    renderFiles();
  }

  function renderPins() {
    const box = element("dev-pins");
    if (!pinned.length) {
      box.replaceChildren(node("p", "dev-hint", t("Choose a folder, then pin files to send them with every request.")));
      return;
    }
    box.replaceChildren(...pinned.map(item => {
      const chip = node("span", "chip pin-chip" + (item.error ? " is-error" : ""));
      chip.title = item.error || item.path;
      chip.append(node("span", "mono", item.path), node("span", "dev-hint", item.error ? t("unavailable") : tokensLabel(item.estimated_tokens)),
                  btn("×", "chip-x", () => togglePin(item.path, false)));
      chip.lastChild.setAttribute("aria-label", t("Unpin ") + item.path);
      return chip;
    }));
  }

  function renderFiles() {
    const list = element("dev-files");
    if (!files.length) return;
    const filter = element("dev-file-filter").value.trim().toLowerCase();
    const shown = files.filter(item => !filter || item.path.toLowerCase().includes(filter)).slice(0, 200);
    const pins = new Set(pinned.map(item => item.path));
    list.replaceChildren(...shown.map(item => {
      const row = node("div", "file-row" + (pins.has(item.path) ? " is-pinned" : ""));
      row.append(node("span", "file-path mono", item.path), node("span", "dev-hint", tokensLabel(item.estimated_tokens)));
      if (/\.html?$/i.test(item.path)) row.append(btn(t("Preview"), "file-btn", () => preview(item.path)));
      const pin = btn(pins.has(item.path) ? t("Unpin") : t("Pin"), "file-btn", () => togglePin(item.path, !pins.has(item.path)));
      pin.disabled = !chatId;
      row.append(pin);
      return row;
    }));
  }

  async function togglePin(path, on) {
    try {
      const result = await call(on ? "pin_file" : "unpin_file", chatId, path);
      pinned = result.pinned_files || [];
      renderPins();
      renderFiles();
    } catch (error) { status(error.message); }
  }

  async function preview(path) {
    try { await call("preview_file", element("dev-project").value.trim(), path); status(t("Preview opened in a separate window.")); }
    catch (error) { status(error.message); }
  }

  // ---------- wiring ----------
  function onShow() { loadCheckpoints(); loadFiles(); loadPins(); void syncTask(); element("dev-task").focus?.(); }

  element("dev-project").value = recall("aiassist-dev-project") || "";
  async function setProject(folder) {
    if (starting || task?.running) throw new Error(t("Stop the current task before changing its folder."));
    if (!chatId) chatId = (await call("new_chat", false, null, folder)).chat.id;
    await call("set_chat_project", chatId, folder);
    element("dev-project").value = folder; remember("aiassist-dev-project", folder);
    await loadFiles(); await loadPins(); await loadCheckpoints();
  }
  element("dev-project").addEventListener("change", () => setProject(element("dev-project").value.trim()).catch(error => status(error.message)));
  element("dev-file-filter").addEventListener("input", renderFiles);
  element("dev-browse").addEventListener("click", async () => {
    try {
      const result = await call("choose_folder");
      if (result.cancelled) return;
      await setProject(result.path);
    } catch (error) { status(error.message); }
  });
  element("dev-form").addEventListener("submit", run);
  element("dev-task").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) run(event);
  });
  element("dev-stop").addEventListener("click", stop);
  controls();
  return {onEvent, onShow, setProject, syncTask, isRunning: () => Boolean(task?.running), stop};
})();
