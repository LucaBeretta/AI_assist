# AI Assist

A local Ollama and Colibri workspace with saved chats, a developer agent, code previews, what each model can do, and hardware-aware downloads. The desktop interface uses HTML, CSS and JavaScript inside pywebview; the model engine remains Python.

## Requirements

- Python 3 and the packages in `requirements.txt`, or the launcher's private Python setup.
- Ollama with a downloaded model, or a configured Colibri installation/server.
- On Windows, pywebview uses WebView2. On macOS, it uses WKWebView. **macOS has not yet been tested on real hardware.**
- On Linux, use the Qt backend (`python3 -m pip install -r requirements.txt 'pywebview[qt]==6.2.1'`) and a graphical desktop with its required system libraries. **Linux launch and native rendering remain untested.**

The interface loads local files with no CDN, build step or application HTTP server. Model inference uses the configured server. Downloads, update checks, remote servers and an explicitly requested Vercel deployment require network access.

## Run

Double-click **Start AI Assist.cmd** on Windows or **Start AI Assist.command** on macOS; on Linux run `bash "Start AI Assist.sh"`. Each launcher checks for pywebview and prefers an existing private runtime. If no suitable Python is found, it offers a checksum-verified private Python download into `runtime`, then installs `requirements.txt` (plus the Qt backend on Linux). It needs no administrator password and does not change other Python installations. Ollama and Colibri are installed separately.

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
- **Models** lists each installed model with what it can do, as its Ollama reports it (reads pictures, can use tools, thinks before answering), and a separate list of the models paired computers share, each with **Use in chat**.
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

### Continuous improvement feedback (opt-in memory)

After a successfully saved chat answer or completed Developer task, AI Assist offers a small,
non-modal **Was this useful?** prompt. **Yes** records a rating; **Not quite** allows a
short correction. **Skip** dismisses the prompt. The separate **Ask for changes…** action
under an answer still prepares a revised prompt without sending it.

Ratings and corrections stay in the private local `task-feedback.json` journal.
They do not trigger a model request, repeat any commands, modify project files,
or automatically change model instructions. A separate unchecked **Remember this as a
preference** option explicitly adds the user's written preference to shared memory.
Incognito chats never persist feedback. The journal contains no original answer,
prompt, file content or task command. Feedback is bounded and saved atomically.
The offer is not modal; the user can continue to work without rating.

The goal is to make learning visible and controlled: corrections may guide a future
revision when the user asks; the journal is not evidence that the model's quality
has improved without subsequent evaluation.

### Feedback and verification

**Ask for changes…** under an answer prepares a follow-up draft with your comments and optional success criteria (the **Ask for a better answer** dialog, then **Put in message box**). Review the draft before sending it. It does not send a request or repeat edits automatically.

Developer commands run with your account's permissions from the chosen project directory; they are not an operating-system sandbox. Review the destination, diff and command before approving. Model file paths remain confined to the project, with secret, binary, dependency and generated paths excluded. Read sizes, context and tool execution are bounded. The saved agent file limit defaults to 64 KB and supports choices up to 1 MB; large text can be read in bounded line ranges.

### Blender

When Blender is installed, Developer tells the model where it is, so a task like "render a 512-pixel preview of scene.blend" or "add a ground plane to every .blend file here" can use it. The model writes a Blender Python script in the project and asks to run Blender in the background (`--background --factory-startup --python script.py`). The file write and the command each need your approval, as usual, and commands stop after 120 seconds. AI Assist looks for Blender on `PATH`, in `Program Files\Blender Foundation` on Windows (the newest version) and in `Applications` on macOS. Set `AI_ASSIST_BLENDER` to choose a specific copy. This does not connect to a Blender window that is already open.

### Blender, live

To let Developer work on the scene you have open, install the add-on once. In Blender, choose **Edit → Preferences → Add-ons → Install…** and pick `blender_addon/ai_assist_bridge.py` from the AI Assist folder, then enable **AI Assist Bridge**. Open the 3D Viewport sidebar (N), choose the **AI Assist** tab and click **Connect**.

While Blender is connected, Developer gets two more tools. **blender_scene** reads the scene: objects, transforms, materials, selection, frames and render settings. **blender_run** runs Blender Python. It shows you the code first, and runs only when you click **Run**. Each run is one undo step in Blender, so Ctrl+Z takes it back.

