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

Choose a model in the sidebar before chatting. Click **Refresh models** after adding models or restarting Ollama. Switching models starts a new conversation.

## Chats and developer workspace

- **New chat** immediately creates and selects a saved entry, clearing any search that would hide it. Messages and drafts save locally. Search, resume, rename, export, or delete chats from the sidebar and **Chat options**.
- **Developer** uses a chosen project folder and a model that advertises tool calling, such as `qwen3-coder:30b`. The agent can inspect text files, propose edits, and run commands. Each edit shows its diff before **Apply change**; each command shows its text and working folder before **Run command**. **Reject** declines an action, and **Stop agent** cancels work and pending reviews.
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

## Configuration

`OLLAMA_HOST` optionally sets the Ollama server address; the default is `http://localhost:11434`.

`OLLAMA_MODEL` can name your preferred installed model at startup. Otherwise, the app uses an already running model or the first installed model. You can change the selection in the sidebar.

## Development and tests

```sh
python -m unittest discover -v
python -m compileall -q .
```

Tests isolate chat history and project changes in temporary directories. Ollama responses are simulated for reproducible checks; subprocess integration tests also run harmless real Python commands and verify timeout cleanup. Real symlink tests may be skipped when Windows does not grant symlink privileges; resolved-path confinement is also checked without that privilege.

The modules separate workspace UI, atomic chat persistence, project inspection, reviewed agent tools, model metadata, downloads, and hardware recommendations. Blocking network, scanning, and command work runs outside the Tk thread; UI updates arrive through queues. Idle polling is slower than active polling, draft saves skip unchanged data, history uses cached records, and activity output appends incrementally.
