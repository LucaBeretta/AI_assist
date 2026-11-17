"""Live appearance preferences without rebuilding the conversation workspace."""

import json
import tkinter as tk
from tkinter import ttk, font


class Appearance:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.warm = {
            "background": "#faf9f5", "surface": "#fffefa", "sidebar": "#eeece5",
            "sidebar_hover": "#e3dfd5", "sidebar_muted": "#81796d", "ink": "#302d27",
            "muted": "#787166", "border": "#dfdbd1", "accent": "#b65e40",
            "accent_hover": "#9f4f34", "user": "#f0ede5", "success": "#42816b", "error": "#a64137",
            "code_background": "#f5f2eb", "code_header": "#ece8de",
        }
        self.themes = {
            "Warm": self.warm,
            "Light": dict(self.warm, background="#f8fafc", surface="#ffffff",
                          sidebar="#f1f5f9", sidebar_hover="#e2e8f0", user="#eef2ff",
                          ink="#172033", muted="#657089", sidebar_muted="#6b778c",
                          border="#dce3ed", accent="#5260d8", accent_hover="#424fc0",
                          success="#237b64", error="#b34545",
                          code_background="#f3f5f9", code_header="#e8edf4"),
            "Dark": dict(self.warm, background="#151922", surface="#1e2430",
                         sidebar="#1a202b", sidebar_hover="#2c3546", user="#29324b",
                         ink="#edf0f7", muted="#a5b1c5", sidebar_muted="#9aa8bf",
                         border="#354156", accent="#5967db", accent_hover="#4a58c8",
                         success="#70d3b0", error="#ff9d93",
                         code_background="#171d28", code_header="#242d3c"),
        }
        # Keep the same dictionary: existing event callbacks hold references to it.
        self.theme = "Light"
        self.style = "Modern"
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
        self.apply(selected.get("theme", "Light"), selected.get("style", "Modern"))
        app.root.bind_all("<Map>", self.mapped, add="+")

    def color_map(self, previous):
        c = self.app.COLORS
        colors = {value.lower(): c[key] for key, value in previous.items()}
        colors.update({"#d9d4c9": c["border"], "#e7d9ca": c["user"],
                       "#e5e9f2": c["border"], "#f5f2eb": c["code_background"],
                       "#ece8de": c["code_header"]})
        return colors

    def update_widget(self, widget, colors, previous):
        options = widget.keys()
        roles = getattr(widget, "_appearance_roles", {})
        role_colors = {value.lower(): key for key, value in previous.items()}
        for option in ("background", "foreground", "activebackground", "activeforeground",
                       "disabledforeground", "insertbackground", "selectbackground", "selectforeground",
                       "highlightbackground", "highlightcolor", "troughcolor", "readonlybackground"):
            if option in options:
                value = str(widget.cget(option)).lower()
                if option == "background" and getattr(widget, "_hovered", False):
                    value = widget._appearance_bg.lower()
                # White action labels and selection text are intentional. A light
                # surface has the same RGB value, but a different semantic role.
                if value in ("white", "#ffffff") and (
                        option == "selectforeground" or
                        isinstance(widget, tk.Button) and option in ("foreground", "activeforeground")):
                    continue
                role = roles.get(option)
                if role and value == previous.get(role, "").lower():
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
        refresh_hover = getattr(widget, "refresh_hover", None)
        if refresh_hover is not None:
            refresh_hover()
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
        theme = theme if isinstance(theme, str) and theme in self.themes else "Light"
        style = style if style in ("Classic", "Modern") else "Modern"
        previous = dict(self.app.COLORS)
        self.theme, self.style = theme, style
        self.app.COLORS.update(self.themes[theme])
        self.app.configure_styles()
        c = self.app.COLORS
        native = ttk.Style(self.app.root)
        native.configure(".", background=c["sidebar"], foreground=c["ink"], font=("Segoe UI", 10),
                         bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        for name in ("TCombobox", "Model.TCombobox", "TEntry"):
            native.configure(name, fieldbackground=c["surface"], foreground=c["ink"],
                             background=c["surface"], arrowcolor=c["muted"], padding=(10, 7),
                             bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                             insertcolor=c["ink"], selectbackground=c["accent"], selectforeground="white")
            native.map(name,
                       fieldbackground=[("disabled", c["sidebar"]), ("readonly", c["surface"])],
                       foreground=[("disabled", c["muted"]), ("readonly", c["ink"])],
                       bordercolor=[("focus", c["accent"])],
                       lightcolor=[("focus", c["accent"])], darkcolor=[("focus", c["accent"])])
            if name != "TEntry":
                native.map(name, selectbackground=[("readonly", c["surface"])],
                           selectforeground=[("readonly", c["ink"])])
        native.configure("TNotebook", background=c["sidebar"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        native.configure("TNotebook.Tab", background=c["sidebar"], foreground=c["muted"],
                         padding=(14, 9), borderwidth=0, focuscolor=c["accent"])
        native.map("TNotebook.Tab", background=[("selected", c["surface"]), ("active", c["sidebar_hover"])],
                   foreground=[("selected", c["ink"]), ("active", c["ink"])])
        native.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["sidebar_hover"],
                         borderwidth=0, thickness=4)
        for name in ("TCheckbutton", "TRadiobutton"):
            native.configure(name, background=c["sidebar"], foreground=c["ink"], focuscolor=c["accent"])
            native.map(name, background=[("active", c["sidebar"])],
                       foreground=[("disabled", c["muted"])])
        for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar", "Chat.Vertical.TScrollbar", "Sidebar.Vertical.TScrollbar"):
            native.configure(name, background=c["border"], troughcolor=c["background"],
                             bordercolor=c["background"], lightcolor=c["background"], darkcolor=c["background"],
                             arrowcolor=c["muted"], borderwidth=0)
            native.map(name, background=[("active", c["muted"]), ("pressed", c["muted"])])
        self.walk(self.app.root, self.color_map(previous), previous)

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        window = self.window = tk.Toplevel(self.app.root)
        window.title("Settings")
        window.geometry("430x460")
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
