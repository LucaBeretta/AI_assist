# AI Assist

A local Ollama workspace with saved chats, a developer agent, code previews, model capabilities, and hardware-aware downloads. Built with Python and Tkinter, plus a browser-based [web workspace (preview)](#web-workspace-preview); no pip packages are required.

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

On Windows, you can double-click **Start AI Assist.cmd** instead. It finds a Python whose Tkinter can open the window, starts the app from the correct folder, and keeps startup errors visible. If `.py` files are associated with Python, you can also double-click `AIassist.py` directly.

On macOS, double-click **Start AI Assist.command**. It looks for a Python 3 that includes Tkinter (the python.org installer does; with Homebrew also run `brew install python-tk`). If macOS refuses to open it the first time, right-click it and choose **Open**, or run `chmod +x "Start AI Assist.command"`. On Apple silicon (M1 and later) the recommendations count the memory the GPU shares with the system (about 2/3 of RAM, 3/4 above 36 GB), so Macs get GPU-sized suggestions rather than CPU-only ones.

macOS requires **Tk 8.6 or newer**. The launcher prefers Python installations from python.org and Homebrew, checks that a real Tk window can initialize, and prints the selected interpreter and Tcl/Tk versions. It skips incompatible installations instead of opening a broken window. Set `AI_ASSIST_PYTHON` to prefer a particular Python executable. Apple's older Tk 8.5 is unsupported; see [Python's macOS Tcl/Tk guidance](https://www.python.org/download/mac/tcltk/).

**No suitable Python? Nothing to install by hand.** If neither launcher finds an installed Python that can open the window, it offers to download a private Python 3.13 with Tk (about 25 MB on macOS, 45 MB on Windows) from [python-build-standalone](https://github.com/astral-sh/python-build-standalone). It is used only after its SHA-256 checksum matches [python-runtime.txt](python-runtime.txt), it is stored only in this folder's `runtime` subfolder, and it needs no administrator password and changes no other Python. Later launches use it automatically; delete `runtime` to remove it. Ollama itself is still installed separately from [ollama.com/download](https://ollama.com/download).

If the Mac interface appears blank or its fields are unreadable, install a current Python from python.org and launch with **Start AI Assist.command** again. To collect runtime details from the project folder, run `python3 gui_runtime.py --probe`; the launcher prints equivalent details for the interpreter it actually selects. The app aligns native macOS window appearance with its Light/Dark theme where supported, and uses labelled input areas with contrasting outlines on both Mac and Windows. Actual rendering on each macOS/Tk combination still needs a check on that Mac.

**Get started.** If AI Assist starts without a working model, a short guide shows three steps (Ollama installed, Ollama running, a model that fits this computer) with one button for whatever is missing: the Ollama download page, starting Ollama, or models suggested for this computer's memory. Anything that goes online still asks for your two confirmations. It opens only when something is missing (you can turn that off) and is always under **Settings > Get started...**.

**Settings > Use the browser version...** closes this window and opens the [web workspace](#web-workspace-preview) in your browser, already signed in, with the same chats. Close its small window to stop it.

Choose a model in the sidebar before chatting. Click **Refresh models** after adding models or restarting Ollama. Switching models starts a new conversation.

The workspace starts in the **Night** console theme. The ☀ / ☾ button in the header (or Ctrl+Shift+L) switches between Night and **Day** and remembers the choice; **Settings > Appearance...** still offers Light, Dark and Warm, or Classic headings, and existing appearance preferences are preserved. A status bar along the bottom shows, from live data: the engine and whether it answers, the model and where it runs (GPU, CPU or both from Ollama's `/api/ps`; RAM + SSD for Colibri), the context the last answer used, the GPU memory of loaded models, the last speed in tokens/s, the developer agent's step, and whether prompts stay on this computer (*local only*, or a warning for a remote server or an Ollama cloud model). The model chip in the header opens **Model controls**. The sidebar keeps recent chats visible, with context size, Refresh, Run all, Stop all, and runtime details under **Model controls...**. Conversation width stays comfortable on large screens, and starter suggestions reflow in the developer workspace.

When a problem has a known cause (Ollama not running or not installed, not enough memory, a model that is missing or cannot use tools, a blocked internet request, a document folder that moved), its message starts with what happened and what to do, with one button that fixes it, such as **Check again** or **Find a model...**; the original text follows under *Details*. When the chosen model is not loaded yet, the status line says so, because the first answer can then take up to a minute.

Chat replies appear while they are generated. The finished reply is formatted with copy and code-export actions. If a stream fails before completion, the app reports the failure rather than saving a partial answer as complete. While a reply or the developer agent runs, **Send** becomes **Stop**; stopping a reply keeps the part already written (Enter never stops it). The status line shows Ollama's reported loading and prompt-processing times and generation rate when available; click model status to view the last summary. Developer step timings appear in **Activity**.

## Internet gate

AI Assist works offline. Nothing it does reaches the internet until you approve it twice.

- **What is checked.** At startup the app turns on a Python audit hook that stays on until the app closes. Every DNS lookup, connection and datagram the app makes is classed as *this computer*, *local network* (private addresses such as 192.168.x.x, 10.x.x.x or fe80::) or *internet*. Host names other than `localhost` count as internet, because looking one up may ask a DNS server outside your network. Requests that make another program go online are checked too: Ollama model downloads and uploads, Ollama cloud models (including local aliases of one, which Ollama's `/api/show` reveals), colibri model installs, and internet links opened in the browser or another program.
- **Two confirmations.** The approval window shows what would go online, where, why, what leaves the computer, and which app file asked. When the code gave no reason, *Why* reads *Not declared* in amber. **Continue to allow...** leads to a second step, where **Allow** unlocks after three seconds. Blocking is the default: closing the window, Escape and Enter all block. An approval covers only that task (or 60 seconds) on the thread that asked; there is no "always allow". After you block a request, the same request is blocked without asking for 30 seconds.
- **Notifications and log.** A system notification announces each request waiting for review, each allowed connection, and each automatic block. **Settings > Connection log...**, or a click on the privacy indicator in the status bar, lists every request and its decision. The same entries are saved as JSON lines in `network.log` beside the saved chats (up to 1 MB, plus one older file). The status bar shows *local only · internet locked*, or *online now: host* for a minute after an approval.
- **Fails closed.** Without the approval window (scripts, tests, the web workspace), internet access is blocked. If the gate cannot check something, such as an unknown socket type or an Ollama model it cannot confirm runs locally, the request is blocked. asyncio's Windows connection method bypasses audit hooks, so the app refuses to load it.
- **Listens on this computer only.** AI Assist itself never accepts connections from other devices: a listening socket on any address other than this computer (127.x, ::1) is refused and logged. Programs it starts, such as colibri, keep their own settings.
- **Updates and previews.** AI Assist never checks GitHub by itself; **Check for updates...** asks first. HTML previews carry a Content Security Policy that lets the browser load only the preview's own files, so scripts, fonts and images from the internet do not load. A page can still send the browser to another address.

**Programs AI Assist starts.** colibri is written in Python, and so is much of what the developer agent runs. Every Python program AI Assist starts, including the colibri server it launches and Python started by an agent command, loads the same gate from `child_gate/sitecustomize.py`. Its internet requests appear in the same approval window, labelled with the program (for example *colibri*), and are blocked if AI Assist is not running to approve them. If the gate cannot start in such a program (for example Python older than 3.8), the program is stopped instead of running unchecked. An approved colibri model install is the one exception: its download runs unchecked after your approval, then the server starts under the gate.

**Developer agent commands.** A command can start any program (git, npm, curl), and Windows cannot firewall "programs started by AI Assist" without cutting off the whole PC. So every command needs two confirmations: **Run command**, then **Run**, which unlocks after three seconds on a second step that explains the command runs outside the gate. Each confirmed command is written to the connection log and announced with a notification.

Each command review also shows **what AI Assist can tell** about it, worked out on this computer: what each program does, the files and web addresses it names, and warnings for hidden or encoded commands, administrator rights, system changes, deleting, going online, installing, paths outside the project, unchecked scripts and unknown programs. It is a reading aid, not a guarantee, and it never replaces a confirmation. The model's own description is shown separately as "The AI says", because a model can be wrong or misled by what it read.

### Firewall lock

Ollama runs as its own native programs, so the gate cannot see inside them. **Settings > Firewall lock...** (Windows) adds Windows Firewall rules that block every program in Ollama's folder, and any native program in the colibri folder, from internet addresses. The rules apply even when AI Assist is closed and when you use Ollama directly. This computer and your local network keep working. A startup task turns the rules back on after a restart.

- **Turn on** needs administrator approval (one Windows UAC prompt). Reading the lock never does.
- **Lift 5 / 15 / 60 min** allows Ollama online for that long, for example to download a model, and then the lock returns by itself. **Lock again now** ends a lift early. While the lock is on, AI Assist refuses Ollama downloads and cloud models with an explanation instead of asking.
- The window lists every locked program. It reports when Windows Firewall is off, when a rule was changed outside AI Assist, or when an Ollama update added a program that is not locked yet. **Turn on again** then covers it. The status bar shows *firewall on*, or a warning while the lock is lifted or incomplete.
- Every change is written to the connection log and announced with a notification.

**Limits.** Audit hooks cannot see into native code: Python extensions that open their own connections are refused where they are known (`hf_xet`, `hf_transfer`, `pycurl`), and others are not checked. Python started with `-I`, `-E` or `-S` skips the child gate. The browser is outside the gate once a page is open. Audit hooks also cannot stop deliberately malicious code inside a gated process; the gate guards AI Assist's features and the programs it starts. The firewall lock is available on Windows only.

## Chat with your documents

**Chat options > Use documents from a folder...** lets a chat answer from your own files, entirely on this computer: manuals, notes, books, exported web pages. AI Assist reads the folder (text and code, Markdown, CSV/JSON, HTML, Word `.docx`, OpenDocument `.odt`, EPUB, and PDFs with ordinary text), finds the passages that best match each message by keyword relevance, and gives them to the model as numbered excerpts. The saved answer ends with **Sources from your documents**, so you can check each file. It needs no extra model and no download, so it works offline with the models you already have.

- Only changed files are read again; the index lives in `libraries/` beside the saved chats, and your files are never changed.
- Secret and private files are skipped exactly as for the developer agent (`.env`, keys, credentials, dependency and hidden folders).
- Scanned PDFs (pictures of pages) and PDFs with unusual internal text encodings have no readable text; they are listed as skipped, never guessed.
- Answers are only as good as the model: very small models (under about 1B parameters) often ignore the excerpts; an 8B model answers and cites them well.
- **Stop using documents** detaches the folder. The web workspace uses a folder attached here and shows it in the chat's title line.

## Web workspace (preview)

A modern interface for the same chats, in your browser, on this computer only. It needs Python but not Tkinter:

```sh
python web_workspace.py            # opens AI Assist in its own window, signed in
python web_workspace.py --tab      # a normal browser tab instead
python web_workspace.py --no-browser --port 8765
```

With Edge, Chrome or Brave installed (your default browser first), it opens as an app window, without tabs or an address bar; otherwise in a normal tab. The console window it runs in prints a single-use sign-in link; press Enter there to open AI Assist again (each link works once, for five minutes) and Ctrl+C to stop. It offers streamed chat with Stop, Markdown and code blocks with Copy, saved chats (search, rename, delete), models and where they run, a details panel (model capabilities, last answer's speed and context, privacy, shared memory, hardware), shared memory editing, the connection log, Night and Day themes, keyboard navigation, and a layout that works on narrow screens.

**Developer agent.** Open a developer chat and describe the change you need; **Run agent** starts the task. Each file change appears as a card with its diff (**Apply change** or **Reject**), and each command needs the same two confirmations as the desktop app: **Run command**, then **Run**, which unlocks after three seconds on a second step that explains the command runs outside the internet gate. Nothing is written or run before you answer; **Stop** declines what is waiting. **Undo last task** puts back the files the task changed, and **New task** starts a fresh chat in the same folder. **Projects** shows each folder's files, applied diffs and command activity, read-only. The model must support tool calling (for example `qwen3-coder`); others are refused before anything runs.

On purpose, it **cannot**:

- **go online**: there is no approval window, so the internet gate blocks every internet request. Model downloads, cloud models and update checks stay in the desktop app.
- **change your projects without you**: the agent edits a file or runs a command only after you approve it on the page, and the server refuses an early or skipped confirmation. It works only in folders chosen for developer chats in the desktop app (**Developer > Open project**).
- **be reached from other devices**: it listens on 127.0.0.1 (or ::1) only, checks every request's Host and Origin, and needs the page's session for every call. No cookies are used.

Only one AI Assist may use a data folder at a time, so close the desktop window before starting the web workspace (and vice versa); the second one explains which is open. Design and limits: `docs/architecture-decision.md`, `docs/target-architecture.md`, `docs/threat-model.md`.

## Chats and developer workspace

- **New chat** immediately creates and selects a saved entry, clearing any search that would hide it. Messages and drafts save locally. Search, resume, rename, export, or delete chats from the sidebar and **Chat options**. **Chat options > Back up all chats...** writes one .zip with every chat as JSON and as readable Markdown, including chats still being saved.
- **Automatic backups.** Once a day AI Assist (the desktop window or the web workspace) writes every chat and your shared notes to `backups/` beside the saved chats, and keeps the newest 14. Backups stay on this computer; copy one to a USB stick to keep it elsewhere. **Chat options > Restore chats from a backup...** lists them, offers **Back up now**, and restores a chosen backup or another backup file. Restoring adds chats that are missing and replaces damaged ones (the damaged file is moved to `damaged/`, not deleted); it never changes a chat you still have, and shared notes come back only if yours are empty. The web workspace has the same under **Settings > Backups**, limited to the backups folder.
- **Developer** uses a chosen project folder and a model that advertises tool calling, such as `qwen3-coder:30b`. The agent can inspect text files, propose edits, and run commands. It changes existing files by replacing exact blocks (`edit_file`), so small changes stay small; it writes whole files only for new files or full rewrites. Each edit shows its diff before **Apply change**; each command shows its text and working folder before **Run command**, then asks a second time, because a command runs outside the internet gate. **Reject** declines an action, and **Stop agent** cancels work and pending reviews.

Opening **Developer** starts at a project chooser, with explicit links to resume previous developer conversations. Switching sections keeps the developer session you already opened during this app session. **Files** filters project paths and lets you pin context; double-click a file or press Enter to read it in **Source**. **Changes** retains the agent's applied diffs with the conversation (up to 40,000 characters per diff), and **Undo the AI's last task...** puts back every file that task changed and removes files it created, after you confirm the list. Before the agent's file tools change a file, its original is saved in the data folder (`checkpoints`, outside your project, no git needed; the newest 50 tasks are kept), and if that copy cannot be saved the change is not made. A file you edited after the AI is left as it is, and changes made by commands the agent ran are not covered. **Output** holds generated code, and **Activity** records commands and tool results. Suggested tasks cover understanding code, building features, fixing bugs, running tests, and reviewing code. HTML preview and Vercel deployment are optional actions under **Project > Tools**.

### Ollama + Colibri quality review

In **Developer**, open **Two-engine review...** to let the engines cooperate. Select **Ollama** and a tool-capable worker model in the sidebar, choose **Start & find Colibri models** to select its reviewer, enable **Use both engines for Developer tasks**, and save. Colibri must already have a model configured; its folder is selected under Find & download models. API keys remain session-only in the existing backend settings.

Ollama remains the only tool executor: it implements the request and runs approved checks. Colibri then receives one bounded, read-only review request with the latest requirements, worker summary, recorded command outcomes, and excerpts from up to four changed project files. The review focuses on professional code, project conventions, polished interface design, useful comments and notes, and missing checks. It is labelled advisory and cannot edit, run commands, prove visual correctness, or turn model claims into verification evidence. **Use review feedback** prepares a focused improvement request in your draft; review it and choose **Run agent**. **Give feedback** also accepts your own criteria. Ordinary chats and single-engine tasks keep their existing behavior.

**Release local models between stages** stops a known local Colibri server, releases/reloads only the selected local Ollama worker, then unloads that worker before starting Colibri. The reviewer is stopped afterward so the next worker request can reclaim the GPU. AI Assist does not explicitly unload unrelated Ollama models; Ollama's own scheduler still controls residency and may evict models to fit a request. Remote servers are not stopped. With manually managed local Colibri, choose its folder or disable this option. Setup discovery uses the same explicit handoff; the selected worker reloads on its next request.

This mode is off by default. It bounds cost to one additional review request (at most 768 output tokens and a 120-second HTTP deadline), uses a conservative partial-context budget, and records stage/handoff timings in **Activity**. It is intended to improve review coverage, not guarantee faster or better answers: model loading and review can add time. A failed or cancelled review preserves completed worker changes and native tool history; the worker is never automatically retried. A disconnected remote server may continue inference until it handles the disconnect.

### Feedback and verification

Each answer has **Give feedback**. Describe what should improve and, optionally, what a good result looks like. The app adds a follow-up to your draft, preserving the original answer and any unsent text; review it and choose **Send** or **Run agent**. Typing `feedback` or `/feedback` opens feedback on the latest answer locally. Giving feedback never automatically repeats edits or commands.

In Developer, **Check result** prepares a verification follow-up for review. The command summary uses actual recorded exit outcomes for the latest task, rather than the assistant's claims. It distinguishes successful, failed, and timed-out commands, and warns when files changed after the last command. A successful command is evidence of that command's outcome; it does not establish that every requirement is met. With no recorded command checks, the app says so. Feedback and success criteria are saved as ordinary conversation drafts and follow-ups; command evidence is saved with Activity.

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

Previews use a local HTTP server bound to `127.0.0.1` on an available port. The app does not publish or deploy anything. The browser may load only the preview's own files: pages that rely on a CDN (for example a script or font from the internet) appear without it, so save those assets into the project. Close AI Assist to stop the preview servers and remove temporary HTML. Generated snippets should include their own styles and scripts; save linked assets into a project to preview them together. This is a static preview, so framework builds and backend servers must be run separately.

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
- **Find & download models** opens on **colibri engine (RAM + SSD)**: Colibri's own assessment of this PC (`coli setup --list --json`) with download size, RAM needed, whether a model runs from RAM or streams experts from the SSD, and whether it supports the coding agent's tools, plus a line comparing it with the Ollama (GPU) pick. **Install & use** runs `coli setup --model <id>` (resumable download, engine build, server start). Downloads show speed and estimated time remaining when Colibri reports them. **Context** sets Colibri's context for an install, and **Apply to the current model** restarts it with a new one.
- The first developer-agent request in a session asks for confirmation unless the last answer ran at 15 tokens per second or more; a model family without tool calls is refused before the agent starts.

A server on another computer, or one without a known folder, is used as it runs: its models are listed and chats work, while installs and start/stop stay in Colibri.

Developer mode uses Colibri's OpenAI tool-calling API and keeps AI Assist's existing file/command approval dialogs. Engine support varies (for example, GLM and Qwen support native tools, while some other engines reject tool requests); see [Colibri's API documentation](https://github.com/JustVugg/colibri/blob/main/docs/api.md). Capability flags and native context limits remain unknown when the model API does not advertise them. The developer agent retains a conservative local history/output budget; the server controls actual context allocation. Slow streaming/prefill is allowed up to a 30-minute socket inactivity timeout. Interrupted or truncated responses are reported as failures.

Saved chats record their backend. Existing chats default to Ollama. Opening a chat switches to the engine it was written with and selects its model once that engine answers. Changing backend starts a new chat and preserves the previous one; a switch requested during a background model check applies as soon as the check ends.

### App updates

AI Assist checks GitHub for updates only when you open **Settings > Check for updates...**, and the internet gate asks for your approval first. The check compares your copy with the latest commit on `LucaBeretta/AI_assist`; from the same window you can retry or **Download latest**. Checks run in the background; Git is not required, so downloaded ZIP copies work too.

The checker compares shipped Python modules and Windows launchers with GitHub file hashes, allowing for Windows line endings. Missing or different files are reported as possible updates or local changes, since an edited/development copy can also differ. It checks the repository's default branch rather than only tagged releases. Network errors and GitHub request limits are reported without interrupting chats.

To update, extract the downloaded ZIP into a new folder and start that copy. The checker does not overwrite files. Saved chats remain in the configured data folder; if you set `AI_ASSIST_DATA_DIR`, keep the same setting for the new copy.

`OLLAMA_HOST` optionally sets the Ollama server address; the default is `http://localhost:11434`.

`OLLAMA_MODEL` can name your preferred installed model at startup. Otherwise, the app uses an already running model or the first installed model. You can change the selection in the sidebar.

**Context tokens** in the sidebar sets the requested context for the selected model's next chat or developer request. The choice is saved with the conversation. **Server default** leaves ordinary chat context to Ollama; the developer agent uses a 4096-token fallback. Larger settings allow more history and project context but need more memory. They do not guarantee model support or GPU placement; inspect **Capabilities** and run `ollama ps`. Recommended model cards still apply their conservative 4096-token setting.

## Development and tests

```sh
python -m unittest discover -v
python -m compileall -q .
node --test webui/tests/markdown.test.mjs webui/tests/format.test.mjs   # optional; also run by the suite when Node is installed
python tools/web_e2e.py                                                # optional; real browser + running Ollama with gemma3:270m
```

`tools/web_e2e.py` drives the web workspace in headless Edge or Chrome on a throwaway data folder: a real streamed answer, Stop, dialogs and focus, the developer project view, the agent's refusal of a model without tools, the Day theme, a phone-sized screen, and any script error or Content-Security-Policy violation. With `--agent-model qwen3-coder:30b` (any installed tool-calling model) it also runs real agent tasks: a command is taken to its second check and rejected, and a file edit is reviewed, applied and undone. It needs no packages.

Code layout: `app_core/` holds the rules both interfaces share (providers, conversation, chat service, privacy, settings, the single-instance lock) and imports no UI toolkit; `webui/` is the loopback API and its page; the root modules are the desktop app, the agent, persistence and the internet gate. See `docs/architecture-audit.md` for the current state and `docs/modernization-backlog.md` for what comes next.

Only one AI Assist runs per data folder: a second desktop window, or the web workspace while the window is open, reports which copy holds the folder instead of overwriting its chats.

Tests isolate chat history and project changes in temporary directories. Ollama responses are simulated for reproducible checks; subprocess integration tests also run harmless real Python commands and verify timeout cleanup. Real symlink tests may be skipped when Windows does not grant symlink privileges; resolved-path confinement is also checked without that privilege.

The modules separate workspace UI, atomic chat persistence, project inspection, reviewed agent tools, model metadata, downloads, and hardware recommendations. Blocking network, scanning, and command work runs outside the Tk thread; UI updates arrive through queues. Idle polling is slower than active polling, draft saves skip unchanged data, history uses cached records, and activity output appends incrementally.

History search checks individual fields and stops at the first match, including titles, models, project paths, drafts, and messages. Project text validation uses a compiled control-character check. Model-library HTML traversal is iterative, avoiding recursion limits on deeply nested pages and repeated text copying through ancestor nodes.

### Profiling

Profile creating, listing, searching, opening, and saving synthetic long conversations without Ollama or a display:

```sh
python profile_workflows.py --chats 20 --messages 200
```

This uses a temporary history directory and prints cumulative timings. Add `--output history.prof` to retain profiler data. For actual UI workflows, run `python -m cProfile -o ui.prof AIassist.py`, open a long chat, search history, type a draft, and close normally. Inspect the result with `python -m pstats ui.prof` (`sort cumulative`, then `stats 25`). UI profiling covers the main thread; background work needs separate profiling, as in the synthetic storage workflow. Compare runs with the same workload and hardware; local microbenchmarks do not measure model inference speed.
