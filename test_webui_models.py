"""Model screen operations with fake catalogs, downloads and no real model changes."""

import io
import threading
import unittest
from unittest.mock import Mock, patch

import ollama_engine
import test_webui_app as fixtures


class ModelApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.engine.ollama_api = Mock()

    def test_recommendations_reuse_detected_hardware_and_catalog(self):
        hardware = {"server_local": True, "gpus": []}
        self.api._hardware = hardware
        with patch("model_library.fetch_popular_models", return_value=[{"name": "family"}]), \
             patch("model_advisor.recommend_models", return_value=[{"name": "recommended"}]) as recommend:
            result = self.api.recommend_models()
        self.assertEqual(result["recommendations"], [{"name": "recommended"}])
        recommend.assert_called_once_with(hardware, [{"name": "family"}])

    def test_pull_progress_success_stop_and_failure(self):
        def pull(host, name, emit, cancel):
            emit({"status": "downloading", "completed": 4, "total": 10})
            emit({"status": "success"})
        with patch("model_library.pull_model", side_effect=pull):
            result = self.api.pull_model("new:7b")
            self.assertTrue(result["ok"])
            self.events.wait("pull_done")
        progress = [item for item in self.events.items if item["type"] == "pull_progress"]
        self.assertEqual((progress[0]["completed"], progress[0]["total"]), (4, 10))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def waiting(host, name, emit, cancel):
            entered.set()
            release.wait()
            emit({"status": "success"})
        with patch("model_library.pull_model", side_effect=waiting):
            identity = self.api.pull_model("new:7b")["pull_id"]
            self.assertTrue(entered.wait(3))
            self.assertFalse(self.api.delete_model("new:7b")["ok"])
            self.assertTrue(self.api.stop_pull(identity)["active"])
            release.set()
            self.events.wait("pull_stopped")
        with patch("model_library.pull_model", side_effect=RuntimeError("offline")):
            self.api.pull_model("new:7b")
            self.assertIn("offline", self.events.wait("pull_error")["error"])

    def test_delete_refuses_loaded_and_active_models(self):
        self.assertFalse(self.api.delete_model("fake:7b")["ok"])
        self.engine.ollama_api.assert_not_called()
        self.assertTrue(self.api.delete_model("cold:7b")["ok"])
        self.engine.ollama_api.assert_called_once_with("/api/delete", {"model": "cold:7b"}, method="DELETE")
        self.assertFalse(self.api.delete_model("bad name")["ok"])
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            entered.set()
            release.wait()
            on_chunk("done")
            return "done"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        identity = self.api.send(chat, "hi", "active", None)["stream_id"]
        self.assertTrue(entered.wait(3))
        self.assertFalse(self.api.delete_model("active")["ok"])
        release.set()
        self.events.wait("done", identity)

    def test_delete_transport_accepts_empty_success_body(self):
        with patch("ollama_engine.urllib.request.urlopen", return_value=io.BytesIO(b"")) as opened:
            self.assertEqual(ollama_engine.ollama_api("/api/delete", {"model": "cold"}, method="DELETE"), {})
        self.assertEqual(opened.call_args.args[0].get_method(), "DELETE")


if __name__ == "__main__":
    unittest.main()
