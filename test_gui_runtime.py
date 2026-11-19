"""Simulated platform/runtime checks; actual Aqua drawing requires a Mac."""

from contextlib import redirect_stderr, redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gui_runtime


class FakeTclError(Exception):
    pass


class GUIRuntimeTests(unittest.TestCase):
    def module(self, version):
        return SimpleNamespace(TkVersion=version, Tk=Mock(), TclError=FakeTclError)

    def test_old_apple_tk_is_rejected_before_root_creation(self):
        module = self.module(8.5)
        with patch.object(gui_runtime.sys, "platform", "darwin"), \
                patch.object(gui_runtime.importlib, "import_module", return_value=module):
            with self.assertRaisesRegex(gui_runtime.GUIRuntimeError, "Tk 8.5.*too old"):
                gui_runtime.create_root()
        module.Tk.assert_not_called()

    def test_modern_tk_including_tk9_is_accepted_on_macos(self):
        for version in (8.6, 9.0, "8.6.14", "9.0.4"):
            module = self.module(version)
            with self.subTest(version=version), patch.object(gui_runtime.sys, "platform", "darwin"), \
                    patch.object(gui_runtime.importlib, "import_module", return_value=module):
                self.assertIs(gui_runtime.create_root(), module.Tk.return_value)
                module.Tk.assert_called_once_with()

    def test_malformed_macos_version_is_rejected_with_guidance(self):
        for version in (None, "unknown", True, "8.x", "8.6.bad", float("nan")):
            module = self.module(version)
            with self.subTest(version=version), patch.object(gui_runtime.sys, "platform", "darwin"), \
                    patch.object(gui_runtime.importlib, "import_module", return_value=module):
                with self.assertRaisesRegex(gui_runtime.GUIRuntimeError, "python.org"):
                    gui_runtime.create_root()
                module.Tk.assert_not_called()

    def test_macos_minimum_does_not_reject_windows_runtime(self):
        module = self.module(8.5)
        with patch.object(gui_runtime.sys, "platform", "win32"), \
                patch.object(gui_runtime.importlib, "import_module", return_value=module):
            self.assertIs(gui_runtime.create_root(), module.Tk.return_value)

    def test_missing_tkinter_and_root_creation_error_are_actionable(self):
        with patch.object(gui_runtime.sys, "platform", "win32"), \
                patch.object(gui_runtime.importlib, "import_module", side_effect=ImportError("No _tkinter")):
            with self.assertRaisesRegex(gui_runtime.GUIRuntimeError, "Tkinter.*python.org"):
                gui_runtime.create_root()
        module = self.module(9.0)
        module.Tk.side_effect = FakeTclError("missing init.tcl")
        with patch.object(gui_runtime.sys, "platform", "win32"), \
                patch.object(gui_runtime.importlib, "import_module", return_value=module):
            with self.assertRaisesRegex(gui_runtime.GUIRuntimeError, "missing init.tcl") as raised:
                gui_runtime.create_root()
            self.assertIn("python.org", str(raised.exception))

    def test_linux_guidance_names_the_launcher_and_tkinter_packages(self):
        with patch.object(gui_runtime.sys, "platform", "linux"), \
                patch.object(gui_runtime.importlib, "import_module", side_effect=ImportError("No _tkinter")):
            with self.assertRaisesRegex(gui_runtime.GUIRuntimeError, r"Start AI Assist\.sh.*python3-tk"):
                gui_runtime.create_root()

    def test_probe_prints_actual_versions_and_always_destroys_root(self):
        root = Mock()
        root.tk.call.side_effect = lambda *args: {
            ("info", "patchlevel"): "9.0.4",
            ("package", "present", "Tk"): "9.0.4",
            ("tk", "windowingsystem"): "aqua",
        }[args]
        output = io.StringIO()
        with patch.object(gui_runtime, "create_root", return_value=root), redirect_stdout(output):
            gui_runtime.probe_root()
        self.assertIn("Python interpreter:", output.getvalue())
        self.assertIn("Tcl version: 9.0.4", output.getvalue())
        self.assertIn("Tk version: 9.0.4", output.getvalue())
        self.assertIn("Windowing system: aqua", output.getvalue())
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()

    def test_probe_destroys_root_when_diagnostics_fail(self):
        root = Mock()
        root.tk.call.side_effect = FakeTclError("diagnostic failed")
        with patch.object(gui_runtime, "create_root", return_value=root):
            with self.assertRaisesRegex(FakeTclError, "diagnostic failed"):
                gui_runtime.probe_root()
        root.destroy.assert_called_once_with()

    def test_probe_cli_reports_errors_without_traceback(self):
        output = io.StringIO()
        with patch.object(gui_runtime.sys, "platform", "win32"), \
                patch.object(gui_runtime, "probe_root", side_effect=FakeTclError("diagnostic failed")), \
                redirect_stderr(output):
            self.assertEqual(gui_runtime.main(["--probe"]), 1)
        self.assertIn("diagnostic failed", output.getvalue())
        self.assertIn("python.org", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
