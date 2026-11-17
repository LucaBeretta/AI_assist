"""Live appearance preferences without rebuilding the conversation workspace."""

import json
import tkinter as tk
from tkinter import ttk, font


DARK_THEMES = ("Dark", "Night")
# The console pair: one switch in the header flips between them.
CONSOLE_PAIR = {"Night": "Day", "Day": "Night"}
DEFAULT_THEME = "Night"


def toggled_theme(theme):
    """Night <-> Day; from any other theme, to the console theme of the opposite brightness."""
    if theme in CONSOLE_PAIR:
        return CONSOLE_PAIR[theme]
    return "Day" if theme in DARK_THEMES else "Night"


def _set_native_appearance(window, theme):
    """Keep Cocoa controls in the same light/dark mode as the explicit palette."""
    try:
        if window.tk.call("tk", "windowingsystem") != "aqua":
            return
        mode = "darkaqua" if theme in DARK_THEMES else "aqua"
        if getattr(window, "_appearance_native_mode", None) != mode:
            window.tk.call("tk::unsupported::MacWindowStyle", "appearance", window._w, mode)
            window._appearance_native_mode = mode
    except tk.TclError:
        # Older supported builds may not expose the per-window Cocoa command.
        # The explicit widget palette and clam styles still apply.
        pass


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
            "input_border": "#968875",
            # Status bar ground, the Colibri/attention tone, and text on accent fills.
            "status": "#e6e2d8", "amber": "#8f5410", "on_accent": "#ffffff",
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
        self.themes["Light"]["input_border"] = "#8190a8"
        self.themes["Dark"]["input_border"] = "#71809a"
        self.themes["Light"]["status"] = "#e9eef5"
        self.themes["Dark"].update(status="#121620", amber="#f2b866")
        # Night console: graphite, one mint accent, dark text on accent fills.
        # Every role has its own value: the live recolouring maps values back to roles.
        self.themes["Night"] = dict(self.warm, background="#0d0f12", surface="#161a20", sidebar="#111418",
                                    sidebar_hover="#1e232b", sidebar_muted="#8f98a6", ink="#e3e7ed",
                                    muted="#a7b0bd", border="#222831", accent="#5ee6d0",
                                    accent_hover="#7febda", user="#1a1f26", success="#45d483",
                                    error="#ff7a7a", code_background="#08090b", code_header="#14181e",
                                    input_border="#4a5361", status="#0a0b0e", amber="#f5b54a",
                                    on_accent="#0b0d10")
        self.themes["Day"] = dict(self.warm, background="#f7f8fa", surface="#ffffff", sidebar="#fbfcfd",
                                  sidebar_hover="#e7ebf0", sidebar_muted="#5f6976", ink="#1b1f25",
                                  muted="#4f5966", border="#e2e6ec", accent="#0b7a6c",
                                  accent_hover="#096559", user="#eef2f5", success="#1e7a45",
                                  error="#b42318", code_background="#f1f3f6", code_header="#e4e8ee",
                                  input_border="#8a94a3", status="#eef1f5", amber="#a35a00",
                                  on_accent="#fffffe")
        # Keep the same dictionary: existing event callbacks hold references to it.
        self.theme = DEFAULT_THEME
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
        self.apply(selected.get("theme", DEFAULT_THEME), selected.get("style", "Modern"))
        app.root.bind_all("<Map>", self.mapped, add="+")

    def color_map(self, previous):
        c = self.app.COLORS
        # on_accent can share its value with a light surface; buttons get it from update_accent_text.
        colors = {value.lower(): c[key] for key, value in previous.items() if key != "on_accent"}
        colors.update({"#d9d4c9": c["border"], "#e7d9ca": c["user"],
                       "#e5e9f2": c["border"], "#f5f2eb": c["code_background"],
                       "#ece8de": c["code_header"]})
        return colors

    def update_widget(self, widget, colors, previous):
        options = widget.keys()
        roles = getattr(widget, "_appearance_roles", {})
        role_colors = {value.lower(): key for key, value in previous.items() if key != "on_accent"}
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
        if isinstance(widget, tk.Button):
            self.update_accent_text(widget)
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

    def update_accent_text(self, widget):
        """Text on an accent fill uses the theme's on_accent colour (dark on Night's mint)."""
        c = self.app.COLORS
        base = str(getattr(widget, "_appearance_bg", widget.cget("background"))).lower()
        accents = (c["accent"].lower(), c["accent_hover"].lower())
        known = {"white", "#ffffff"} | {theme["on_accent"].lower() for theme in self.themes.values()}
        if base in accents and str(widget.cget("foreground")).lower() in known:
            tk.Button.configure(widget, foreground=c["on_accent"], activeforeground=c["on_accent"])

    def walk(self, widget, colors, previous):
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            _set_native_appearance(widget, self.theme)
        self.update_widget(widget, colors, previous)
        for child in widget.winfo_children():
            self.walk(child, colors, previous)

    def mapped(self, event):
        if isinstance(event.widget, tk.Misc):
            self.walk(event.widget, self.color_map(self.warm), self.warm)

    def apply(self, theme, style):
        theme = theme if isinstance(theme, str) and theme in self.themes else DEFAULT_THEME
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
                             bordercolor=c["input_border"], lightcolor=c["input_border"], darkcolor=c["input_border"],
                             insertcolor=c["ink"], selectbackground=c["accent"], selectforeground=c["on_accent"])
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
        self.app.root.option_add("*TCombobox*Listbox.selectForeground", c["on_accent"])
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
        refresh = getattr(self.app, "refresh_theme_controls", None)
        if refresh is not None:
            refresh()

    def save(self):
        """Remember the theme and style; returns an error message or None."""
        if not self.path:
            return None
        try:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"theme": self.theme, "style": self.style}), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as error:
            return str(error)
        return None

    def toggle(self):
        """The header's day/night switch."""
        self.apply(toggled_theme(self.theme), self.style)
        return self.save()

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
            error = self.save()
            if error:
                status.configure(text=f"Applied, but could not save preferences: {error}")

        self.app.button(window, "Done", window.destroy, c["accent"], "white", c["accent_hover"]).pack(anchor="e", padx=24)
