// @ts-check
/**
 * The page's only connection: AI Assist's API on this computer.
 *
 * The session token travels in an Authorization header, never in a cookie
 * (other pages on 127.0.0.1 share this site, and would get cookies). It is kept
 * in this tab's sessionStorage so a reload stays signed in; closing the tab
 * ends it, and stopping AI Assist invalidates it.
 */
import { createLineSplitter } from "./ndjson.js";

const TOKEN_KEY = "aiassist.session";

export class ApiError extends Error {
  /** @param {string} message @param {number} status @param {any} [help] what happened and what to do */
  constructor(message, status, help = null) {
    super(message);
    this.status = status;
    this.help = help;
  }
}

function storage() {
  try { return window.sessionStorage; } catch { return null; }
}

export function createApi() {
  let token = storage()?.getItem(TOKEN_KEY) || "";
  /** @type {Set<() => void>} */
  const expired = new Set();

  function headers(json) {
    /** @type {Record<string, string>} */
    const result = { Authorization: `Bearer ${token}` };
    if (json) result["Content-Type"] = "application/json";
    return result;
  }

  /** @param {Response} response */
  async function failure(response) {
    let message = `AI Assist answered ${response.status}.`;
    let help = null;
    try {
      const body = await response.json();
      if (body && typeof body.error === "string") message = body.error;
      if (body && body.help && typeof body.help.title === "string") help = body.help;
    } catch { /* Not JSON: keep the status message. */ }
    if (response.status === 401) {
      token = "";
      storage()?.removeItem(TOKEN_KEY);
      expired.forEach((listener) => listener());
    }
    return new ApiError(message, response.status, help);
  }

  return {
    hasSession: () => Boolean(token),
    /** @param {() => void} listener */
    onExpired(listener) { expired.add(listener); },

    /** Exchange the single-use code from the link's fragment for a session. @param {string} code */
    async signIn(code) {
      const response = await fetch("/api/session", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ launch: code }),
        credentials: "omit", cache: "no-store",
      });
      if (!response.ok) throw await failure(response);
      const body = await response.json();
      token = body.token;
      storage()?.setItem(TOKEN_KEY, token);
    },

    /**
     * @param {string} method
     * @param {string} path
     * @param {any} [body]
     */
    async request(method, path, body) {
      const response = await fetch(path, {
        method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit", cache: "no-store",
      });
      if (!response.ok) throw await failure(response);
      return response.json();
    },

    get(/** @type {string} */ path) { return this.request("GET", path); },

    /**
     * POST, then read newline-delimited JSON events until the stream ends.
     * @param {string} path
     * @param {any} body
     * @param {(event: any) => void} onEvent
     * @param {AbortSignal} [signal]
     */
    async stream(path, body, onEvent, signal) {
      const response = await fetch(path, {
        method: "POST", headers: headers(true), body: JSON.stringify(body), signal, credentials: "omit", cache: "no-store",
      });
      if (!response.ok) throw await failure(response);
      if (!response.body) throw new ApiError("This browser cannot read streamed answers.", 0);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const splitter = createLineSplitter(onEvent);
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        splitter.push(decoder.decode(value, { stream: true }));
      }
      splitter.push(decoder.decode());
      splitter.end();
    },
  };
}
