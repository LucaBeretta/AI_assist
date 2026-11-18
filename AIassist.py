import json
import sys
import http.client
import os
import queue
import threading
import subprocess
import shutil
import time
import tkinter as tk
from tkinter import ttk, END
import urllib.error
import urllib.request
import urllib.parse

from model_downloads import ModelDownloadDialog
from workspace_ui import WorkspaceMixin
from response_metrics import response_metrics
from backend_ui import BackendMixin
from ui_widgets import HoverButton


def ollama_url():
    """Convert Ollama's listening address into a usable client URL."""
    host = os.environ.get("OLLAMA_HOST", "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = f"http://{host}"
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname in ("0.0.0.0", "::"):
        port = parsed.port if parsed.port is not None else 11434
        parsed = parsed._replace(netloc=f"127.0.0.1:{port}")
    return parsed.geturl().rstrip("/")


def list_ollama_models():
    """Read the models installed on the configured Ollama server."""
    host = ollama_url()
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            f"Could not load models from {host}. Start Ollama, then click Refresh."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid model list.") from error

    models = result.get("models") if isinstance(result, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid model list.")
    return sorted({
        model["name"] for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
        and model["name"].strip()
    })


def ollama_api(path, payload=None, timeout=10):
    request = urllib.request.Request(
        ollama_url() + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(str(result))
        return result
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode("utf-8", errors="replace")) from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error


def running_models():
    result = ollama_api("/api/ps")
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    return {m["name"] for m in models if isinstance(m, dict) and "name" in m}


def running_details():
    """Per running model: its size, the part in GPU memory, and its context length
    when Ollama reports one (``/api/ps``). Used by the status bar."""
    result = ollama_api("/api/ps")
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    details = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue
        number = lambda key: model.get(key) if type(model.get(key)) is int and model.get(key) >= 0 else None
        details[model["name"]] = {"size": number("size") or 0, "size_vram": number("size_vram") or 0,
                                  "context_length": number("context_length")}
    return details


def ensure_ollama(progress):
    try:
        running_models()
        return
    except RuntimeError:
        pass
    host = urllib.parse.urlsplit(ollama_url()).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("The configured remote Ollama server is offline. Start it on that computer.")
    executable = shutil.which("ollama")
    if not executable:
        candidate = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(candidate):
            executable = candidate
    if not executable:
        raise RuntimeError("Ollama is not installed. Install Ollama, then click Refresh models.")
    progress("Starting Ollama...")
    subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(40):
        try:
            running_models()
            return
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError("Ollama did not become ready. Check Ollama and click Refresh models.")


def set_model_running(model, start=True, context_length=None):
    payload = {"model": model, "stream": False, "keep_alive": -1 if start else 0}
    if start and context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    ollama_api("/api/generate", payload, timeout=300)


def request_ollama(messages, model=None, context_length=None, on_chunk=None, on_metrics=None):
    """Send the conversation to the local Ollama chat API."""
    host = ollama_url()
    model = model or os.environ.get("OLLAMA_MODEL", "qwen3:30b")
    payload = {"model": model, "messages": messages, "stream": True, "keep_alive": -1}
    if context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            parts = []
            received = 0
            while True:
                line = response.readline(1024 * 1024 + 1)
                if not line:
                    raise RuntimeError("The response connection closed before Ollama finished. Please retry.")
                received += len(line)
                if len(line) > 1024 * 1024 or received > 16 * 1024 * 1024:
                    raise RuntimeError("Ollama returned too much chat output.")
                if not line.strip():
                    continue
                result = json.loads(line)
                if not isinstance(result, dict):
                    raise RuntimeError("Ollama returned an invalid response.")
                if result.get("error"):
                    raise RuntimeError(f"Ollama could not complete the response: {result['error']}")
                message = result.get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else None
                if not isinstance(content, str):
                    raise RuntimeError("Ollama returned an invalid message.")
                if content:
                    parts.append(content)
                    if on_chunk:
                        on_chunk(content)
                if result.get("done") is True:
                    if on_metrics:
                        on_metrics(response_metrics(result))
                    break
    except urllib.error.HTTPError as error:
        try:
            details = error.read(2000).decode("utf-8", errors="replace").strip()
        finally:
            error.close()
        raise RuntimeError(
            f"Ollama returned HTTP {error.code}: {details or error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not connect to Ollama at {host}. Start Ollama or run 'ollama serve', then try again."
        ) from error
    except TimeoutError as error:
        raise RuntimeError("The Ollama request timed out.") from error
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Ollama returned an invalid response.") from error
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"The Ollama connection failed: {error}") from error

    content = "".join(parts)
    if not content.strip():
        raise RuntimeError("Ollama's response did not contain a message.")
    return content.strip()

class StreamStopped(Exception):
    """Raised inside a stream callback to stop reading; the connection closes,
    which tells Ollama or Colibri to stop generating."""


def run_stoppable(request, parts, cancel_event):
    """Run ``request()`` on its own thread. Returns (answer, error, stopped).

    On Stop the caller gets the text received so far at once; the reader
    thread ends at its next chunk, when the stream callback raises."""
    if cancel_event is None:
        try:
            return request(), None, False
        except (RuntimeError, ValueError) as error:
            return None, str(error), False
    finished = queue.Queue(maxsize=1)

    def worker():
        try:
            finished.put((request(), None))
        except StreamStopped:
            finished.put((None, "stopped"))
        except (RuntimeError, ValueError, OSError) as error:
            finished.put((None, str(error)))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        if cancel_event.is_set():
            return "".join(parts), None, True
        try:
            answer, error = finished.get(timeout=.1)
        except queue.Empty:
            continue
        if error == "stopped" or cancel_event.is_set():
            return "".join(parts), None, True
        return answer, error, False


class OllamaAssistantBase:
    COLORS = {
        "background": "#f5f6fa",
        "surface": "#ffffff",
        "sidebar": "#171e2d",
        "sidebar_hover": "#2b354a",
        "sidebar_muted": "#9aa8bf",
        "ink": "#243047",
        "muted": "#65738b",
        "border": "#e1e6ef",
        "accent": "#4c61d8",
        "accent_hover": "#3c4fbd",
        "user": "#edf0ff",
        "success": "#51c7a4",
        "error": "#ad473e",
        "status": "#e9eef5",
        "amber": "#8f5410",
        "on_accent": "#ffffff",
    }
    # Plain functions the status bar's poll thread can call without touching Tk.
    # Resolved at call time, so tests can patch the module functions.
    running_details = staticmethod(lambda: running_details())
    ollama_url = staticmethod(lambda: ollama_url())

    def __init__(self, root):
        self.root = root
        self.conversation = []
        self.response_queue = queue.Queue()
        self.model_queue = queue.Queue()
        self.lifecycle_queue = queue.Queue()
        self.lifecycle_busy = False
        self.loaded_models = set()
        self.startup_pending = True
        self.health_poll_at = 0
        self.model_name = tk.StringVar(root, value="")
        self.model_contexts = {}
        self.active_model = None
        self.is_loading_models = False
        self.is_waiting = False
        self.empty_state = None
        self.closed = False
        self.download_dialog = None
        self.retired_dialogs = []
        self.download_refresh_pending = False
        self.recent_downloads = set()
        self.root.title("AI Assist | Your local workspace")
        self.root.geometry("1120x780")
        self.root.minsize(860, 640)
        self.root.configure(bg=self.COLORS["background"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.configure_styles()
        self.build_sidebar()
        self.build_workspace()
        self.show_empty_state()
        self.show_loading()
        self.root.after(100, lambda: self.manage_models("startup"))
        self.poll_id = self.root.after(100, self.process_responses)
        self.root.bind("<Control-n>", self.new_chat)
        self.root.bind_all("<MouseWheel>", self.scroll_messages, add="+")

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        c = self.COLORS
        style.configure(
            "Model.TCombobox", padding=(10, 7), arrowsize=12,
            fieldbackground=c["surface"], background=c["sidebar"],
            foreground=c["ink"], arrowcolor=c["muted"],
            bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
        )
        style.map(
            "Model.TCombobox",
            fieldbackground=[("disabled", c["sidebar"]), ("readonly", c["surface"])],
            foreground=[("disabled", c["muted"]), ("readonly", c["ink"])],
            selectbackground=[("readonly", c["surface"])],
            selectforeground=[("readonly", c["ink"])],
            bordercolor=[("focus", c["accent"])],
        )
        style.configure(
            "Chat.Vertical.TScrollbar", background=c["border"], troughcolor=c["background"],
            borderwidth=0, width=8, gripcount=0, troughborderwidth=0,
            lightcolor=self.COLORS["background"], darkcolor=self.COLORS["background"],
            bordercolor=self.COLORS["background"],
        )
        style.layout("Chat.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.map("Chat.Vertical.TScrollbar", background=[("active", c["muted"])])
        style.configure(
            "Sidebar.Vertical.TScrollbar", background=c["border"], troughcolor=c["sidebar"],
            borderwidth=0, width=6, gripcount=0, troughborderwidth=0,
            lightcolor=self.COLORS["sidebar"], darkcolor=self.COLORS["sidebar"],
            bordercolor=self.COLORS["sidebar"],
        )
        style.layout("Sidebar.Vertical.TScrollbar", style.layout("Chat.Vertical.TScrollbar"))
        style.configure("Code.Horizontal.TScrollbar", background=c["border"],
                        troughcolor=c.get("code_background", c["background"]),
                        borderwidth=0, width=8, gripcount=0)
        style.layout("Code.Horizontal.TScrollbar", [
            ("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
                ("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.map("Code.Horizontal.TScrollbar", background=[("active", c["muted"])])
        style.configure(
            "Activity.Horizontal.TProgressbar", background=self.COLORS["accent"],
            troughcolor=c["sidebar_hover"], borderwidth=0, thickness=3,
        )
        self.root.option_add("*TCombobox*Listbox.background", c["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    def button(self, parent, text, command, bg, fg, hover, **options):
        button = HoverButton(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            disabledforeground=self.COLORS["muted"], font=("Segoe UI", 10),
            relief="flat", borderwidth=0, highlightthickness=0,
            cursor="hand2", padx=14, pady=10, **options,
        )
        return button




    def resize_chat(self, event):
        self.chat_canvas.itemconfigure(self.chat_window, width=event.width)

    def scroll_messages(self, event):
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            # Combobox popdowns are native Tk widgets, not Python widget objects.
            return None
        while widget is not None:
            if widget == self.sidebar_canvas or widget == self.sidebar_content:
                self.sidebar_canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            if widget == self.chat_canvas or widget == self.message_frame:
                self.chat_canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = widget.master


    def use_suggestion(self, text):
        if self.is_waiting or self.is_loading_models or self.lifecycle_busy or not self.model_name.get():
            return
        self.input_box.delete("1.0", END)
        self.input_box.insert("1.0", text)
        self.input_box.focus_set()

    def clear_messages(self):
        for child in self.message_frame.winfo_children():
            child.destroy()
        self.empty_state = None
        self.suggestion_buttons = []
        self.chat_canvas.yview_moveto(0)

    def new_chat(self, event=None):
        if self.is_waiting or self.is_loading_models or self.lifecycle_busy:
            return "break"
        self.conversation.clear()
        self.input_box.config(state="normal")
        self.input_box.delete("1.0", END)
        self.input_box.edit_reset()
        self.clear_messages()
        self.show_empty_state()
        self.chat_subtitle.config(text=(f"Chatting with {self.model_name.get()}" if self.model_name.get()
                                       else "A little space to think, create, and explore."))
        self.set_status("Ready when you are." if self.model_name.get() else "Choose a model to begin.")
        self.update_controls()
        if self.model_name.get():
            self.input_box.focus_set()
        return "break"

    def update_controls(self):
        busy = self.is_waiting or self.is_loading_models or self.lifecycle_busy
        ready = bool(self.model_name.get()) and not busy
        self.model_picker.config(state="disabled" if busy else "readonly")
        self.run_all_button.config(state="disabled" if busy else "normal")
        self.stop_all_button.config(state="disabled" if busy else "normal")
        self.refresh_button.config(state="disabled" if busy else "normal")
        self.new_chat_button.config(state="disabled" if busy else "normal")
        self.input_box.config(state="normal" if ready else "disabled")
        self.send_button.config(
            state="normal" if ready else "disabled",
            text="Working..." if self.is_waiting else "Send  \u2192",
            bg=self.COLORS["accent"] if ready else "#e5e9f2",
        )
        self.composer_hint.config(text=("Enter to send \u00b7 Shift+Enter for a new line" if self.model_name.get()
                                       else "Choose a model to start chatting"))
        for button in getattr(self, "suggestion_buttons", []):
            button.config(state="normal" if ready else "disabled")

    @staticmethod
    def ollama_host():
        return ollama_url()

    def open_downloads(self):
        if self.download_dialog is not None and not self.download_dialog.closed:
            self.download_dialog.window.lift()
            return
        if self.download_dialog is not None:
            # Closed dialogs stay referenced until the app exits: a worker that
            # outlives one must never drop the last reference to its Tk objects,
            # because Tk can only free them on the main thread.
            self.retired_dialogs.append(self.download_dialog)
        self.download_dialog = ModelDownloadDialog(self)

    def model_downloaded(self, name):
        self.recent_downloads.add(name)
        self.download_refresh_pending = True
        models = sorted(set(self.model_picker["values"]) | {name})
        self.model_picker.config(values=models)
        self.model_status.config(text="\n".join(
            f"{'Running' if model in self.loaded_models else 'Stopped'}: {model}" for model in models))
        self.model_help.config(text=f"{len(models)} installed, {len(self.loaded_models)} running. Models share available memory.")
        self.refresh_after_download()

    def select_recommended_model(self, name, context_length=4096):
        if self.is_waiting or self.is_loading_models or self.lifecycle_busy:
            return False
        if type(context_length) is not int or not 1 <= context_length <= 131072:
            raise ValueError("Choose a valid context length.")
        self.model_contexts[name] = context_length
        if self.active_model == name:
            self.manage_models("start_one", name)
        else:
            self.model_name.set(name)
            self.select_model()
        return True

    def merged_model_names(self, models):
        # A pre-download snapshot may still be in flight. Keep completed names
        # until a later authoritative snapshot actually includes them.
        names = set(models)
        self.recent_downloads.difference_update(names)
        return sorted(names | self.recent_downloads)

    def refresh_after_download(self):
        if (self.download_refresh_pending and not self.startup_pending
                and not self.is_waiting and not self.is_loading_models and not self.lifecycle_busy):
            self.refresh_models()

    def set_status(self, text, error=False):
        self.status_label.config(text=text, fg=self.COLORS["error"] if error else self.COLORS["muted"])

    def set_connection(self, text, color):
        self.connection_label.config(text=text)
        self.connection_dot.config(fg=color)

    def show_loading(self):
        self.loading = tk.Toplevel(self.root)
        self.loading.title("Starting AI Assist")
        self.loading.geometry("460x190")
        self.loading.resizable(False, False)
        self.loading.transient(self.root)
        self.loading.protocol("WM_DELETE_WINDOW", self.close)
        tk.Label(self.loading, text="Getting your local AI ready", font=("Segoe UI", 16, "bold")).pack(pady=(24, 12))
        self.loading_text = tk.Label(self.loading, text="Checking Ollama...", wraplength=420)
        self.loading_text.pack()
        self.loading_bar = ttk.Progressbar(self.loading, mode="indeterminate", length=380)
        self.loading_bar.pack(pady=20)
        self.loading_bar.start()

    def refresh_models(self):
        self.manage_models("refresh")

    def manage_models(self, action, selected=None):
        if self.is_waiting or self.is_loading_models or self.lifecycle_busy:
            return
        if action == "refresh":
            self.download_refresh_pending = False
        self.lifecycle_busy = True
        self.update_controls()
        self.set_status("Checking local models...")
        threading.Thread(target=self.model_worker, args=(action, selected, dict(self.model_contexts)), daemon=True).start()

    def model_worker(self, action, selected, contexts=None):
        contexts = contexts or {}
        progress = lambda text: self.lifecycle_queue.put(("progress", text))
        errors = []
        models, loaded = [], set()
        try:
            ensure_ollama(progress)
            models = list_ollama_models()
            loaded = running_models()
            if action == "startup" and models:
                preferred = os.environ.get("OLLAMA_MODEL")
                selected = preferred if preferred in models else next(
                    (m for m in models if m in loaded), models[0])
            targets = models if action == "start_all" else (
                sorted(loaded) if action == "stop_all" else (
                    [selected] if action in ("startup", "start_one") and selected else []))
            for index, model in enumerate(targets, 1):
                starting = action != "stop_all"
                if starting and model in loaded and model not in contexts:
                    continue
                progress(f"{'Loading' if starting else 'Stopping'} {model} ({index}/{len(targets)})...")
                try:
                    if starting and model in contexts:
                        set_model_running(model, starting, context_length=contexts[model])
                    else:
                        set_model_running(model, starting)
                except RuntimeError as error:
                    errors.append(f"{model}: {error}")
            loaded = running_models()
            if action == "start_all":
                missing = set(models) - loaded
                if missing:
                    errors.append("Not all models fit in memory or stayed loaded: " + ", ".join(sorted(missing)))
            elif action == "stop_all" and loaded:
                errors.append("Still running: " + ", ".join(sorted(loaded)))
            elif action in ("startup", "start_one") and selected and selected not in loaded:
                errors.append(f"{selected} is not loaded. Try a smaller model.")
        except (RuntimeError, OSError, ValueError) as error:
            errors.append(str(error))
        self.lifecycle_queue.put(("done", (action, selected, models, loaded, errors)))

    def process_lifecycle(self):
        while True:
            try:
                kind, value = self.lifecycle_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "progress":
                self.set_status(value)
                if self.startup_pending:
                    self.loading_text.config(text=value)
                continue
            action, selected, models, loaded, errors = value
            models = self.merged_model_names(models)
            self.lifecycle_busy = False
            self.loaded_models = loaded
            self.model_picker.config(values=models)
            if selected in models and selected != self.model_name.get():
                self.model_name.set(selected)
                self.active_model = selected
                self.new_chat()
            self.model_status.config(text="\n".join(
                f"{'Running' if m in loaded else 'Stopped'}: {m}" for m in models) or "No installed models found.")
            self.model_help.config(text=f"{len(models)} installed, {len(loaded)} running. Models share available memory.")
            self.set_connection("Ollama connected" if models else "Check Ollama", self.COLORS["success"] if models else "#efa396")
            if self.startup_pending:
                self.startup_pending = False
                self.loading_bar.stop()
                self.loading.destroy()
            if errors:
                self.set_status("Model setup needs attention.", error=True)
                self.display_message("Model status", "\n".join(errors), "error")
            else:
                self.set_status("All models stopped. Sending a message will load the selected model." if action == "stop_all"
                                else "Local models checked. Ready when you are.")
            self.health_poll_at = time.monotonic() + 15
            self.update_controls()

    def fetch_models(self):
        try:
            self.model_queue.put((list_ollama_models(), None))
        except (RuntimeError, ValueError) as error:
            self.model_queue.put(([], str(error)))

    def process_models(self):
        try:
            models, error = self.model_queue.get_nowait()
        except queue.Empty:
            return
        self.is_loading_models = False
        if error:
            self.set_status("Could not load models. Click Refresh models to retry.", error=True)
            self.set_connection("Connection issue", "#efa396")
            self.display_message("Connection issue", error, "error")
        else:
            models = self.merged_model_names(models)
            self.model_picker.config(values=models)
            self.set_connection("Ollama connected", self.COLORS["success"])
            if self.model_name.get() not in models:
                had_model = self.active_model is not None
                self.model_name.set("")
                self.active_model = None
                self.conversation.clear()
                if had_model:
                    self.new_chat()
            if not models:
                self.set_status("No models installed. Choose Download models to install one.")
            elif self.model_name.get():
                self.set_status("Ready when you are.")
            else:
                self.set_status("Choose a model in the sidebar to begin.")
                self.model_picker.focus_set()
            self.model_help.config(text=f"{len(models)} installed model{'s' if len(models) != 1 else ''}. Switching starts a fresh chat.")
        self.update_controls()

    def select_model(self, event=None):
        if self.is_waiting or self.is_loading_models or self.lifecycle_busy or self.model_name.get() == self.active_model:
            return
        self.active_model = self.model_name.get()
        self.new_chat()
        self.manage_models("start_one", self.model_name.get())


    def copy_message(self, message, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(message)
        button.config(text="Copied")
        self.root.after(1600, lambda: button.config(text="Copy") if button.winfo_exists() else None)

    def handle_enter(self, event):
        if event.state & 0x0001:
            return None
        return self.send_prompt(event)


    def stream_callbacks(self, cancel_event=None):
        """Chunk and metrics callbacks for one streamed answer, plus the list of
        received parts (the partial answer if the user presses Stop)."""
        request_id = self.request_serial
        pending, parts = [], []
        last_update = [time.monotonic()]

        def chunk(text):
            if cancel_event is not None and cancel_event.is_set():
                raise StreamStopped()
            parts.append(text)
            pending.append(text)
            if time.monotonic() - last_update[0] >= .05:
                self.workspace_events.put(("chat_chunk", request_id, "".join(pending), None))
                pending.clear()
                last_update[0] = time.monotonic()

        def flush():
            if pending:
                self.workspace_events.put(("chat_chunk", request_id, "".join(pending), None))
                pending.clear()

        def metrics(data):
            self.workspace_events.put(("chat_metrics", request_id, data, None))

        return chunk, metrics, flush, parts

    def fetch_response(self, messages, user_input, model, context_length=None, cancel_event=None):
        chunk, metrics, flush, parts = self.stream_callbacks(cancel_event)
        options = dict(on_chunk=chunk, on_metrics=metrics)
        if context_length is not None:
            options["context_length"] = context_length
        response, error, stopped = run_stoppable(lambda: request_ollama(messages, model, **options), parts, cancel_event)
        flush()
        self.response_queue.put((user_input, response, error, stopped))


    def close(self):
        self.closed = True
        if hasattr(self, "update_checker"):
            self.update_checker.close()
        if self.download_dialog is not None:
            self.download_dialog.close(force=True)
        if hasattr(self, "poll_id"):
            self.root.after_cancel(self.poll_id)
        self.activity.stop()
        self.root.destroy()


class AIAssistantGUI(BackendMixin, WorkspaceMixin, OllamaAssistantBase):
    """Saved chats, developer tools, and capabilities over the Ollama runtime."""


if __name__ == "__main__":
    from gui_runtime import GUIRuntimeError, create_root, redirect_missing_streams
    redirect_missing_streams()
    if "--web" in sys.argv[1:]:
        from webui.app import main
        raise SystemExit(main())
    try:
        root = create_root()
    except GUIRuntimeError as error:
        raise SystemExit(f"AI Assist could not start: {error}") from None
    app = AIAssistantGUI(root)
    root.mainloop()
