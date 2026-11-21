# Repository Guidelines

## Project Structure & Module Organization

AI Assist is a Python desktop app. `AIassist.py` installs the mandatory internet gate before loading the pywebview window. `webui/app.py` exposes a plain-JSON native API and `webui/bridge.py` batches worker events to JavaScript. The page loads local files, with no application HTTP server. Tk and the browser-preview API have been removed. Shared services live in `app_core/`; keep them independent of UI toolkits. Reuse its Ollama, streaming, privacy, settings, paths, provider and instance-lock modules instead of duplicating engine logic. `child_gate/` supplies the network gate to Python children. Explicit LAN pairing uses a separate child service, never the desktop API.

## Build, Test, and Development Commands

The frontend is vanilla JavaScript, offline and without a build step. Install `requirements.txt`; the launchers also support a checksum-verified private Python setup.

- `python AIassist.py` — run the pywebview desktop app (`--web` remains an alias).
- On Windows, run the full suite with `python run_windows_tests.py --timeout 420`: it uses a separate invisible desktop. Do not open test windows on the user's desktop.
- `python -m unittest test_app_core -v` — target a known headless module.
- `python -m compileall -q .` — compile-check Python files.
- `ruff check .` — check for unused or undefined names and syntax errors (rules in `ruff.toml`; CI runs it on every push).
- `python -m unittest test_webui_static` — frontend DOM/API tests through Node, without a window.
- `node --test vscode-extension/extension.test.js` — VS Code extension tests.

## Coding Style & Naming Conventions

`ruff.toml` checks for unused or undefined names and syntax errors only; there is no formatter or type checker. Follow surrounding code style. Python tests use `unittest` and `test_*.py` names; frontend tests use a small Node DOM/API harness and the extension uses `node:test`. Preserve the UI-free boundary of `app_core/` when changing shared services.

## Testing Guidelines

The suite uses simulated responses and temporary directories. Run it after cross-module changes, and run `test_webui_static` for frontend changes. `test_webui_imports` enforces Tk-free imports. Inject evaluation functions in tests; workers never retain a native window. Keep security modules and VS Code extension behavior intact. Never start LAN listeners at startup: sharing starts off every run. Hourly check-ins default off; microphone levels require explicit opt-in, which can be saved. Native checks require the separate invisible desktop described above.

## Commit & Pull Request Guidelines

Recent commit subjects use concise, imperative, sentence-case descriptions (for example, “Add…” or “Keep…”); no scope prefix is evident. Follow that pattern for commit subjects. No pull-request template was found.
