"""Get started guide and the switch to the browser version."""

from contextlib import ExitStack
import gc
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import AIassist
from test_night_console import join_workers
from welcome_ui import readiness, ready


class ReadinessTests(unittest.TestCase):
    def test_what_is_missing_is_read_from_the_last_model_check(self):
        self.assertEqual(readiness({"models": [], "errors": ["Ollama is not installed. Install Ollama, then click Refresh."]}),
                         {"installed": False, "running": False, "models": False})
        self.assertEqual(readiness({"models": [], "errors": ["Ollama did not become ready. Check Ollama."]}),
                         {"installed": True, "running": False, "models": False})
        self.assertEqual(readiness({"models": [], "errors": []}), {"installed": True, "running": True, "models": False})
        self.assertTrue(ready({"models": ["a:1b"], "errors": []}))
        self.assertFalse(ready(None))
        self.assertTrue(ready({"models": ["m"], "errors": []}, backend="Colibri"))


class GuideCase(unittest.TestCase):
    models = []
    ensure_error = None

    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        self.threads_before = set(threading.enumerate())
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.history = Path(temporary.name) / "history"
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(self.history), "OLLAMA_MODEL": ""}))
        self.mocks.enter_context(patch("update_checker.UpdateChecker.check", lambda self: None))
        self.ensure = self.mocks.enter_context(patch("AIassist.ensure_ollama", side_effect=self.ensure_error))
        self.mocks.enter_context(patch("AIassist.list_ollama_models", side_effect=lambda: list(self.models)))
        self.mocks.enter_context(patch("AIassist.running_models", return_value=set()))
        self.mocks.enter_context(patch("AIassist.set_model_running"))
        self.mocks.enter_context(patch("AIassist.running_details", return_value={}))
        self.mocks.enter_context(patch("model_downloads.ModelDownloadDialog.reload_catalogue", lambda self: None))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = AIassist.AIAssistantGUI(self.root)
        self.addCleanup(self.close)
        self.wait_for(lambda: not self.app.startup_pending and not self.app.busy(), "startup")

    def close(self):
        for scheduled in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", scheduled)
        if not self.app.closed:
            self.app.close()
            deadline = time.monotonic() + 5
            while not self.app.closed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.app.saver.thread.join(timeout=2)
        join_workers(self.threads_before)
        self.assertEqual([str(error[1]) for error in self.errors], [])

    def wait_for(self, predicate, description, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail(f"Timed out waiting for {description}")

    def guide_text(self):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        window = self.app.welcome.window
        return [(type(w).__name__, w.cget("text")) for w in walk(window) if isinstance(w, (tk.Label, tk.Button))]


class NoModelTests(GuideCase):
    def test_the_guide_opens_and_leads_to_models_for_this_computer(self):
        self.assertIsNotNone(self.app.welcome)
        texts = [text for _, text in self.guide_text()]
        self.assertIn("A model that fits this computer", texts)
        self.assertIn("Find a model...", texts)
        self.assertNotIn("Get Ollama...", texts)  # Ollama is fine here
        button = next(w for w in self.app.welcome.rows.winfo_children()[2].winfo_children()
                      if isinstance(w, tk.Button) and w.cget("text") == "Find a model...")
        button.invoke()
        dialog = self.app.download_dialog
        self.assertEqual(dialog.notebook.select(), str(dialog.advisor.frame))
        dialog.close(force=True)

    def test_installing_a_model_then_checking_again_says_ready(self):
        self.models.append("small:1b")
        self.app.welcome.check_button.invoke()
        self.wait_for(lambda: not self.app.lifecycle_busy, "model check")
        self.assertIn("You're ready. Close this and type a message below.", [text for _, text in self.guide_text()])
        self.models.clear()

    def test_turning_it_off_is_remembered(self):
        self.app.welcome.show_at_start.set(False)
        self.app.welcome.save_choice()
        self.app.welcome.close()
        self.app.welcome = None
        self.app.maybe_show_welcome()
        self.assertIsNone(self.app.welcome)
        self.app.open_welcome()  # Settings > Get started still works
        self.assertTrue(self.app.welcome.window.winfo_exists())


class NoOllamaTests(GuideCase):
    ensure_error = RuntimeError("Ollama is not installed. Install Ollama, then click Refresh models.")

    def test_the_guide_offers_the_ollama_download_page_through_the_gate(self):
        texts = [text for _, text in self.guide_text()]
        self.assertIn("Get Ollama...", texts)
        self.assertNotIn("Find a model...", texts)
        with patch("welcome_ui.webbrowser.open") as opened:
            next(w for w in self.app.welcome.rows.winfo_children()[0].winfo_children()
                 if isinstance(w, tk.Button) and w.cget("text") == "Get Ollama...").invoke()
        opened.assert_called_once_with("https://ollama.com/download")


class ReadyTests(GuideCase):
    models = ["alpha:8b"]

    def test_no_guide_when_everything_works_and_the_browser_switch_closes_the_window(self):
        self.assertIsNone(self.app.welcome)
        with patch("workspace_ui.messagebox.askokcancel", return_value=False):
            self.app.settings_menu.invoke("Use the browser version...")
        self.assertFalse(getattr(self.app, "switch_to_web", False))
        with patch("workspace_ui.messagebox.askokcancel", return_value=True):
            self.app.settings_menu.invoke("Use the browser version...")
        self.assertTrue(self.app.switch_to_web)
        self.wait_for(lambda: self.app.closed, "window closed")

    def test_the_browser_version_starts_as_its_own_program(self):
        with patch("subprocess.Popen") as started:
            AIassist.launch_web_workspace()
        command = started.call_args.args[0]
        self.assertTrue(command[1].endswith("web_workspace.py"))
        if os.name == "nt":
            import subprocess
            self.assertEqual(started.call_args.kwargs["creationflags"], subprocess.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    unittest.main()
