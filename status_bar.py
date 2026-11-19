"""The console status bar along the bottom of the window.

It shows what is true right now, from data the app already has: the engine and
whether it answers, the selected model and where it runs (GPU, CPU or both, from
Ollama's /api/ps; RAM + SSD for Colibri), how much of the context the last
answer used, the GPU memory Ollama's running models occupy, the last measured
speed, the agent's step, and whether prompts stay on this computer."""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import urllib.parse

MONO_CANDIDATES = ("Cascadia Mono", "Cascadia Code", "Consolas", "SF Mono", "Menlo",
                   "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New")
from status_values import AGENT_MAX_STEPS, short_tokens, placement, is_local, is_cloud_model, generation_rate, snapshot_values


def mono_family(root):
    """The first installed monospace face the console style prefers."""
    try:
        installed = {name.lower(): name for name in tkfont.families(root)}
    except tk.TclError:
        return "TkFixedFont"
    for name in MONO_CANDIDATES:
        if name.lower() in installed:
            return installed[name.lower()]
    return "TkFixedFont"


class StatusBar:
    POLL_SECONDS = 10

    def __init__(self, app):
        self.app = app
        self.details = {}
        self.results = queue.Queue()
        self.polling = False
        self.next_poll = 0.0
        self.closed = False
        c = app.COLORS
        self.mono = mono_family(app.root)
        self.frame = tk.Frame(app.root, bg=c["status"])
        self.frame.pack(side="bottom", fill="x", before=app.sidebar)
        tk.Frame(self.frame, height=1, bg=c["border"]).pack(side="top", fill="x")
        row = self.row = tk.Frame(self.frame, bg=c["status"])
        row.pack(fill="x", padx=14, pady=5)
        small = (self.mono, 9)

        def label(**options):
            widget = tk.Label(row, bg=c["status"], fg=c["muted"], font=small, **options)
            widget.pack(side="left", padx=(0, 18))
            return widget

        self.engine_dot = tk.Label(row, text="●", bg=c["status"], fg=c["muted"], font=(self.mono, 7))
        self.engine_dot.pack(side="left", padx=(0, 6))
        self.engine_label = label()
        self.model_label = label()
        context = self.context_frame = tk.Frame(row, bg=c["status"])
        context.pack(side="left", padx=(0, 18))
        self.context_title = tk.Label(context, text="ctx", bg=c["status"], fg=c["muted"], font=small)
        self.context_title.pack(side="left", padx=(0, 6))
        self.context_bar = tk.Canvas(context, width=80, height=4, bg=c["border"], highlightthickness=0, borderwidth=0)
        self.context_bar.pack(side="left", padx=(0, 6))
        self.context_label = tk.Label(context, bg=c["status"], fg=c["muted"], font=small)
        self.context_label.pack(side="left")
        self.vram_label = label()
        self.speed_label = label()
        self.agent_label = label()
        self.privacy_label = tk.Label(row, bg=c["status"], fg=c["success"], font=small)
        self.privacy_label.pack(side="right")
        self.tick()

    # ------------------------------------------------------------ values

    def snapshot(self):
        """Gather Tk values on its UI thread, then use the shared plain-data logic."""
        app = self.app
        engine = getattr(app, "backend", "Ollama")
        try:
            url = getattr(app, "colibri_url", "") if engine == "Colibri" else app.ollama_url()
        except (AttributeError, RuntimeError, ValueError):
            url = ""
        return snapshot_values(engine=engine,
            connected="connected" in str(app.connection_label.cget("text")).lower(),
            model=app.model_name.get(), details=self.details,
            metrics=getattr(app, "last_metrics", {}),
            choice=app.context_choice.get() if hasattr(app, "context_choice") else "",
            report=getattr(app, "colibri_report", None), url=url,
            step=getattr(app, "agent_step", 0), agent_active=getattr(app, "agent_active", False))

    # ------------------------------------------------------------ drawing

    def tick(self):
        if self.closed:
            return
        try:
            self.apply_results()
            self.render(self.snapshot())
            self.poll_if_due()
        except tk.TclError:
            return
        self.after_id = self.app.root.after(1000, self.tick)

    def render(self, values):
        c = self.app.COLORS
        for widget in (self.frame, self.row, self.context_frame):
            widget.configure(bg=c["status"])
        self.engine_dot.configure(fg=c[values["engine_role"]], bg=c["status"])
        for widget, key in ((self.engine_label, "engine"), (self.model_label, "model"), (self.context_label, "context"),
                            (self.vram_label, "vram"), (self.speed_label, "speed"), (self.agent_label, "agent")):
            widget.configure(text=values[key], bg=c["status"],
                             fg=c["accent"] if key == "agent" else c["muted"])
            if key in ("vram", "speed", "agent"):
                if values[key] and not widget.winfo_manager():
                    widget.pack(side="left", padx=(0, 18), before=self.privacy_label)
                elif not values[key] and widget.winfo_manager():
                    widget.pack_forget()
        self.context_title.configure(bg=c["status"], fg=c["muted"])
        self.privacy_label.configure(text=values["privacy"], fg=c[values["privacy_role"]], bg=c["status"])
        chip = getattr(self.app, "model_chip", None)
        if chip is not None:
            model = self.app.model_name.get() or "no model"
            chip.configure(text=f"\u25cf {getattr(self.app, 'backend', 'Ollama').lower()} / {model}")
        self.context_bar.configure(bg=c["border"])
        self.context_bar.delete("all")
        if values["fraction"] > 0:
            self.context_bar.create_rectangle(0, 0, max(2, int(80 * values["fraction"])), 4,
                                              fill=c["accent"], width=0)

    # ------------------------------------------------------------ Ollama /api/ps

    def poll_if_due(self):
        if self.polling or time.monotonic() < self.next_poll or getattr(self.app, "backend", "Ollama") != "Ollama":
            return
        self.polling = True
        self.next_poll = time.monotonic() + self.POLL_SECONDS
        source = getattr(self.app, "running_details", None)
        if source is None:
            self.polling = False
            return
        threading.Thread(target=self.poll, args=(source, self.results), daemon=True).start()

    @staticmethod
    def poll(source, results):
        # Plain values only: the thread never holds Tk objects.
        try:
            results.put(source())
        except Exception:
            results.put({})

    def apply_results(self):
        try:
            while True:
                self.details = self.results.get_nowait()
                self.polling = False
        except queue.Empty:
            pass

    def refresh_now(self):
        self.next_poll = 0.0

    def close(self):
        self.closed = True
        try:
            self.app.root.after_cancel(self.after_id)
        except (AttributeError, tk.TclError):
            pass
