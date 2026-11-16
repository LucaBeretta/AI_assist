"""A live Ollama model catalogue and download window."""

import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from hardware_panel import HardwareRecommendationsPanel

from model_library import (
    DownloadCancelled, fetch_model_variants, fetch_popular_models, pull_model,
)


POPULAR_MODELS_URL = "https://ollama.com/library?sort=popular"


def readable_bytes(value):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1000


class ModelDownloadDialog:
    def __init__(self, app):
        self.app = app
        self.events = queue.Queue()
        self.catalogue = []
        self.variants = []
        self.catalogue_request = 0
        self.variant_request = 0
        self.loading_catalogue = False
        self.loading_variants = False
        self.downloading = False
        self.cancel_event = threading.Event()
        self.downloaded_models = set()
        self.recommended_target = None
        self.closed = False
        self.window = tk.Toplevel(app.root)
        self.window.title("Model recommendations | AI Assist")
        self.window.geometry("1020x820")
        self.window.minsize(820, 660)
        self.window.configure(bg=app.COLORS["surface"])
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.search = tk.StringVar(self.window)
        self.variant_choice = tk.StringVar(self.window)
        self.build()
        self.search.trace_add("write", lambda *args: self.filter_catalogue())
        self.reload_catalogue()
        self.poll_id = self.window.after(100, self.process_events)

    def build(self):
        c = self.app.COLORS
        style = ttk.Style(self.window)
        style.configure("Library.Treeview", background=c["surface"], fieldbackground=c["surface"],
                        foreground=c["ink"], rowheight=34, font=("Segoe UI", 10), borderwidth=0)
        style.map("Library.Treeview", background=[("selected", "#edf0ff")],
                  foreground=[("selected", c["accent"])])
        style.configure("Library.Treeview.Heading", font=("Segoe UI", 9, "bold"),
                        background=c["background"], foreground=c["muted"], padding=8)
        style.configure("Library.TCombobox", padding=8, fieldbackground="white", foreground=c["ink"])
        style.map("Library.TCombobox", fieldbackground=[("readonly", "white")],
                  selectbackground=[("readonly", "white")], selectforeground=[("readonly", c["ink"])])
        title = tk.Frame(self.window, bg=c["surface"])
        title.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(title, text="Models for your computer", bg=c["surface"], fg=c["ink"],
                 font=("Segoe UI", 21, "bold"), anchor="w").pack(fill="x")
        tk.Label(title, text="Hardware-based recommendations, per-model downloads, and setup guides.",
                 bg=c["surface"], fg=c["muted"], font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(5, 0))

        self.notebook = ttk.Notebook(self.window)
        self.advisor = HardwareRecommendationsPanel(self, self.notebook)
        self.popular_tab = tk.Frame(self.notebook, bg=c["surface"])
        self.notebook.add(self.advisor.frame, text="  Recommended for this PC  ")
        self.notebook.add(self.popular_tab, text="  Popular models  ")
        self.notebook.bind("<<NotebookTabChanged>>", self.change_tab)
        toolbar = tk.Frame(self.popular_tab, bg=c["surface"])
        toolbar.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(toolbar, text="Search", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 10)).pack(side="left", padx=(0, 10))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search, font=("Segoe UI", 10))
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.reload_button = self.app.button(toolbar, "Refresh list", self.reload_catalogue,
                                            c["background"], c["ink"], "#e8ecf6")
        self.reload_button.pack(side="right", padx=(12, 0))

        # Keep installation controls visible even when the list or description is long.
        bottom = tk.Frame(self.window, bg=c["surface"])
        bottom.pack(side="bottom", fill="x", padx=24, pady=(12, 20))
        self.status = tk.Label(bottom, text="", bg=c["surface"], fg=c["muted"],
                               font=("Segoe UI", 9), anchor="w", justify="left", wraplength=750)
        self.status.pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100,
                                       style="Activity.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 12))
        actions = tk.Frame(bottom, bg=c["surface"])
        actions.pack(fill="x")
        source = self.app.button(actions, "Source: Ollama popular", lambda: webbrowser.open(POPULAR_MODELS_URL),
                                 c["surface"], c["accent"], c["background"])
        source.pack(side="left")
        self.download_button = self.app.button(actions, "Download model", self.start_download,
                                              c["accent"], "white", c["accent_hover"])
        self.download_button.pack(side="right")
        self.cancel_button = self.app.button(actions, "Cancel download", self.cancel_download,
                                            c["background"], c["ink"], "#e8ecf6")
        self.cancel_button.pack(side="right", padx=8)
        self.use_button = self.app.button(actions, "Use in chat", self.use_model,
                                         c["background"], c["ink"], "#e8ecf6")
        self.use_button.pack(side="right")

        details = tk.Frame(self.popular_tab, bg=c["background"])
        details.pack(side="bottom", fill="x", padx=24, pady=(12, 0))
        self.description = tk.Label(details, text="Select a model to see its available downloads.",
                                    bg=c["background"], fg=c["ink"], font=("Segoe UI", 10),
                                    anchor="w", justify="left", wraplength=850)
        self.description.pack(fill="x", padx=14, pady=(12, 10))
        details.bind("<Configure>", lambda event: self.description.config(wraplength=max(250, event.width - 28)))
        variants = tk.Frame(details, bg=c["background"])
        variants.pack(fill="x", padx=14)
        tk.Label(variants, text="Download size", bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 10)).pack(side="left", padx=(0, 12))
        self.variant_picker = ttk.Combobox(variants, textvariable=self.variant_choice,
                                         state="disabled", style="Library.TCombobox", font=("Segoe UI", 10))
        self.variant_picker.pack(side="left", fill="x", expand=True)
        self.variant_picker.bind("<<ComboboxSelected>>", self.choose_popular_variant)
        tk.Label(details, text="Sizes show disk downloads. Running a model also needs enough memory.",
                 bg=c["background"], fg=c["muted"], font=("Segoe UI", 9), anchor="w").pack(
                     fill="x", padx=14, pady=(8, 12))

        listing = tk.Frame(self.popular_tab, bg=c["surface"])
        listing.pack(fill="both", expand=True, padx=24)
        self.tree = ttk.Treeview(listing, columns=("pulls", "sizes"), show="tree headings",
                                 selectmode="browse", height=7, style="Library.Treeview")
        self.tree.heading("#0", text="MODEL", anchor="w")
        self.tree.heading("pulls", text="DOWNLOADS", anchor="w")
        self.tree.heading("sizes", text="MODEL SIZES", anchor="w")
        self.tree.column("#0", width=230, minwidth=160)
        self.tree.column("pulls", width=105, minwidth=85, stretch=False)
        self.tree.column("sizes", width=350, minwidth=150)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview,
                                  style="Chat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_family)
        self.notebook.pack(fill="both", expand=True, padx=10)
        self.update_controls()

    def change_tab(self, event=None):
        if self.notebook.select() == str(self.popular_tab):
            self.recommended_target = None
        if hasattr(self, "download_button"):
            self.update_controls()

    def choose_popular_variant(self, event=None):
        self.recommended_target = None
        self.update_controls()

    def choose_recommendation(self, model):
        if self.downloading or self.loading_catalogue or self.loading_variants:
            return
        self.notebook.select(self.advisor.frame)
        self.recommended_target = model
        self.update_controls()
        if model["name"] in self.installed_models():
            self.use_model()
        else:
            self.start_download()

    def set_status(self, text, error=False):
        self.status.config(text=text, fg=self.app.COLORS["error"] if error else self.app.COLORS["muted"])

    def selected_variant(self):
        if self.notebook.select() == str(self.advisor.frame):
            return self.recommended_target
        index = self.variant_picker.current()
        return self.variants[index] if 0 <= index < len(self.variants) else None

    def installed_models(self):
        return set(self.app.model_picker["values"]) | self.downloaded_models

    def app_busy(self):
        if callable(getattr(self.app, "busy", None)):
            return self.app.busy()
        return self.app.is_waiting or self.app.is_loading_models or getattr(self.app, "lifecycle_busy", False)

    def update_controls(self):
        variant = self.selected_variant()
        installed = bool(variant and variant["name"] in self.installed_models())
        busy = self.downloading or self.loading_catalogue or self.loading_variants
        ready = bool(variant) and not busy
        self.download_button.config(state="normal" if ready and not installed else "disabled",
                                    text="Installed" if installed else "Download model",
                                    bg=self.app.COLORS["accent"] if ready and not installed else "#e5e9f2")
        can_use = ready and installed and not self.app_busy()
        self.use_button.config(state="normal" if can_use else "disabled")
        self.cancel_button.config(state="normal" if self.downloading and not self.cancel_event.is_set() else "disabled")
        self.reload_button.config(state="disabled" if self.downloading or self.loading_catalogue else "normal")
        self.search_entry.config(state="disabled" if self.downloading or self.loading_catalogue else "normal")
        self.variant_picker.config(state="readonly" if ready else "disabled")
        self.tree.config(selectmode="none" if self.downloading else "browse")
        if hasattr(self, "advisor"):
            self.advisor.update_controls()

    def reload_catalogue(self):
        if self.downloading or self.loading_catalogue:
            return
        self.catalogue_request += 1
        self.variant_request += 1
        request = self.catalogue_request
        self.loading_catalogue = True
        self.loading_variants = False
        self.catalogue = []
        self.variants = []
        self.recommended_target = None
        self.variant_picker.config(values=())
        self.variant_choice.set("")
        self.tree.delete(*self.tree.get_children())
        self.set_status("Loading the current popular models from ollama.com...")
        self.update_controls()
        threading.Thread(target=self.fetch_catalogue, args=(request,), daemon=True).start()

    def fetch_catalogue(self, request):
        try:
            self.events.put(("catalogue", request, fetch_popular_models(), None))
        except Exception as error:
            self.events.put(("catalogue", request, None, str(error)))

    def filter_catalogue(self):
        if self.downloading:
            return
        self.variant_request += 1
        self.recommended_target = None
        self.loading_variants = False
        self.variants = []
        self.variant_picker.config(values=())
        self.variant_choice.set("")
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().strip().lower()
        for model in self.catalogue:
            if query in (model["name"] + " " + model["description"]).lower():
                self.tree.insert("", "end", iid=model["name"], text=model["name"],
                                 values=(model["pulls"], ", ".join(model["sizes"])))
        self.description.config(text="Select a model to see its available downloads.")
        if self.catalogue:
            self.set_status(f"{len(self.tree.get_children())} popular chat models. List refreshed live from Ollama.")
        self.update_controls()

    def select_family(self, event=None):
        if self.downloading or self.loading_catalogue:
            return
        selected = self.tree.selection()
        if not selected:
            return
        self.recommended_target = None
        name = selected[0]
        model = next((item for item in self.catalogue if item["name"] == name), None)
        if model is None:
            return
        self.variant_request += 1
        request = self.variant_request
        self.loading_variants = True
        self.variants = []
        self.variant_picker.config(values=())
        self.variant_choice.set("")
        description = model["description"]
        self.description.config(text=f"{name}: {description[:240]}" + ("..." if len(description) > 240 else ""))
        self.set_status(f"Finding local download sizes for {name}...")
        self.update_controls()
        threading.Thread(target=self.fetch_variants, args=(request, name), daemon=True).start()

    def fetch_variants(self, request, name):
        try:
            self.events.put(("variants", request, fetch_model_variants(name), None))
        except Exception as error:
            self.events.put(("variants", request, None, str(error)))

    def start_download(self):
        variant = self.selected_variant()
        if not variant or self.downloading or self.loading_variants or self.loading_catalogue:
            return
        if variant["name"] in self.installed_models():
            return
        self.downloading = True
        self.cancel_event = threading.Event()
        self.progress.config(value=0)
        self.set_status(f"Starting download of {variant['name']} ({variant['size']})...")
        self.update_controls()
        threading.Thread(target=self.download, args=(variant["name"], self.app.ollama_host(), self.cancel_event),
                         daemon=True).start()

    def download(self, name, host, cancel_event):
        try:
            pull_model(host, name, lambda item: self.events.put(("progress", name, item, None)), cancel_event=cancel_event)
            self.events.put(("downloaded", name, None, None))
        except DownloadCancelled:
            self.events.put(("cancelled", name, None, None))
        except Exception as error:
            self.events.put(("downloaded", name, None, str(error)))

    def cancel_download(self):
        if self.downloading:
            self.cancel_event.set()
            self.set_status("Stopping the download...")
            self.update_controls()

    def use_model(self):
        variant = self.selected_variant()
        if not variant or variant["name"] not in self.installed_models():
            return
        if self.app_busy():
            self.set_status("Wait for the current operation to finish, then choose Use in chat.")
            return
        if self.recommended_target is not None:
            if not self.app.select_recommended_model(variant["name"], variant.get("context_length", 4096)):
                return
        else:
            self.app.model_name.set(variant["name"])
            self.app.select_model()
        self.close()
        self.app.root.lift()

    def process_events(self):
        for _ in range(100):
            try:
                kind, request, data, error = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "catalogue" and request == self.catalogue_request:
                self.loading_catalogue = False
                if error:
                    self.set_status(f"Could not load the live list. Check your internet connection and refresh. {error}", error=True)
                    self.advisor.set_catalogue([])
                else:
                    self.catalogue = data
                    self.filter_catalogue()
                    self.advisor.set_catalogue(data)
            elif kind == "variants" and request == self.variant_request:
                self.loading_variants = False
                if error:
                    self.set_status(f"Could not load download sizes. Select the model again to retry. {error}", error=True)
                else:
                    self.variants = data
                    self.variant_picker.config(values=[f"{item['name']}  /  {item['size']}" for item in data])
                    if data:
                        self.variant_picker.current(0)
                        self.set_status("Choose a size, then download. Nothing is downloaded until you click Download model.")
                    else:
                        self.set_status("No local downloads are available for this model.")
            elif kind == "progress" and not self.cancel_event.is_set():
                total = data.get("total", 0)
                completed = data.get("completed", 0)
                if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
                    self.progress.config(value=min(100, max(0, completed / total * 100)))
                    self.set_status(f"Downloading {request}: {readable_bytes(completed)} / {readable_bytes(total)}")
                else:
                    status = data.get("status", "Downloading model...")
                    friendly = {"pulling manifest": "Getting download details...",
                                "verifying sha256 digest": "Verifying the download...",
                                "writing manifest": "Finishing installation...", "success": "Download complete."}
                    self.set_status(friendly.get(status, "Downloading model..."))
            elif kind in ("downloaded", "cancelled"):
                self.downloading = False
                if kind == "cancelled":
                    self.set_status("Download cancelled. You can choose another model or retry.")
                elif error:
                    self.set_status(f"Download failed. {error}", error=True)
                else:
                    self.progress.config(value=100)
                    self.downloaded_models.add(request)
                    self.app.model_downloaded(request)
                    self.set_status(f"{request} is installed. Choose Use in chat to start a conversation.")
        if not self.closed:
            self.update_controls()
            self.poll_id = self.window.after(100, self.process_events)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.advisor.close()
        self.cancel_event.set()
        self.window.after_cancel(self.poll_id)
        self.window.destroy()
