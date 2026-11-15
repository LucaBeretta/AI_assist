# AI Assist

A Tkinter desktop chat app for your Ollama models. Choose an installed model at startup, chat in a resizable window, copy replies, and start fresh conversations.

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

- **Enter:** send a message.
- **Shift+Enter:** insert a new line.
- **Ctrl+N:** start a new chat with the current model.
- **Copy:** copy the complete message.

## Configuration

`OLLAMA_HOST` optionally sets the Ollama server address; the default is `http://localhost:11434`.

The GUI always requires a model selection at startup. `OLLAMA_MODEL` only supplies the fallback model when calling the Python `request_ollama()` helper without a model argument; its default is `qwen3:30b`.
