"""Backend selection and Colibri integration for the existing workspace."""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

from colibri_backend import ColibriClient, normalize_url


class BackendMixin:
    def initialize_backend(self):
        self.backend = "Ollama"
        self.colibri_url = "http://127.0.0.1:8000/v1"
        self.colibri_key = os.environ.get("COLI_API_KEY", "")
        self.backend_window = None
        self.backend_settings = self.chat_store.directory / "backend.settings" if self.chat_store else None
        if self.backend_settings:
            try:
                data = json.loads(self.backend_settings.read_text(encoding="utf-8"))
                url = normalize_url(data.get("url", self.colibri_url))
                if data.get("backend") in ("Ollama", "Colibri"):
                    self.backend = data["backend"]
                    self.colibri_url = url
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        if os.environ.get("COLIBRI_URL"):
            self.colibri_url = normalize_url(os.environ["COLIBRI_URL"])

    def colibri_client(self):
        return ColibriClient(self.colibri_url, self.colibri_key)

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
        ttk.Label(window, text="Start Colibri separately with coli serve or START-HERE.bat, then connect here. "
                  "Models are discovered from the running server. Loading, context size, and downloads "
                  "are managed in Colibri. Developer tools require a tool-capable engine.",
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
            if self.download_dialog is not None and not self.download_dialog.closed:
                status.configure(text="Close the Ollama downloads window before changing backend.")
                return
            try:
                selected_url = normalize_url(url.get())
                if "\n" in key.get() or "\r" in key.get():
                    raise ValueError("API keys cannot contain line breaks.")
                if self.backend_settings:
                    temporary = self.backend_settings.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"backend": backend.get(), "url": selected_url}), encoding="utf-8")
                    temporary.replace(self.backend_settings)
            except (ValueError, OSError) as error:
                status.configure(text=str(error))
                return
            self.persist_current()
            self.backend, self.colibri_url, self.colibri_key = backend.get(), selected_url, key.get()
            self.settings_menu.entryconfigure(1, label="AI backend: " + self.backend + "...")
            self.model_name.set("")
            self.active_model = None
            self.loaded_models = set()
            self.model_picker.configure(values=())
            self.recent_downloads.clear()
            self.download_refresh_pending = False
            self.model_contexts.clear()
            self.suspend_save = True
            self.start_new_chat()
            self.capabilities_view.refresh()
            self.manage_models("refresh")
            window.destroy()

        ttk.Button(actions, text="Connect", command=connect).pack(side="right")

    def open_downloads(self):
        if self.backend == "Colibri":
            self.open_backend()
            return
        return super().open_downloads()

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
            for widget in (self.run_all_button, self.stop_all_button, self.context_picker,
                           self.download_models_button):
                widget.configure(state="disabled")
            self.composer_hint.configure(text="Colibri manages context and model loading on its server")

    def model_worker(self, action, selected, contexts=None):
        if self.backend != "Colibri":
            return super().model_worker(action, selected, contexts)
        errors, models = [], []
        try:
            models = self.colibri_client().models()
            if action == "startup" and models:
                selected = models[0]
        except (RuntimeError, ValueError) as error:
            errors.append(str(error))
        self.lifecycle_queue.put(("done", (action, selected, models, set(models), errors)))

    def process_lifecycle(self):
        was_busy = self.lifecycle_busy
        super().process_lifecycle()
        if self.backend == "Colibri" and not self.lifecycle_busy:
            connected = bool(self.loaded_models)
            self.set_connection("Colibri connected" if connected else "Colibri unavailable",
                                self.COLORS["success"] if connected else self.COLORS["error"])
            self.model_help.configure(text="Models served by Colibri. Manage loading and downloads in Colibri.")
            self.model_status.configure(text="\n".join("Served: " + name for name in sorted(self.loaded_models))
                                        or "Start Colibri, then click Refresh.")
            if was_busy:
                self.health_poll_at = time.monotonic() + 60
                if self.model_name.get() and self.model_name.get() not in self.loaded_models:
                    self.model_name.set("")
                    self.active_model = None
                    self.update_controls()

    def fetch_response(self, messages, user_input, model, context_length=None):
        if self.backend != "Colibri":
            return super().fetch_response(messages, user_input, model, context_length)
        request = self.request_serial
        pending = []
        last_update = time.monotonic()

        def chunk(text):
            nonlocal last_update
            pending.append(text)
            if time.monotonic() - last_update >= .05:
                self.workspace_events.put(("chat_chunk", request, "".join(pending), None))
                pending.clear()
                last_update = time.monotonic()

        try:
            response = self.colibri_client().chat(messages, model, on_chunk=chunk,
                on_metrics=lambda data: self.workspace_events.put(("chat_metrics", request, data, None)))
            if pending:
                self.workspace_events.put(("chat_chunk", request, "".join(pending), None))
            self.response_queue.put((user_input, response, None))
        except (RuntimeError, ValueError) as error:
            self.response_queue.put((user_input, None, str(error)))

    def agent_worker(self, request, host, model, root, messages, context, cancel):
        if self.backend != "Colibri":
            return super().agent_worker(request, host, model, root, messages, context, cancel)
        from coding_agent import run_agent

        def event(item):
            self.workspace_events.put(("agent_event", request, item, None))

        def approval(action):
            decision = {"approved": False, "event": threading.Event(), "action": action}
            self.workspace_events.put(("approval", request, decision, None))
            while not decision["event"].wait(.1):
                if cancel.is_set() or self.closed:
                    return False
            if decision["approved"] and not cancel.is_set():
                return ({"approved": True, "destination": decision["destination"]}
                        if decision.get("destination") else True)
            return False

        try:
            client = self.colibri_client()
            result = run_agent(client.url, model, root, messages, event, approval,
                               cancel_event=cancel, context_length=context,
                               chat_fn=client.agent_chat)
            self.workspace_events.put(("agent_done", request, result, None))
        except Exception as error:
            self.workspace_events.put(("agent_done", request, None, str(error)))
