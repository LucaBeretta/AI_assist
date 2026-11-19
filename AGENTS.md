# Repository Guidelines

## Project Structure & Module Organization

AI Assist is a Python desktop app with a local browser workspace. `AIassist.py` starts the Tkinter application; shared, UI-independent services live in `app_core/`, while `webui/` contains a loopback HTTP server, static browser assets, and JavaScript tests. Keep shared business rules in `app_core/`: it must not import Tkinter or another UI toolkit. Desktop UI, persistence, agent tools, and network controls are primarily in root-level modules. `child_gate/` supplies the network gate to Python child processes; `tools/web_e2e.py` exercises the browser workspace.

## Build, Test, and Development Commands

No separate build or package-install step is defined; the app uses Python's standard library.

- `python AIassist.py` — run the desktop app (requires Tkinter and Ollama).
- `python web_workspace.py` — run the local web workspace.
- `python -m unittest discover -v` — run the Python suite; target one module with `python -m unittest test_app_core -v`.
- `python -m compileall -q .` — compile-check Python files.
- `node --test webui/tests/markdown.test.mjs webui/tests/format.test.mjs` — run the browser JavaScript unit tests; Node is optional.
- `python tools/web_e2e.py` — optional browser end-to-end test; requires Edge or Chrome and a running Ollama model (`gemma3:270m`).

## Coding Style & Naming Conventions

No repository linter, formatter, or type-checker configuration is present. Follow surrounding code style. Python tests use `unittest` and `test_*.py` names; browser tests use Node's built-in `node:test` with `.test.mjs` files. Preserve the UI-free boundary of `app_core/` when changing shared services.

## Testing Guidelines

The Python suite uses simulated Ollama responses and temporary directories to avoid changing real chats or projects. Run the full suite after cross-module changes; use the corresponding `test_*.py` module for a focused check. Browser UI changes should also run the Node tests, and browser behavior can be checked with the optional end-to-end script.

## Commit & Pull Request Guidelines

Recent commit subjects use concise, imperative, sentence-case descriptions (for example, “Add…” or “Keep…”); no scope prefix is evident. Follow that pattern for commit subjects. No pull-request template was found.
