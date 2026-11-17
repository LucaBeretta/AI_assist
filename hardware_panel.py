"""Hardware report, model recommendation cards, and per-model setup guides."""

import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
import webbrowser

from model_advisor import detect_hardware, recommend_models


def gib(value):
    return f"{value / (1024 ** 3):.1f} GiB" if isinstance(value, (int, float)) and value > 0 else "unknown"


class HardwareRecommendationsPanel:
    def __init__(self, dialog, parent):
        self.dialog = dialog
        self.app = dialog.app
        self.events = queue.Queue()
        self.hardware = None
        self.hardware_loading = False
        self.catalogue = None
        self.recommendations = []
        self.card_buttons = []
        self.hardware_request = 0
        self.recommendation_request = 0
        self.closed = False
        c = self.app.COLORS
        self.frame = tk.Frame(parent, bg=c["surface"])
        report = tk.Frame(self.frame, bg=c["background"])
        report.pack(fill="x", padx=16, pady=14)
        heading = tk.Frame(report, bg=c["background"])
        heading.pack(fill="x", padx=14, pady=(10, 3))
        tk.Label(heading, text="YOUR LOCAL SETUP", bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.rescan_button = self.app.button(heading, "Rescan hardware", self.rescan,
                                            c["background"], c["accent"], "#e8ecf6")
        self.rescan_button.config(pady=4)
        self.rescan_button.pack(side="right")
        self.summary = tk.Label(report, text="Checking memory, graphics card, and Ollama...", bg=c["background"],
                                fg=c["ink"], font=("Segoe UI", 10), anchor="w", justify="left", wraplength=820)
        self.summary.pack(fill="x", padx=14, pady=(0, 12))
        report.bind("<Configure>", lambda event: self.summary.config(wraplength=max(300, event.width - 28)))
        self.message = tk.Label(self.frame, text="Reading your computer's hardware...", bg=c["surface"],
                                fg=c["muted"], font=("Segoe UI", 9), anchor="w", justify="left", wraplength=850)
        self.message.pack(fill="x", padx=18, pady=(0, 10))
        area = tk.Frame(self.frame, bg=c["surface"])
        area.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        self.canvas = tk.Canvas(area, bg=c["surface"], highlightthickness=0, width=1)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview,
                                  style="Chat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.cards = tk.Frame(self.canvas, bg=c["surface"])
        self.cards_window = self.canvas.create_window((0, 0), window=self.cards, anchor="nw", width=740)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.cards_window, width=event.width))
        self.cards.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<MouseWheel>", self.scroll_cards)
        self.cards.bind("<MouseWheel>", self.scroll_cards)
        self.rescan()
        self.poll_id = self.frame.after(100, self.process_events)

    def scroll_cards(self, event):
        if self.closed:
            return
        try:
            widget = self.frame.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        while widget is not None:
            if widget == self.canvas or widget == self.cards:
                self.canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = widget.master

    def rescan(self):
        if self.dialog.downloading:
            return
        self.dialog.recommended_target = None
        self.hardware_request += 1
        self.recommendation_request += 1
        self.hardware = None
        self.hardware_loading = True
        self.clear_cards()
        self.summary.config(text="Checking memory, graphics card, and Ollama...")
        self.message.config(text="Reading your computer's hardware...")
        self.rescan_button.config(state="disabled")
        host = self.app.ollama_host()
        threading.Thread(target=self.scan_worker, args=(self.hardware_request, host), daemon=True).start()

    def scan_worker(self, request, host):
        try:
            self.events.put(("hardware", request, detect_hardware(host), None))
        except Exception as error:
            self.events.put(("hardware", request, None, str(error)))

    def set_catalogue(self, catalogue):
        self.catalogue = catalogue
        if self.hardware is not None:
            self.load_recommendations()

    def load_recommendations(self):
        if self.hardware is None or self.catalogue is None:
            return
        self.recommendation_request += 1
        self.clear_cards()
        self.message.config(text="Matching current download sizes to your graphics card and memory...")
        threading.Thread(target=self.recommendation_worker,
                         args=(self.recommendation_request, self.hardware, list(self.catalogue)), daemon=True).start()

    def recommendation_worker(self, request, hardware, catalogue):
        try:
            self.events.put(("recommendations", request, recommend_models(hardware, catalogue), None))
        except Exception as error:
            self.events.put(("recommendations", request, None, str(error)))

    def show_hardware(self):
        hardware = self.hardware
        lines = [f"{hardware.get('cpu') or 'CPU unknown'}  |  RAM {gib(hardware.get('ram_total_bytes'))} total, "
                 f"{gib(hardware.get('ram_available_bytes'))} free"]
        for gpu in hardware.get("gpus", []):
            if gpu.get("unified"):
                lines.append(f"{gpu['name']}  |  usable by the GPU about {gib(gpu.get('total_bytes'))} of shared memory, "
                             f"{gib(gpu.get('free_bytes'))} free now")
            else:
                lines.append(f"{gpu['name']}  |  VRAM {gib(gpu.get('total_bytes'))} total, {gib(gpu.get('free_bytes'))} free")
        if not hardware.get("gpus"):
            lines.append("No supported graphics card detected. Recommendations will use CPU / RAM estimates.")
        version = hardware.get("ollama_version")
        server = f"Ollama {version}" if version else "Ollama version unavailable"
        lines.append(f"{server}  |  Free model disk space {gib(hardware.get('disk_free_bytes'))}")
        if not hardware.get("server_local", True):
            lines.append("Ollama is on another computer; this local hardware cannot establish its model capacity.")
        if hardware.get("notes"):
            notes = " ".join(hardware["notes"])
            lines.append("Notes: " + notes[:220] + ("..." if len(notes) > 220 else ""))
        self.summary.config(text="\n".join(lines))

    def clear_cards(self):
        for child in self.cards.winfo_children():
            child.destroy()
        self.card_buttons = []
        self.recommendations = []

    def show_cards(self, recommendations):
        self.clear_cards()
        self.recommendations = recommendations
        c = self.app.COLORS
        for model in recommendations:
            card = tk.Frame(self.cards, bg=c["background"], highlightbackground=c["border"], highlightthickness=1)
            card.pack(fill="x", pady=(0, 12), padx=3)
            content = tk.Frame(card, bg=c["background"])
            content.pack(fill="x", padx=16, pady=12)
            tk.Label(content, text=model["name"], bg=c["background"], fg=c["ink"],
                     font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
            tk.Label(content, text=model["purpose"], bg=c["background"], fg=c["accent"],
                     font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 5))
            details = tk.Label(content, text=(f"{model['fit']}\n{model['reason']}\n"
                               f"Download {model['size']}  |  Estimated runtime memory {gib(model.get('estimated_memory_bytes'))}"
                               f"  |  Context {model.get('context_length', 4096):,} tokens"),
                               bg=c["background"], fg=c["muted"], font=("Segoe UI", 9),
                               anchor="w", justify="left", wraplength=740)
            details.pack(fill="x", pady=(0, 10))
            card.bind("<Configure>", lambda event, label=details: label.config(wraplength=max(250, event.width - 36)))
            actions = tk.Frame(content, bg=c["background"])
            actions.pack(fill="x")
            download = self.app.button(actions, "Download", lambda item=model: self.dialog.choose_recommendation(item),
                                       c["accent"], "white", c["accent_hover"])
            download.pack(side="right")
            guide = self.app.button(actions, "Setup guide", lambda item=model: self.show_guide(item),
                                    "#e8ecf6", c["ink"], "#dce2f1")
            guide.pack(side="right", padx=(0, 8))
            self.card_buttons.append((model, download))
            widgets = [card]
            while widgets:
                widget = widgets.pop()
                widget.bind("<MouseWheel>", self.scroll_cards, add="+")
                widgets.extend(widget.winfo_children())
        if recommendations:
            self.message.config(text="Fits are estimates for one model at a time with a 4K context. Setup guide explains each choice.")
        else:
            self.message.config(text="No verified recommendations are available. Rescan, or browse the Popular models tab.")
        self.update_controls()

    def update_controls(self):
        if self.closed:
            return
        busy = self.dialog.downloading or self.dialog.loading_catalogue or self.dialog.loading_variants
        installed = self.dialog.installed_models()
        for model, button in self.card_buttons:
            present = model["name"] in installed
            enabled = not busy and not (present and self.dialog.app_busy())
            button.config(text="Use in chat" if present else f"Download {model['size']}",
                          state="normal" if enabled else "disabled",
                          bg=self.app.COLORS["accent"] if enabled else "#e5e9f2")
        self.rescan_button.config(state="normal" if not self.hardware_loading and not self.dialog.downloading else "disabled")

    def guide_text(self, model):
        hardware = self.hardware or {}
        lines = [model["name"], model["purpose"], "", "WHY THIS MODEL", model["fit"], model["reason"], "",
                 f"Download: {model['size']}", f"Estimated runtime memory: {gib(model.get('estimated_memory_bytes'))}",
                 f"Starting context: {model.get('context_length', 4096)} tokens", "",
                 "YOUR SETUP", self.summary["text"], "", "SETUP"]
        steps = model.get("setup_steps", [])
        if isinstance(steps, str):
            steps = [steps]
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, 1))
        lines += ["", "IN AI ASSIST",
                  "Click this card's Download button. When installation finishes, choose Use in chat on the same card.",
                  "Use in chat applies this model's 4096-token context for the current app session, including loading and chat requests.",
                  "If other models are using GPU memory, use Stop all models in the main sidebar before loading a larger choice.",
                  "", "VERIFY GPU USE", "Run: ollama ps",
                  ("On a Mac: Activity Monitor > Window > GPU History." if any(gpu.get("unified") for gpu in hardware.get("gpus", []))
                   else "Run: nvidia-smi"),
                  "In ollama ps, 100% GPU describes model placement. A CPU/GPU split means some of the model is in system RAM and may run more slowly.",
                  "Memory estimates include headroom but cannot guarantee fit. Longer contexts need more memory."]
        if hardware.get("model_directory"):
            lines += ["", "OLLAMA MODEL FOLDER", str(hardware["model_directory"])]
        if hardware.get("notes"):
            lines += ["", "HARDWARE DETECTION NOTES", *hardware["notes"]]
        lines += ["", "MODEL INFORMATION", model.get("source_url", "https://ollama.com/library"),
                  "https://docs.ollama.com/gpu", "https://docs.ollama.com/context-length"]
        return "\n".join(lines)

    def show_guide(self, model):
        c = self.app.COLORS
        window = tk.Toplevel(self.dialog.window)
        window.title(f"Setup guide | {model['name']}")
        window.geometry("780x650")
        window.minsize(640, 480)
        window.configure(bg=c["surface"])
        window.transient(self.dialog.window)
        tk.Label(window, text=f"Set up {model['name']}", bg=c["surface"], fg=c["ink"],
                 font=("Segoe UI", 17, "bold"), anchor="w").pack(fill="x", padx=22, pady=(20, 14))
        actions = tk.Frame(window, bg=c["surface"])
        actions.pack(side="bottom", fill="x", padx=22, pady=16)
        text = self.guide_text(model)
        def copy():
            window.clipboard_clear()
            window.clipboard_append(text)
            copy_button.config(text="Copied")
        copy_button = self.app.button(actions, "Copy guide", copy, c["accent"], "white", c["accent_hover"])
        copy_button.pack(side="right")
        self.app.button(actions, "Model page", lambda: webbrowser.open(model["source_url"]),
                        c["background"], c["ink"], "#e8ecf6").pack(side="left")
        body = scrolledtext.ScrolledText(window, wrap="word", font=("Segoe UI", 10), padx=16, pady=14,
                                         relief="flat", bg=c["background"], fg=c["ink"], spacing3=5)
        body.pack(fill="both", expand=True, padx=22)
        body.insert("1.0", text)
        body.config(state="disabled")
        return window

    def process_events(self):
        for _ in range(10):
            try:
                kind, request, data, error = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "hardware" and request == self.hardware_request:
                self.hardware_loading = False
                self.rescan_button.config(state="normal")
                if error:
                    self.summary.config(text=f"Hardware check failed: {error}")
                    self.message.config(text="Rescan hardware to retry, or browse Popular models.")
                else:
                    self.hardware = data
                    self.show_hardware()
                    if self.catalogue is not None:
                        self.load_recommendations()
                    else:
                        self.message.config(text="Waiting for the live model list...")
            elif kind == "recommendations" and request == self.recommendation_request:
                if error:
                    self.message.config(text=f"Could not verify model downloads. Rescan to retry. {error}")
                else:
                    self.show_cards(data)
        if not self.closed:
            self.poll_id = self.frame.after(100, self.process_events)

    def close(self):
        self.closed = True
        self.frame.after_cancel(self.poll_id)
