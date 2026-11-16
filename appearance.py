"""Live appearance preferences without rebuilding the conversation workspace."""

import json
import tkinter as tk
from tkinter import ttk, font


class Appearance:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.warm = dict(app.COLORS)
        self.themes = {
            "Warm": self.warm,
            "Light": dict(self.warm, background="#f5f6fa", surface="#ffffff",
                          sidebar="#e9edf5", sidebar_hover="#dce2ef", user="#edf0ff",
                          ink="#243047", muted="#65738b", sidebar_muted="#65738b",
                          border="#d5dcea", accent="#4c61d8", accent_hover="#3c4fbd"),
            "Dark": dict(self.warm, background="#191c24", surface="#232834",
                         sidebar="#202430", sidebar_hover="#343c4d", user="#30384c",
                         ink="#edf0f7", muted="#b0bacd", sidebar_muted="#b0bacd",
                         border="#414b60", accent="#8096f4", accent_hover="#6b81df",
                         success="#70d3b0", error="#ff9d93"),
        }
        # Keep the same dictionary: existing event callbacks hold references to it.
        self.theme = "Warm"
        self.style = "Classic"
        self.path = app.chat_store.directory / "appearance.settings" if app.chat_store else None
        selected = {}
        if self.path:
            try:
                source = self.path if self.path.exists() else self.path.with_suffix(".json")
                selected = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if not isinstance(selected, dict):
            selected = {}
        self.apply(selected.get("theme", "Warm"), selected.get("style", "Classic"))
        app.root.bind_all("<Map>", self.mapped, add="+")

    def color_map(self, previous):
        c = self.app.COLORS
        colors = {value.lower(): c[key] for key, value in previous.items()}
        colors.update({"#d9d4c9": c["border"], "#e7d9ca": c["user"],
                       "#e5e9f2": c["border"], "#f5f2eb": c["background"],
                       "#ece8de": c["sidebar_hover"]})
        return colors

    def update_widget(self, widget, colors, previous):
        options = widget.keys()
        roles = getattr(widget, "_appearance_roles", {})
        role_colors = {value.lower(): key for key, value in previous.items()}
        for option in ("background", "foreground", "activebackground", "activeforeground",
                       "disabledforeground", "insertbackground", "selectbackground",
                       "highlightbackground", "troughcolor"):
            if option in options:
                value = str(widget.cget(option)).lower()
                role = roles.get(option)
                if role and value == previous[role].lower():
                    widget.configure(**{option: self.app.COLORS[role]})
                elif value in role_colors:
                    roles[option] = role_colors[value]
                    widget.configure(**{option: self.app.COLORS[roles[option]]})
                elif value in colors:
                    widget.configure(**{option: colors[value]})
        widget._appearance_roles = roles
        for attr in ("_appearance_bg", "_appearance_hover"):
            if hasattr(widget, attr):
                value = getattr(widget, attr)
                setattr(widget, attr, colors.get(value.lower(), value))
        if "font" in options:
            current = font.Font(root=self.app.root, font=widget.cget("font"))
            family = current.actual("family")
            if family.lower() in ("georgia", "segoe ui"):
                if not hasattr(widget, "_appearance_font"):
                    widget._appearance_font = current.actual()
                original = widget._appearance_font
                family = "Segoe UI" if self.style == "Modern" else original["family"]
                widget.configure(font=(family, original["size"],
                                       original["weight"], original["slant"]))

    def walk(self, widget, colors, previous):
        self.update_widget(widget, colors, previous)
        for child in widget.winfo_children():
            self.walk(child, colors, previous)

    def mapped(self, event):
        if isinstance(event.widget, tk.Misc):
            self.walk(event.widget, self.color_map(self.warm), self.warm)

    def apply(self, theme, style):
        theme = theme if isinstance(theme, str) and theme in self.themes else "Warm"
        style = style if style in ("Classic", "Modern") else "Classic"
        previous = dict(self.app.COLORS)
        self.theme, self.style = theme, style
        self.app.COLORS.update(self.themes[theme])
        self.app.configure_styles()
        c = self.app.COLORS
        native = ttk.Style(self.app.root)
        native.configure(".", background=c["sidebar"], foreground=c["ink"])
        for name in ("TCombobox", "TEntry"):
            native.configure(name, fieldbackground=c["surface"], foreground=c["ink"])
            native.map(name, fieldbackground=[("readonly", c["surface"])],
                       selectbackground=[("!disabled", c["accent"])])
        native.configure("TNotebook.Tab", background=c["sidebar"], foreground=c["ink"])
        native.map("TNotebook.Tab", background=[("selected", c["surface"])])
        self.walk(self.app.root, self.color_map(previous), previous)

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        window = self.window = tk.Toplevel(self.app.root)
        window.title("Settings")
        window.geometry("430x390")
        window.resizable(False, False)
        window.transient(self.app.root)
        c = self.app.COLORS
        window.configure(bg=c["background"])
        theme = tk.StringVar(window, value=self.theme)
        style = tk.StringVar(window, value=self.style)
        for label, variable, values in (("Theme", theme, tuple(self.themes)),
                                        ("Style", style, ("Classic", "Modern"))):
            tk.Label(window, text=label, bg=c["background"], fg=c["ink"],
                     font=("Segoe UI", 11)).pack(anchor="w", padx=24, pady=(16, 5))
            picker = ttk.Combobox(window, textvariable=variable, values=values, state="readonly")
            picker.pack(fill="x", padx=24)
            picker.bind("<<ComboboxSelected>>", lambda event: change())
        status = tk.Label(window, text="Changes apply immediately. Classic uses serif headings;\nModern uses sans serif headings.",
                          bg=c["background"], fg=c["muted"], justify="left", wraplength=340)
        status.pack(fill="x", padx=24, pady=14)

        tk.Label(window, text="Software updates", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=24, pady=(4, 5))
        tk.Label(window, text="Check whether a newer version is available on GitHub.",
                 bg=c["background"], fg=c["muted"], font=("Segoe UI", 9),
                 wraplength=370, justify="left").pack(fill="x", padx=24)
        self.app.button(window, "Check for updates", self.app.update_checker.open,
                        c["surface"], c["ink"], c["sidebar_hover"]).pack(anchor="w", padx=24, pady=(8, 12))

        def change():
            self.apply(theme.get(), style.get())
            if self.path:
                try:
                    temporary = self.path.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"theme": self.theme, "style": self.style}), encoding="utf-8")
                    temporary.replace(self.path)
                except OSError as error:
                    status.configure(text=f"Applied, but could not save preferences: {error}")

        self.app.button(window, "Done", window.destroy, c["accent"], "white", c["accent_hover"]).pack(anchor="e", padx=24)
