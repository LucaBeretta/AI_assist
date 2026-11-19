const vscode = acquireVsCodeApi();
const byId = (id) => document.getElementById(id);
const chatPicker = byId("chat-picker");
const modelPicker = byId("model-picker");
const messageList = byId("messages");
const approvalList = byId("approval-list");
const promptBox = byId("prompt");
let state = { chats: [], messages: [], activity: [], approvals: [], models: [], running: false, model: "" };
let liveActivity = [];

function send(type, values = {}) {
  vscode.postMessage({ type, ...values });
}

function appendInlineText(parent, value) {
  const pieces = value.split(/(`[^`]+`)/g);
  for (const piece of pieces) {
    if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
      const code = document.createElement("code");
      code.textContent = piece.slice(1, -1);
      parent.append(code);
    } else {
      parent.append(document.createTextNode(piece));
    }
  }
}

function renderContent(parent, value) {
  let inCode = false;
  let codeLines = [];
  let paragraphLines = [];
  const flushParagraph = () => {
    if (!paragraphLines.length) return;
    const paragraph = document.createElement("p");
    appendInlineText(paragraph, paragraphLines.join("\n"));
    parent.append(paragraph);
    paragraphLines = [];
  };
  const flushCode = () => {
    const code = document.createElement("pre");
    code.textContent = codeLines.join("\n");
    parent.append(code);
    codeLines = [];
  };
  for (const line of String(value || "").split(/\r?\n/)) {
    if (line.trimStart().startsWith("```")) {
      if (inCode) flushCode();
      else flushParagraph();
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      codeLines.push(line);
    } else if (!line.trim()) {
      flushParagraph();
    } else {
      paragraphLines.push(line);
    }
  }
  if (inCode) flushCode();
  else flushParagraph();
}

function renderMessages() {
  messageList.replaceChildren();
  for (const message of state.messages || []) {
    const article = document.createElement("article");
    article.className = `message ${message.role === "user" ? "user" : "assistant"}`;
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.textContent = message.role === "user" ? "Y" : "A";
    const name = document.createElement("span");
    name.textContent = message.role === "user" ? "YOU" : "AI ASSIST";
    meta.append(avatar, name);
    const body = document.createElement("div");
    body.className = "message-body";
    renderContent(body, message.content);
    article.append(meta, body);
    messageList.append(article);
  }
  const welcome = byId("welcome");
  welcome.hidden = Boolean((state.messages || []).length);
  renderActivity();
  renderApprovals();
  byId("conversation").scrollTop = byId("conversation").scrollHeight;
}

function renderActivity() {
  const items = [...(state.activity || []), ...liveActivity].slice(-100);
  const container = byId("activity");
  container.replaceChildren();
  container.hidden = items.length === 0;
  if (!items.length) return;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `Agent activity · ${items.length} steps`;
  const list = document.createElement("div");
  list.className = "activity-list";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "activity-item";
    const marker = document.createElement("i");
    marker.textContent = item.type === "tool_start" ? "↳" : item.type === "changed" ? "✓" : "·";
    const text = document.createElement("span");
    text.textContent = item.text || item.type || "Agent update";
    row.append(marker, text);
    list.append(row);
  }
  details.append(summary, list);
  container.append(details);
}

function renderApprovals() {
  approvalList.replaceChildren();
  for (const approval of state.approvals || []) {
    const action = approval.action || {};
    const command = action.kind === "run_command";
    const card = document.createElement("section");
    card.className = "approval-card";
    const label = document.createElement("p");
    label.className = "approval-label";
    label.textContent = command ? "Command approval required" : "File change approval required";
    card.append(label);
    const pathText = document.createElement("p");
    pathText.className = "approval-path";
    pathText.textContent = command ? `Working directory · ${action.cwd || "project"}` : `File · ${action.destination || action.path || "project file"}`;
    card.append(pathText);
    if (command) {
      const note = document.createElement("p");
      note.className = "approval-note";
      note.textContent = "Runs with your account permissions; it is not sandboxed. VS Code will ask for two confirmations.";
      card.append(note);
    }
    const body = document.createElement("pre");
    body.textContent = command ? action.command || "" : action.diff || action.content || "";
    card.append(body);
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    const reject = document.createElement("button");
    reject.type = "button";
    reject.textContent = "Reject";
    reject.addEventListener("click", () => send("approve", { id: approval.id, approved: false }));
    const accept = document.createElement("button");
    accept.type = "button";
    accept.className = "approve";
    accept.textContent = command ? "Review command" : "Apply change";
    accept.addEventListener("click", () => send("approve", { id: approval.id, approved: true }));
    actions.append(reject, accept);
    card.append(actions);
    approvalList.append(card);
  }
}