The add-on is off until you click Connect, and off again when you click Disconnect or quit Blender. It listens only on this computer (127.0.0.1) and answers only AI Assist, which reads a secret from its private data folder (`blender-bridge.json`). Code running in Blender is not sandboxed, and AI Assist's internet gate cannot see inside Blender, so read it before you approve. To check the add-on with a real Blender, run `blender --background --factory-startup --python tools/blender_bridge_smoke.py`.

### Your 3D character

`character_builder.py` creates a stylised 3D character with the Blender installed on this computer, running in the background with nothing downloaded. It accepts a name, height, build, skin tone, hairstyle (none, short, long, bun, curly, spiky), hair, eye and clothing colours, and an accessory (glasses, hat, headphones). `blender_character.py` builds the character from Blender's own shapes and writes `portrait.png`, `character.blend` and, where Blender can export glTF, `character.glb`. A description in your own words can fill in the choices: a local model's reply is reduced to valid choices only (`choices_from_model_reply`). The first-run page offers this step.

The character has a humanoid skeleton (hips, spine, chest, neck, head, arms, hands, legs and feet, named as in VRM 1.0). Its rest pose is a T-pose, as game engines expect; the portrait lowers the arms. AI Assist also writes `character.vrm`, the same model with VRM's bone map and terms of use, for VRM apps and worlds. To use the character in Unreal Engine 5, import `character.glb` as a skeletal mesh (drag it into the Content Browser), or import `character.vrm` with the VRM4U plugin. The VRM terms start at the most careful settings (only you may use the avatar, non-commercial use); change them in any VRM editor.

### Shared memory across chats

Use **Settings → Shared memory** to enable/disable notes, add, edit or forget them. A chat's right-click menu selects its memories. Replies show the memories used for that request. Incognito requests do not read or write shared memory. Changing notes affects future requests and does not remove text already in a conversation.

History defaults to `%LOCALAPPDATA%\AI Assist\chats` on Windows or `~/.aiassist/chats` elsewhere. `AI_ASSIST_DATA_DIR` changes the data directory. Chat JSON files are replaced atomically. Settings → Export saves a backup with chat and project data; incognito sessions are excluded. Previously saved chats and `appearance.settings` preferences remain readable.

## Model downloads

Open **Models** for hardware-based recommendations and installed models. Downloads show progress and can be stopped; a model can then be selected in the chat composer. Loaded models can be unloaded explicitly or after the configured idle period. Recommendations are estimates, not guarantees of speed or GPU placement. The status bar reports model placement, VRAM and measured token speed when available, and how full the context is during Developer tasks (in Chats, the meter sits by the message box).

Recommendations come from two public lists, read once per session after one approval that names both sites: Ollama's library on ollama.com, and Hugging Face's most downloaded GGUF chat models on huggingface.co. For each Hugging Face model, AI Assist picks the first quantization in the order Q4_K_M, Q5_K_M, Q4_K_S, Q6_K, Q8_0, IQ4_XS, Q3_K_M that fits this computer, using the same memory estimate as the Ollama suggestions, and shows up to three. Download counts show popularity, not quality. Nothing downloads until you click Download.

Any single-file GGUF model on Hugging Face can also be downloaded by name in **Download another model**, for example `hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M` (repository, then quantization). Ollama fetches it from Hugging Face, and the internet approval says so.

## Privacy, Compute and local check-ins

The mandatory internet gate starts before the native UI. External requests show what is sent and why, then require two confirmations. Block is the default, including Enter, Escape and closing the dialog; Allow unlocks after three seconds on the second step. Local Ollama traffic does not prompt. Model downloads, update checks, cloud models and deployment require approval. Settings → Privacy & internet shows the connection log and the Windows engine firewall lock. Firewall changes need explicit confirmation and may require administrator approval. Native engine networking is not covered by the Python audit hook; the firewall lock is Windows-only.

Compute shows local CPU, GPU, RAM and Ollama model readings at most once every five seconds while visible. Unknown metrics stay unavailable. Paired-device records are enrollment metadata, not evidence a peer is online, and are not contacted for telemetry.

