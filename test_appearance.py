"""Appearance persistence and repeated theme changes on real Tk widgets."""

import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from appearance import Appearance, _set_native_appearance
from chat_store import ChatStore
from message_widgets import render_message
from ui_widgets import HoverButton
from workspace_ui import WorkspaceMixin


class AppearanceTests(unittest.TestCase):
    def test_native_mac_controls_follow_the_app_theme_when_available(self):
        calls = []
        def call(*args):
            calls.append(args)
            return "aqua" if args == ("tk", "windowingsystem") else ""
        window = SimpleNamespace(tk=SimpleNamespace(call=call), _w=".preview")
        for theme in ("Dark", "Light", "Warm"):
            _set_native_appearance(window, theme)
        self.assertEqual([args for args in calls if args[0] == "tk::unsupported::MacWindowStyle"], [
            ("tk::unsupported::MacWindowStyle", "appearance", ".preview", "darkaqua"),
            ("tk::unsupported::MacWindowStyle", "appearance", ".preview", "aqua"),
        ])
        window = SimpleNamespace(tk=SimpleNamespace(call=Mock(return_value="win32")), _w=".")
        _set_native_appearance(window, "Dark")
        window.tk.call.assert_called_once_with("tk", "windowingsystem")

    def test_missing_mac_appearance_command_is_nonfatal(self):
        native = Mock(side_effect=["aqua", tk.TclError("Unsupported appearance command")])
        window = SimpleNamespace(tk=SimpleNamespace(call=native), _w=".")
        _set_native_appearance(window, "Dark")
        self.assertFalse(hasattr(window, "_appearance_native_mode"))

    def test_entry_text_and_outline_remain_distinct_across_themes(self):
        appearance = Appearance(self.app)
        entry = ttk.Entry(self.root)
        entry.insert(0, "Project folder")
        native = ttk.Style(self.root)

        def contrast(first, second):
            def luminance(color):
                channels = [value / 65535 for value in self.root.winfo_rgb(color)]
                linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                          for value in channels]
                return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))
            values = sorted((luminance(first), luminance(second)))
            return (values[1] + .05) / (values[0] + .05)

        for theme in ("Light", "Dark", "Warm", "Light"):
            appearance.apply(theme, "Modern")
            background = native.lookup("TEntry", "fieldbackground")
            self.assertGreaterEqual(contrast(native.lookup("TEntry", "foreground"), background), 4.5)
            self.assertGreaterEqual(contrast(native.lookup("TEntry", "bordercolor"), background), 3)
            disabled_bg = native.lookup("TEntry", "fieldbackground", state=("disabled",))
            disabled_fg = native.lookup("TEntry", "foreground", state=("disabled",))
            self.assertGreaterEqual(contrast(disabled_fg, disabled_bg), 3)
            self.assertEqual(entry.get(), "Project folder")

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = SimpleNamespace(root=self.root, COLORS=dict(WorkspaceMixin.COLORS),
            chat_store=ChatStore(self.folder.name),
            configure_styles=lambda: ttk.Style(self.root).theme_use("clam"),
            update_checker=SimpleNamespace(open=lambda: None))
        self.app.button = lambda parent, text, command, bg, fg, hover: tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg)

    def test_repeated_theme_changes_preserve_text_color_roles(self):
        muted = tk.Label(self.root, text="Hint", bg=self.app.COLORS["background"], fg=self.app.COLORS["muted"])
        appearance = Appearance(self.app)
        for theme in ("Dark", "Light", "Warm", "Light", "Dark", "Warm"):
            appearance.apply(theme, "Modern")
            self.assertEqual(muted.cget("fg"), self.app.COLORS["muted"])
            self.assertEqual(muted.cget("bg"), self.app.COLORS["background"])

    def test_new_install_defaults_to_light_modern(self):
        appearance = Appearance(self.app)
        self.assertEqual((appearance.theme, appearance.style), ("Light", "Modern"))
        self.assertEqual(self.app.COLORS["background"], "#f8fafc")

    def test_saved_warm_classic_preferences_are_preserved(self):
        Path(self.folder.name, "appearance.settings").write_text(
            json.dumps({"theme": "Warm", "style": "Classic"}))
        appearance = Appearance(self.app)
        self.assertEqual((appearance.theme, appearance.style), ("Warm", "Classic"))
        self.assertEqual(self.app.COLORS["background"], "#faf9f5")
        self.assertEqual(self.app.COLORS["accent"], "#b65e40")

    def test_external_button_state_survives_hover_and_disabled_state(self):
        button = HoverButton(self.root, text="Developer", bg="#f1f5f9", activebackground="#e2e8f0")
        button.config(bg="#eef2ff")  # The application selects this navigation item.
        button._enter(None)
        self.assertEqual(button.cget("bg"), "#e2e8f0")
        button._leave(None)
        self.assertEqual(button.cget("bg"), "#eef2ff")
        button._enter(None)
        button.configure(background="#5260d8")  # A state change while the pointer is over it.
        self.assertEqual(button._appearance_bg, "#5260d8")
        button.config(state="disabled")
        self.assertEqual(button.cget("bg"), "#5260d8")
        button._leave(None)
        self.assertEqual(button.cget("bg"), "#5260d8")

    def test_theme_change_while_hovered_preserves_selected_base(self):
        appearance = Appearance(self.app)
        c = self.app.COLORS
        button = HoverButton(self.root, text="Developer", bg=c["user"], activebackground=c["sidebar_hover"])
        button._enter(None)
        for theme in ("Dark", "Warm", "Light"):
            appearance.apply(theme, "Modern")
            self.assertEqual(button._appearance_bg, c["user"])
            self.assertEqual(button._appearance_hover, c["sidebar_hover"])
            self.assertEqual(button.cget("bg"), c["sidebar_hover"])
        button._leave(None)
        self.assertEqual(button.cget("bg"), c["user"])

    def test_action_text_and_rendered_code_colors_survive_repeated_themes(self):
        appearance = Appearance(self.app)
        c = self.app.COLORS
        button = HoverButton(self.root, text="Send", bg=c["accent"], activebackground=c["accent_hover"],
                             fg="#ffffff", activeforeground="#ffffff")
        body = render_message(self.root, "```python\nprint('hello')\n```", self.app)
        pending = [body]
        code = header = None
        while pending:
            widget = pending.pop()
            if getattr(widget, "_aiassist_code", False):
                code = widget
            if isinstance(widget, tk.Label) and widget.cget("text") == "python":
                header = widget
            pending.extend(widget.winfo_children())
        self.assertIsNotNone(code)
        self.assertIsNotNone(header)
        for theme in ("Dark", "Light", "Warm", "Light", "Dark"):
            appearance.apply(theme, "Modern")
            self.assertEqual(button.cget("fg"), "#ffffff")
            self.assertEqual(button.cget("activeforeground"), "#ffffff")
            self.assertEqual(button._appearance_bg, c["accent"])
            self.assertEqual(code.cget("bg"), c["code_background"])
            self.assertEqual(header.cget("bg"), c["code_header"])
            self.assertEqual(code.cget("fg"), c["ink"])
            self.assertEqual(code.cget("selectforeground"), "#ffffff")

    def test_legacy_settings_load_and_new_preferences_do_not_pollute_history(self):
        Path(self.folder.name, "appearance.json").write_text(json.dumps({"theme": "Dark", "style": "Modern"}))
        appearance = Appearance(self.app)
        self.assertEqual((appearance.theme, appearance.style), ("Dark", "Modern"))
        appearance.open()
        picker = next(widget for widget in appearance.window.winfo_children() if isinstance(widget, ttk.Combobox))
        picker.set("Light")
        picker.event_generate("<<ComboboxSelected>>")
        saved = json.loads(Path(self.folder.name, "appearance.settings").read_text())
        self.assertEqual(saved, {"theme": "Light", "style": "Modern"})
        self.assertEqual(self.app.chat_store.list_chats(), [])
        self.assertEqual(self.app.chat_store.errors, [])


if __name__ == "__main__":
    unittest.main()
