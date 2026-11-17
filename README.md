# AI Assist

A local Ollama workspace with saved chats, a developer agent, code previews, model capabilities, and hardware-aware downloads. Built with Python and Tkinter; no pip packages are required.

## Requirements

- Python 3 with Tkinter available.
- [Ollama](https://ollama.com/) installed and running, with at least one model downloaded.

The app uses only the Python standard library; no pip packages are required.

## Run

Download a model, for example:

```sh
ollama pull deepseek-r1:8b
```

Start Ollama, then run the app from this folder:

```sh
python AIassist.py
```

On Windows, you can double-click **Start AI Assist.cmd** instead. It finds Python, starts the app from the correct folder, and keeps startup errors visible. If `.py` files are associated with Python, you can also double-click `AIassist.py` directly.

On macOS, double-click **Start AI Assist.command**. It looks for a Python 3 that includes Tkinter (the python.org installer does; with Homebrew also run `brew install python-tk`). If macOS refuses to open it the first time, right-click it and choose **Open**, or run `chmod +x "Start AI Assist.command"`. On Apple silicon (M1 and later) the recommendations count the memory the GPU shares with the system (about 2/3 of RAM, 3/4 above 36 GB), so Macs get GPU-sized suggestions rather than CPU-only ones.

Choose a model in the sidebar before chatting. Click **Refresh models** after adding models or restarting Ollama. Switching models starts a new conversation.

Chat replies appear while they are generated. The finished reply is formatted with copy and code-export actions. If a stream fails before completion, the app reports the failure rather than saving a partial answer as complete. While a reply or the developer agent runs, **Send** becomes **Stop**; stopping a reply keeps the part already written (Enter never stops it). The status line shows Ollama's reported loading and prompt-processing times and generation rate when available; click model status to view the last summary. Developer step timings appear in **Activity**.

## Chats and developer workspace

- **New chat** immediately creates and selects a saved entry, clearing any search that would hide it. Messages and drafts save locally. Search, resume, rename, export, or delete chats from the sidebar and **Chat options**. **Chat options > Back up all chats...** writes one .zip with every chat as JSON (copy those files back into the chats folder to restore) and as readable Markdown, including chats still being saved.
- **Developer** uses a chosen project folder and a model that advertises tool calling, such as `qwen3-coder:30b`. The agent can inspect text files, propose edits, and run commands. It changes existing files by replacing exact blocks (`edit_file`), so small changes stay small; it writes whole files only for new files or full rewrites. Each edit shows its diff before **Apply change**; each command shows its text and working folder before **Run command**. **Reject** declines an action, and **Stop agent** cancels work and pending reviews.

To write into a specific folder, open **Developer**, enter its path under **Folder for reading and writing files**, and click **Use folder**, or choose **Browse folders…**. **Use folder** creates missing folders. Then ask, for example, “Create a Python script named `main.py` in this folder.” New-file proposals open a **Save As** picker so you can choose the folder and filename. Cancelling declines the write. Review the full destination and diff, then click **Apply change**. If you choose an existing file, its actual overwrite diff is reviewed first. An explicitly chosen destination may be outside the project; that approval applies only to that file, and does not expand the agent's general file access. New subfolders can be created by the agent's file tool. The selected project folder is saved with the developer chat; changing it starts a new conversation. Reviewed shell commands still run with your account's permissions.
- **Files** sets **Largest file the agent reads**: 64 KB (default), 128 KB, 256 KB, 512 KB or 1 MB. A larger choice shows what it means for the current context and free memory before it applies. A file larger than about a quarter of the model's context is returned to the agent in parts, with the line numbers to read next, and edited block by block. **Files** also lets you pin selected project files to context. Generated code appears in **Code**, with explicit copy/save actions. **Activity** records tool results and command output. Native tool history is retained when a developer chat is resumed.
- **Capabilities** distinguishes actual app features from the selected model's reported support. It shows the native context maximum separately from the app's configured context.

Saved chats retain their conversation and native developer-tool history. The model can only use what fits in its context window. When developer history grows too large, requests preserve bounded excerpts of the original user request and recent dialogue alongside the latest request and recent tool work. Reopen the same chat to continue it, pin important project files, and choose a larger context when hardware permits.

### Shared memory across chats

Shared memory is enabled by default for both regular chats and the developer agent. Say **“Remember that I prefer Python”**, `/remember I prefer Python`, or **“Add to shared memory: I prefer Python”** to save a persistent note. **“Add this to shared memory”** saves the previous assistant reply. Each message also has an **Add to memory** button for saving that exact message. Explicit memory commands are handled by the app locally and confirmed only after the note is saved, without relying on a model's claim. Disabled memory and write failures show an error and keep the prompt for retrying.

Click **Shared memory** in the sidebar to view, edit, or clear notes, or disable shared memory. Up to 4,000 characters of notes are stored locally in `shared.memory` beside the chat files and restored on restart.

The app also retrieves bounded excerpts from up to two other saved conversations when their titles or sampled dialogue match the current message. This uses saved chat text, without an extra model request. Recall is incomplete and limited by context size; use explicit notes for preferences that must carry across chats. The current request and selected folder take precedence over remembered context. Historical assistant claims do not establish that files exist or commands ran.

**Clear notes** takes effect when you click **Save**; it leaves conversations available for recall. Disable the shared-memory checkbox and save to stop using both notes and cross-chat excerpts. Deleting a chat removes it from future recall; separately saved notes remain until edited or cleared. Memory applies to future requests, so changing it does not remove text already present in a conversation or interrupt an active request.

Commands run with your account's permissions from the selected project directory; the shell is not an operating-system sandbox. Model-supplied file paths stay inside the selected project; a user-selected Save As destination permits a reviewed write to that one file. Secret, binary, dependency, and generated paths remain excluded. Command output, tool steps, context, scan work, and execution time are bounded.

History defaults to `%LOCALAPPDATA%\AI Assist\chats` on Windows, or `~/.aiassist/chats` elsewhere. Set `AI_ASSIST_DATA_DIR` to change it. Each chat has its own atomically replaced JSON file; history is stored outside the repository by default.

Chat writes run on one background worker, preserving the order of saves, renames, and deletions. Pending changes remain available when switching chats. Closing waits for queued writes while keeping the window responsive; a failed write leaves the window open and reports the error so it can be retried.

## Model downloads

Click **Download models** to browse Ollama's current popular chat models, search the list, and choose an exact download size. The list is fetched live from [Ollama's popular library](https://ollama.com/library?sort=popular); its download counts measure Ollama popularity, not worldwide usage. Model downloads show progress and can be cancelled. After installation, choose **Use in chat** or select the model in the sidebar. Your current conversation stays in place until you switch models.

The **Recommended for this PC** tab reads your CPU, available RAM, graphics card, GPU memory, Ollama version, and model-disk space locally. It matches current download variants to conservative memory estimates. Each recommended model has its own **Download** / **Use in chat** button and **Setup guide** with exact commands and GPU verification steps. **Rescan hardware** updates the report after you unload models or change your setup.

Recommendations distinguish available GPU memory from capacity after unloading other models. Download size is not runtime memory: context cache and overhead also count. **Use in chat** on a recommended card explicitly applies a 4096-token context to loading and chat requests for the current app session. Check `ollama ps` to verify actual GPU placement. Recommendations for a remote Ollama server cannot use your local PC's specifications to establish fit.

Browsing requires internet access, and Ollama must be running to install models. Copy the complete application folder, including all Python modules, when moving the app. The launcher inside this repository starts this copy; the parent-folder launcher also forwards to this repository when present.

- **Enter:** send a message.
- **Shift+Enter:** insert a new line.
- **Ctrl+N:** start a new chat with the current model.
- **Copy:** copy the complete message.

## Preview HTML in your browser

Click **Preview** on a complete HTML code block in a chat, or choose it in **Developer → Code** and click **Preview HTML in browser**. Generated HTML is written to a temporary preview folder without changing your project.

For saved pages, select an `.html` or `.htm` file in **Developer → Files** and click **Preview HTML in browser**. With no file selected, the button opens `index.html`. Project previews serve relative CSS, JavaScript, images, and other assets from the selected folder. Refresh the browser after editing a saved file.

Previews use a local HTTP server bound to `127.0.0.1` on an available port. The app does not publish or deploy anything. Close AI Assist to stop the preview servers and remove temporary HTML. Generated snippets should include their own styles and scripts; save linked assets into a project to preview them together. This is a static preview, so framework builds and backend servers must be run separately.

## Deploy a web project to Vercel

In **Developer**, click **Deploy to Vercel…**. Choose the generated web project's folder, enter the Vercel project name, optionally enter your team slug and custom domain, then click **Deploy to production**. This uploads the local project and may update its existing production site. The desktop Tkinter application itself is not a website; select a web project instead.

Create an access token at https://vercel.com/account/tokens with access to the intended account or team. Paste it into the masked **Vercel access token** field, or set VERCEL_TOKEN in the environment before starting AI Assist. The app keeps the token in memory and does not save it to settings, chat history, or project files. Node.js, the Vercel CLI, and terminal login are not required for this deployment flow.

The dialog uploads source files through the Vercel REST API, creates a production deployment, and waits up to 15 minutes for its build to reach READY. Existing project build settings and environment variables remain managed in Vercel. New projects receive the settings required for their first deployment; plain HTML projects use the static framework setting. Enter the team slug or team ID for team-owned projects. With Team empty, the app selects the token's only available team; if several teams are available, it asks you to choose before uploading. The token must have access to that team.

The uploader excludes dependency folders, local environments, Git metadata, local AI Assist data, .env files, and common credential files. Root .vercelignore rules override .gitignore rules; nested .gitignore files are supported. Rules support slash-separated globs, **, directory patterns, and ! negation; ignored parent directories must be included before their children. Files and directory links are skipped. The uploader limits projects to 10,000 files, 50 MB per file, and 100 MB total. Configure production secrets in the Vercel dashboard instead of uploading environment files.

After a successful build, the app adds and assigns the requested custom domain. DNS and ownership verification may still be required at your domain provider or in Vercel. A domain error leaves the working deployment URL available. **Stop** stops local uploads and build monitoring after the current HTTP request finishes (up to 30 seconds); a submitted deployment may continue on Vercel. Check the dashboard before retrying an interrupted request. Tests simulate the API and never publish files.

Reference: [Vercel REST API](https://vercel.com/docs/rest-api), [create a deployment](https://vercel.com/docs/rest-api/deployments/create-a-new-deployment).

## Configuration

### Colibri backend

Open **Settings ? AI backend: Ollama...** from the top menu, choose **Colibri**, enter the server URL (default `http://127.0.0.1:8000/v1`), and click **Connect**. AI Assist discovers the model IDs served by Colibri and streams chat responses. Your backend and URL are remembered for the next launch. An optional API key stays in memory for this session; alternatively set `COLI_API_KEY`. `COLIBRI_URL` overrides the saved Colibri URL at startup.

Install Colibri separately using its [official setup guide](https://github.com/JustVugg/colibri#get-started-in-one-step). On Windows, download/extract Colibri and run `START-HERE.bat`, then follow its model setup prompts. The manual server command from its `c` folder is:

```sh
./coli serve --model /path/to/model --host 127.0.0.1 --port 8000
```

For a Windows release archive, use `coli.cmd serve --model D:\models\your-model --host 127.0.0.1 --port 8000`. In a source checkout, use `py -3 c\coli serve --model D:\models\your-model`. If the server enforces `COLI_API_KEY`, enter that same key in AI Assist.

**Managed by AI Assist.** When the Colibri folder is known and the server URL is on this computer, AI Assist drives Colibri's own launcher (`coli status|start|stop|setup --json`) instead of asking you to keep a terminal open:

- The **Engine** picker in the sidebar switches between Ollama and Colibri (the same choice as the Settings dialog). The folder is found in `COLIBRI_DIR`, beside AI Assist, or in your home, Documents, Desktop or Downloads folder (including `colibri-main` from a ZIP); otherwise choose it in the colibri tab.
- Colibri serves one model at a time. Selecting the engine, **Run all**, or sending a message starts it; **Stop all** stops it. The sidebar shows Colibri's measured speed on the last answer.
- **Find & download models** opens on **colibri engine (RAM + SSD)**: Colibri's own assessment of this PC (`coli setup --list --json`) with download size, RAM needed, whether a model runs from RAM or streams experts from the SSD, and whether it supports the coding agent's tools, plus a line comparing it with the Ollama (GPU) pick. **Install & use** runs `coli setup --model <id>` (resumable download, engine build, server start). **Context** sets Colibri's context for an install, and **Apply to the current model** restarts it with a new one.
- The first developer-agent request in a session asks for confirmation unless the last answer ran at 15 tokens per second or more; a model family without tool calls is refused before the agent starts.

A server on another computer, or one without a known folder, is used as it runs: its models are listed and chats work, while installs and start/stop stay in Colibri.

Developer mode uses Colibri's OpenAI tool-calling API and keeps AI Assist's existing file/command approval dialogs. Engine support varies (for example, GLM and Qwen support native tools, while some other engines reject tool requests); see [Colibri's API documentation](https://github.com/JustVugg/colibri/blob/main/docs/api.md). Capability flags and native context limits remain unknown when the model API does not advertise them. The developer agent retains a conservative local history/output budget; the server controls actual context allocation. Slow streaming/prefill is allowed up to a 30-minute socket inactivity timeout. Interrupted or truncated responses are reported as failures.

Saved chats record their backend. Existing chats default to Ollama. Opening a chat switches to the engine it was written with and selects its model once that engine answers. Changing backend starts a new chat and preserves the previous one; a switch requested during a background model check applies as soon as the check ends.

### App updates

AI Assist checks the latest commit on `LucaBeretta/AI_assist` on GitHub once after startup. Open **Settings ? Check for updates...** to see details, retry a failed check, or **Download latest**. Checks run in the background and require internet access; Git is not required, so downloaded ZIP copies work too.

The checker compares shipped Python modules and Windows launchers with GitHub file hashes, allowing for Windows line endings. Missing or different files are reported as possible updates or local changes, since an edited/development copy can also differ. It checks the repository's default branch rather than only tagged releases. Network errors and GitHub request limits are reported without interrupting chats.

To update, extract the downloaded ZIP into a new folder and start that copy. The checker does not overwrite files. Saved chats remain in the configured data folder; if you set `AI_ASSIST_DATA_DIR`, keep the same setting for the new copy.

`OLLAMA_HOST` optionally sets the Ollama server address; the default is `http://localhost:11434`.

`OLLAMA_MODEL` can name your preferred installed model at startup. Otherwise, the app uses an already running model or the first installed model. You can change the selection in the sidebar.

**Context tokens** in the sidebar sets the requested context for the selected model's next chat or developer request. The choice is saved with the conversation. **Server default** leaves ordinary chat context to Ollama; the developer agent uses a 4096-token fallback. Larger settings allow more history and project context but need more memory. They do not guarantee model support or GPU placement; inspect **Capabilities** and run `ollama ps`. Recommended model cards still apply their conservative 4096-token setting.

## Development and tests

```sh
python -m unittest discover -v
python -m compileall -q .
```

Tests isolate chat history and project changes in temporary directories. Ollama responses are simulated for reproducible checks; subprocess integration tests also run harmless real Python commands and verify timeout cleanup. Real symlink tests may be skipped when Windows does not grant symlink privileges; resolved-path confinement is also checked without that privilege.

The modules separate workspace UI, atomic chat persistence, project inspection, reviewed agent tools, model metadata, downloads, and hardware recommendations. Blocking network, scanning, and command work runs outside the Tk thread; UI updates arrive through queues. Idle polling is slower than active polling, draft saves skip unchanged data, history uses cached records, and activity output appends incrementally.

History search checks individual fields and stops at the first match, including titles, models, project paths, drafts, and messages. Project text validation uses a compiled control-character check. Model-library HTML traversal is iterative, avoiding recursion limits on deeply nested pages and repeated text copying through ancestor nodes.

### Profiling

Profile creating, listing, searching, opening, and saving synthetic long conversations without Ollama or a display:

```sh
python profile_workflows.py --chats 20 --messages 200
```

This uses a temporary history directory and prints cumulative timings. Add `--output history.prof` to retain profiler data. For actual UI workflows, run `python -m cProfile -o ui.prof AIassist.py`, open a long chat, search history, type a draft, and close normally. Inspect the result with `python -m pstats ui.prof` (`sort cumulative`, then `stats 25`). UI profiling covers the main thread; background work needs separate profiling, as in the synthetic storage workflow. Compare runs with the same workload and hardware; local microbenchmarks do not measure model inference speed.