Hourly quiet-time check-ins default off. On Windows, enabling them measures microphone sound levels locally; it saves or sends no audio. After one hour and twenty seconds of quiet, the report waits until chat and Developer work are idle. It summarizes recent saved work and hardware, with an optional reflection from a local Ollama model. Preview now does not enable audio detection. Update checks still require a separate user action and gate approval.

LAN pairing is manual and **off on every launch**. Enter this computer's private IPv4 address in Settings before enabling it. A gated child process offers only discovery and TLS pairing, never the desktop API, chats, inference, files or commands. Compare the certificate code displayed by the target before entering its six-digit PIN; the PIN expires after 120 seconds and allows three attempts. Turning sharing off or closing the app stops the service. Creating the host identity uses the `cryptography` package from `requirements.txt`, which the launchers install. Pairing credentials remain in the private data folder and never enter Compute or bridge metadata.

**Usage sharing** is a second opt-in under LAN pairing, also off on every launch: "Also share this computer's CPU, RAM and GPU usage with paired devices". While it is on, the pairing service answers usage requests only from devices paired with a PIN that present their token over TLS, after they have checked this computer's pinned certificate. Only CPU, RAM and GPU load, the computer's name and its loaded model names are sent. Mission Control on the paired device then shows these as live meters, refreshed every five seconds while it is open.

**Headless computers such as a Raspberry Pi** can share their usage without the desktop app. Copy this folder to the Pi, then over SSH:

```sh
sudo apt install python3-cryptography
python3 share_usage.py
```

It prints an address, port, certificate code and PIN: enter them on your Mac in Settings → Privacy & network → Pair another device. Keep the command running (for example inside `tmux`); Ctrl+C stops sharing. CPU and RAM are reported; a Raspberry Pi reports no GPU load.

## Preview HTML

Preview opens a project HTML file or a generated HTML code block in a separate preview window. Main chat output remains sanitized Markdown; executable preview content has no application API bridge. Relative project assets stay with the project file. Close the preview independently or close the app.

## Deploy a web project to Vercel

In **Developer → Publish web project**, choose **Deploy to Vercel**. Enter an existing web project folder, a Vercel project name and optional team/domain. Confirm the upload before deploying to production. This publishes that web project, not the desktop app.

Install Vercel CLI separately (`npm install -g vercel`) and sign in with `vercel login`. Output and the returned URL appear in the dialog. Cancellation stops local work; a deployment already submitted to Vercel may continue, so check its dashboard. Deployment is blocked while an agent changes the same project.

## Configuration

`OLLAMA_HOST` selects the Ollama server, defaulting to `http://localhost:11434`. Select a model and requested context in the composer. Server/model support and available memory determine the actual allocation; on Models, hovering a model's name shows its advertised limit, separately from the measured usage in the status bar. Larger contexts need more memory.

### Colibri backend

Install Colibri separately using its setup instructions. In **Models → Colibri engine**, Browse for its folder, load the model catalog, install a supported model, set the context, or start/stop its server. These actions reuse Colibri's own launcher. Folder discovery also checks `COLIBRI_DIR` and common local installation locations. Colibri serves one model at a time.

`COLIBRI_URL` overrides the server URL (default `http://127.0.0.1:8000/v1`); `COLI_API_KEY` provides an optional session credential. Existing backend settings remain readable. A remote server can serve chats, but local installation and start/stop controls require a known local folder. The composer groups models by Ollama and Colibri; unavailable choices show a reason.

### App updates

Use **Settings → Check for updates** to compare this copy with Luca's GitHub default branch. It runs in the background without requiring Git. Different files can mean local development changes as well as an update. Downloading source does not overwrite this copy; extract it into a new folder and keep the same data-directory setting.

## Development and tests

```powershell
python run_windows_tests.py --timeout 420
```

On Windows the full suite runs on a separate, invisible desktop. Focused headless modules can run directly, for example `python -m unittest test_webui_static test_app_core`. Other platforms use `python -m unittest discover`; native checks need an isolated graphical session. Engine/API tests inject providers and windows; they do not require a real model. Link tests may be skipped when Windows does not permit symbolic links. Colibri and Vercel tests use fixtures or fake commands, not real downloads or deployments. The VS Code extension check is `node --test vscode-extension/extension.test.js`.

