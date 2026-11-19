# AI Assist

A local Ollama and Colibri workspace with saved chats, a developer agent, code previews, model capabilities, and hardware-aware downloads. The desktop interface uses HTML, CSS and JavaScript inside pywebview; the model engine remains Python.

## Requirements

- Python 3 and the packages in `requirements.txt`, or the launcher's private Python setup.
- Ollama with a downloaded model, or a configured Colibri installation/server.
- On Windows, pywebview uses WebView2. On macOS, it uses WKWebView. **macOS has not yet been tested on real hardware.**

The interface loads local files with no CDN, build step or application HTTP server. Model inference uses the configured server. Downloads, update checks, remote servers and an explicitly requested Vercel deployment require network access.

## Run

Double-click **Start AI Assist.cmd** on Windows or **Start AI Assist.command** on macOS. Each launcher checks for pywebview and prefers an existing private runtime. If no suitable Python is found, it offers a checksum-verified private Python download into `runtime`, then installs `requirements.txt`. It needs no administrator password and does not change other Python installations. Ollama and Colibri are installed separately.

On Windows the app starts detached with `pythonw` when available: closing the launcher does not close the app. Startup errors go to `%LOCALAPPDATA%\AI Assist\last-run.log`. Set `AI_ASSIST_PYTHON` to prefer a particular interpreter.

To run from a terminal:

```sh
python -m pip install -r requirements.txt
python AIassist.py
```

`python AIassist.py --web` remains accepted and opens the same interface. `python gui_runtime.py --probe` checks the dependency without opening a window. On macOS, right-click the launcher and choose **Open** if needed, or run `chmod +x "Start AI Assist.command"`. The macOS launcher and native rendering still need a check on a Mac.

The first-run page shows the computer's hardware and recommended models, offers a model download with progress, and opens the first chat. It can be skipped.

## Chats and developer workspace

- **Chat** streams selectable Markdown and code. The composer selects the engine/model and context. Context usage stays visible; its menu compacts the conversation or starts a new chat with the same environment. Stop keeps the partial reply. Chats support drafts, recovery of interrupted replies, attachments, answer versions and incognito sessions. Incognito chats remain in memory and are excluded from exports.
- **Developer** uses an existing project folder and an Ollama model with native tools. Browse for a folder, pin files and describe a task. Plan first asks for approval before actions; file diffs and shell commands have their own approvals. Activity distinguishes real tool results from model prose. Checkpoints can restore task changes. Colibri is available for chat and optional quality review; the web Developer tool executor currently requires Ollama.
- **Models** shows hardware, installed/loaded Ollama models, recommendations and download progress. Its Colibri panel manages a known local Colibri installation.
- **Capabilities** shows tools, vision, thinking and context metadata reported by the selected model. Unreported support remains unknown.
- **Settings** contains language, Night/Day appearance, idle model unloading, shared memory, saved prompts, update checks, Colibri quality review and chat export.

The chat sidebar supports search, rename and deletion, date groups and projects. Projects share instructions, a folder and a default environment. Right-click a chat for instructions and memory selection. Existing chats keep their titles, messages and tool history; their storage format is unchanged. Attachments and pinned paths remain subject to the configured file limit and project access rules. Images require reported vision support; PDF text extraction uses the offline `pypdf` dependency.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+K | Search chats or run a command |
| Ctrl+N | New chat |
| Ctrl+Shift+N | New incognito chat |
| Esc | Stop the active response or task |
| Up in an empty composer | Edit the last user message |
| Ctrl+/ | Show shortcuts |
| Ctrl+Shift+L | Switch Night/Day |
| Ctrl+plus / Ctrl+minus / Ctrl+0 | Zoom in, out or reset |
| Ctrl+Enter in Developer | Run the task |

Type `/` in the composer to insert a saved prompt. Zoom and language preferences are saved. Code blocks have Copy, Save as and, for HTML, Preview actions.

### Ollama + Colibri quality review

In **Settings → Colibri quality review**, enter the reviewer model and optionally enable the default. The Developer checkbox selects review for an individual task. Configure Colibri first in Models.

Ollama executes the approved task once. Colibri then receives one bounded review containing requirements, worker results, recorded command outcomes and changed-file excerpts. The review has no tools and is labelled advisory: it cannot prove visual correctness or turn model claims into verification evidence.

**Manage local memory handoff** releases only the selected local Ollama worker and manages a known local Colibri server between stages. Remote servers are not stopped. Choose a local Colibri folder or disable handoff for a manually managed server. Conflicting uses of the worker and reviewer are blocked during this operation.

