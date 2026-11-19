# ADR 0001: Python core, loopback API, web interface

Status: **accepted** for the first modernization milestone (2026-11-19). Tk stays the default interface until the web workspace reaches parity.

## Context

AI Assist is an offline-first local AI workstation. These requirements constrain any interface change:

1. **Offline and private by default.** Nothing goes online without two human confirmations (`network_gate.py`). Protections must be mandatory and fail closed.
2. **No package installation.** The app runs on the Python standard library. When no suitable Python exists, the launchers fetch a pinned, checksum-verified private Python. A dependency tree would undermine both properties.
3. **Preserve what works:** the Ollama and Colibri engines, streaming and Stop, the reviewed developer agent, shared memory, backups, the firewall lock, and Vercel deployment.
4. **Long-term reach:** Windows, macOS and Linux desktops, then Android and iOS. These differ in what they can do (section 5).
5. **Incremental and testable.** The Tk app has 397 passing tests; a rewrite would discard them.

The audit (`docs/architecture-audit.md`) found that the business logic is mostly UI-free already (`coding_agent`, `chat_store`, `shared_memory`, `network_gate`, `colibri_backend`). It is held together by a 7-class Tk mixin chain that shares 183 attributes, and request routing and prompt assembly live inside Tk handlers.

## Options considered

| | A. Keep Tk only | B. PySide6 / Qt | C. Python core + loopback API + web UI | D. Electron | E. Rewrite (Rust/TS) |
| --- | --- | --- | --- | --- | --- |
| New runtime dependencies | none | PySide6, about 150 MB with QtWebEngine ([pythonguis](https://www.pythonguis.com/faq/packaged-installer-file-sizes/)) | **none** (stdlib server; the system browser now, the system WebView later) | Chromium and Node, about 300 MB ([Better Stack](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun.md)) | new toolchain |
| Fits "no pip packages" and the private-runtime bootstrap | yes | no | **yes** | no | no |
| Modern UI ceiling (Markdown, diffs, responsive, accessible) | low: Tk has no rich text layout or accessibility API | high | **high** | high | high |
| Reuse for browser and mobile companions | none | none | **same API and page** | partial | yes |
| Fit with the internet gate | in-process | in-process | API in-process; **the browser is a separate process** (mitigated by a strict CSP, section 4) | separate Node process outside the gate | gate must be rebuilt |
| Migration cost | none | rewrite every widget | **incremental**; Tk keeps running | rewrite | very high |
| Packaging | source ZIP + private Python | PyInstaller with large Qt bundles | source ZIP now; optional [Tauri 2](https://v2.tauri.app/develop/sidecar/) shell with a Python sidecar later | electron-builder | new |

What comparable projects do:

- **Open WebUI**: FastAPI backend with a SvelteKit frontend served as static files ([docs](https://docs.openwebui.com/getting-started/advanced-topics/development.md)).
- **AnythingLLM**: Express server and a React frontend, inside Electron for desktop ([repo](https://github.com/mintplex-labs/anything-llm)).
- **Jan**: React frontend in Tauri, with an OpenAI-compatible local server on `127.0.0.1:1337` ([docs](https://jan.ai/docs/desktop/api-server)).
- **Aider**: stays a Python CLI, with git as its safety net ([docs](https://aider.chat/docs/repomap.html)).

The common pattern is a local HTTP API with a web frontend; projects differ only in whether a desktop shell wraps it. Option C follows that pattern without adding dependencies.

## Decision

**Option C, incrementally:**

1. **Python stays the application core.** Business rules move out of Tk into `app_core/` (no UI imports, which a test enforces). The Tk window keeps its long-standing function names as thin facades over the core, so all existing tests and patch points still apply.
2. **A loopback-only HTTP API** (`webui/server.py`) built on `http.server`, with no framework dependency. Its security rules are in `docs/threat-model.md`. It exposes conversations and read-only views. It has **no** command, file-write or internet-approval endpoints; those stay in the desktop window until they are designed for the web. *Update (backlog P1-1):* the developer agent now runs in the web workspace, with every edit and command paused for the person's answer and the approval rules enforced by `app_core/approvals.py` on the server (`docs/threat-model.md` T6, T13, T14). There is still no direct command or file-write endpoint, and internet approvals stay in the desktop window.
3. **The web interface** (`webui/static/`) is plain ES modules with JSDoc types and `// @ts-check`, with **no build step**. TypeScript was the stated preference, but compiling `.ts` files would make every source checkout depend on Node and an npm toolchain. The annotations let the TypeScript compiler check the page without a build (`tsc --allowJs --checkJs --noEmit`). **That check has not been run yet:** TypeScript is not installed here, and installing it is backlog item P1-6. Node is optional and used only for the page's unit tests. Revisit this if the frontend outgrows hand-written modules; the annotations make a later move to `.ts` mechanical.
4. **Rust is not used in this milestone.** It earns its place only as the shell for a packaged desktop app. Tauri 2 gives a small bundle using the system WebView, a sidecar for the Python core, and a capability-based permission model. That is backlog item P2-1 (`docs/modernization-backlog.md`), to be revisited once the web workspace reaches parity.
5. **Tk remains the default** (`AIassist.py`). The web workspace is a preview (`web_workspace.py`). Only one may use a data folder at a time (`app_core/instance_lock.py`).

## Consequences

- Two interfaces exist during the migration. This is mitigated by putting the rules in `app_core` once, and by the single-instance lock, which prevents the two from overwriting each other's chats.
- A new attack surface: a local HTTP server. This is mitigated by loopback-only binding (checked twice), exact `Host` and `Origin` checks, single-use launch codes, bearer tokens with no cookies, a CSP of `default-src 'none'`, and a route allowlist pinned by a test.
- The browser renders the page outside the internet gate. The page's CSP allows connections to its own origin only, and model output is never parsed as HTML: links are shown as text. A malicious browser extension is out of scope (`docs/threat-model.md`).
- Web mode has no approval window, so the gate blocks all internet access there. That is fail-closed, as required, and it is why downloads and update checks stay in the desktop app.

## Mobile (summary; details in `docs/platform-support.md`)

The Python core and its desktop sidecar do not run on iOS, and should not on Android. Shell execution, the firewall lock, and the developer agent's unrestricted file access are desktop-only by design. Mobile has two separate tracks:

- A **companion client** for this API, over the LAN, after explicit pairing (TLS-PSK, short codes compared on both screens, per-device scopes). This needs the LAN design from `project_doomsday_direction`, and it is **not** enabled by this milestone.
- **On-device inference**: a separate native app built on llama.cpp, LiteRT-LM (Android, with iOS support), MLC LLM, or Apple's Foundation Models. Only small models (up to a few billion parameters) are practical.
