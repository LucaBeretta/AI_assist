const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");

let registeredProvider;
const vscode = {
  workspace: {
    workspaceFolders: [],
    getConfiguration() {
      return {
        get(_name, fallback) {
          return fallback;
        },
        update() {
          return Promise.resolve();
        }
      };
    }
  },
  window: {
    registerWebviewViewProvider(_id, provider) {
      registeredProvider = provider;
      return { dispose() {} };
    }
  },
  commands: {
    registerCommand() {
      return { dispose() {} };
    },
    executeCommand() {
      return Promise.resolve();
    }
  }
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return vscode;
  return originalLoad.call(this, request, parent, isMain);
};
const extension = require("./extension");
Module._load = originalLoad;

test("a rejected turn posts a non-running state to the webview", async () => {
  const messages = [];
  const context = {
    extensionPath: __dirname,
    subscriptions: [],
    workspaceState: {
      get(_key, fallback) {
        return fallback;
      },
      update() {
        return Promise.resolve();
      }
    }
  };
  extension.activate(context);
  registeredProvider.view = { webview: { postMessage: (message) => messages.push(message) } };
  registeredProvider.models = ["qwen3:8b"];
  registeredProvider.model = "qwen3:8b";
  vscode.workspace.workspaceFolders = [];

  await registeredProvider.startTurn("Show me this project");

  const state = messages.filter((message) => message.type === "state").at(-1);
  assert.equal(state.running, false);
  assert.equal(messages.some((message) => message.type === "status" && message.error), true);
});
