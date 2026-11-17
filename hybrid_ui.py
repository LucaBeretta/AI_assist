"""Optional Ollama implementation followed by a bounded Colibri quality review."""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk
import urllib.request

from coding_agent import AgentCancelled
from colibri_backend import ColibriClient
import colibri_launcher as launcher


DEFAULTS = {"enabled": False, "review_model": "", "manage_memory": True}
QUALITY_GUIDANCE = (
    "Produce professional, maintainable code that follows this project's conventions. "
    "For interfaces, consider visual hierarchy, spacing, typography, contrast, responsive layouts, "
    "and loading, empty, and error states. Use comments to explain non-obvious decisions rather "
    "than restating code. Add concise setup or usage notes where needed. Use relevant tests; "
    "do not claim visual verification from source code alone. A separate read-only reviewer "
    "will assess the result; its suggestions do not replace user approval or actual checks."
)


def _cancelled(cancel):
    if cancel.is_set():
        raise AgentCancelled("Two-engine work stopped.")


def unload_worker(host, model):
    """Release only the selected worker on its configured Ollama server."""
    payload = {"model": model, "stream": False, "keep_alive": 0}
    request = urllib.request.Request(host.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise RuntimeError("Ollama returned too much unload output.")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("error") or result.get("done") is not True:
        raise RuntimeError("Ollama did not confirm that the selected worker was unloaded.")


def memory_callbacks(snapshot, host, model, cancel):
    """Capture provider settings; never stop remote or unknown Colibri servers."""
    state = {"url": snapshot["url"], "start_attempted": False}
    managed = launcher.is_local_url(snapshot["url"]) and bool(snapshot["directory"])
    handoff = snapshot["manage_memory"] and managed and launcher.is_local_url(host)

    def before_worker():
        _cancelled(cancel)
        if handoff:
            launcher.stop_server(snapshot["directory"])
            _cancelled(cancel)
            # A worker loaded while Colibri owned the GPU can remain on CPU.
            # Reload only that worker after releasing Colibri's allocation.
            unload_worker(host, model)
        _cancelled(cancel)

    def before_review():
        _cancelled(cancel)
        if handoff:
            unload_worker(host, model)
        _cancelled(cancel)
        if managed:
            state["start_attempted"] = True
            report = launcher.start_server(snapshot["directory"], timeout=300, cancel_event=cancel)
            if snapshot["follow_report"]:
                state["url"] = launcher.server_url(report) or state["url"]
        _cancelled(cancel)

    def after_review():
        if handoff and state["start_attempted"]:
            launcher.stop_server(snapshot["directory"])

    return state, before_worker, before_review, after_review


def discover_worker(events, snapshot, host, model, cancel):
    """Load/list the reviewer only after an explicit setup action; no Tk objects."""
    state, _, prepare, cleanup = memory_callbacks(snapshot, host, model, cancel)
    result, error = None, None
    try:
        prepare()
        models = ColibriClient(state["url"], snapshot["key"]).models()
        _cancelled(cancel)
        result = {"models": models, "url": state["url"]}
    except Exception as failure:
        error = str(failure)
    finally:
        try:
            cleanup()
        except Exception as failure:
            error = (error + "\n" if error else "") + f"Could not release Colibri: {failure}"
        events.put((result, error))


class HybridMixin:
    def initialize_hybrid(self):
        self.hybrid_settings = self.chat_store.directory / "hybrid.settings" if self.chat_store else None
        self.hybrid_config = dict(DEFAULTS)
        self.hybrid_window = None
        self.hybrid_preparing = False
        self.hybrid_cancel = threading.Event()
        self.hybrid_events = queue.Queue()
        self.active_hybrid_config = None
        if self.hybrid_settings:
            try:
                data = json.loads(self.hybrid_settings.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("enabled", "manage_memory"):
                        if type(data.get(key)) is bool:
                            self.hybrid_config[key] = data[key]
                    model = data.get("review_model")
                    if isinstance(model, str) and len(model) <= 256 and not any(c in model for c in "\r\n"):
                        self.hybrid_config["review_model"] = model.strip()
            except (OSError, ValueError):
                pass

    def hybrid_snapshot(self, config=None, require_model=True):
        config = config or self.hybrid_config
        reviewer = config["review_model"].strip()
        if require_model and (not reviewer or len(reviewer) > 256 or any(c in reviewer for c in "\r\n")):
            raise ValueError("Choose a Colibri reviewer in Two-engine review first.")
        directory = self.colibri_directory()
        if (config["manage_memory"] and launcher.is_local_url(self.colibri_url)
                and launcher.is_local_url(self.ollama_host()) and not directory):
            raise ValueError("Choose the local Colibri folder in Find & download models, or turn off automatic memory handoff for a manually managed server.")
        return dict(config, review_model=reviewer, url=self.colibri_url, key=self.colibri_key,
                    directory=directory, follow_report=self.colibri_url == "http://127.0.0.1:8000/v1"
                    and not os.environ.get("COLIBRI_URL"))

    def capture_hybrid_request(self):
        self.active_hybrid_config = None
        if self.current_mode == "dev" and self.backend == "Ollama" and self.hybrid_config["enabled"]:
            self.active_hybrid_config = self.hybrid_snapshot()

    def run_with_quality_review(self, worker, messages, project, event, cancel, host, model, snapshot):
        from hybrid_engine import run_hybrid
        state, before, prepare, cleanup = memory_callbacks(snapshot, host, model, cancel)

        def review(review_messages):
            return ColibriClient(state["url"], snapshot["key"]).review(
                review_messages, snapshot["review_model"], cancel, max_tokens=768, timeout=120)

        return run_hybrid(worker, review, messages, project, event, cancel,
                          before_worker=before, before_review=prepare, after_review=cleanup)

    def update_hybrid_controls(self):
        if not hasattr(self, "hybrid_button"):
            return
        enabled = self.hybrid_config["enabled"]
        label = ("Ollama builds · Colibri reviews" if self.backend == "Ollama"
                 else "Select Ollama to use two-engine review") if enabled else "One engine per task"
        self.hybrid_label.config(text=label)
        self.hybrid_button.config(state="disabled" if self.busy() else "normal")

    def open_hybrid(self):
        if self.busy():
            return
        if self.hybrid_window is not None and self.hybrid_window.winfo_exists():
            self.hybrid_window.lift()
            return
        c = self.COLORS
        window = self.hybrid_window = tk.Toplevel(self.root)
        window.title("Ollama + Colibri")
        window.geometry("580x600")
        window.minsize(520, 560)
        window.configure(bg=c["background"])
        window.transient(self.root)
        tk.Label(window, text="Build, then get a second opinion", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 17, "bold"), anchor="w").pack(fill="x", padx=24, pady=(22, 10))
        tk.Label(window, text="Ollama implements and runs approved checks. Colibri independently reviews the result for polished design, professional code, useful comments, and missing checks. Review suggestions can become your next feedback request.",
                 bg=c["background"], fg=c["muted"], font=("Segoe UI", 10),
                 wraplength=520, justify="left").pack(fill="x", padx=24, pady=(0, 12))
        window.enabled = tk.BooleanVar(window, value=self.hybrid_config["enabled"])
        window.memory = tk.BooleanVar(window, value=self.hybrid_config["manage_memory"])
        window.reviewer = tk.StringVar(window, value=self.hybrid_config["review_model"])
        for text, variable in (("Use both engines for Developer tasks", window.enabled),
                               ("Release local models between stages", window.memory)):
            tk.Checkbutton(window, text=text, variable=variable, bg=c["background"], fg=c["ink"],
                           activebackground=c["background"], activeforeground=c["ink"],
                           selectcolor=c["surface"], font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=4)
        tk.Label(window, text="Ollama worker: " + (self.model_name.get() if self.backend == "Ollama" else "select Ollama in the sidebar"),
                 bg=c["background"], fg=c["ink"], font=("Segoe UI", 10),
                 anchor="w", wraplength=520).pack(fill="x", padx=24, pady=(12, 8))
        tk.Label(window, text="Colibri reviewer", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=24, pady=(4, 6))
        window.picker = ttk.Combobox(window, textvariable=window.reviewer, font=("Segoe UI", 10))
        window.picker.pack(fill="x", padx=24)
        window.discover = self.button(window, "Start & find Colibri models", self.discover_hybrid_models,
                                       c["surface"], c["ink"], c["user"])
        window.discover.pack(anchor="w", padx=24, pady=(8, 12))
        tk.Label(window, text="Memory handoff releases the selected local Ollama worker and managed Colibri server. Remote servers are never stopped; other models follow Ollama's own memory policy. One bounded review is added per task; loading and review can add time.",
                 bg=c["background"], fg=c["muted"], font=("Segoe UI", 9), wraplength=520,
                 justify="left").pack(fill="x", padx=24)
        window.status = tk.Label(window, text="", bg=c["background"], fg=c["error"],
                                 font=("Segoe UI", 9), wraplength=520, anchor="w", justify="left")
        window.status.pack(fill="x", padx=24, pady=(10, 0))
        actions = tk.Frame(window, bg=c["background"])
        actions.pack(side="bottom", fill="x", padx=24, pady=20)

        def save():
            if self.busy():
                window.status.config(text="Wait for the current operation to finish.")
                return
            config = dict(enabled=window.enabled.get(), review_model=window.reviewer.get().strip(),
                          manage_memory=window.memory.get())
            try:
                if len(config["review_model"]) > 256 or any(c in config["review_model"] for c in "\r\n"):
                    raise ValueError("Choose a reviewer model ID of at most 256 characters, without line breaks.")
                if config["enabled"]:
                    if self.backend != "Ollama" or not self.model_name.get():
                        raise ValueError("Choose Ollama and a tool-capable worker model in the sidebar first.")
                    self.hybrid_snapshot(config)
                if self.hybrid_settings:
                    temporary = self.hybrid_settings.with_suffix(".tmp")
                    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
                    temporary.replace(self.hybrid_settings)
                self.hybrid_config = config
            except (ValueError, OSError) as error:
                window.status.config(text=str(error))
                return
            self.update_hybrid_controls()
            self.close_hybrid()

        window.save_button = self.button(actions, "Save", save, c["accent"], "white", c["accent_hover"])
        window.save_button.pack(side="right")
        self.button(actions, "Cancel", self.close_hybrid, c["background"], c["muted"], c["user"]).pack(side="right", padx=10)
        window.protocol("WM_DELETE_WINDOW", self.close_hybrid)

    def close_hybrid(self):
        self.hybrid_cancel.set()
        if self.hybrid_window is not None and self.hybrid_window.winfo_exists():
            self.hybrid_window.destroy()
        self.hybrid_window = None

    def discover_hybrid_models(self):
        window = self.hybrid_window
        if self.busy() or window is None:
            return
        if self.backend != "Ollama" or not self.model_name.get():
            window.status.config(text="Choose Ollama and a worker model in the sidebar first.")
            return
        try:
            snapshot = self.hybrid_snapshot(dict(enabled=False, review_model="", manage_memory=window.memory.get()),
                                            require_model=False)
        except ValueError as error:
            window.status.config(text=str(error))
            return
        self.hybrid_cancel = threading.Event()
        self.hybrid_preparing = True
        window.discover.config(state="disabled")
        window.status.config(text="Preparing Colibri and reading its model list...")
        self.set_status("Preparing the Colibri reviewer...")
        self.update_controls()
        threading.Thread(target=discover_worker, args=(self.hybrid_events, snapshot, self.ollama_host(),
                         self.model_name.get(), self.hybrid_cancel), daemon=True).start()

    def process_hybrid_events(self):
        try:
            result, error = self.hybrid_events.get_nowait()
        except queue.Empty:
            return
        self.hybrid_preparing = False
        if error:
            self.set_status("Reviewer setup cancelled." if self.hybrid_cancel.is_set() else error,
                            error=not self.hybrid_cancel.is_set())
        else:
            self.set_status("Colibri reviewer models are ready. Save the two-engine settings to use them.")
        window = self.hybrid_window
        if window is not None and window.winfo_exists():
            window.discover.config(state="normal")
            if error:
                window.status.config(text=error)
            elif result:
                window.picker.config(values=result["models"])
                if window.reviewer.get() not in result["models"]:
                    window.reviewer.set(result["models"][0] if result["models"] else "")
                window.status.config(text="Reviewer models found. Save to use two-engine review." if result["models"] else "Colibri reported no models.")
        self.health_poll_at = 0
        self.update_controls()

