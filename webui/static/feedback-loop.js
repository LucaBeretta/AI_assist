/* Post-task check-in: user initiated, local-only journal; no automatic memory. */
"use strict";
window.feedbackui = (() => {
  const t = key => window.i18n ? window.i18n.t(key) : key;
  const root = document.createElement("section");
  root.id = "task-feedback";
  root.className = "task-feedback";
  root.hidden = true;
  root.setAttribute("aria-label", "Task feedback");
  const heading = document.createElement("strong");
  heading.textContent = t("Was this useful?");
  const actions = document.createElement("div");
  actions.className = "task-feedback-actions";
  const details = document.createElement("div");
  details.hidden = true;
  const textarea = document.createElement("textarea");
  textarea.maxLength = 1200;
  textarea.rows = 2;
  textarea.placeholder = t("What could I do better?");
  const memoryLabel = document.createElement("label");
  memoryLabel.className = "check-row";
  const remember = document.createElement("input");
  remember.type = "checkbox";
  memoryLabel.append(remember, document.createTextNode(t("Remember this as a preference")));
  const note = document.createElement("small");
  note.textContent = t("Feedback stays on this computer. Only checked preferences enter shared memory.");
  const detailActions = document.createElement("div");
  detailActions.className = "task-feedback-actions";
  const error = document.createElement("span");
  error.setAttribute("role", "alert");
  let current = null;
  let rating = null;
  let pending = false;
  const key = item => "aiassist-feedback:" + item.kind + ":" + item.chatId + ":" + (item.kind === "chat" ? item.index : item.taskId);
  const done = new Set();
  function hide() { root.hidden = true; details.hidden = true; current = null; }
  function btn(text, fn) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "btn small-btn";
    button.textContent = t(text); button.addEventListener("click", fn);
    return button;
  }
  function seen(item) {
    if (done.has(key(item))) return true;
    try { return window.localStorage?.getItem(key(item)) === "done"; }
    catch (_error) { return false; }
  }
  function mark(item) {
    done.add(key(item));
    try { window.localStorage?.setItem(key(item), "done"); } catch (_error) { /* optional */ }
  }
  async function save() {
    if (!current || pending) return;
    const item = current;
    const text = textarea.value.trim();
    if (remember.checked && !text) { error.textContent = t("Describe a preference to remember."); return; }
    pending = true; error.textContent = "";
    try {
      const result = await window.pywebview.api.record_task_feedback(
        item.chatId, item.kind === "chat" ? item.index : -1, rating, text,
        remember.checked, item.kind === "developer" ? item.taskId : "");
      if (!result || result.ok === false) throw new Error(result?.error || t("Could not save feedback."));
      mark(item);
      if (current === item) hide();
    } catch (failure) { error.textContent = failure.message || String(failure); }
    finally { pending = false; }
  }
  function choose(value) {
    rating = value;
    if (value === "useful") { save(); return; }
    details.hidden = false;
    textarea.focus?.();
  }
  const yes = btn("Yes", () => choose("useful"));
  const improve = btn("Not quite", () => choose("not_useful"));
  const skip = btn("Skip", () => { if (current) mark(current); hide(); });
  actions.append(yes, improve, skip);
  const saveBtn = btn("Save feedback", save);
  const cancelBtn = btn("Cancel", () => { details.hidden = true; textarea.value = ""; remember.checked = false; });
  detailActions.append(cancelBtn, saveBtn);
  details.append(textarea, memoryLabel, note, detailActions, error);
  root.append(heading, actions, details);
  document.body.append(root);
  function offer(item) {
    if (!item?.chatId || seen(item)) return;
    // Do not interrupt the user while they are filling in another feedback form.
    if (current && !root.hidden) return;
    current = item; rating = null; textarea.value = ""; remember.checked = false;
    error.textContent = ""; details.hidden = true; root.hidden = false;
  }
  function chat(chatId, index) {
    if (!chatId || !Number.isInteger(index) || index < 0) return;
    offer({kind:"chat", chatId, index});
  }
  function developer(chatId, taskId) {
    if (!chatId || !taskId) return;
    offer({kind:"developer", chatId, taskId});
  }
  function clear() { hide(); }
  return {chat, developer, clear};
})();
