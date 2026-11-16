"""Capabilities UI checks with mocked metadata; no Ollama server is needed."""

import gc
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from capabilities_view import CapabilitiesView


COLORS = {
    "background": "#f7f5f0", "surface": "#fffefb", "sidebar": "#efede7",
    "sidebar_hover": "#e6e3db", "sidebar_muted": "#888477", "ink": "#302d28",
    "muted": "#777267", "border": "#e1ddd4", "accent": "#bd6546",
    "accent_hover": "#a35338", "user": "#eee9e0", "success": "#4f7759",
    "error": "#b4483c",
}


def metadata(model="alpha:8b", context=131072, flags=None):
    return {"name": model, "capabilities": ["completion", "vision", "tools", "thinking"]
            if flags is None else flags, "context_length": context, "family": "alpha",
            "parameter_size": "8B", "quantization": "Q4_K_M"}


class CapabilitiesViewTests(unittest.TestCase):
    def setUp(self):
        # Collect closed widget cycles on the Tk thread before starting workers.
        gc.collect()
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.download_requests = []
        self.section_requests = []
        self.app = SimpleNamespace(
            COLORS=COLORS, model_name=tk.StringVar(self.root, value="alpha:8b"),
            model_contexts={"alpha:8b": 4096}, ollama_host=lambda: "http://localhost:11434",
            button=lambda parent, text, command, bg, fg, hover, **kw: tk.Button(
                parent, text=text, command=command, bg=bg, fg=fg, activebackground=hover, **kw),
            open_downloads=lambda: self.download_requests.append(True),
            show_section=lambda section: self.section_requests.append(section), project_path=None,
        )
        self.view = None
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        if self.view is not None:
            self.view.close()
        self.root.update()
        self.root.destroy()
        self.assertEqual(self.callback_errors, [])
        self.view = self.app = self.root = None
        gc.collect()

    def build(self):
        self.view = CapabilitiesView(self.root, self.app)
        self.view.frame.pack(fill="both", expand=True)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "UI did not reach the expected state")

    def test_metadata_separates_native_context_and_app_setting(self):
        main_thread = threading.get_ident()
        worker_threads = []

        def fetch(host, model):
            worker_threads.append(threading.get_ident())
            self.assertEqual((host, model), ("http://localhost:11434", "alpha:8b"))
            return metadata()

        with patch("capabilities_view.fetch_model_capabilities", side_effect=fetch):
            self.build()
            self.wait_for(lambda: self.view._loaded_model == "alpha:8b")
        self.assertTrue(all(worker != main_thread for worker in worker_threads))
        self.assertEqual(self.view.native_context_label.cget("text"), "131,072 tokens")
        self.assertEqual(self.view.app_context_label.cget("text"), "4,096 tokens")
        self.assertEqual(self.view.support_labels["vision"].cget("text"), "Advertised")
        self.app.model_contexts["alpha:8b"] = 8192
        self.wait_for(lambda: self.view.app_context_label.cget("text") == "8,192 tokens")
        self.assertEqual(self.view.native_context_label.cget("text"), "131,072 tokens")
        self.view.download_button.invoke()
        self.assertEqual(self.download_requests, [True])

    def test_empty_selection_does_not_fetch(self):
        self.app.model_name.set("")
        with patch("capabilities_view.fetch_model_capabilities") as fetch:
            self.build()
            self.root.update()
            fetch.assert_not_called()
        self.assertEqual(self.view.model_label.cget("text"), "No model selected")
        self.assertEqual(self.view.support_labels["tools"].cget("text"), "Not checked")
        self.assertIn("download", self.view.status_label.cget("text"))

    def test_older_metadata_keeps_missing_capabilities_unknown(self):
        with patch("capabilities_view.fetch_model_capabilities", return_value=metadata(context=None, flags=[])):
            self.build()
            self.wait_for(lambda: self.view._loaded_model is not None)
        self.assertEqual(self.view.native_context_label.cget("text"), "Not reported")
        self.assertEqual(self.view.support_labels["tools"].cget("text"), "Not advertised")
        self.assertIn("older servers", self.view.flags_label.cget("text"))

    def test_offline_error_can_be_retried(self):
        with patch("capabilities_view.fetch_model_capabilities",
                   side_effect=[RuntimeError("Could not reach Ollama."), metadata()]):
            self.build()
            self.wait_for(lambda: self.view.refresh_button.cget("text") == "Retry")
            self.assertIn("Could not reach Ollama", self.view.status_label.cget("text"))
            self.assertEqual(self.view.support_labels["tools"].cget("text"), "Unavailable")
            self.view.refresh_button.invoke()
            self.wait_for(lambda: self.view._loaded_model == "alpha:8b")

    def test_late_response_cannot_replace_new_model(self):
        release = threading.Event()
        entered = threading.Event()
        finished = threading.Event()
        self.releases.append(release)

        def fetch(_host, model):
            if model == "alpha:8b":
                entered.set()
                release.wait(3)
                finished.set()
                return metadata()
            return metadata(model, 32768, ["completion"])

        with patch("capabilities_view.fetch_model_capabilities", side_effect=fetch):
            self.build()
            self.wait_for(entered.is_set)
            self.app.model_name.set("beta:3b")
            self.wait_for(lambda: self.view._loaded_model == "beta:3b")
            release.set()
            self.wait_for(finished.is_set)
            self.wait_for(lambda: self.view.events.empty())
        self.assertEqual(self.view.model_label.cget("text"), "beta:3b")
        self.assertEqual(self.view.native_context_label.cget("text"), "32,768 tokens")
        self.assertEqual(self.view.app_context_label.cget("text"), "Ollama default")
        self.assertEqual(self.view.support_labels["vision"].cget("text"), "Not advertised")

    def test_close_cancels_polling_and_detaches_model_trace(self):
        self.app.model_name.set("")
        self.build()
        poll_id = self.view.poll_id
        self.assertIn(poll_id, self.root.tk.call("after", "info"))
        self.view.close()
        request_id = self.view.request_id
        self.assertNotIn(poll_id, self.root.tk.call("after", "info"))
        self.app.model_name.set("beta:3b")
        self.assertEqual(self.view.request_id, request_id)
        self.assertIsNone(self.view._model_trace)
        self.view.close()

    def test_coding_agent_requires_native_tools_and_a_selected_project(self):
        with patch("capabilities_view.fetch_model_capabilities", return_value=metadata()):
            self.build()
            self.wait_for(lambda: self.view._loaded_model == "alpha:8b")
        self.assertIn("Choose a project", self.view.agent_status_label.cget("text"))
        self.assertNotIn("Coding agent available", self.view.agent_status_label.cget("text"))
        self.view.agent_button.invoke()
        self.assertEqual(self.section_requests, ["dev"])
        self.app.project_path = "C:/selected-project"
        self.wait_for(lambda: "Coding agent available" in self.view.agent_status_label.cget("text"))
        self.assertIn("Review each file change and command", self.view.agent_status_label.cget("text"))
        self.app.project_path = None
        self.wait_for(lambda: "Choose a project" in self.view.agent_status_label.cget("text"))

    def test_selected_project_does_not_enable_agent_without_model_tools(self):
        self.app.project_path = "C:/selected-project"
        with patch("capabilities_view.fetch_model_capabilities", return_value=metadata(flags=["completion"])):
            self.build()
            self.wait_for(lambda: self.view._loaded_model == "alpha:8b")
        self.assertIn("not advertised", self.view.agent_status_label.cget("text"))
        self.assertNotIn("Coding agent available", self.view.agent_status_label.cget("text"))

    def test_model_refresh_clears_previous_agent_readiness(self):
        self.app.project_path = "C:/selected-project"
        with patch("capabilities_view.fetch_model_capabilities", return_value=metadata()):
            self.build()
            self.wait_for(lambda: "Coding agent available" in self.view.agent_status_label.cget("text"))
        self.app.model_name.set("")
        self.assertNotIn("Coding agent available", self.view.agent_status_label.cget("text"))
        self.assertIn("Choose a model", self.view.agent_status_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
