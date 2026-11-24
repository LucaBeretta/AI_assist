"""Model screen operations with fake catalogs, downloads and no real model changes."""

import io
import threading
import unittest
from unittest.mock import Mock, patch

from app_core import runtime
import model_library
import test_webui_app as fixtures


class ModelApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.engine.ollama_api = Mock()
        # Never reach Hugging Face from a test; tests that need its list patch this again.
        hugging_face = patch("model_library.fetch_hugging_face_models", return_value=[])
        self.hugging_face = hugging_face.start()
        self.addCleanup(hugging_face.stop)

    def test_recommendations_reuse_detected_hardware_and_catalog(self):
        hardware = {"server_local": True, "gpus": []}
        self.api._hardware = hardware
        with patch("model_library.fetch_popular_models", return_value=[{"name": "family"}]), \
             patch("model_advisor.recommend_models", return_value=[{"name": "recommended"}]) as recommend:
            result = self.api.recommend_models()
        self.assertEqual(result["recommendations"], [{"name": "recommended"}])
        recommend.assert_called_once_with(hardware, [{"name": "family"}])

    def test_hugging_face_suggestions_follow_ollamas_under_the_same_approval(self):
        import network_gate
        hardware = {"server_local": True, "gpus": []}
        self.api._hardware = hardware
        seen = []
        def models():
            current = network_gate._current_task()
            seen.append((current.purpose, sorted(current.hosts)))
            return [{"repo": "org/model-GGUF"}]
        with patch("model_library.fetch_popular_models", return_value=[{"name": "family"}]), \
             patch("model_advisor.recommend_models", return_value=[{"name": "family:7b"}]), \
             patch("model_library.fetch_hugging_face_models", side_effect=models), \
             patch("model_advisor.recommend_hugging_face", return_value=[{"name": "hf.co/org/model-GGUF:Q4_K_M"}]) as hf:
            result = self.api.recommend_models()
        self.assertEqual(seen, [("Read model lists from Ollama and Hugging Face", ["huggingface.co", "ollama.com"])])
        self.assertEqual([card["name"] for card in result["recommendations"]], ["family:7b", "hf.co/org/model-GGUF:Q4_K_M"])
        hf.assert_called_once_with(hardware, [{"repo": "org/model-GGUF"}], skip={"family:7b"},
                                   available=model_library.hugging_face_tag_available)

    def test_hugging_face_failure_keeps_ollamas_suggestions(self):
        self.api._hardware = {"server_local": True, "gpus": []}
        with patch("model_library.fetch_popular_models", return_value=[{"name": "family"}]), \
             patch("model_advisor.recommend_models", return_value=[{"name": "family:7b"}]), \
             patch("model_library.fetch_hugging_face_models", side_effect=RuntimeError("Could not reach Hugging Face.")):
            result = self.api.recommend_models()
        self.assertEqual(result["recommendations"], [{"name": "family:7b"}])
        self.assertIsNone(result["catalog_error"])

    def test_blocked_catalog_still_returns_hardware_and_is_not_asked_again(self):
        hardware = {"server_local": True, "gpus": []}
        self.api._hardware = hardware
        # model_library turns the gate's refusal into RuntimeError.
        blocked = RuntimeError("Internet access was not approved, so it was blocked (Internet connection: ollama.com).")
        with patch("model_library.fetch_popular_models", side_effect=blocked) as fetch:
            first = self.api.recommend_models()
            second = self.api.recommend_models()
        self.assertTrue(first["ok"])
        self.assertEqual(first["hardware"], hardware)
        self.assertIn("not approved", first["catalog_error"])
        self.assertEqual(second["catalog_error"], first["catalog_error"])
        self.assertEqual(fetch.call_count, 1)

    def test_catalog_is_read_once_per_session_until_refresh(self):
        self.api._hardware = {"server_local": True, "gpus": []}
        with patch("model_library.fetch_popular_models", return_value=[{"name": "family"}]) as fetch,              patch("model_advisor.recommend_models", return_value=[]):
            self.api.recommend_models()
            self.api.recommend_models()
            self.assertEqual(fetch.call_count, 1)
            self.api.recommend_models(True)
            self.assertEqual(fetch.call_count, 2)
        self.assertFalse(self.api.recommend_models("yes")["ok"])

    def test_catalog_and_variant_pages_share_one_gated_task_and_are_cached(self):
        import network_gate
        self.api._hardware = {"server_local": True, "gpus": []}
        seen = []
        def catalog():
            seen.append(("catalog", network_gate.in_task()))
            return [{"name": "family"}]
        def advise(hardware, rows):
            seen.append(("variants", network_gate.in_task()))
            return [{"name": "family:7b"}]
        with patch("model_library.fetch_popular_models", side_effect=catalog),              patch("model_advisor.recommend_models", side_effect=advise):
            first = self.api.recommend_models()
            second = self.api.recommend_models()
        self.assertEqual(seen, [("catalog", True), ("variants", True)])
        self.assertEqual(first["recommendations"], [{"name": "family:7b"}])
        self.assertEqual(second["recommendations"], first["recommendations"])

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
        with patch("app_core.ollama.urllib.request.urlopen", return_value=io.BytesIO(b"")) as opened:
            self.assertEqual(runtime.ollama_api("/api/delete", {"model": "cold"}, method="DELETE"), {})
        self.assertEqual(opened.call_args.args[0].get_method(), "DELETE")


if __name__ == "__main__":
    unittest.main()
