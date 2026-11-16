"""The colibri engine tab: colibri's own recommendations for this PC, resumable
installs through ``coli setup``, and a comparison with the Ollama (GPU) picks."""

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import colibri_client


def gb(value):
    return f"{value:g} GB" if isinstance(value, (int, float)) else "unknown"


def readable(value):
    return f"{value / 1e9:.1f} GB" if isinstance(value, (int, float)) else "?"


def context_label(value):
    return "Automatic" if not value else f"{value:,} tokens"


def context_value(label):
    if not label or label == "Automatic":
        return 0
    return int(label.split()[0].replace(",", "").replace(".", ""))


def card_text(row):
    """Short lines for one colibri model card."""
    streams = "stream" in (row.get("runs") or "")
    engine = ("RAM + SSD: the experts stream from the disk. Bigger model, slower answers."
              if streams else "In RAM: colibri keeps the whole model in memory.")
    sizes = (f"Download {gb(row.get('download_gb'))}  |  RAM {gb(row.get('ram_min_gb'))} minimum, "
             f"{gb(row.get('ram_good_gb'))} best")
    if row.get("kind") == "text":
        sizes += f"  |  Coding agent tools: {'yes' if row.get('tools') else 'no'}"
    else:
        sizes += f"  |  {row.get('kind', '').capitalize() or 'Other'} model (not for chat)"
    if row.get("fits"):
        fit = ("Runs, with a small expert cache: " if row.get("status") == "tight" else "Fits: ") + (row.get("reason") or "")
    else:
        fit = "Does not fit this PC: " + (row.get("reason") or row.get("status") or "")
    return engine, sizes, fit


def comparison_text(ollama_recommendations, colibri_rows):
    """One line contrasting the fast GPU pick with what colibri can reach."""
    parts = []
    if ollama_recommendations:
        parts.append(f"With Ollama the suggested fast pick is {ollama_recommendations[0].get('name', '?')}.")
    text_rows = [row for row in colibri_rows if row.get("fits") and row.get("kind") == "text"]
    if text_rows:
        pick = next((row for row in text_rows if row.get("recommended")), text_rows[0])
        largest = max(text_rows, key=lambda row: row.get("download_gb") or 0)
        line = f"With colibri: {pick['name']} is recommended"
        if largest is not pick:
            line += f", and you can go up to {largest['name']} ({gb(largest.get('download_gb'))}), slower"
        parts.append(line + ".")
    elif colibri_rows:
        parts.append("No colibri model fits this PC's RAM and disk.")
    return " ".join(parts)


# Workers get plain values and the event queue, never the panel: a thread that
# outlives a closed dialog must not hold its Tk objects.

def load_worker(events, request, directory):
    try:
        if not directory:
            events.put(("loaded", request, None, None))
            return
        report = colibri_client.status(directory)
        rows = colibri_client.recommendations(directory)
        events.put(("loaded", request, (directory, report, rows), None))
    except Exception as error:
        events.put(("loaded", request, None, str(error)))


def install_worker(events, directory, row, cancel_event, ctx=None):
    def progress(phase, done, total):
        events.put(("progress", row["id"], (phase, done, total), None))
    try:
        colibri_client.install_model(directory, row["id"], progress, cancel_event, ctx=ctx or None)
        events.put(("installed", row["id"], row, None))
    except colibri_client.ColibriCancelled as error:
        events.put(("installed", row["id"], row, "cancelled: " + str(error)))
    except Exception as error:
        events.put(("installed", row["id"], row, str(error)))


def context_worker(events, directory, ctx):
    try:
        colibri_client.set_context(directory, ctx)
        events.put(("context", "__context__", ctx, None))
    except Exception as error:
        events.put(("context", "__context__", ctx, str(error)))


