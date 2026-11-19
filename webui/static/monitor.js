// @ts-check
/** Standalone, compute-only monitor tab. Never imports the full workspace API. */
import { createComputeView } from "./compute-view.js";

const root = /** @type {HTMLElement} */ (document.getElementById("monitor-root"));
const errorBox = /** @type {HTMLElement} */ (document.getElementById("monitor-error"));
const SESSION_KEY = "aiassist.monitor.session";
let token = "";
let busy = false;
let report = null;

function showError(message) {
  errorBox.hidden = false;
  errorBox.textContent = message;
}

async function exchangeCode(code) {
  const response = await fetch("/api/monitor-session", {
    method: "POST", credentials: "omit", cache: "no-store",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({code}),
  });
  if (!response.ok) throw new Error("Monitor link expired. Reopen it from the AI Assist Compute view.");
  const data = await response.json();
  if (!data || typeof data.token !== "string") throw new Error("Invalid monitor session.");
  return data.token;
}

const state = {view: "compute", compute: null};
const view = createComputeView({
  loadCompute: () => refresh(),
  toggleFullscreen: async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      showError("Fullscreen was blocked by your browser. Use its fullscreen shortcut.");
    }
  },
});
root.appendChild(view.element);

function render(data) {
  const before = {...state};
  state.compute = data;
  view.render(state, before);
}

async function refresh() {
  if (!token || busy) return;
  busy = true;
  try {
    const response = await fetch("/api/compute", {
      headers: {Authorization: "Bearer " + token},
      credentials: "omit", cache: "no-store",
    });
    if (response.status === 401) {
      token = "";
      try { sessionStorage.removeItem(SESSION_KEY); } catch { /* private mode */ }
      throw new Error("Monitor session expired. Reopen from the main AI Assist tab.");
    }
    if (!response.ok) throw new Error("Local hardware metrics are unavailable.");
    report = await response.json();
    render(report);
    errorBox.hidden = true;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (report === null) render({error: message});
    showError(message);
  } finally {
    busy = false;
  }
}

async function start() {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  // The short-lived one-use code must not remain in browser history.
  if (code) history.replaceState(null, "", "/monitor.html");
  try {
    token = code ? await exchangeCode(code) : (sessionStorage.getItem(SESSION_KEY) || "");
    if (!token) throw new Error("Open this monitor using the Compute view in AI Assist.");
    try { sessionStorage.setItem(SESSION_KEY, token); } catch { /* memory-only session */ }
    render({loading: true});
    await refresh();
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  }
}

start();
window.setInterval(() => {
  if (!document.hidden && token) refresh();
}, 5000);
