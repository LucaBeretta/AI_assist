"""Launch wiring and dependency diagnostics without opening a window."""

from contextlib import redirect_stderr
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from webui.app import main
from test_agent_support import isolate_agent_data


class Hook:
    def __iadd__(self, callback):
        self.callback = callback
        return self


class WebLaunchTests(unittest.TestCase):
    def setUp(self):
        isolate_agent_data(self)

    def test_absolute_file_uri_and_no_http_server(self):
        loaded = Hook()
        window = SimpleNamespace(events=SimpleNamespace(loaded=loaded), evaluate_js=Mock())
        api = SimpleNamespace(_ensure_engine=Mock(), _close=Mock(), _start_status=Mock())
        view = SimpleNamespace(create_window=Mock(return_value=window))
        def start(callback, **options):
            self.assertEqual(options, {"http_server": False})
            loaded.callback()
            callback()
        view.start = start
        with patch("webui.app.Api", return_value=api), patch("webui.app.importlib.import_module", return_value=view):
            self.assertEqual(main(), 0)
        self.assertTrue(view.create_window.call_args.args[1].startswith("file:///"))
        self.assertTrue(view.create_window.call_args.kwargs["text_select"])
        api._ensure_engine.assert_called_once_with()
        api._close.assert_called_once_with()
        api._start_status.assert_called_once_with()

    def test_missing_dependency_is_clear_and_nonzero(self):
        error = io.StringIO()
        with patch("webui.app.importlib.import_module", side_effect=ImportError("webview")), redirect_stderr(error):
            self.assertEqual(main(), 1)
        self.assertIn("pip install -r requirements.txt", error.getvalue())

    def test_command_line_web_mode_does_not_fall_back_to_tk(self):
        root = Path(__file__).parent
        # -S excludes installed pywebview, so this also exercises the real CLI
        # dependency diagnostic without ever constructing a GUI root.
        result = subprocess.run([sys.executable, "-S", str(root / "AIassist.py"), "--web"],
                                cwd=root, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The web UI needs pywebview", result.stderr)

    def test_private_setups_install_the_same_requirements(self):
        root = Path(__file__).parent
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pywebview==", requirements)
        for path in ("install_runtime.ps1", "Start AI Assist.command"):
            text = (root / path).read_text(encoding="utf-8")
            self.assertIn("-m ensurepip --upgrade", text)
            self.assertIn("-m pip install -r", text)
            self.assertIn("requirements.txt", text)


if __name__ == "__main__":
    unittest.main()
