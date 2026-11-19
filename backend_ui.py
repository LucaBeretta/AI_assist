"""Backend selection and Colibri integration for the existing workspace."""

import os
import time
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from app_core import settings
from colibri_backend import ColibriClient, normalize_url
import colibri_launcher as launcher

DEFAULT_COLIBRI_URL = settings.DEFAULT_COLIBRI_URL


class BackendMixin:
    def initialize_backend(self):
        # The colibri folder lets AI Assist drive colibri's own launcher: its
        # recommendations, installs, context, and starting or stopping the server.
        self.colibri_settings = self.chat_store.directory / "colibri.settings" if self.chat_store else None
        self.colibri_dir = None
        self.colibri_report = None
        self.colibri_agent_confirmed = False
        self.colibri_key = os.environ.get("COLI_API_KEY", "")
        self.backend_window = None
        self.backend_settings = self.chat_store.directory / "backend.settings" if self.chat_store else None
        self.backend, self.colibri_url = settings.load_backend(self.backend_settings, normalize_url)

    def colibri_client(self):
        return ColibriClient(self.colibri_url, self.colibri_key)

    # ------------------------------------------------------------ colibri launcher

    def colibri_directory(self):
        """The colibri folder: the saved choice, $COLIBRI_DIR, or a usual place."""
        if self.colibri_dir is None or launcher.coli_path(self.colibri_dir) is None:
            saved = launcher.load_settings(self.colibri_settings).get("directory") if self.colibri_settings else None
            self.colibri_dir = launcher.find_directory(saved if isinstance(saved, str) else None)
        return self.colibri_dir

    def set_colibri_directory(self, directory):
        self.colibri_dir = launcher.save_directory(directory, self.colibri_settings)
        return self.colibri_dir

    def colibri_manageable(self):
        """True when AI Assist can start and stop colibri: a local server and a known folder."""
        return launcher.is_local_url(self.colibri_url) and self.colibri_directory() is not None

    def colibri_family(self):
        return (self.colibri_report or {}).get("family")

    def adopt_colibri_url(self, report):
        """Follow the port colibri reports, unless the user chose a server URL."""
        url = launcher.server_url(report)
        if url and self.colibri_url == DEFAULT_COLIBRI_URL and not os.environ.get("COLIBRI_URL"):
            self.colibri_url = normalize_url(url)

    def save_backend_settings(self):
        if not self.backend_settings:
            return
        settings.save_backend(self.backend_settings, self.backend, self.colibri_url)

    def switch_backend(self, backend, refresh=True, fresh_chat=True):
        """Change engines: the model list reloads and, by default, a new chat starts."""
        if backend not in ("Ollama", "Colibri"):
            raise ValueError("Choose Ollama or Colibri.")
        if self.busy():
            return False
        if self.download_dialog is not None and not self.download_dialog.closed and backend != self.backend:
            self.download_dialog.close()
        if fresh_chat:
            self.persist_current()
        self.backend = backend
        try:
            self.save_backend_settings()
        except OSError:
            pass
        if hasattr(self, "settings_menu"):
            self.settings_menu.entryconfigure(1, label="AI backend: " + self.backend + "...")
        if hasattr(self, "engine_choice"):
            self.engine_choice.set(self.backend)
        self.model_name.set("")
        self.active_model = None
        self.loaded_models = set()
        self.model_picker.configure(values=())
        self.recent_downloads.clear()
        self.download_refresh_pending = False
        if fresh_chat:
            self.model_contexts.clear()
            self.suspend_save = True
            self.start_new_chat()
        self.set_connection("Connecting to " + self.backend, self.COLORS["muted"])
        self.capabilities_view.refresh()
        if refresh:
            self.manage_models("startup")
        return True

    def select_colibri_model(self, model_id=None):
        """After a colibri install or context change: use colibri and its model."""
        if self.backend == "Colibri":
            if self.busy():
                return False
            self.manage_models("startup")
            return True
        return self.switch_backend("Colibri")

    def confirm_colibri_agent(self):
        """Once per session: the agent asks the model many times, and colibri can be slow."""
        if self.backend != "Colibri" or self.colibri_agent_confirmed:
            return True
        family = self.colibri_family()
        if family and family not in launcher.TOOL_FAMILIES:
            self.set_status(f"This colibri model ({family}) does not support tool calls. Install a Qwen3.6/Qwen3-Coder, "
                            "GLM, DeepSeek V4, Kimi, MiMo or Qwen3.8 model for the coding agent.", error=True)
            return False
        speed = launcher.tokens_per_second(self.colibri_report or {})
        if speed is not None and speed >= 15:
            self.colibri_agent_confirmed = True
            return True
        measured = (f"colibri answered at about {speed:.1f} tokens per second last time. " if speed
                    else "colibri streams large models from the disk, so it can be slow. ")
        if not messagebox.askokcancel(
                "Coding agent on colibri",
                measured + "The coding agent reads files and asks the model several times, so one request "
                "can take many minutes. You can stop it at any time with Stop.\n\nStart the agent?",
                parent=self.root):
            self.set_status("Agent not started. Switch the engine to Ollama for faster agent work.")
            return False
        self.colibri_agent_confirmed = True
        return True

    def open_backend(self):
        if self.backend_window is not None and self.backend_window.winfo_exists():
            self.backend_window.lift()
            return
        window = self.backend_window = tk.Toplevel(self.root)
        window.title("AI backend")
        window.geometry("540x420")
        window.transient(self.root)
        backend = tk.StringVar(window, value=self.backend)
        url = tk.StringVar(window, value=self.colibri_url)
        key = tk.StringVar(window, value=self.colibri_key)
        for label, variable, values in (("Backend", backend, ("Ollama", "Colibri")),
                                        ("Colibri server URL", url, None), ("Colibri API key (optional, session only)", key, None)):
            ttk.Label(window, text=label).pack(anchor="w", padx=22, pady=(12, 4))
            widget = (ttk.Combobox(window, textvariable=variable, values=values, state="readonly") if values
                      else ttk.Entry(window, textvariable=variable, show="*" if variable is key else ""))
            widget.pack(fill="x", padx=22)
        ttk.Label(window, text="Install Colibri once with START-HERE.bat (Windows) or ./start-here.sh. When its folder "
                  "is found and the server is on this computer, AI Assist starts and stops it, and Find & download "
                  "models > colibri engine recommends, installs and configures models. A server on another computer "
                  "is used as it runs. Developer tools require a tool-capable engine.",
                  wraplength=485, justify="left").pack(fill="x", padx=22, pady=14)
        status = ttk.Label(window, wraplength=485)
        status.pack(fill="x", padx=22)
        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=22, pady=12)
        ttk.Button(actions, text="Colibri setup guide", command=lambda: webbrowser.open(
            "https://github.com/JustVugg/colibri#get-started-in-one-step")).pack(side="left")

        def connect():
            if self.busy():
                status.configure(text="Wait for the current operation to finish before changing backend.")
                return
            try:
                selected_url = normalize_url(url.get())
                if "\n" in key.get() or "\r" in key.get():
                    raise ValueError("API keys cannot contain line breaks.")
            except ValueError as error:
                status.configure(text=str(error))
                return
            self.persist_current()
            self.colibri_url, self.colibri_key = selected_url, key.get()
            if backend.get() == self.backend:
                try:
                    self.save_backend_settings()
                except OSError as error:
                    status.configure(text=str(error))
                    return
                self.manage_models("refresh")
            else:
                self.switch_backend(backend.get(), refresh=False)
                self.manage_models("refresh")
            window.destroy()

        ttk.Button(actions, text="Connect", command=connect).pack(side="right")

    def open_downloads(self):
        result = super().open_downloads()
        if self.backend == "Colibri" and self.download_dialog is not None and not self.download_dialog.closed:
            self.download_dialog.show_colibri()
        return result

    def select_recommended_model(self, name, context_length=4096):
        if self.backend == "Colibri":
            self.set_status("Colibri model downloads are managed in its setup launcher.")
            return False
        return super().select_recommended_model(name, context_length)

    def update_controls(self):
        super().update_controls()
        if not hasattr(self, "settings_menu"):
            return
        self.settings_menu.entryconfigure(1, state="disabled" if self.busy() else "normal")
        self.download_models_button.configure(state="disabled" if self.closing else "normal")
        if self.backend == "Colibri":
            self.context_picker.configure(state="disabled")
            if not (launcher.is_local_url(self.colibri_url) and self.colibri_dir):
                for widget in (self.run_all_button, self.stop_all_button):
                    widget.configure(state="disabled")
            if not self.is_waiting:
                self.composer_hint.configure(text="Colibri's context is set in Find & download models > colibri engine")

    def model_worker(self, action, selected, contexts=None):
        if self.backend != "Colibri":
            return super().model_worker(action, selected, contexts)
        progress = lambda text: self.lifecycle_queue.put(("progress", text))
        errors, models = [], []
        try:
            if self.colibri_manageable():
                self.colibri_lifecycle(action, progress, errors)
            if action != "stop_all" or not self.colibri_manageable():
                models = self.colibri_client().models()
            if action in ("startup", "start_one") and models:
                selected = selected if selected in models else models[0]
        except (RuntimeError, ValueError) as error:
            if not errors:
                errors.append(str(error))
        self.lifecycle_queue.put(("done", (action, selected, models, set(models), errors)))

    def colibri_lifecycle(self, action, progress, errors):
        """colibri's launcher: status on refresh, start on startup/start, stop on Stop all."""
        directory = self.colibri_directory()
        if action == "stop_all":
            progress("Stopping colibri...")
            launcher.stop_server(directory)
            self.colibri_report = launcher.status(directory)
            return
        report = launcher.status(directory)
        ready = report.get("configured") and (report.get("setup") or {}).get("ready")
        if action in ("startup", "start_one", "start_all") and ready:
            report = launcher.start_server(directory, progress)
        self.colibri_report = report
        self.adopt_colibri_url(report)
        if not report.get("configured"):
            errors.append("colibri has no model yet. Open Find & download models > colibri engine to pick one for this PC.")
        elif not ready:
            errors.append("colibri's setup did not finish. Open Find & download models > colibri engine to resume it.")

    def process_lifecycle(self):
        was_busy = self.lifecycle_busy
        super().process_lifecycle()
        if self.backend == "Colibri" and not self.lifecycle_busy:
            connected = bool(self.loaded_models)
            self.set_connection("Colibri connected" if connected else "Colibri unavailable",
                                self.COLORS["success"] if connected else self.COLORS["error"])
            speed = launcher.speed_text(self.colibri_report or {})
            if self.colibri_dir and launcher.is_local_url(self.colibri_url):
                help_text = ("Served by colibri, one model at a time" + (f", {speed}." if speed and connected else ".")
                             if connected else "colibri is stopped: Run all or sending a message starts it.")
            else:
                help_text = "Models served by Colibri. Manage loading and downloads in Colibri."
            self.model_help.configure(text=help_text)
            self.model_status.configure(text="\n".join("Served: " + name for name in sorted(self.loaded_models))
                                        or ("Stopped." if self.colibri_dir else "Start Colibri, then click Refresh."))
            if was_busy:
                self.health_poll_at = time.monotonic() + 60
                if self.model_name.get() and self.model_name.get() not in self.loaded_models:
                    self.model_name.set("")
                    self.active_model = None
                    self.update_controls()

    def fetch_response(self, messages, user_input, model, context_length=None, cancel_event=None):
        if self.backend != "Colibri":
            return super().fetch_response(messages, user_input, model, context_length, cancel_event)
        from AIassist import run_stoppable
        chunk, metrics, flush, parts = self.stream_callbacks(cancel_event)

        def request():
            if self.colibri_manageable():
                self.colibri_report = launcher.start_server(self.colibri_directory(), cancel_event=cancel_event)
                self.adopt_colibri_url(self.colibri_report)
            return self.colibri_client().chat(messages, model, on_chunk=chunk, on_metrics=metrics)

        response, error, stopped = run_stoppable(request, parts, cancel_event)
        flush()
        if self.colibri_manageable():
            self.health_poll_at = 0  # refresh colibri's status, and its measured speed
        self.response_queue.put((user_input, response, error, stopped))

    def agent_worker(self, request, host, model, root, messages, context, cancel):
        if self.backend != "Colibri":
            return super().agent_worker(request, host, model, root, messages, context, cancel)
        from coding_agent import run_agent

        def event(item):
            self.workspace_events.put(("agent_event", request, item, None))

        approval = self.agent_approver(request, cancel)
        try:
            if self.colibri_manageable():
                event({"type": "status", "text": "Checking that colibri is running..."})
                self.colibri_report = launcher.start_server(
                    self.colibri_directory(), lambda text: event({"type": "status", "text": text}), cancel_event=cancel)
                self.adopt_colibri_url(self.colibri_report)
            client = self.colibri_client()
            result = run_agent(client.url, model, root, messages, event, approval,
                               cancel_event=cancel, context_length=context,
                               chat_fn=client.agent_chat, file_limit=self.agent_file_limit)
            self.workspace_events.put(("agent_done", request, result, None))
        except Exception as error:
            self.workspace_events.put(("agent_done", request, None, str(error)))
