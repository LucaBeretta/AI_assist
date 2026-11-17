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
AGENT_MAX_STEPS = 12


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


def short_tokens(count):
    """6200 -> 6.2k, 16384 -> 16k, 950 -> 950."""
    if count < 1000:
        return str(count)
    value = count / 1000
    return f"{value:.1f}k" if value < 10 and round(value, 1) != int(value) else f"{round(value):.0f}k"


def placement(detail):
    """Where Ollama placed a running model: gpu, cpu, or the GPU share."""
    if not detail or not detail.get("size"):
        return ""
    share = detail.get("size_vram", 0) / detail["size"]
    if share >= 0.98:
        return "gpu"
    if share <= 0.02:
        return "cpu"
    return f"gpu {share:.0%} · cpu"


def is_local(url):
    try:
        host = urllib.parse.urlsplit(url if "://" in url else "http://" + url).hostname
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


def is_cloud_model(name):
    """Ollama's cloud models run on Ollama's servers: names end in -cloud or :cloud."""
    name = (name or "").lower()
    return name.endswith("-cloud") or name.endswith(":cloud")


def generation_rate(metrics):
    if metrics.get("eval_duration", 0) > 0 and metrics.get("eval_count"):
        return metrics["eval_count"] / (metrics["eval_duration"] / 1e9)
    return None


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
        """Every text and colour role the bar shows, computed from the app's state."""
        app = self.app
        engine = getattr(app, "backend", "Ollama")
        connected = "connected" in str(app.connection_label.cget("text")).lower()
        report = getattr(app, "colibri_report", None) or {}
        server = report.get("server") if isinstance(report, dict) else None
        stopped = engine == "Colibri" and isinstance(server, dict) and server.get("state") == "stopped"
        state = "stopped" if stopped else ("ready" if connected else "offline")
        model = app.model_name.get()
        detail = self.details.get(model) if engine == "Ollama" else None
        where = placement(detail) if engine == "Ollama" else ("ram + ssd" if model else "")
        model_text = (f"{model} · {where}" if where else model) if model else "no model selected"

        metrics = getattr(app, "last_metrics", {}) or {}
        used = None
        if isinstance(metrics.get("prompt_eval_count"), int):
            used = metrics["prompt_eval_count"] + int(metrics.get("eval_count") or 0)
        choice = str(app.context_choice.get()) if hasattr(app, "context_choice") else ""
        total = int(choice) if choice.isdigit() else (detail or {}).get("context_length")
        if used is not None and total:
            context_text, fraction = f"{short_tokens(used)} / {short_tokens(total)}", min(1.0, used / total)
        elif total:
            context_text, fraction = short_tokens(total), 0.0
        elif used is not None:
            context_text, fraction = short_tokens(used), 0.0
        else:
            context_text, fraction = "default", 0.0

        vram = sum(item.get("size_vram", 0) for item in self.details.values()) if engine == "Ollama" else 0
        vram_text = f"vram {vram / 1024 ** 3:.1f} GB" if vram else ""

        rate = generation_rate(metrics)
        if engine == "Colibri" and isinstance(server, dict):
            speed = server.get("tokens_per_second")
            rate = speed if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0 else rate
        speed_text = (f"{rate:.1f} tok/s" if rate < 10 else f"{rate:.0f} tok/s") if rate else ""

        step = getattr(app, "agent_step", 0)
        agent_text = f"agent step {step} / {AGENT_MAX_STEPS}" if getattr(app, "agent_active", False) and step else ""

        if engine == "Colibri":
            url = getattr(app, "colibri_url", "")
        else:
            try:
                url = app.ollama_url() if hasattr(app, "ollama_url") else ""
            except (RuntimeError, ValueError):
                url = ""
        if is_cloud_model(model):
            privacy, privacy_role = "cloud model: prompts leave this computer", "amber"
        elif url and not is_local(url):
            privacy, privacy_role = "remote server", "amber"
        else:
            privacy, privacy_role = "local only", "success"
        return {
            "engine": f"{engine.lower()} {state}", "engine_role": "success" if state == "ready" else "muted",
            "model": model_text, "context": context_text, "fraction": fraction,
            "vram": vram_text, "speed": speed_text, "agent": agent_text,
            "privacy": privacy, "privacy_role": privacy_role,
        }

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
