/* Post-task check-in: user initiated, local-only journal; no automatic memory.
   Chats and Developer each have their own slim line just above their message box: it never covers
   Send or Run, hides with its page, never moves to the other page, and goes away when the person
   moves on there. */
"use strict";
window.feedbackui = (() => {
  const t = key => window.i18n ? window.i18n.t(key) : key;
  const key = item => "aiassist-feedback:" + item.kind + ":" + item.chatId + ":" + (item.kind === "chat" ? item.index : item.taskId);
  const done = new Set();
  function seen(item) {
    if (done.has(key(item))) return true;
    try { return window.localStorage?.getItem(key(item)) === "done"; }
    catch (_error) { return false; }
  }
  function mark(item) {
    done.add(key(item));
    try { window.localStorage?.setItem(key(item), "done"); } catch (_error) { /* optional */ }
  }
  function btn(text, fn) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "btn small-btn";
    button.textContent = t(text); button.addEventListener("click", fn);
    return button;
  }

  // One line for a page: "chat" sits in the Chats page, "developer" in the Developer page.
  function line(kind) {
    const root = document.createElement("section");
    root.id = kind === "developer" ? "task-feedback-dev" : "task-feedback";
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
    let current = null, rating = null, pending = false;
    function hide() { root.hidden = true; details.hidden = true; current = null; }
    const typing = () => Boolean(current) && !root.hidden && !details.hidden;
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
    actions.append(btn("Yes", () => choose("useful")), btn("Not quite", () => choose("not_useful")),
      btn("Skip", () => { if (current) mark(current); hide(); }));
    detailActions.append(btn("Cancel", () => { details.hidden = true; textarea.value = ""; remember.checked = false; }),
      btn("Save feedback", save));
    details.append(textarea, memoryLabel, note, detailActions, error);
    root.append(heading, actions, details);
    function place() {
      if (root.parentNode) return;
      const wrap = document.getElementById(kind === "developer" ? "dev-view" : "chat-view")?.querySelector?.(".composer-wrap");
      const box = wrap?.querySelector?.("form");
      if (wrap && box) wrap.insertBefore(root, box);
      else if (wrap) wrap.append(root);
      else document.body?.append?.(root);
    }
    function offer(item) {
      if (seen(item)) return;
      if (typing()) return;  // a newer answer replaces the question, unless the person is typing in "Not quite"
      place();
      // The line takes room from the answer above it: keep the end of the answer in view.
      const scroller = document.getElementById(kind === "developer" ? "dev-scroll" : "conversation");
      const atEnd = Boolean(scroller) && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 32;
      current = item; rating = null; textarea.value = ""; remember.checked = false;
      error.textContent = ""; details.hidden = true; root.hidden = false;
      if (atEnd) scroller.scrollTop = scroller.scrollHeight;
    }
    return {offer, hide, current: () => current};
  }
  const lines = {chat: line("chat"), developer: line("developer")};

  // Moving on hides a page's line: its next message or task, or (on Chats) another chat.
  document.getElementById("composer")?.addEventListener?.("submit", () => lines.chat.hide());
  document.getElementById("dev-form")?.addEventListener?.("submit", () => lines.developer.hide());
  // Enter in the message box and Ctrl+Enter in the task box send without a form submit.
  document.getElementById("composer")?.addEventListener?.("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.target !== document.getElementById("prompt")) return;
    if (document.getElementById("saved-prompt-menu")?.hidden === false) return;  // Enter picks a saved prompt
    if (!document.getElementById("send")?.disabled && String(event.target.value || "").trim()) lines.chat.hide();
  }, true);
  document.getElementById("dev-form")?.addEventListener?.("keydown", event => {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) || event.target !== document.getElementById("dev-task")) return;
    if (!document.getElementById("dev-run")?.disabled && String(event.target.value || "").trim()) lines.developer.hide();
  }, true);
  const title = document.getElementById("chat-title");
  if (title && window.MutationObserver) {
    new window.MutationObserver(() => {
      const shown = lines.chat.current();
      if (shown && window.app?.currentChat?.()?.id !== shown.chatId) lines.chat.hide();
    }).observe(title, {childList: true, characterData: true, subtree: true});
  }
  function chat(chatId, index) {
    if (!chatId || !Number.isInteger(index) || index < 0) return;
    lines.chat.offer({kind:"chat", chatId, index});
  }
  function developer(chatId, taskId) {
    if (!chatId || !taskId) return;
    lines.developer.offer({kind:"developer", chatId, taskId});
  }
  function clear() { lines.chat.hide(); lines.developer.hide(); }
  return {chat, developer, clear};
})();
