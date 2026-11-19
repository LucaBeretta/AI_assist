"""Exercise the download UI without fetching model weights."""

import io
import gc
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import AIassist
import ollama_engine
from model_library import DownloadCancelled


CATALOGUE = [
    {"name": "alpha", "description": "General chat", "pulls": "12M", "sizes": ["1b", "8b"], "capabilities": []},
    {"name": "beta", "description": "Coding assistant", "pulls": "9M", "sizes": ["3b"], "capabilities": []},
]

HARDWARE = {
    "cpu": "Test CPU", "ram_total_bytes": 128 * 1024**3, "ram_available_bytes": 90 * 1024**3,
    "gpus": [{"name": "Test RTX GPU", "total_bytes": 24 * 1024**3, "free_bytes": 12 * 1024**3}],
    "disk_free_bytes": 900 * 1024**3, "server_local": True, "ollama_version": "0.40.14",
    "model_directory": "C:/test/models", "notes": [],
}
RECOMMENDATION = {
    "name": "alpha:1b", "size": "1 GB", "size_bytes": 1_000_000_000,
    "purpose": "Fast everyday chat", "fit": "Fits available GPU memory", "reason": "Fits at a 4K context.",
    "estimated_memory_bytes": 3 * 1024**3, "context_length": 4096,
    "setup_steps": ["Open Ollama.", "Run: ollama pull alpha:1b", "Use a 4096-token context."],
    "source_url": "https://ollama.com/library/alpha:1b",
}


def variants(name):
    return [{"name": f"{name}:1b", "size": "1 GB", "size_bytes": 1_000_000_000}]


class DownloadUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.chat_directory = tempfile.TemporaryDirectory(prefix="aiassist-ui-test-")
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.installed = ["existing:8b"]
        self.loaded = set(self.installed)
        self.load_requests = []
        def set_running(name, start=True, context_length=None):
            self.load_requests.append((name, start, context_length))
            if start:
                self.loaded.add(name)
            else:
                self.loaded.discard(name)
        self.patches = [
            patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": self.chat_directory.name}),
            patch("capabilities_view.fetch_model_capabilities", return_value={"name": "existing:8b", "capabilities": ["completion", "tools"], "context_length": 32768, "family": "test", "parameter_size": "8B", "quantization": "Q4"}),
            patch("ollama_engine.list_ollama_models", side_effect=lambda: list(self.installed)),
            patch("ollama_engine.ensure_ollama", create=True),
            patch("ollama_engine.running_models", side_effect=lambda: set(self.loaded), create=True),
            patch("ollama_engine.set_model_running", side_effect=set_running, create=True),
            patch("model_downloads.fetch_popular_models", return_value=CATALOGUE),
            patch("model_downloads.fetch_model_variants", side_effect=variants),
            patch("hardware_panel.detect_hardware", return_value=HARDWARE),
            patch("hardware_panel.recommend_models", return_value=[RECOMMENDATION]),
        ]
        for item in self.patches:
            item.start()
        self.app = AIassist.AIAssistantGUI(self.root)
        self.wait(lambda: not getattr(self.app, "startup_pending", False) and not self.app_busy())
        self.app.model_name.set("existing:8b")
        self.app.select_model()
        self.app.open_downloads()
        self.dialog = self.app.download_dialog
        self.dialog.window.withdraw()
        self.wait(lambda: not self.dialog.loading_catalogue)

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
            deadline = time.monotonic() + 5
            while not self.app.closed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.assertTrue(self.app.closed)
            self.app.saver.thread.join(timeout=2)
        elif hasattr(self, "root"):
            self.root.destroy()
        for item in reversed(getattr(self, "patches", [])):
            item.stop()
        if hasattr(self, "chat_directory"):
            self.chat_directory.cleanup()
        self.assertFalse(getattr(self, "callback_errors", []))

    def wait(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "GUI operation timed out")
            self.root.update()
            time.sleep(.01)
        self.root.update()

    def app_busy(self):
        return self.app.is_loading_models or getattr(self.app, "lifecycle_busy", False)

    def select(self, name):
        self.dialog.notebook.select(self.dialog.popular_tab)
        self.dialog.tree.selection_set(name)
        self.root.update()
        self.wait(lambda: bool(self.dialog.variants) and self.dialog.variants[0]["name"].startswith(name + ":"))

    def test_live_list_search_and_size_selection(self):
        self.assertEqual(self.dialog.tree.get_children(), ("alpha", "beta"))
        self.assertEqual(self.dialog.download_button["state"], "disabled")
        self.dialog.search.set("coding")
        self.assertEqual(self.dialog.tree.get_children(), ("beta",))
        self.select("beta")
        self.assertEqual(self.dialog.selected_variant()["name"], "beta:1b")
        self.assertIn("1 GB", self.dialog.variant_choice.get())
        self.assertEqual(self.dialog.download_button["state"], "normal")

    def test_stale_variant_response_cannot_change_selected_family(self):
        release = threading.Event()
        started = threading.Event()
        def delayed(name):
            if name == "alpha":
                started.set()
                release.wait(4)
            return variants(name)
        try:
            with patch("model_downloads.fetch_model_variants", side_effect=delayed):
                self.dialog.tree.selection_set("alpha")
                self.wait(started.is_set)
                self.select("beta")
                release.set()
                self.root.after(200, lambda: None)
                deadline = time.monotonic() + .3
                while time.monotonic() < deadline:
                    self.root.update()
                    time.sleep(.01)
            self.assertEqual(self.dialog.selected_variant()["name"], "beta:1b")
        finally:
            release.set()

    def test_progress_success_and_explicit_use_preserve_chat_until_chosen(self):
        self.select("alpha")
        self.app.conversation.append({"role": "user", "content": "Keep my current chat"})
        def download(host, name, callback, cancel_event=None):
            self.assertEqual(name, "alpha:1b")
            self.assertTrue(host.startswith("http"))
            callback({"status": "pulling files", "completed": 50, "total": 100})
            self.installed.append(name)
            callback({"status": "success"})
        with patch("model_downloads.pull_model", side_effect=download):
            self.dialog.start_download()
            self.assertTrue(self.dialog.downloading)
            self.assertEqual(str(self.dialog.variant_picker["state"]), "disabled")
            self.wait(lambda: not self.dialog.downloading and not self.app_busy())
        self.assertIn("alpha:1b", self.app.model_picker["values"])
        self.assertEqual(self.app.model_name.get(), "existing:8b")
        self.assertEqual(len(self.app.conversation), 1)
        self.assertEqual(self.dialog.progress["value"], 100)
        self.assertEqual(self.dialog.download_button["text"], "Installed")
        self.dialog.use_model()
        self.assertEqual(self.app.model_name.get(), "alpha:1b")
        self.assertFalse(self.app.conversation)
        self.assertTrue(self.dialog.closed)

    def test_cancellation_and_failure_restore_retry_controls(self):
        self.select("alpha")
        def cancellable(host, name, callback, cancel_event=None):
            cancel_event.wait(4)
            raise DownloadCancelled("Cancelled")
        with patch("model_downloads.pull_model", side_effect=cancellable):
            self.dialog.start_download()
            self.dialog.cancel_download()
            self.wait(lambda: not self.dialog.downloading)
        self.assertIn("cancelled", self.dialog.status["text"])
        self.assertNotIn("alpha:1b", self.app.model_picker["values"])
        self.assertEqual(self.dialog.download_button["state"], "normal")
        with patch("model_downloads.pull_model", side_effect=RuntimeError("Out of disk space")):
            self.dialog.start_download()
            self.wait(lambda: not self.dialog.downloading)
        self.assertIn("Out of disk space", self.dialog.status["text"])
        self.assertEqual(self.dialog.download_button["state"], "normal")

    def test_catalogue_failure_can_be_refreshed(self):
        with patch("model_downloads.fetch_popular_models", side_effect=RuntimeError("Offline")):
            self.dialog.reload_catalogue()
            self.wait(lambda: not self.dialog.loading_catalogue)
        self.assertIn("internet connection", self.dialog.status["text"])
        self.assertEqual(self.dialog.reload_button["state"], "normal")
        self.dialog.reload_catalogue()
        self.wait(lambda: not self.dialog.loading_catalogue)
        self.assertEqual(len(self.dialog.tree.get_children()), 2)

    def test_download_during_old_refresh_is_not_lost(self):
        release = threading.Event()
        started = threading.Event()
        def old_snapshot():
            snapshot = list(self.installed)
            started.set()
            release.wait(4)
            return snapshot
        try:
            with patch("ollama_engine.list_ollama_models", side_effect=old_snapshot):
                self.app.refresh_models()
                self.wait(started.is_set)
                self.installed.append("alpha:1b")
                self.app.model_downloaded("alpha:1b")
                release.set()
                self.wait(lambda: not self.app_busy())
            self.assertIn("alpha:1b", self.app.model_picker["values"])
            self.assertEqual(self.app.model_name.get(), "existing:8b")
        finally:
            release.set()

    def test_hardware_cards_have_own_download_and_setup_guide(self):
        panel = self.dialog.advisor
        self.wait(lambda: bool(panel.recommendations))
        self.assertIn("Test RTX GPU", panel.summary["text"])
        self.assertIn("24.0 GiB", panel.summary["text"])
        self.assertEqual(len(panel.card_buttons), 1)
        card, button = panel.card_buttons[0]
        self.assertEqual(button["text"], "Download 1 GB")
        guide = panel.guide_text(card)
        self.assertIn("ollama pull alpha:1b", guide)
        self.assertIn("nvidia-smi", guide)
        self.assertIn("4096", guide)
        self.assertIn("Test RTX GPU", guide)
        self.assertIn("C:/test/models", guide)
        window = panel.show_guide(card)
        self.assertTrue(window.winfo_exists())
        window.destroy()

    def test_card_download_installs_and_use_applies_context_to_loading_and_chat(self):
        panel = self.dialog.advisor
        self.wait(lambda: bool(panel.recommendations))
        def download(host, name, callback, cancel_event=None):
            self.assertEqual(name, "alpha:1b")
            self.installed.append(name)
            callback({"status": "success"})
        with patch("model_downloads.pull_model", side_effect=download):
            panel.card_buttons[0][1].invoke()
            self.wait(lambda: not self.dialog.downloading and not self.app_busy())
        self.assertIn("alpha:1b", self.app.model_picker["values"])
        self.assertEqual(self.app.model_name.get(), "existing:8b")
        self.assertEqual(panel.card_buttons[0][1]["text"], "Use in chat")
        self.wait(lambda: panel.card_buttons[0][1]["state"] == "normal")
        panel.card_buttons[0][1].invoke()
        self.wait(lambda: not self.app_busy())
        self.assertEqual(self.app.model_contexts["alpha:1b"], 4096)
        self.assertIn(("alpha:1b", True, 4096), self.load_requests)
        self.app.input_box.insert("1.0", "Hello")
        with patch("ollama_engine.request_ollama", return_value="Hello back") as request:
            self.app.send_prompt()
            self.wait(lambda: not self.app.is_waiting)
            self.assertEqual(request.call_args.kwargs["context_length"], 4096)
            self.assertTrue(callable(request.call_args.kwargs["on_chunk"]))
        self.assertEqual(self.app.conversation[-1]["content"], "Hello back")

    def test_failed_hardware_scan_can_be_retried(self):
        panel = self.dialog.advisor
        self.wait(lambda: panel.hardware is not None)
        with patch("hardware_panel.detect_hardware", side_effect=RuntimeError("Unavailable")):
            panel.rescan()
            self.wait(lambda: not panel.hardware_loading)
        self.assertIn("failed", panel.summary["text"])
        self.assertEqual(panel.rescan_button["state"], "normal")
        panel.rescan()
        self.wait(lambda: panel.hardware is not None and bool(panel.recommendations))
        self.assertIn("Test RTX GPU", panel.summary["text"])

    def test_footer_never_targets_a_hidden_tab_or_stale_rescan_card(self):
        self.select("beta")
        self.assertEqual(self.dialog.selected_variant()["name"], "beta:1b")
        self.dialog.notebook.select(self.dialog.advisor.frame)
        self.root.update()
        self.assertIsNone(self.dialog.selected_variant())
        self.dialog.recommended_target = RECOMMENDATION
        self.dialog.downloading = True
        self.dialog.notebook.select(self.dialog.popular_tab)
        self.root.update()
        self.assertIsNone(self.dialog.recommended_target)
        self.assertEqual(self.dialog.selected_variant()["name"], "beta:1b")
        self.dialog.downloading = False
        self.dialog.notebook.select(self.dialog.advisor.frame)
        self.dialog.recommended_target = RECOMMENDATION
        self.dialog.advisor.rescan()
        self.assertIsNone(self.dialog.recommended_target)


class ContextContractTests(unittest.TestCase):
    def test_recommended_context_is_sent_to_chat_and_default_context_is_preserved(self):
        for context in (None, 4096):
            with self.subTest(context=context), patch("ollama_engine.urllib.request.urlopen", return_value=io.BytesIO(
                    b'{"message":{"content":"Hello"},"done":true}\n')) as opened:
                self.assertEqual(ollama_engine.request_ollama([], "test:1b", context), "Hello")
                payload = json.loads(opened.call_args.args[0].data)
                if context is None:
                    self.assertNotIn("options", payload)
                else:
                    self.assertEqual(payload["options"]["num_ctx"], 4096)

    def test_preloading_uses_recommended_context_and_unloading_keeps_its_original_behavior(self):
        with patch("ollama_engine.ollama_api") as api:
            ollama_engine.set_model_running("test:1b", True, 4096)
            payload = api.call_args.args[1]
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertEqual(payload["keep_alive"], -1)
            ollama_engine.set_model_running("test:1b", False, 4096)
            payload = api.call_args.args[1]
            self.assertEqual(payload["keep_alive"], 0)
            self.assertNotIn("options", payload)


if __name__ == "__main__":
    unittest.main()