Review is off by default. It adds at most one 768-token review request with a 120-second HTTP deadline. A failed or cancelled review preserves completed worker changes and history; the worker is never automatically retried. Loading and review can add time. Activity records the stages.

### Feedback and verification

**Give feedback** on an answer prepares a follow-up draft with your comments and optional success criteria. Review the draft before sending it. Feedback does not send a request or repeat edits automatically.

Developer commands run with your account's permissions from the chosen project directory; they are not an operating-system sandbox. Review the destination, diff and command before approving. Model file paths remain confined to the project, with secret, binary, dependency and generated paths excluded. Read sizes, context and tool execution are bounded. The saved agent file limit defaults to 64 KB and supports choices up to 1 MB; large text can be read in bounded line ranges.

### Shared memory across chats

Use **Settings → Shared memory** to enable/disable notes, add, edit or forget them. A chat's right-click menu selects its memories. Replies show the memories used for that request. Incognito requests do not read or write shared memory. Changing notes affects future requests and does not remove text already in a conversation.

History defaults to `%LOCALAPPDATA%\AI Assist\chats` on Windows or `~/.aiassist/chats` elsewhere. `AI_ASSIST_DATA_DIR` changes the data directory. Chat JSON files are replaced atomically. Settings → Export saves a backup with chat and project data; incognito sessions are excluded. Previously saved chats and `appearance.settings` preferences remain readable.

## Model downloads

Open **Models** for hardware-based recommendations and installed models. Downloads show progress and can be stopped; a model can then be selected in the chat composer. Loaded models can be unloaded explicitly or after the configured idle period. Recommendations are estimates, not guarantees of speed or GPU placement. The status bar reports model placement, context, VRAM and measured token speed when available.

## Preview HTML

Preview opens a project HTML file or a generated HTML code block in a separate preview window. Main chat output remains sanitized Markdown; executable preview content has no application API bridge. Relative project assets stay with the project file. Close the preview independently or close the app.

## Deploy a web project to Vercel

In **Developer → Publish web project**, choose **Deploy to Vercel**. Enter an existing web project folder, a Vercel project name and optional team/domain. Confirm the upload before deploying to production. This publishes that web project, not the desktop app.

Install Vercel CLI separately (`npm install -g vercel`) and sign in with `vercel login`. Output and the returned URL appear in the dialog. Cancellation stops local work; a deployment already submitted to Vercel may continue, so check its dashboard. Deployment is blocked while an agent changes the same project.

## Configuration

`OLLAMA_HOST` selects the Ollama server, defaulting to `http://localhost:11434`. Select a model and requested context in the composer. Server/model support and available memory determine the actual allocation; Capabilities shows advertised limits separately from measured usage. Larger contexts need more memory.

### Colibri backend

Install Colibri separately using its setup instructions. In **Models → Colibri engine**, Browse for its folder, load the model catalog, install a supported model, set the context, or start/stop its server. These actions reuse Colibri's own launcher. Folder discovery also checks `COLIBRI_DIR` and common local installation locations. Colibri serves one model at a time.

`COLIBRI_URL` overrides the server URL (default `http://127.0.0.1:8000/v1`); `COLI_API_KEY` provides an optional session credential. Existing backend settings remain readable. A remote server can serve chats, but local installation and start/stop controls require a known local folder. The composer groups models by Ollama and Colibri; unavailable choices show a reason.

### App updates

Use **Settings → Check for updates** to compare this copy with Luca's GitHub default branch. It runs in the background without requiring Git. Different files can mean local development changes as well as an update. Downloading source does not overwrite this copy; extract it into a new folder and keep the same data-directory setting.

## Development and tests

```sh
python -m unittest discover
```

Engine and web API tests inject providers and windows; they do not require a display or a real model. On Windows, `python run_windows_tests.py` runs the suite on a separate, invisible desktop when testing native integrations. Link tests may be skipped when Windows does not permit creating symbolic links. Colibri and Vercel tests use local fixtures or fake commands, not real downloads or deployments.

`ollama_engine.py` contains the unchanged Ollama engine. Plain status and handoff logic lives in `status_values.py` and `hybrid_memory.py`. `webui/app.py` exposes JSON methods; workers emit through `webui/bridge.py`. The frontend is vanilla JavaScript with offline assets and English/Italian strings. No worker receives native UI objects.
