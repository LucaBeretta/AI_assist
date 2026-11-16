"""A read-only overview of workspace features and native Ollama model support.

Only the Tk thread reads application variables or updates widgets. Network
workers return plain data through a queue and never call Tk.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

import colibri_client
from developer_tools import fetch_model_capabilities


def _token_count(value, fallback):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return f"{value:,} tokens"
    return fallback


def _capability_status(capabilities, name):
    """Missing /api/show flags are unknown rather than a negative claim."""
    return "Advertised" if name in capabilities else "Not advertised"


class CapabilitiesView:
    """Embeddable capabilities page; the caller places ``frame`` in its layout."""

    def __init__(self, parent, app):
        self.app = app
        self.closed = False
        self.events = queue.Queue()
        self.request_id = 0
        self.poll_id = None
        self._model_trace = None
        self._metadata = None
        self._loaded_model = None
        self._loading = False
        c = app.COLORS
        self.frame = tk.Frame(parent, bg=c["background"])
        self.frame.bind("<Destroy>", self._destroyed, add="+")

        area = tk.Frame(self.frame, bg=c["background"])
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, bg=c["background"], borderwidth=0,
                                highlightthickness=0, width=1, yscrollincrement=24)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview,
                                  style="Chat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = tk.Frame(self.canvas, bg=c["background"])
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", self._resize)
        self.content.bind("<Configure>", self._content_resized)

        page = tk.Frame(self.content, bg=c["background"])
        page.pack(fill="both", expand=True, padx=32, pady=(30, 34))
        self._label(page, "YOUR WORKSPACE", 9, c["accent"], bold=True).pack(anchor="w")
        self._label(page, "Capabilities", 30, c["ink"], family="Georgia").pack(anchor="w", pady=(7, 8))
        self._label(page, "Know what your workspace can do, and what your selected model brings to it.",
                    11, c["muted"], wrap=True).pack(fill="x", pady=(0, 24))

        self._build_model_card(page)
        self._section_heading(page, "01", "Available in this app",
                              "Tools you can use today with your Ollama conversations.")
        self._build_workspace_card(page)
        self._section_heading(page, "02", "Native model support",
                              "Reported by Ollama for the selected model, independently of app features.")
        self._build_support_card(page)

        note = tk.Frame(page, bg=c["background"])
        note.pack(fill="x", pady=(18, 0))
        self._label(note, "Your model and your workspace have separate capabilities.", 10,
                    c["ink"], bold=True, wrap=True).pack(fill="x")
        self._label(note, "Model vision, tool-calling, or thinking flags describe the model. "
                    "The coding agent uses a tool-capable model and a selected project, with your review "
                    "before each file change or command. Image attachments, web search, and voice are not available.",
                    10, c["muted"], wrap=True).pack(fill="x", pady=(5, 0))

        self._bind_scroll(self.content)
        self.canvas.bind("<MouseWheel>", self._scroll)
        self.canvas.bind("<Button-4>", self._scroll)
        self.canvas.bind("<Button-5>", self._scroll)
        self._model_trace = app.model_name.trace_add("write", self._model_changed)
        self.refresh()
        self._schedule_poll()

    def _label(self, parent, text, size, color, bold=False, wrap=False, family="Segoe UI"):
        label = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                         font=(family, size, "bold" if bold else "normal"),
                         anchor="w", justify="left", borderwidth=0)
        if wrap:
            label.configure(wraplength=500)
            label.bind("<Configure>", lambda event: label.configure(wraplength=max(80, event.width)))
        return label

    def _card(self, parent):
        c = self.app.COLORS
        card = tk.Frame(parent, bg=c["surface"], highlightbackground=c["border"],
                        highlightthickness=1, borderwidth=0)
        card.pack(fill="x")
        return card

    def _build_model_card(self, parent):
        c = self.app.COLORS
        card = self._card(parent)
        interior = tk.Frame(card, bg=c["surface"])
        interior.pack(fill="x", padx=22, pady=20)
        header = tk.Frame(interior, bg=c["surface"])
        header.pack(fill="x")
        self._label(header, "SELECTED MODEL", 9, c["muted"], bold=True).pack(side="left")
        self.refresh_button = self.app.button(header, "Refresh", self.refresh,
                                              c["surface"], c["accent"], c["background"])
        self.refresh_button.configure(padx=10, pady=4)
        self.refresh_button.pack(side="right")
        self.model_label = self._label(interior, "No model selected", 19, c["ink"], bold=True, wrap=True)
        self.model_label.pack(fill="x", pady=(10, 3))
        self.detail_label = self._label(interior, "", 10, c["muted"], wrap=True)
        self.detail_label.pack(fill="x", pady=(0, 12))
        self.status_label = self._label(interior, "", 10, c["muted"], wrap=True)
        self.status_label.pack(fill="x", pady=(0, 16))

        self.context_area = tk.Frame(interior, bg=c["surface"])
        self.context_area.pack(fill="x")
        self.context_area.columnconfigure(0, weight=1, uniform="context")
        self.context_area.columnconfigure(1, weight=1, uniform="context")
        self.context_boxes = []
        context_labels = []
        for column, title in enumerate(("Native maximum context", "Configured app context")):
            box = tk.Frame(self.context_area, bg=c["background"])
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
            self.context_boxes.append(box)
            self._label(box, title, 9, c["muted"], wrap=True).pack(fill="x", padx=12, pady=(11, 3))
            value = self._label(box, "Unknown", 13, c["ink"], bold=True, wrap=True)
            value.pack(fill="x", padx=12, pady=(0, 11))
            context_labels.append(value)
        self.native_context_label, self.app_context_label = context_labels
        self._label(interior, "The native maximum is model metadata. App context is the requested chat setting; "
                    "neither value measures the context currently allocated by Ollama.",
                    9, c["muted"], wrap=True).pack(fill="x", pady=(10, 15))
        self.download_button = self.app.button(interior, "Download models", self.app.open_downloads,
                                               c["accent"], c["surface"], c["accent_hover"])
        self.download_button.configure(pady=8)
        self.download_button.pack(anchor="w")

    def _section_heading(self, parent, number, title, subtitle):
        c = self.app.COLORS
        heading = tk.Frame(parent, bg=c["background"])
        heading.pack(fill="x", pady=(27, 12))
        top = tk.Frame(heading, bg=c["background"])
        top.pack(fill="x")
        self._label(top, number, 10, c["accent"]).pack(side="left", padx=(0, 10))
        self._label(top, title, 14, c["ink"], bold=True).pack(side="left")
        self._label(heading, subtitle, 10, c["muted"], wrap=True).pack(fill="x", pady=(6, 0))

    def _build_workspace_card(self, parent):
        c = self.app.COLORS
        card = self._card(parent)
        features = (
            ("Local Ollama chat", "Talk with installed models through your configured Ollama server."),
            ("Saved conversations", "Save and search chats locally, then rename, export, or delete them."),
            ("Project text context", "Choose a project and select readable text files to include in a conversation."),
            ("Coding agent", "Let the agent read project text files and propose changes. Review each file diff "
             "before Apply, and each command before Run. Command output returns to the agent."),
            ("Code artifacts", "View code from fenced responses, copy it, or explicitly choose a destination with Save As."),
            ("Model setup", "Find hardware-based download recommendations and model setup guides."),
        )
        for index, (title, detail) in enumerate(features):
            if index:
                tk.Frame(card, bg=c["border"], height=1).pack(fill="x", padx=20)
            row = tk.Frame(card, bg=c["surface"])
            row.pack(fill="x", padx=20, pady=14)
            self._label(row, title, 11, c["ink"], bold=True).pack(fill="x")
            self._label(row, detail, 10, c["muted"], wrap=True).pack(fill="x", pady=(4, 0))
            if title == "Coding agent":
                self.agent_status_label = self._label(row, "Choose a model and project to use the coding agent.",
                                                      10, c["muted"], wrap=True)
                self.agent_status_label.pack(fill="x", pady=(9, 4))
                self.agent_button = self.app.button(row, "Open Developer", self._open_developer,
                                                    c["background"], c["accent"], c["user"])
                self.agent_button.configure(padx=10, pady=6)
                self.agent_button.pack(anchor="w")

    def _build_support_card(self, parent):
        c = self.app.COLORS
        card = self._card(parent)
        self.support_labels = {}
        descriptions = (
            ("completion", "Text generation", "The model advertises text completion support."),
            ("vision", "Vision", "Image understanding is a model feature. Image attachments are not available in this app."),
            ("tools", "Tool calling", "Used by the coding agent when a project is selected. "
             "File changes and commands each require your review."),
            ("thinking", "Thinking", "The model advertises a reasoning mode. This page does not enable or control that mode."),
        )
        for index, (key, title, detail) in enumerate(descriptions):
            if index:
                tk.Frame(card, bg=c["border"], height=1).pack(fill="x", padx=20)
            row = tk.Frame(card, bg=c["surface"])
            row.pack(fill="x", padx=20, pady=14)
            top = tk.Frame(row, bg=c["surface"])
            top.pack(fill="x")
            status = self._label(top, "Not checked", 9, c["muted"])
            status.pack(side="right", padx=(8, 0))
            self._label(top, title, 11, c["ink"], bold=True).pack(side="left")
            self.support_labels[key] = status
            self._label(row, detail, 10, c["muted"], wrap=True).pack(fill="x", pady=(4, 0))
        footer = tk.Frame(card, bg=c["background"])
        footer.pack(fill="x")
        self.flags_label = self._label(footer, "Capability flags have not been checked.", 9, c["muted"], wrap=True)
        self.flags_label.pack(fill="x", padx=20, pady=13)

    def _resize(self, event):
        if self.closed:
            return
        self.canvas.itemconfigure(self._window, width=max(1, event.width))
        narrow = event.width < 560
        for index, box in enumerate(self.context_boxes):
            box.grid_configure(row=index if narrow else 0, column=0 if narrow else index,
                               columnspan=2 if narrow else 1,
                               padx=0 if narrow else ((0, 6) if index == 0 else (6, 0)),
                               pady=(0, 8) if narrow and index == 0 else 0)

    def _content_resized(self, _event):
        if not self.closed:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _bind_scroll(self, widget):
        widget.bind("<MouseWheel>", self._scroll, add="+")
        widget.bind("<Button-4>", self._scroll, add="+")
        widget.bind("<Button-5>", self._scroll, add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _scroll(self, event):
        if self.closed:
            return
        if self.canvas.yview() == (0.0, 1.0):
            return
        if getattr(event, "num", None) in (4, 5):
            amount = -1 if event.num == 4 else 1
        else:
            delta = getattr(event, "delta", 0)
            amount = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            if not delta:
                return
        self.canvas.yview_scroll(amount, "units")
        return "break"

    def _model_changed(self, *_args):
        self.refresh()

    def _open_developer(self):
        self.app.show_section("dev")

    def _show_agent_status(self):
        """Model support alone does not enable access to a project."""
        c = self.app.COLORS
        model = self.app.model_name.get().strip()
        project = getattr(self.app, "project_path", None)
        if isinstance(project, tk.Variable):
            project = project.get()
        if not getattr(self.app, "project_ready", True):
            project = None
        metadata_matches = self._loaded_model == model and self._metadata is not None
        tools = metadata_matches and "tools" in self._metadata.get("capabilities", [])
        ready = False
        if not model:
            text = "Choose a model and project to use the coding agent."
        elif not metadata_matches:
            text = "Checking model tool support..." if self._loading else "Refresh model metadata to check agent availability."
        elif not tools:
            text = "Tool calling is not advertised by this model. Choose a tool-capable model for the coding agent."
        elif not project:
            text = "Tool calling is advertised. Choose a project in Developer to enable the coding agent."
        else:
            ready = True
            text = "Coding agent available for the selected project. Review each file change and command before it runs."
        self.agent_status_label.configure(text=text, fg=c["success"] if ready else c["muted"])

    def _show_configured_context(self, model):
        context = self.app.model_contexts.get(model) if model else None
        colibri = getattr(self.app, "backend_name", "ollama") == "colibri"
        default = "Set by colibri setup (--ctx)" if colibri else "Ollama default"
        self.app_context_label.configure(text=_token_count(None if colibri else context, default if model else "No model selected"))

    def _reset_metadata(self, status):
        c = self.app.COLORS
        self._metadata = None
        self._loaded_model = None
        self.detail_label.configure(text="Model family, size, and quantization appear after inspection.")
        self.native_context_label.configure(text="Unknown")
        for label in self.support_labels.values():
            label.configure(text=status, fg=c["muted"])
        self.flags_label.configure(text="Capability flags have not been checked.")
        self._show_agent_status()

    def refresh(self):
        """Inspect the selected model without blocking the Tk event loop."""
        if self.closed:
            return
        self.request_id += 1
        request_id = self.request_id
        model = self.app.model_name.get().strip()
        self._loading = bool(model)
        self.model_label.configure(text=model or "No model selected")
        self._show_configured_context(model)
        self._reset_metadata("Checking..." if model else "Not checked")
        self.refresh_button.configure(text="Refresh")
        if not model:
            self.status_label.configure(text="Choose an installed model from the sidebar, or download one to get started.",
                                        fg=self.app.COLORS["muted"])
            return
        colibri = getattr(self.app, "backend_name", "ollama") == "colibri"
        self.status_label.configure(text=f"Reading model metadata from {'colibri' if colibri else 'Ollama'}...", fg=self.app.COLORS["muted"])
        try:
            host = self.app.active_host() if colibri else self.app.ollama_host()
        except Exception as error:
            self._show_error(str(error))
            return
        family = self.app.colibri_family() if colibri else None
        threading.Thread(target=self._fetch_worker, args=(request_id, host, model, colibri, family), daemon=True).start()

    def _fetch_worker(self, request_id, host, model, colibri=False, family=None):
        try:
            metadata = (colibri_client.capabilities(host, model, family) if colibri
                        else fetch_model_capabilities(host, model))
            self.events.put((request_id, model, metadata, None))
        except Exception as error:
            self.events.put((request_id, model, None, str(error)))

    def _show_error(self, error):
        self._loading = False
        self._reset_metadata("Unavailable")
        self.status_label.configure(text=(error.strip() or "Model metadata is unavailable.") +
                                    " Use Refresh to retry.", fg=self.app.COLORS["error"])
        self.refresh_button.configure(text="Retry")

    def _show_metadata(self, model, metadata):
        c = self.app.COLORS
        self._loading = False
        self._metadata = metadata
        self._loaded_model = model
        self.model_label.configure(text=model)
        details = [metadata.get(key) for key in ("family", "parameter_size", "quantization") if metadata.get(key)]
        self.detail_label.configure(text="  ·  ".join(details) or "Family, size, and quantization were not reported.")
        self.native_context_label.configure(text=_token_count(metadata.get("context_length"), "Not reported"))
        self._show_configured_context(model)
        capabilities = metadata.get("capabilities", [])
        for name, label in self.support_labels.items():
            label.configure(text=_capability_status(capabilities, name),
                            fg=c["success"] if name in capabilities else c["muted"])
        flags = ", ".join(capabilities) if capabilities else "none returned"
        if metadata.get("engine") == "colibri":
            self.flags_label.configure(text=f"colibri capabilities: {flags}. Tool calling follows the model family "
                                       "(GLM, DeepSeek V4, Kimi, Qwen3.6/3.8, MiMo); the context is the one colibri loaded.")
            self.status_label.configure(text="Model metadata received from colibri.", fg=c["muted"])
        else:
            self.flags_label.configure(text=f"Ollama /api/show flags: {flags}. "
                                       "A missing flag is not proof that a model lacks the feature; older servers may omit flags.")
            self.status_label.configure(text="Model metadata received from Ollama.", fg=c["muted"])
        self.refresh_button.configure(text="Refresh")
        self._show_agent_status()

    def _process_events(self):
        self.poll_id = None
        if self.closed:
            return
        selected = self.app.model_name.get().strip()
        self._show_configured_context(selected)
        self._show_agent_status()
        while True:
            try:
                request_id, model, metadata, error = self.events.get_nowait()
            except queue.Empty:
                break
            if request_id != self.request_id or model != selected:
                continue
            if error is not None:
                self._show_error(error)
            else:
                self._show_metadata(model, metadata)
        self._schedule_poll()

    def _schedule_poll(self):
        if not self.closed:
            self.poll_id = self.frame.after(120, self._process_events)

    def _destroyed(self, event):
        if event.widget == self.frame:
            self.close()

    def close(self):
        """Stop UI polling and detach model traces; outstanding workers may finish."""
        if self.closed:
            return
        self.closed = True
        self.request_id += 1
        if self.poll_id is not None:
            try:
                self.frame.after_cancel(self.poll_id)
            except tk.TclError:
                pass
            self.poll_id = None
        if self._model_trace is not None:
            try:
                self.app.model_name.trace_remove("write", self._model_trace)
            except tk.TclError:
                pass
            self._model_trace = None