GitHub Actions (`.github/workflows/tests.yml`) runs `ruff check .` (unused or undefined names and syntax errors, configured in `ruff.toml`), then the compile check, the whole suite and the extension check on Windows, macOS and Linux for every push and pull request, using Python 3.13 like the private runtime. Results are on the repository's **Actions** tab. Windows-only and macOS-only checks run on their own platform there, so a change that breaks one system shows up before it reaches users.

The [macOS desktop checks](.github/workflows/macos.yml) workflow runs the headless suite with default macOS temporary paths. Its optional `native_smoke` input checks chat through the real WKWebView on the hosted runner using temporary data and a simulated model.

`app_core/ollama.py`, `streaming.py` and `providers.py` supply the shared engine; `app_core/runtime.py` is the desktop's thin compatibility adapter. Plain status and handoff logic lives in `status_values.py` and `hybrid_memory.py`. `webui/app.py` exposes JSON methods; workers emit through `webui/bridge.py`. The frontend is vanilla JavaScript with offline assets and English/Italian strings. No worker receives native UI objects. See [the D8 integration report](docs/d8-integration.md) for conflict decisions and verification limits.

## Generate

Open **Generate → Quick animation** to preview a slow zoom, side-to-side pan, or ripple on a JPEG or PNG. Quick animation uses WebGL 2 locally and can save a 3-second MP4 or WebM when the desktop webview supports canvas recording. It does not need ComfyUI or a model download.

### Infinite

[Infinite](https://github.com/n1m21n/Infinite) is a free, open-source app (MIT licence) that builds moving pictures, music and 3D from connected nodes. When it is installed, open **Generate → Infinite**, describe a clip (for example “a glowing star that pulses with a slow synth melody”), choose a model and a length of 4, 8 or 12 seconds, and click **Make clip**. A local Ollama model writes the clip as an Infinite patch, a small text file. Infinite checks the patch without opening a window. When it reports a problem, the model gets Infinite's message and the controls of the nodes it used, and tries again, up to three times. Infinite then renders the MP4, with sound when the patch makes any. **Save clip** saves the MP4; **Save patch** saves the `.inf` file, which opens in Infinite for further changes. The last eight clips stay in the app data folder (`infinite`).

AI Assist looks for Infinite in `Applications` on macOS, in `Program Files\Infinite` or `%LOCALAPPDATA%\Infinite` on Windows, and on `PATH`. Set `AI_ASSIST_INFINITE` (or Infinite's own `INFINITE_BIN`) to choose a copy. The first clip reads Infinite's list of nodes, which takes about 20 seconds; the list is kept for that Infinite version.

Infinite runs outside AI Assist's internet gate, so every patch is checked first. Patches that read or write files, use a camera, microphone or MIDI device, talk to other programs or the network (Syphon, NDI, OSC), load audio plugins or run JavaScript are refused before Infinite sees them. Thinking models (such as Qwen 3.5 or DeepSeek R1) are asked to answer without reasoning first, because their reasoning can take many minutes. Cloud models are not used. Developer also knows when Infinite is installed: it can check and render `.inf` patches in a project, with the usual approval for each command.

### AI video

Start ComfyUI on `127.0.0.1:8188` and install the three model files for **Wan2.2 TI2V 5B** using the [official ComfyUI setup guide](https://docs.comfy.org/tutorials/video/wan/wan2_2#wan2-2-ti2v-5b-hybrid-version-workflow-example). In AI Assist, open **Generate → AI video**, select a JPEG or PNG, describe the motion, and pick an orientation. For example, try “The camera slowly circles the toy car.”

The clip plays in AI Assist and the MP4 is saved in ComfyUI's `output/ai-assist` folder. ComfyUI also stores a copy of the photo in its input folder.

### 3D Camera

Open **Generate → 3D Camera** to orbit an animated 3D scene and switch between a 24 mm wide lens and a 50 mm close lens. Select **Record clip** to save four seconds of the view as an MP4 or WebM. The scene is rendered locally with WebGL 2.

### Recent clips

Open **Generate → Recent clips** to play your last 12 clips, open their files, or reuse an AI video's prompt and orientation. Quick animations and 3D Camera recordings stay where you saved them. AI Assist also keeps its own copy of each new AI video in the app data directory, so you can replay it after ComfyUI closes; the copy is deleted when the video drops off the list, and ComfyUI's original stays in its output folder.
