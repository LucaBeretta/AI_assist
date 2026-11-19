"""Get started: a short guide when AI Assist starts without a working model.

It shows three steps and what is missing: the Ollama program, Ollama
running, and a model that fits this computer. Each missing step has one
button. It opens by itself only when something is missing, and you can turn
that off. Getting Ollama or a model goes online, so those still ask for your
two confirmations.
"""

import tkinter as tk
import webbrowser

from app_core import settings

OLLAMA_DOWNLOAD = "https://ollama.com/download"


def readiness(lifecycle, backend="Ollama"):
    """What the last model check found: {"installed", "running", "models"} as True/False."""
    lifecycle = lifecycle or {}
    errors = " ".join(lifecycle.get("errors", [])).lower()
    models = bool(lifecycle.get("models"))
    if backend != "Ollama":
        return {"installed": True, "running": models, "models": models}
    installed = "not installed" not in errors
    stopped = any(text in errors for text in ("could not load models", "did not become ready", "offline",
                                              "connection", "request failed"))
    running = installed and (models or not stopped)
    return {"installed": installed, "running": running, "models": models}


def ready(lifecycle, backend="Ollama"):
    return all(readiness(lifecycle, backend).values())


class WelcomeGuide:
    def __init__(self, app):
        self.app = app
        c = app.COLORS
        window = self.window = tk.Toplevel(app.root)
        window.title("Get started")
        # No fixed size: the window grows to fit its text at any display scaling.
        window.configure(bg=c["background"])
        window.transient(app.root)
        window.bind("<Escape>", lambda event: self.close())
        window.protocol("WM_DELETE_WINDOW", self.close)
        tk.Label(window, text="Let's get your local AI ready", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 16, "bold"), anchor="w", justify="left", wraplength=500).pack(
            fill="x", padx=24, pady=(20, 4))
        tk.Label(window, text="AI Assist runs AI models on this computer with a free program called Ollama. "
                 "Three things are needed:", bg=c["background"], fg=c["muted"], font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=500).pack(fill="x", padx=24, pady=(0, 12))
        self.rows = tk.Frame(window, bg=c["background"])
        self.rows.pack(fill="both", expand=True, padx=24)
        footer = tk.Frame(window, bg=c["background"])
        footer.pack(side="bottom", fill="x", padx=24, pady=16)
        self.show_at_start = tk.BooleanVar(window, value=self.wanted(app))
        tk.Checkbutton(footer, text="Show this when something is missing", variable=self.show_at_start,
                       command=self.save_choice, bg=c["background"], fg=c["muted"], selectcolor=c["surface"],
                       activebackground=c["background"], font=("Segoe UI", 9)).pack(side="left")
        app.button(footer, "Close", self.close, c["user"], c["ink"], c["border"]).pack(side="right")
        self.check_button = app.button(footer, "Check again", self.check_again, c["user"], c["ink"], c["border"])
        self.check_button.pack(side="right", padx=(0, 8))
        self.render()

    @staticmethod
    def settings_path(app):
        store = getattr(app, "chat_store", None)
        return store.directory / "welcome.settings" if store else None

    @classmethod
    def wanted(cls, app):
        path = cls.settings_path(app)
        return settings.read_json(path).get("show", True) is not False if path else True

    def save_choice(self):
        path = self.settings_path(self.app)
        if path:
            try:
                settings.write_json(path, {"show": bool(self.show_at_start.get())})
            except OSError as error:
                self.app.set_status(f"Could not save this choice: {error}", error=True)

    def row(self, number, title, done, detail, action=None, action_text=""):
        c = self.app.COLORS
        frame = tk.Frame(self.rows, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"])
        frame.pack(fill="x", pady=(0, 8))
        mark = "✓" if done else str(number)
        tk.Label(frame, text=mark, bg=c["surface"], fg=c["success"] if done else c["accent"], width=2,
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=(10, 6), pady=10)
        text = tk.Frame(frame, bg=c["surface"])
        text.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(text, text=title, bg=c["surface"], fg=c["ink"], font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(text, text=detail, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=330).pack(fill="x")
        if action is not None and not done:
            self.app.button(frame, action_text, action, c["accent"], c["on_accent"], c["accent_hover"]).pack(
                side="right", padx=10)

    def render(self):
        if not self.window.winfo_exists():
            return
        for child in self.rows.winfo_children():
            child.destroy()
        state = readiness(getattr(self.app, "last_lifecycle", None), getattr(self.app, "backend", "Ollama"))
        checking = self.app.lifecycle_busy
        self.row(1, "Ollama is installed", state["installed"],
                 "Installed." if state["installed"] else
                 "Get Ollama from its website (it asks to go online first), install it, then choose Check again.",
                 self.open_ollama_page, "Get Ollama...")
        self.row(2, "Ollama is running", state["installed"] and state["running"],
                 "Running." if state["running"] else
                 "AI Assist can start it for you." if state["installed"] else "After installing.",
                 self.check_again if state["installed"] else None, "Start Ollama")
        self.row(3, "A model that fits this computer", state["models"],
                 f"{len(self.app.last_lifecycle['models'])} installed. Choose one in the sidebar." if state["models"] else
                 "AI Assist suggests models that fit this computer's memory. Downloading one goes online, so it "
                 "asks you first.", self.find_model if state["running"] else None, "Find a model...")
        c = self.app.COLORS
        if checking:
            message, color = "Checking...", c["muted"]
        elif all(state.values()):
            message, color = "You're ready. Close this and type a message below.", c["success"]
        else:
            message, color = "", c["muted"]
        tk.Label(self.rows, text=message, bg=c["background"], fg=color, font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(fill="x", pady=(4, 0))
        self.check_button.config(state="disabled" if checking else "normal")

    def open_ollama_page(self):
        # Opening an internet page asks for the internet gate's two confirmations first.
        try:
            webbrowser.open(OLLAMA_DOWNLOAD)
        except OSError as error:
            self.app.set_status(f"Could not open the browser: {error}. The page is {OLLAMA_DOWNLOAD}", error=True)

    def check_again(self):
        self.app.manage_models("refresh")
        self.render()

    def find_model(self):
        self.app.open_downloads()
        dialog = self.app.download_dialog
        if dialog is not None and not dialog.closed and hasattr(dialog, "advisor"):
            dialog.notebook.select(dialog.advisor.frame)

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()
