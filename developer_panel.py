"""Project files, source, applied changes, and output for the native developer workspace."""

import tkinter as tk
from tkinter import END, scrolledtext, ttk

from coding_agent import FILE_LIMIT_CHOICES


def _limit_label(value):
    return f"{value // 1000:,} KB" if value < 1000000 else f"{value / 1000000:g} MB"


class DeveloperPanelMixin:
    def _developer_text(self, parent, wrap="none"):
        c = self.COLORS
        widget = scrolledtext.ScrolledText(
            parent, width=1, height=5, wrap=wrap, font=("Consolas", 10),
            bg=c["surface"], fg=c["ink"], insertbackground=c["accent"],
            selectbackground=c["accent"], selectforeground="white",
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=10, pady=10, state="disabled",
        )
        widget._aiassist_code = True
        if wrap == "none":
            horizontal = ttk.Scrollbar(parent, orient="horizontal", command=widget.xview,
                                       style="Code.Horizontal.TScrollbar")
            horizontal.pack(side="bottom", fill="x", pady=(0, 6))
            widget.configure(xscrollcommand=horizontal.set)
        return widget

    def build_developer_panel(self):
        c = self.COLORS
        self.dev_panel = tk.Frame(self.chat_section, bg=c["sidebar"], width=350)
        self.dev_panel.pack_propagate(False)
        heading = tk.Frame(self.dev_panel, bg=c["sidebar"])
        heading.pack(fill="x", padx=14, pady=(16, 10))
        self.project_tools_menu = tk.Menu(self.root, tearoff=False)
        self.project_tools_button = self.button(
            heading, "Tools", self.open_project_tools,
            c["sidebar"], c["muted"], c["sidebar_hover"],
        )
        self.project_tools_button.config(font=("Segoe UI", 9), padx=10, pady=4)
        self.project_tools_button.pack(side="right")
        tk.Label(heading, text="Project", bg=c["sidebar"], fg=c["ink"],
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        self.project_directory = tk.StringVar(self.root)
        self.project_entry = ttk.Entry(self.dev_panel, textvariable=self.project_directory,
                                       font=("Segoe UI", 9))
        self.project_entry.pack(fill="x", padx=14)
        self.project_entry.bind("<Return>", lambda event: self.use_project_directory())
        actions = tk.Frame(self.dev_panel, bg=c["sidebar"])
        actions.pack(fill="x", padx=14, pady=(8, 0))
        self.choose_project_button = self.button(
            actions, "Open project...", self.choose_project,
            c["accent"], "white", c["accent_hover"],
        )
        self.use_project_button = self.button(
            actions, "Use path", self.use_project_directory,
            c["sidebar_hover"], c["ink"], c["border"],
        )
        for button in (self.choose_project_button, self.use_project_button):
            button.config(font=("Segoe UI", 9), padx=12, pady=7)
            button.pack(side="left", padx=(0, 8))
        self.project_label = tk.Label(
            self.dev_panel, text="Open a project to browse files and work on its code.",
            bg=c["sidebar"], fg=c["muted"], font=("Segoe UI", 9),
            wraplength=320, justify="left", anchor="w",
        )
        self.project_label.pack(fill="x", padx=14, pady=(10, 12))

        # These remain real buttons so existing state checks and invocations
        # work; the project menu makes these secondary, optional actions.
        self.deploy_button = self.button(
            self.dev_panel, "Deploy to Vercel...", self.open_deploy,
            c["accent"], "white", c["accent_hover"],
        )
        self.preview_file_button = self.button(
            self.dev_panel, "Preview HTML in browser", self.preview_html_file,
            c["sidebar_hover"], c["ink"], c["border"],
        )
        self.project_tools_menu.add_command(label="Refresh files", command=self.scan_project)
        self.project_tools_menu.add_command(label="Preview HTML in browser", command=self.preview_file_button.invoke)
        self.project_tools_menu.add_separator()
        self.project_tools_menu.add_command(label="Deploy to Vercel...", command=self.deploy_button.invoke)

        style = ttk.Style(self.root)
        style.configure("Developer.TNotebook", borderwidth=0, tabmargins=(0, 3, 0, 0))
        style.configure("Developer.TNotebook.Tab", padding=(7, 7), font=("Segoe UI", 9))
        self.dev_tabs = ttk.Notebook(self.dev_panel, style="Developer.TNotebook")
        self.dev_tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        files, source, changes, output, log = (
            tk.Frame(self.dev_tabs, bg=c["sidebar"]) for _ in range(5)
        )
        for tab, title in ((files, "Files"), (source, "Source"), (changes, "Changes"),
                           (output, "Output"), (log, "Activity")):
            self.dev_tabs.add(tab, text=title)
        self.source_tab = source
        self.activity_tab = log

        tk.Label(files, text="Filter project files", bg=c["sidebar"], fg=c["muted"],
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(9, 4))
        self.file_search = tk.StringVar(self.root)
        self.file_search_entry = ttk.Entry(files, textvariable=self.file_search, font=("Segoe UI", 9))
        self.file_search_entry.pack(fill="x", pady=(0, 6))
        self.visible_project_files = []
        self.files_list = tk.Listbox(
            files, selectmode="extended", exportselection=False, width=1, height=6,
            font=("Consolas", 9), bg=c["surface"], fg=c["ink"],
            selectbackground=c["user"], selectforeground=c["ink"],
            relief="flat", borderwidth=0, highlightthickness=0,
        )
        self.files_list.pack(fill="both", expand=True)
        self.files_list.bind("<Double-Button-1>", self.preview_project_file)
        self.files_list.bind("<Return>", self.preview_project_file)
        self.file_search.trace_add("write", lambda *args: self.render_project_files())
        self.attach_button = self.button(
            files, "Pin selected files", self.attach_selected_files,
            c["sidebar_hover"], c["ink"], c["border"],
        )
        self.attach_button.config(font=("Segoe UI", 9), pady=7)
        self.attach_button.pack(fill="x", pady=(8, 6))
        self.context_label = tk.Label(
            files, text="No pinned files. Double-click a file to read its source.",
            bg=c["sidebar"], fg=c["muted"], font=("Segoe UI", 9),
            wraplength=310, justify="left", anchor="w",
        )
        self.context_label.pack(fill="x", pady=(0, 8))
        limit_row = tk.Frame(files, bg=c["sidebar"])
        limit_row.pack(fill="x", pady=(0, 6))
        tk.Label(limit_row, text="File read limit", bg=c["sidebar"], fg=c["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        self.file_limit_choice = tk.StringVar(self.root, value=_limit_label(self.agent_file_limit))
        self.file_limit_picker = ttk.Combobox(
            limit_row, textvariable=self.file_limit_choice, state="readonly", width=9,
            values=[_limit_label(value) for value in FILE_LIMIT_CHOICES], font=("Segoe UI", 9),
        )
        self.file_limit_picker.pack(side="right")
        self.file_limit_picker.bind("<<ComboboxSelected>>", self.choose_file_limit)

        self.source_title = tk.Label(
            source, text="No file selected", bg=c["sidebar"], fg=c["muted"],
            font=("Segoe UI", 9), wraplength=310, justify="left", anchor="w",
        )
        self.source_title.pack(fill="x", pady=9)
        self.source_preview = self._developer_text(source)
        self.source_preview.pack(fill="both", expand=True, pady=(0, 6))
        self.replace_text(self.source_preview, "Double-click a project file in Files to read its source here.")

        tk.Label(changes, text="Applied changes in this conversation", bg=c["sidebar"],
                 fg=c["muted"], font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(9, 6))
        self.changes_list = ttk.Combobox(changes, state="disabled", font=("Segoe UI", 9))
        self.changes_list.pack(fill="x", pady=(0, 8))
        self.changes_list.bind("<<ComboboxSelected>>", self.preview_change)
        self.changes_preview = self._developer_text(changes)
        self.changes_preview.pack(fill="both", expand=True, pady=(0, 6))
        self.applied_changes = []
        self.refresh_changes()

        self.code_list = ttk.Combobox(output, state="readonly", font=("Segoe UI", 9))
        self.code_list.pack(fill="x", pady=9)
        self.code_list.bind("<<ComboboxSelected>>", self.preview_artifact)
        self.code_preview = self._developer_text(output)
        self.code_preview.pack(fill="both", expand=True)
        output_actions = tk.Frame(output, bg=c["sidebar"])
        output_actions.pack(side="bottom", fill="x", pady=8)
        self.save_code_button = self.button(
            output_actions, "Save selected code...", self.save_selected_artifact,
            c["sidebar_hover"], c["ink"], c["border"],
        )
        self.save_code_button.config(font=("Segoe UI", 9), pady=7)
        self.save_code_button.pack(fill="x")
        self.preview_code_button = self.button(
            output_actions, "Preview HTML in browser", self.preview_selected_html,
            c["sidebar_hover"], c["ink"], c["border"],
        )
        self.preview_code_button.config(state="disabled", font=("Segoe UI", 9), pady=7)
        self.preview_code_button.pack(fill="x", pady=(5, 0))

        self.stop_agent_button = self.button(
            log, "Stop agent", self.stop_agent,
            c["sidebar_hover"], c["error"], c["border"],
        )
        self.stop_agent_button.config(font=("Segoe UI", 9), pady=7)
        self.stop_agent_button.pack(side="bottom", fill="x", pady=(6, 6))
        self.dev_log = self._developer_text(log, wrap="word")
        self.dev_log.pack(fill="both", expand=True, pady=(9, 0))

    def open_project_tools(self):
        self.project_tools_menu.tk_popup(
            self.project_tools_button.winfo_rootx(),
            self.project_tools_button.winfo_rooty() + self.project_tools_button.winfo_height(),
        )

    def render_project_files(self):
        query = self.file_search.get().strip().casefold()
        self.visible_project_files = [
            item for item in self.project_files if query in item["path"].casefold()
        ]
        self.files_list.delete(0, END)
        pinned = set(self.project_selected_paths)
        for index, item in enumerate(self.visible_project_files):
            self.files_list.insert(END, item["path"])
            if item["path"] in pinned:
                self.files_list.selection_set(index)

    def selected_project_paths(self):
        return [self.visible_project_files[index]["path"] for index in self.files_list.curselection()
                if index < len(self.visible_project_files)]

    def refresh_changes(self):
        self.applied_changes = [event for event in self.dev_activity if event.get("type") == "changed"]
        self.changes_list.config(
            values=[f"{index + 1}. {event.get('path') or 'Project file'}"
                    for index, event in enumerate(self.applied_changes)],
            state="readonly" if self.applied_changes else "disabled",
        )
        if self.applied_changes:
            self.changes_list.current(len(self.applied_changes) - 1)
            self.preview_change()
        else:
            self.changes_list.set("")
            self.replace_text(self.changes_preview,
                              "No applied changes yet.\n\nReviewed file edits appear here after you apply them.")

    def preview_change(self, event=None):
        index = self.changes_list.current()
        if 0 <= index < len(self.applied_changes):
            diff = self.applied_changes[index].get("diff")
            self.replace_text(self.changes_preview, diff if isinstance(diff, str) and diff
                              else "No diff was saved for this change.")