class ColibriRecommendationsPanel:
    def __init__(self, dialog, parent):
        self.dialog = dialog
        self.app = dialog.app
        self.events = queue.Queue()
        self.rows = []
        self.report = None
        self.loading = False
        self.installing = None
        self.cancel_event = threading.Event()
        self.card_buttons = []
        self.request = 0
        self.notice = ("", False)
        self.closed = False
        c = self.app.COLORS
        self.frame = tk.Frame(parent, bg=c["surface"])
        header = tk.Frame(self.frame, bg=c["background"])
        header.pack(fill="x", padx=16, pady=14)
        top = tk.Frame(header, bg=c["background"])
        top.pack(fill="x", padx=14, pady=(10, 3))
        tk.Label(top, text="COLIBRI ENGINE  |  RAM + SSD", bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.refresh_button = self.app.button(top, "Rescan", self.refresh, c["background"], c["accent"], "#e8ecf6")
        self.refresh_button.config(pady=4)
        self.refresh_button.pack(side="right")
        self.folder_button = self.app.button(top, "Choose colibri folder...", self.choose_folder,
                                             c["background"], c["accent"], "#e8ecf6")
        self.folder_button.config(pady=4)
        self.folder_button.pack(side="right", padx=(0, 6))
        self.github_button = self.app.button(top, "colibri on GitHub", lambda: webbrowser.open(colibri_client.GITHUB_URL),
                                             c["background"], c["accent"], "#e8ecf6")
        self.github_button.config(pady=4)
        self.github_button.pack(side="right", padx=(0, 6))
        self.summary = tk.Label(header, text="Looking for colibri...", bg=c["background"], fg=c["ink"],
                                font=("Segoe UI", 10), anchor="w", justify="left", wraplength=820)
        self.summary.pack(fill="x", padx=14, pady=(0, 4))
        self.comparison = tk.Label(header, text="", bg=c["background"], fg=c["accent"],
                                   font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=820)
        self.comparison.pack(fill="x", padx=14, pady=(0, 10))
        header.bind("<Configure>", lambda event: [label.config(wraplength=max(300, event.width - 28))
                                                   for label in (self.summary, self.comparison)])
        context_row = tk.Frame(self.frame, bg=c["surface"])
        context_row.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(context_row, text="Context", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.context_choice = tk.StringVar(self.frame, value="Automatic")
        self.context_picker = ttk.Combobox(context_row, textvariable=self.context_choice, state="readonly", width=16,
                                           values=[context_label(value) for value in colibri_client.CONTEXT_CHOICES],
                                           font=("Segoe UI", 9))
        self.context_picker.pack(side="left", padx=8)
        self.context_button = self.app.button(context_row, "Apply to the current model", self.apply_context,
                                              c["background"], c["accent"], "#e8ecf6")
        self.context_button.config(pady=4)
        self.context_button.pack(side="left")
        self.context_note = tk.Label(context_row, text="Used by Install & use. Longer contexts need more RAM.",
                                     bg=c["surface"], fg=c["muted"], font=("Segoe UI", 8))
        self.context_note.pack(side="left", padx=10)
        self.message = tk.Label(self.frame, text="", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9),
                                anchor="w", justify="left", wraplength=850)
        self.message.pack(fill="x", padx=18, pady=(0, 6))
        progress_row = tk.Frame(self.frame, bg=c["surface"])
        progress_row.pack(fill="x", padx=18, pady=(0, 8))
        self.cancel_button = self.app.button(progress_row, "Cancel setup", self.cancel_install,
                                             c["background"], c["ink"], "#e8ecf6")
        self.cancel_button.config(pady=4)
        self.cancel_button.pack(side="right")
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100,
                                        style="Activity.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=6)
        area = tk.Frame(self.frame, bg=c["surface"])
        area.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        self.canvas = tk.Canvas(area, bg=c["surface"], highlightthickness=0, width=1)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview, style="Chat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.cards = tk.Frame(self.canvas, bg=c["surface"])
        self.cards_window = self.canvas.create_window((0, 0), window=self.cards, anchor="nw", width=740)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.cards_window, width=event.width))
        self.cards.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<MouseWheel>", self.scroll_cards)
        self.refresh()
        self.poll_id = self.frame.after(100, self.process_events)

    # ------------------------------------------------------------ loading

    def refresh(self):
        if self.installing or self.closed:
            return
        self.request += 1
        self.loading = True
        self.clear_cards()
        self.summary.config(text="Looking for colibri...")
        self.message.config(text="colibri checks this PC's RAM, disk, CPU and GPU against its own model catalog.")
        self.update_controls()
        directory = self.app.colibri_directory()
        threading.Thread(target=load_worker, args=(self.events, self.request, directory), daemon=True).start()

    def choose_folder(self):
        if self.installing:
            return
        folder = filedialog.askdirectory(parent=self.dialog.window, title="Choose the colibri folder (it contains c/coli)")
        if not folder:
            return
        try:
            self.app.set_colibri_directory(folder)
        except colibri_client.ColibriError as error:
            self.message.config(text=str(error), fg=self.app.COLORS["error"])
            return
        self.message.config(fg=self.app.COLORS["muted"])
        self.refresh()

    # ------------------------------------------------------------ cards

    def scroll_cards(self, event):
        if self.closed:
            return None
        self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def clear_cards(self):
        for child in self.cards.winfo_children():
            child.destroy()
        self.card_buttons = []

    def configured_model(self):
        setup = (self.report or {}).get("setup") or {}
        if not setup.get("ready"):
            return None
        return (setup.get("model") or {}).get("id")

    def show_cards(self):
        self.clear_cards()
        c = self.app.COLORS
        for row in self.rows:
            card = tk.Frame(self.cards, bg=c["background"], highlightbackground=c["accent"] if row.get("recommended") else c["border"],
                            highlightthickness=1)
            card.pack(fill="x", pady=(0, 12), padx=3)
            content = tk.Frame(card, bg=c["background"])
            content.pack(fill="x", padx=16, pady=12)
            title = row["name"] + ("   ★ Recommended for this PC" if row.get("recommended") else "")
            tk.Label(content, text=title, bg=c["background"], fg=c["ink"] if row.get("fits") else c["muted"],
                     font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
            engine, sizes, fit = card_text(row)
            tk.Label(content, text=engine, bg=c["background"], fg=c["accent"], font=("Segoe UI", 9),
                     anchor="w").pack(fill="x", pady=(2, 4))
            details = tk.Label(content, text="\n".join(item for item in (row.get("summary"), sizes, fit) if item),
                               bg=c["background"], fg=c["muted"], font=("Segoe UI", 9), anchor="w",
                               justify="left", wraplength=740)
            details.pack(fill="x", pady=(0, 10))
            card.bind("<Configure>", lambda event, label=details: label.config(wraplength=max(250, event.width - 36)))
            actions = tk.Frame(content, bg=c["background"])
            actions.pack(fill="x")
            button = self.app.button(actions, "Install & use", lambda item=row: self.choose(item),
                                     c["accent"], "white", c["accent_hover"])
            button.pack(side="right")
            if row.get("doc"):
                self.app.button(actions, "Model notes", lambda item=row: webbrowser.open(
                    colibri_client.GITHUB_URL + "/blob/main/" + item["doc"].lstrip("/")),
                    "#e8ecf6", c["ink"], "#dce2f1").pack(side="right", padx=(0, 8))
            self.card_buttons.append((row, button))
            widgets = [card]
            while widgets:
                widget = widgets.pop()
                widget.bind("<MouseWheel>", self.scroll_cards, add="+")
                widgets.extend(widget.winfo_children())
        self.update_controls()

    def update_controls(self):
        if self.closed:
            return
        busy = bool(self.installing) or self.loading
        configured = self.configured_model()
        running = colibri_client.server_state(self.report or {}) == "ready"
        for row, button in self.card_buttons:
            current = row["id"] == configured
            usable = row.get("fits") and row.get("kind") == "text"
            if self.installing == row["id"]:
                text = "Setting up..."
            elif current:
                text = "Use in chat" if running else "Start & use"
            elif not usable:
                text = "Not for chat" if row.get("fits") else "Doesn't fit"
            else:
                text = f"Install & use ({gb(row.get('download_gb'))})"
            enabled = not busy and (current or usable) and not self.dialog.app_busy()
            button.config(text=text, state="normal" if enabled else "disabled",
                          bg=self.app.COLORS["accent"] if enabled else "#e5e9f2")
        self.refresh_button.config(state="disabled" if busy else "normal")
        self.folder_button.config(state="disabled" if busy else "normal")
        self.context_picker.config(state="disabled" if busy else "readonly")
        can_apply = (not busy and configured is not None and not self.dialog.app_busy()
                     and context_value(self.context_choice.get()) != ((self.report or {}).get("ctx") or 0))
        self.context_button.config(state="normal" if can_apply else "disabled")
        self.cancel_button.config(state="normal" if self.installing and not self.cancel_event.is_set() else "disabled")

    def update_comparison(self):
        advisor = getattr(self.dialog, "advisor", None)
        text = comparison_text(getattr(advisor, "recommendations", []) or [], self.rows)
        if text != self.comparison.cget("text"):
            self.comparison.config(text=text)

    # ------------------------------------------------------------ install and use

    def choose(self, row):
        if self.installing or self.loading:
            return
        if self.dialog.app_busy():
            self.message.config(text="Wait for the current chat or model operation to finish first.")
            return
        if row["id"] == self.configured_model():
            self.use()
            return
        size = gb(row.get("download_gb"))
        if not messagebox.askyesno(
                "Install with colibri",
                f"colibri will download {row['name']} ({size}), prepare its engine for this PC, and start it.\n\n"
                "Large downloads take a long time. You can cancel; the next run resumes where it stopped.",
                parent=self.dialog.window):
            return
        self.installing = row["id"]
        self.cancel_event = threading.Event()
        self.progress.config(value=0)
        self.message.config(text=f"Setting up {row['name']} with colibri...", fg=self.app.COLORS["muted"])
        self.update_controls()
        ctx = context_value(self.context_choice.get())
        threading.Thread(target=install_worker, args=(self.events, self.app.colibri_directory(), dict(row), self.cancel_event, ctx),
                         daemon=True).start()

    def apply_context(self):
        if self.installing or self.loading or self.configured_model() is None:
            return
        if self.dialog.app_busy():
            self.message.config(text="Wait for the current chat or model operation to finish first.")
            return
        ctx = context_value(self.context_choice.get())
        self.installing = "__context__"
        self.message.config(text=f"Restarting colibri with context {context_label(ctx)}...", fg=self.app.COLORS["muted"])
        self.update_controls()

        threading.Thread(target=context_worker, args=(self.events, self.app.colibri_directory(), ctx), daemon=True).start()

    def cancel_install(self):
        if self.installing:
            self.cancel_event.set()
            self.message.config(text="Stopping colibri setup...")
            self.update_controls()

    def use(self):
        if self.dialog.app_busy():
            self.message.config(text="Wait for the current operation to finish, then choose Use in chat.")
            return
        if self.app.select_colibri_model(self.configured_model()):
            self.dialog.close()
            self.app.root.lift()

    # ------------------------------------------------------------ events

    def process_events(self):
        for _ in range(20):
            try:
                kind, request, data, error = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "loaded" and request == self.request:
                self.loading = False
                if error:
                    self.summary.config(text="colibri was found, but it did not answer.")
                    self.message.config(text=f"{error}\nUpdate colibri (git pull) or run START-HERE once, then Rescan.",
                                        fg=self.app.COLORS["error"])
                elif data is None:
                    self.rows = []
                    self.summary.config(text=(
                        "colibri is not installed, or AI Assist does not know its folder.\n"
                        "1. Download colibri from GitHub (Code > Download ZIP) and extract it.\n"
                        "2. Run START-HERE.bat (Windows) or ./start-here.sh (macOS/Linux) once.\n"
                        "3. Choose colibri folder... here, then Rescan."))
                    self.message.config(text="colibri runs very large models from RAM and SSD, with or without a GPU.",
                                        fg=self.app.COLORS["muted"])
                else:
                    directory, self.report, self.rows = data
                    self.context_choice.set(context_label(self.report.get("ctx")))
                    setup = (self.report.get("setup") or {})
                    model = (setup.get("model") or {}).get("name")
                    state = colibri_client.server_state(self.report)
                    self.summary.config(text=f"colibri folder: {directory}\n"
                                        + (f"Set up: {model} ({setup.get('backend') or 'cpu'})  |  context "
                                           f"{context_label(self.report.get('ctx')).lower()}  |  server {state}"
                                           if model else "No model set up yet: pick one below."))
                    fitting = sum(1 for row in self.rows if row.get("fits"))
                    # A result of the last action stays visible above the general explanation.
                    (notice, failed), self.notice = self.notice, ("", False)
                    self.message.config(text=(notice + "\n" if notice else "")
                                        + f"{fitting} of {len(self.rows)} colibri models fit this PC. "
                                        "Install & use downloads the model, prepares the engine, and starts it. "
                                        "colibri serves one model at a time.",
                                        fg=self.app.COLORS["error" if failed else "muted"])
                    self.show_cards()
                self.update_comparison()
                self.update_controls()
            elif kind == "progress" and request == self.installing and not self.cancel_event.is_set():
                phase, done, total = data
                if phase == "download" and total:
                    self.progress.config(value=min(100, max(0, done / total * 100)))
                    self.message.config(text=f"Downloading {request}: {readable(done)} / {readable(total)}")
                elif phase:
                    self.message.config(text=f"colibri setup: {phase}...")
            elif kind == "context" and request == self.installing:
                self.installing = None
                if error:
                    self.notice = (error, True)
                else:
                    self.notice = (f"colibri restarted with context {context_label(data)}.", False)
                    if self.app.backend_name == "colibri":
                        self.app.health_poll_at = 0
                self.refresh()
            elif kind == "installed" and request == self.installing:
                self.installing = None
                if error and error.startswith("cancelled: "):
                    self.notice = (error[len("cancelled: "):], False)
                elif error:
                    self.notice = (error, True)
                else:
                    self.progress.config(value=100)
                    self.notice = (f"{data['name']} is set up and starting. Choose Use in chat.", False)
                self.refresh()
        if not self.closed:
            self.update_comparison()
            self.update_controls()
            self.poll_id = self.frame.after(150, self.process_events)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_event.set()
        try:
            self.frame.after_cancel(self.poll_id)
        except (tk.TclError, AttributeError):
            pass
