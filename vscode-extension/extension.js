const vscode = require("vscode");
const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

class AgentViewProvider {
  constructor(context) {
    this.context = context;
    this.view = undefined;
    this.process = undefined;
    this.buffer = "";
    this.models = [];
    this.placements = {};
    this.chats = context.workspaceState.get("aiAssist.chats", []);
    this.activeChatId = context.workspaceState.get("aiAssist.activeChatId", "");
    this.pendingApprovals = new Map();
    this.running = false;
    this.status = "Choose a local model to start chatting.";
    this.statusError = false;
    this.model = vscode.workspace.getConfiguration("aiAssist").get("model", "");
    this.repository = this.getRepository();
  }

  getRepository() {
    const configured = vscode.workspace.getConfiguration("aiAssist").get("repositoryPath", "..");
    return path.resolve(this.context.extensionPath, configured);
  }

  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.file(path.join(this.context.extensionPath, "media"))]
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((message) => {
      Promise.resolve(this.handleMessage(message)).catch((error) => this.setStatus(error.message, true));
    }, undefined, this.context.subscriptions);
    this.postState();
    this.ensureBridge();
  }

  html(webview) {
    const nonce = Array.from({ length: 32 }, () => "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789".charAt(Math.floor(Math.random() * 62))).join("");
    const script = webview.asWebviewUri(vscode.Uri.file(path.join(this.context.extensionPath, "media", "chat.js")));
    const style = webview.asWebviewUri(vscode.Uri.file(path.join(this.context.extensionPath, "media", "chat.css")));
    return fs.readFileSync(path.join(this.context.extensionPath, "media", "chat.html"), "utf8")
      .replaceAll("{{cspSource}}", webview.cspSource)
      .replaceAll("{{nonce}}", nonce)
      .replaceAll("{{scriptUri}}", script.toString())
      .replaceAll("{{styleUri}}", style.toString());
  }

  post(message) {
    if (this.view) this.view.webview.postMessage(message);
  }

  currentChat() {
    return this.chats.find((chat) => chat.id === this.activeChatId);
  }

  async saveState() {
    const active = this.currentChat();
    if (active) this.chats = [...this.chats.filter((chat) => chat.id !== active.id), active];
    this.chats = this.chats.slice(-20);
    await this.context.workspaceState.update("aiAssist.chats", this.chats);
    await this.context.workspaceState.update("aiAssist.activeChatId", this.activeChatId);
  }

  postState() {
    const chat = this.currentChat();
    this.post({
      type: "state",
      chats: this.chats.map(({ id, title, updated }) => ({ id, title, updated })),
      activeChatId: this.activeChatId,
      messages: chat ? chat.messages : [],
      activity: chat ? chat.activity : [],
      models: this.models,
      placements: this.placements,
      model: this.model,
      running: this.running,
      status: this.status,
      error: this.statusError,
      approvals: [...this.pendingApprovals.values()]
    });
  }

  setStatus(text, error = false) {
    this.status = String(text || "");
    this.statusError = error;
    this.post({ type: "status", text: this.status, error });
  }

  rejectTurn(text) {
    this.setStatus(text, true);
    this.postState();
  }

  ensureBridge() {
    if (this.process && !this.process.killed) return true;
    const bridge = path.join(this.context.extensionPath, "agent_bridge.py");
    if (!fs.existsSync(path.join(this.repository, "coding_agent.py")) || !fs.existsSync(bridge)) {
      this.setStatus(`AI_Assist source not found at ${this.repository}. Set aiAssist.repositoryPath in Settings.`, true);
      return false;
    }
    const python = vscode.workspace.getConfiguration("aiAssist").get("pythonPath", "python");
    const env = {
      ...process.env,
      PYTHONUTF8: "1",
      AI_ASSIST_REPOSITORY: this.repository,
      PYTHONPATH: [this.repository, process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter)
    };
    try {
      const child = childProcess.spawn(python, ["-u", bridge], {
        cwd: this.repository,
        env,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"]
      });
      this.process = child;
      this.buffer = "";
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => this.readOutput(chunk));
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (text) => {
        if (text.trim()) this.setStatus(text.trim().slice(-2000), true);
      });
      child.on("error", (error) => this.setStatus(`Could not start Python: ${error.message}`, true));
      child.on("exit", (code) => {
        if (this.process === child) this.process = undefined;
        this.running = false;
        if (code !== 0) this.setStatus(`AI Assist agent stopped (${code === null ? "terminated" : `exit ${code}`}).`, true);
        this.postState();
      });
      this.setStatus("Connecting to local Ollama…");
      this.refreshModels();
      return true;
    } catch (error) {
      this.setStatus(`Could not start the AI Assist agent: ${error.message}`, true);
      return false;
    }
  }

  sendBridge(request) {
    if (!this.process || this.process.killed || !this.process.stdin.writable) {
      this.setStatus("The AI Assist agent is not running. Reopen the chat to reconnect.", true);
      return false;
    }
    this.process.stdin.write(`${JSON.stringify(request)}\n`);
    return true;
  }

  readOutput(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 32 * 1024 * 1024) {
      this.setStatus("The local agent returned an oversized response.", true);
      this.process?.kill();
      return;
    }
    let newline;
    while ((newline = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      try {
        this.handleBridgeMessage(JSON.parse(line));
      } catch (error) {
        this.setStatus(`Invalid response from the AI Assist agent: ${error.message}`, true);
      }
    }
  }

  handleBridgeMessage(message) {
    if (message.type === "models") {
      this.models = Array.isArray(message.models) ? message.models : [];
      this.placements = message.placements && typeof message.placements === "object" ? message.placements : {};
      if (!this.models.includes(this.model)) {
        this.model = this.models[0] || "";
        if (this.model) vscode.workspace.getConfiguration("aiAssist").update("model", this.model, vscode.ConfigurationTarget.Workspace);
      }
      this.setStatus(this.models.length ? "Local Ollama ready. Agent actions require your approval." : "No local Ollama models found.");
      this.postState();
    } else if (message.type === "approval") {
      const request = { id: message.id, action: message.action };
      this.pendingApprovals.set(message.id, request);
      this.post({ type: "approval", ...request });
    } else if (message.type === "event") {
      const chat = this.currentChat();
      if (chat) {
        chat.activity.push(message.event);
        chat.activity = chat.activity.slice(-100);
      }
      this.post({ type: "agentEvent", event: message.event });
    } else if (message.type === "done") {
      this.running = false;
      this.pendingApprovals.clear();
      const chat = this.currentChat();
      if (chat && message.result) {
        chat.agentHistory = message.result.messages || chat.agentHistory;
        const answer = message.result.assistant_text || "The agent finished without a response.";
        chat.messages.push({ role: "assistant", content: answer });
        chat.updated = Date.now();
        this.setStatus("Ready for your next message.");
      } else if (message.error) {
        this.setStatus(message.error, true);
      } else if (message.stopped) {
        this.setStatus("Agent stopped.");
      }
      this.saveState().then(() => this.postState());
    } else if (message.type === "error") {
      this.setStatus(message.message || "The local agent reported an error.", true);
    }
  }

  async handleMessage(message) {
    if (!message || typeof message.type !== "string") return;
    if (message.type === "ready") {
      this.postState();
      this.refreshModels();
    } else if (message.type === "refreshModels") {
      this.refreshModels();
    } else if (message.type === "selectModel") {
      if (!this.models.includes(message.model)) return;
      if (this.model !== message.model && this.currentChat()?.messages.length) await this.createChat();
      this.model = message.model;
      await vscode.workspace.getConfiguration("aiAssist").update("model", this.model, vscode.ConfigurationTarget.Workspace);
      this.postState();
    } else if (message.type === "newChat") {
      if (this.running) return this.setStatus("Stop the current turn before starting another chat.", true);
      await this.createChat();
    } else if (message.type === "selectChat") {
      if (this.running) return this.setStatus("Stop the current turn before switching chats.", true);
      if (this.chats.some((chat) => chat.id === message.id)) {
        this.activeChatId = message.id;
        await this.saveState();
        this.postState();
      }
    } else if (message.type === "submit") {
      await this.startTurn(message.text);
    } else if (message.type === "stop") {
      this.sendBridge({ type: "cancel" });
      this.setStatus("Stopping the agent…");
    } else if (message.type === "approve") {
      await this.resolveApproval(message.id, message.approved === true);
    }
  }

  async createChat() {
    const chat = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      title: "New conversation",
      updated: Date.now(),
      messages: [],
      agentHistory: [],
      activity: []
    };
    this.chats.push(chat);
    this.activeChatId = chat.id;
    this.setStatus("New local agent conversation.");
    await this.saveState();
    this.postState();
  }

  async startTurn(text) {
    const prompt = typeof text === "string" ? text.trim() : "";
    if (!prompt || this.running) return;
    if (!this.model || !this.models.includes(this.model)) {
      this.rejectTurn("Select an installed local Ollama model first.");
      return;
    }
    const folders = vscode.workspace.workspaceFolders || [];
    if (!folders.length) {
      this.rejectTurn("Open a local project folder in VS Code before chatting with the coding agent.");
      return;
    }
    if (folders.some((folder) => folder.uri.scheme !== "file")) {
      this.rejectTurn("The local agent needs a project folder stored on this computer.");
      return;
    }
    if (!this.currentChat()) await this.createChat();
    const chat = this.currentChat();
    let folder;
    if (chat.project) {
      folder = folders.find((item) => path.resolve(item.uri.fsPath) === path.resolve(chat.project));
      if (!folder) {
        this.rejectTurn(`Reopen ${chat.project} in VS Code to continue this conversation.`);
        return;
      }
    } else if (folders.length > 1) {
      const selected = await vscode.window.showQuickPick(folders.map((item) => ({ label: item.name, description: item.uri.fsPath, folder: item })), {
        placeHolder: "Choose the project folder the agent may inspect"
      });
      if (!selected) {
        this.postState();
        return;
      }
      folder = selected.folder;
    } else {
      folder = folders[0];
    }
    chat.project = folder.uri.fsPath;
    chat.messages.push({ role: "user", content: prompt });
    if (chat.title === "New conversation") chat.title = prompt.slice(0, 56);
    chat.updated = Date.now();
    this.running = true;
    this.statusError = false;
    this.status = "AI Assist is working in the selected project. Review every proposed write and command.";
    await this.saveState();
    this.postState();
    const host = vscode.workspace.getConfiguration("aiAssist").get("ollamaHost", "http://localhost:11434");
    const contextLength = vscode.workspace.getConfiguration("aiAssist").get("contextLength", 4096);
    const messages = [...chat.agentHistory, { role: "user", content: prompt }];
    if (!this.ensureBridge() || !this.sendBridge({
      type: "run",
      project: folder.uri.fsPath,
      host,
      model: this.model,
      contextLength,
      messages
    })) {
      this.running = false;
      this.postState();
    }
  }

  refreshModels() {
    if (!this.ensureBridge()) return;
    const host = vscode.workspace.getConfiguration("aiAssist").get("ollamaHost", "http://localhost:11434");
    this.sendBridge({ type: "models", host });
  }

  async resolveApproval(id, approved) {
    const pending = this.pendingApprovals.get(id);
    if (!pending) return;
    const action = pending.action || {};
    if (approved && action.kind === "run_command") {
      const detail = `${action.command}\n\nWorking directory: ${action.cwd}\n\nThis command runs with your account permissions. It is not sandboxed, and non-Python programs it starts are outside AI Assist's network gate.`;
      const first = await vscode.window.showWarningMessage("Review this agent command carefully.", {
        modal: true,
        detail
      }, "Continue to second check", "Reject");
      if (first !== "Continue to second check") approved = false;
      if (approved) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const second = await vscode.window.showWarningMessage("Second check: run this command?", {
          modal: true,
          detail
        }, "Run command", "Reject");
        approved = second === "Run command";
      }
    }
    this.pendingApprovals.delete(id);
    this.sendBridge({ type: "approve", id, approved });
    this.post({ type: "approvalResolved", id, approved });
  }

  dispose() {
    const child = this.process;
    if (!child) return Promise.resolve();
    this.sendBridge({ type: "shutdown" });
    child.stdin.end();
    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        child.kill();
        resolve();
      }, 3000);
      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }
}

function activate(context) {
  const provider = new AgentViewProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("aiAssist.chat", provider, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand("aiAssist.openChat", () => vscode.commands.executeCommand("workbench.view.extension.aiAssist")),
    vscode.commands.registerCommand("aiAssist.newChat", () => provider.handleMessage({ type: "newChat" })),
    vscode.commands.registerCommand("aiAssist.refreshModels", () => provider.refreshModels()),
    { dispose: () => provider.dispose() }
  );
}

function deactivate() {
  return undefined;
}

module.exports = { activate, deactivate };
