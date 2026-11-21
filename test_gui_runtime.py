"""Web runtime diagnostics run without constructing a native window."""
from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import Mock, patch

import gui_runtime


class GUIRuntimeTests(unittest.TestCase):
    def test_pending_requirements_install_before_launch_and_retry_after_failure(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = Mock(return_value=Mock(returncode=0))
            self.assertIsNone(gui_runtime.install_pending_requirements(root, run=run))
            run.assert_not_called()
            marker = root / ".update" / "requirements-pending"
            marker.parent.mkdir()
            marker.write_text("pending", encoding="utf-8")
            run.return_value = Mock(returncode=1)
            self.assertEqual(gui_runtime.install_pending_requirements(root, run=run, executable="py"), 1)
            self.assertTrue(marker.is_file())
            self.assertEqual(run.call_args.args[0], ["py", "-m", "pip", "install", "-r", str(root / "requirements.txt")])
            run.return_value = Mock(returncode=0)
            self.assertEqual(gui_runtime.install_pending_requirements(root, run=run, executable="py"), 0)
            self.assertFalse(marker.exists())

    def test_probe_reports_python_and_web_dependency_without_creating_windows(self):
        view = Mock()
        output = io.StringIO()
        with patch.object(gui_runtime.importlib, "import_module", return_value=view), redirect_stdout(output):
            gui_runtime.probe_webview()
        self.assertIn("Python interpreter:", output.getvalue())
        self.assertIn("Web UI: pywebview available", output.getvalue())
        view.create_window.assert_not_called()
        view.start.assert_not_called()

    def test_probe_cli_reports_missing_dependency_without_traceback(self):
        output = io.StringIO()
        with patch.object(gui_runtime.importlib, "import_module", side_effect=ImportError("missing webview")), redirect_stderr(output):
            self.assertEqual(gui_runtime.main(["--probe"]), 1)
        self.assertIn("The web UI needs pywebview", output.getvalue())
        self.assertIn("pip install -r requirements.txt", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
