"""Appearance persistence and repeated theme changes on real Tk widgets."""

import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest

from appearance import Appearance
from chat_store import ChatStore
from workspace_ui import WorkspaceMixin


class AppearanceTests(unittest.TestCase):
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
