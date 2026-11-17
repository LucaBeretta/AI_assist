"""The Windows launcher starts the app detached, so closing its console keeps the app open."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_runtime

ROOT = Path(__file__).resolve().parent


class DetachedLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.python = self.folder / "python.exe"
        self.python.write_bytes(b"")
        self.script = self.folder / "AIassist.py"
        self.script.write_text("", encoding="utf-8")

    def test_uses_pythonw_detached_on_windows(self):
        (self.folder / "pythonw.exe").write_bytes(b"")
        popen = Mock()
        with patch.object(gui_runtime.sys, "platform", "win32"):
            self.assertTrue(gui_runtime.launch_detached(self.script, self.python, popen))
        command = popen.call_args.args[0]
        self.assertEqual(command, [str(self.folder / "pythonw.exe"), str(self.script)])
        flags = popen.call_args.kwargs["creationflags"]
        self.assertEqual(flags, getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def test_falls_back_without_pythonw_or_off_windows(self):
        popen = Mock()
        with patch.object(gui_runtime.sys, "platform", "win32"):
            self.assertFalse(gui_runtime.launch_detached(self.script, self.python, popen))
        (self.folder / "pythonw.exe").write_bytes(b"")
        with patch.object(gui_runtime.sys, "platform", "darwin"):
            self.assertFalse(gui_runtime.launch_detached(self.script, self.python, popen))
        popen.assert_not_called()
        with patch.object(gui_runtime, "launch_detached", return_value=False):
            self.assertEqual(gui_runtime.main(["--launch", str(self.script)]), 3)

    def test_missing_streams_go_to_a_log_file(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": str(self.folder)}), \
                patch.object(sys, "stderr", None), patch.object(sys, "stdout", None):
            path = gui_runtime.redirect_missing_streams()
            print("hello", file=sys.stderr)
            sys.stderr.close()
        self.assertEqual(path, self.folder / "AI Assist" / "last-run.log")
        self.assertIn("hello", path.read_text(encoding="utf-8"))

    def test_console_streams_are_left_alone(self):
        self.assertIsNone(gui_runtime.redirect_missing_streams())

    def test_windows_launcher_starts_detached_before_the_foreground_fallback(self):
        text = (ROOT / "Start AI Assist.cmd").read_text(encoding="utf-8")
        self.assertLess(text.index("--launch"), text.index('%* "%APP_DIR%AIassist.py"'))


if __name__ == "__main__":
    unittest.main()
