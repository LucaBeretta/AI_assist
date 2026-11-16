# AI Assist

A local Ollama and colibri workspace with saved chats, a developer agent, code previews, model capabilities, and hardware-aware downloads. Built with Python and Tkinter; no pip packages are required.

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

On macOS, double-click **Start AI Assist.command**. It looks for a Python 3 that includes Tkinter (the python.org installer does; with Homebrew also run `brew install python-tk`). If macOS refuses to open it the first time, right-click it and choose **Open**, or run `chmod +x "Start AI Assist.command"`. On Apple silicon (M1 and later) the recommendations count the memory the GPU shares with the system, so Macs get GPU-sized suggestions rather than CPU-only ones.

Choose a model in the sidebar before chatting. Click **Refresh models** after adding models or restarting Ollama. Switching models starts a new conversation.

## Chats and developer workspace

- **New chat** immediately creates and selects a saved entry, clearing any search that would hide it. Messages and drafts save locally. Search, resume, rename, export, or delete chats from the sidebar and **Chat options**. **Chat options > Back up all chats...** writes one .zip with every chat as JSON (copy those files back into the chats folder to restore) and as readable Markdown.
- Answers **stream**: the text appears while the model writes it. While an answer or the agent runs, **Send** becomes **Stop**; stopping keeps the part already written.
- **Developer** uses a chosen project folder and a model that advertises tool calling, such as `qwen3-coder:30b`. The agent can inspect text files, propose edits, and run commands. It changes existing files by replacing exact blocks (`edit_file`), so small changes stay small and quick; it writes whole files only for new files or full rewrites. Each edit shows its diff before **Apply change**; each command shows its text and working folder before **Run command**. **Reject** declines an action, and **Stop agent** cancels work and pending reviews.
- **Files** lets you pin selected project files to context. Generated code appears in **Code**, with explicit copy/save actions. **Activity** records tool results and command output. Native tool history is retained when a developer chat is resumed.
- **Capabilities** distinguishes actual app features from the selected model's reported support. It shows the native context maximum separately from the app's configured context.

Commands run with your account's permissions from the selected project directory; the shell is not an operating-system sandbox. File tools confine edits to the selected project and exclude secrets, binaries, dependencies, and generated folders. Command output, tool steps, context, scan work, and execution time are bounded.

History defaults to `%LOCALAPPDATA%\AI Assist\chats` on Windows, or `~/.aiassist/chats` elsewhere. Set `AI_ASSIST_DATA_DIR` to change it. Each chat has its own atomically replaced JSON file; history is stored outside the repository by default.

## Model downloads

Click **Download models** to browse Ollama's current popular chat models, search the list, and choose an exact download size. The list is fetched live from [Ollama's popular library](https://ollama.com/library?sort=popular); its download counts measure Ollama popularity, not worldwide usage. Model downloads show progress and can be cancelled. After installation, choose **Use in chat** or select the model in the sidebar. Your current conversation stays in place until you switch models.

The **Recommended for this PC** tab reads your CPU, available RAM, graphics card, GPU memory, Ollama version, and model-disk space locally. It matches current download variants to conservative memory estimates. Each recommended model has its own **Download** / **Use in chat** button and **Setup guide** with exact commands and GPU verification steps. **Rescan hardware** updates the report after you unload models or change your setup.

Recommendations distinguish available GPU memory from capacity after unloading other models. Download size is not runtime memory: context cache and overhead also count. **Use in chat** on a recommended card explicitly applies a 4096-token context to loading and chat requests for the current app session. Check `ollama ps` to verify actual GPU placement. Recommendations for a remote Ollama server cannot use your local PC's specifications to establish fit.

Browsing requires internet access, and Ollama must be running to install models. Copy the complete application folder, including all Python modules, when moving the app. The launcher inside this repository starts this copy; the parent-folder launcher also forwards to this repository when present.

- **Enter:** send a message.
- **Shift+Enter:** insert a new line.
- **Ctrl+N:** start a new chat with the current model.
- **Copy:** copy the complete message.

## colibri engine (large models from RAM + SSD)

AI Assist can also use [colibri](https://github.com/JustVugg/colibri), an engine that runs very large mixture-of-experts models (Qwen3-Coder, Qwen3.6, DeepSeek V4, GLM-5.2, Kimi K3...) on an ordinary PC: it keeps the always-used part of the model in RAM and streams the experts from the SSD, with or without a GPU. Answers are slower than a model that fits your GPU in Ollama, but the models can be far larger.

1. Install colibri once: download it from GitHub (**Code > Download ZIP**, or `git clone`) and run `START-HERE.bat` (Windows) or `./start-here.sh` (macOS/Linux).
2. In AI Assist choose **Find & download models > colibri engine (RAM + SSD)**. AI Assist finds colibri in `COLIBRI_DIR`, beside this folder, or in your home, Documents, Desktop, or Downloads folder; otherwise click **Choose colibri folder...** (the folder that contains `c/coli`).
3. The tab shows colibri's own assessment of this PC (`coli setup --list --json`): which models fit, how much RAM and disk each needs, whether it runs from RAM or streams from the SSD, and whether it supports the coding agent's tools. A summary line compares it with the Ollama (GPU) recommendation.
4. **Install & use** runs `coli setup --model <id>`: it downloads the model (resumable: cancel and retry later), prepares the engine for this PC, and starts the server. Then choose **Use in chat**.

Switch engines with **Engine** in the sidebar. colibri serves one model at a time; **Run all** starts it and **Stop all** stops it, and sending a message starts it if it is stopped. Saved chats remember their engine and reopen on it. The **Developer** agent works on colibri with models whose family supports tool calls (Qwen3.6/Qwen3-Coder, Qwen3.8, GLM, DeepSeek V4, Kimi, MiMo). The **Context** picker in the colibri tab sets the context for Install & use, and **Apply to the current model** restarts colibri with a new one (Automatic lets colibri choose). The sidebar shows colibri's measured speed after each answer; because colibri can be slow, the first agent request in a session asks for confirmation unless the last answer ran at 15 tokens per second or more.

Everything stays local: AI Assist runs your own `coli` launcher and talks to colibri's server on `127.0.0.1`. Only colibri's model download uses the internet.

## Configuration

`OLLAMA_HOST` optionally sets the Ollama server address; the default is `http://localhost:11434`.

`COLIBRI_DIR` points to the colibri folder; `COLIBRI_HOST` overrides colibri's server address (default: the one `coli status` reports, normally `http://127.0.0.1:8000`); `COLI_API_KEY` is sent if your colibri server requires one. `AI_ASSIST_ENGINE` (`ollama` or `colibri`) chooses the engine at startup; otherwise the last engine used is kept.

`OLLAMA_MODEL` can name your preferred installed model at startup. Otherwise, the app uses an already running model or the first installed model. You can change the selection in the sidebar.

## Development and tests

```sh
python -m unittest discover -v
python -m compileall -q .
```

Tests isolate chat history and project changes in temporary directories. Ollama responses are simulated for reproducible checks; subprocess integration tests also run harmless real Python commands and verify timeout cleanup. Real symlink tests may be skipped when Windows does not grant symlink privileges; resolved-path confinement is also checked without that privilege.

The modules separate workspace UI, atomic chat persistence, project inspection, reviewed agent tools, model metadata, downloads, and hardware recommendations. Blocking network, scanning, and command work runs outside the Tk thread; UI updates arrive through queues. Idle polling is slower than active polling, draft saves skip unchanged data, history uses cached records, and activity output appends incrementally.
