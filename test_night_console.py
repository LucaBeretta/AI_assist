"""Night console: the Night/Day themes, the header switch and the status bar."""

from contextlib import ExitStack
import gc
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import AIassist
from appearance import DARK_THEMES, toggled_theme
from status_bar import generation_rate, is_cloud_model, is_local, placement, short_tokens
from test_colibri_launcher import join_workers


class HelperTests(unittest.TestCase):
    def test_toggle_pairs(self):
        self.assertEqual(toggled_theme("Night"), "Day")
        self.assertEqual(toggled_theme("Day"), "Night")
        self.assertEqual(toggled_theme("Dark"), "Day")
        self.assertEqual(toggled_theme("Light"), "Night")
        self.assertEqual(toggled_theme("Warm"), "Night")
        self.assertIn("Night", DARK_THEMES)

    def test_short_tokens(self):
        self.assertEqual(short_tokens(950), "950")
        self.assertEqual(short_tokens(6200), "6.2k")
        self.assertEqual(short_tokens(16384), "16k")
        self.assertEqual(short_tokens(131072), "131k")

    def test_placement(self):
        self.assertEqual(placement({"size": 100, "size_vram": 100}), "gpu")
        self.assertEqual(placement({"size": 100, "size_vram": 0}), "cpu")
        self.assertEqual(placement({"size": 100, "size_vram": 60}), "gpu 60% · cpu")
        self.assertEqual(placement(None), "")

    def test_privacy_helpers(self):
        self.assertTrue(is_local("http://localhost:11434"))
        self.assertTrue(is_local("127.0.0.1:11434"))
        self.assertFalse(is_local("http://192.168.1.20:11434"))
        self.assertTrue(is_cloud_model("gpt-oss:120b-cloud"))
        self.assertFalse(is_cloud_model("qwen3:14b"))

    def test_generation_rate(self):
        self.assertAlmostEqual(generation_rate({"eval_count": 50, "eval_duration": 2_000_000_000}), 25.0)
        self.assertIsNone(generation_rate({}))

    def test_running_details_reads_api_ps(self):
        reply = {"models": [{"name": "alpha:8b", "size": 8, "size_vram": 6, "context_length": 8192}, "junk"]}
        with patch("AIassist.ollama_api", return_value=reply):
            self.assertEqual(AIassist.running_details(),
                             {"alpha:8b": {"size": 8, "size_vram": 6, "context_length": 8192}})
        with patch("AIassist.ollama_api", return_value={}):
            with self.assertRaises(RuntimeError):
                AIassist.running_details()


class NightConsoleUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        self.threads_before = set(threading.enumerate())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.history = Path(self.temporary.name) / "history"
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(self.history), "OLLAMA_MODEL": "alpha:8b"}))
        self.mocks.enter_context(patch("update_checker.UpdateChecker.check", lambda self: None))
        self.mocks.enter_context(patch("AIassist.ensure_ollama"))
        self.mocks.enter_context(patch("AIassist.list_ollama_models", return_value=["alpha:8b"]))
        self.mocks.enter_context(patch("AIassist.running_models", return_value={"alpha:8b"}))
        self.mocks.enter_context(patch("AIassist.set_model_running"))
        self.mocks.enter_context(patch("AIassist.running_details", return_value={
            "alpha:8b": {"size": 8 * 1024 ** 3, "size_vram": 8 * 1024 ** 3, "context_length": 8192}}))
        self.mocks.enter_context(patch("capabilities_view.fetch_model_capabilities",
                                       return_value={"name": "alpha:8b", "capabilities": ["completion"]}))
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

    def test_starts_in_night_and_the_switch_saves_day(self):
        self.assertEqual(self.app.appearance.theme, "Night")
        self.assertEqual(self.app.theme_button.cget("text"), "☀")
        self.assertEqual(self.app.send_button.cget("fg"), self.app.COLORS["on_accent"])
        self.app.theme_button.invoke()
        self.assertEqual(self.app.appearance.theme, "Day")
        self.assertEqual(self.app.theme_button.cget("text"), "☾")
        self.assertEqual(self.app.main_frame.cget("bg"), "#f7f8fa")
        self.assertEqual(self.app.status_bar.frame.cget("bg"), self.app.COLORS["status"])
        self.assertEqual(self.app.send_button.cget("fg"), "#fffffe")
        saved = json.loads((self.history / "appearance.settings").read_text(encoding="utf-8"))
        self.assertEqual(saved["theme"], "Day")
        self.app.theme_button.invoke()
        self.assertEqual(self.app.main_frame.cget("bg"), "#0d0f12")
        self.assertEqual(self.app.send_button.cget("fg"), "#0b0d10")

    def test_status_bar_shows_engine_model_gpu_context_and_privacy(self):
        self.wait_for(lambda: self.app.status_bar.details, "running details")
        self.app.last_metrics = {"prompt_eval_count": 3000, "eval_count": 1100, "eval_duration": 2_000_000_000}
        self.app.agent_active, self.app.agent_step = True, 3
        values = self.app.status_bar.snapshot()
        self.assertEqual(values["model"], "alpha:8b · gpu")
        self.assertEqual(values["context"], "4.1k / 8.2k")
        self.assertEqual(values["vram"], "vram 8.0 GB")
        self.assertEqual(values["speed"], "550 tok/s")
        self.assertEqual(values["agent"], "agent step 3 / 12")
        self.assertEqual(values["privacy"], "local only")
        self.app.status_bar.render(values)
        self.assertIn("alpha:8b", self.app.model_chip.cget("text"))

    def test_remote_server_is_flagged(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://192.168.1.20:11434"}):
            self.assertEqual(self.app.status_bar.snapshot()["privacy"], "remote server")

    def test_privacy_shows_the_internet_gate_and_opens_the_connection_log(self):
        with patch("status_bar.network_gate.status", return_value={"installed": True, "online": None}):
            self.assertEqual(self.app.status_bar.snapshot()["privacy"], "local only · internet locked")
        with patch("status_bar.network_gate.status", return_value={"installed": True, "online": "api.github.com"}):
            values = self.app.status_bar.snapshot()
        self.assertEqual((values["privacy"], values["privacy_role"]), ("online now: api.github.com", "amber"))
        self.app.status_bar.open_connection_log()
        self.assertTrue(self.app.connection_log.window.winfo_exists())
        self.app.connection_log.close()


if __name__ == "__main__":
    unittest.main()