function renderChats() {
  chatPicker.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Choose a conversation";
  chatPicker.append(empty);
  for (const chat of [...(state.chats || [])].reverse()) {
    const option = document.createElement("option");
    option.value = chat.id;
    option.textContent = chat.title || "New conversation";
    chatPicker.append(option);
  }
  chatPicker.value = state.activeChatId || "";
}

function renderModels() {
  modelPicker.replaceChildren();
  if (!(state.models || []).length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No local models found";
    modelPicker.append(option);
  } else {
    for (const model of state.models) {
      const option = document.createElement("option");
      option.value = model;
      const placement = state.placements && state.placements[model];
      option.textContent = placement ? `${model} · ${placement.toUpperCase()}` : model;
      modelPicker.append(option);
    }
  }
  modelPicker.value = state.model || "";
  modelPicker.disabled = state.running || !(state.models || []).length;
}

function renderControls() {
  const status = byId("status");
  byId("status-text").textContent = state.status || "Ready.";
  status.classList.toggle("error", Boolean(state.error));
  status.classList.toggle("busy", Boolean(state.running));
  byId("send").hidden = Boolean(state.running);
  byId("stop").hidden = !state.running;
  byId("send").disabled = !(state.model && (state.models || []).includes(state.model));
  chatPicker.disabled = Boolean(state.running);
  byId("new-chat").disabled = Boolean(state.running);
}

function setState(next) {
  state = { ...state, ...next };
  if (next.activity) liveActivity = [];
  renderChats();
  renderModels();
  renderControls();
  renderMessages();
}

function submit() {
  const text = promptBox.value.trim();
  if (!text || state.running) return;
  send("submit", { text });
  promptBox.value = "";
  promptBox.style.height = "auto";
  state.messages = [...(state.messages || []), { role: "user", content: text }];
  state.running = true;
  renderControls();
  renderMessages();
}

byId("new-chat").addEventListener("click", () => send("newChat"));
byId("refresh-models").addEventListener("click", () => send("refreshModels"));
byId("send").addEventListener("click", submit);
byId("stop").addEventListener("click", () => send("stop"));
chatPicker.addEventListener("change", () => send("selectChat", { id: chatPicker.value }));
modelPicker.addEventListener("change", () => send("selectModel", { model: modelPicker.value }));
promptBox.addEventListener("input", () => {
  promptBox.style.height = "auto";
  promptBox.style.height = `${Math.min(promptBox.scrollHeight, 150)}px`;
});
promptBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});
for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => {
    promptBox.value = button.dataset.prompt || "";
    promptBox.focus();
    submit();
  });
}

window.addEventListener("message", (event) => {
  const message = event.data;
  if (message.type === "state") {
    setState(message);
  } else if (message.type === "status") {
    state.status = message.text;
    state.error = message.error;
    renderControls();
  } else if (message.type === "approval") {
    state.approvals = [...(state.approvals || []), { id: message.id, action: message.action }];
    renderApprovals();
    byId("conversation").scrollTop = byId("conversation").scrollHeight;
  } else if (message.type === "approvalResolved") {
    state.approvals = (state.approvals || []).filter((approval) => approval.id !== message.id);
    renderApprovals();
  } else if (message.type === "agentEvent") {
    liveActivity.push(message.event);
    liveActivity = liveActivity.slice(-100);
    renderActivity();
    byId("conversation").scrollTop = byId("conversation").scrollHeight;
  }
});

renderChats();
renderModels();
renderControls();
send("ready");
