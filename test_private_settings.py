"""Every settings file AI Assist writes goes through app_core.settings.write_json: it is
replaced atomically, leaves no temporary file, and on macOS and Linux only this user can
read it (0600). The hybrid settings dialog is checked in test_hybrid_ui."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app_core import settings
import colibri_launcher
import firewall_lock
from test_firewall_lock import status


class PrivateSettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def assert_private(self, writer, path):
        """``writer`` (a wrapped write_json) wrote ``path``; returns what the file holds."""
        self.assertEqual(Path(writer.call_args.args[0]), path)
        self.assertEqual([item.name for item in path.parent.iterdir()], [path.name])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_appearance_settings(self):
        try:
            from appearance import Appearance
        except ImportError as error:  # a Python without Tkinter (some Linux servers)
            self.skipTest(f"no Tkinter: {error}")
        path = self.folder / "appearance.settings"
        appearance = Appearance.__new__(Appearance)
        appearance.path, appearance.theme, appearance.style = path, "Day", "Modern"
        with patch("appearance.write_json", wraps=settings.write_json) as writer:
            self.assertIsNone(appearance.save())
        self.assertEqual(self.assert_private(writer, path), {"theme": "Day", "style": "Modern"})

    def test_colibri_settings(self):
        colibri = self.folder / "colibri"
        path = self.folder / "chats" / "colibri.settings"
        with patch("colibri_launcher.coli_path", return_value=str(colibri / "c" / "coli")), \
                patch("colibri_launcher.write_json", wraps=settings.write_json) as writer:
            saved = colibri_launcher.save_directory(colibri, path)
        self.assertEqual(self.assert_private(writer, path), {"directory": saved})

    def test_firewall_lift_time(self):
        path = self.folder / "firewall-lift.json"
        controller = firewall_lock.Lock(lambda: None, path)
        with patch("firewall_lock.lift", return_value=(True, "")), \
                patch("firewall_lock.read_status", return_value=status()), \
                patch("firewall_lock.engine_programs", return_value=[]), \
                patch("firewall_lock.write_json", wraps=settings.write_json) as writer:
            controller.run("lift", 15)
            deadline = time.monotonic() + 10
            while controller.busy and time.monotonic() < deadline:
                controller.poll()
                time.sleep(0.02)
        self.assertGreater(self.assert_private(writer, path)["until"], time.time() + 800)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_an_older_readable_settings_file_becomes_private_when_saved(self):
        path = self.folder / "appearance.settings"
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o644)
        settings.write_json(path, {"theme": "Night"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
