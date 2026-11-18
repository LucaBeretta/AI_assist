"""Model residency settings, unload and GPU budget warnings without hardware."""

import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

import AIassist
import test_webui_app as fixtures
from webui.app import Api


class ModelMemoryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_setting_is_durable_off_by_default_and_sent_as_keep_alive(self):
        self.assertEqual(self.api.state()["settings"]["idle_unload_minutes"], 0)
        for invalid in (True, -1, 1441, "5", 1.5):
            self.assertFalse(self.api.set_idle_unload_minutes(invalid)["ok"])
        self.assertTrue(self.api.set_idle_unload_minutes(5)["ok"])
        restarted = Api(self.events, self.store, self.engine)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.state()["settings"]["idle_unload_minutes"], 5)
        options = []
        def request(*args, **kwargs):
            options.append(kwargs)
            kwargs["on_chunk"]("done")
            return "done"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        self.events.wait("done", self.api.send(chat, "hi", "m", 8192)["stream_id"])
        self.assertEqual(options[-1]["keep_alive"], "5m")
        self.api.set_idle_unload_minutes(0)
        self.events.wait("done", self.api.send(chat, "again", "m", 8192)["stream_id"])
        self.assertNotIn("keep_alive", options[-1])

    def test_unload_uses_existing_engine_and_errors_are_json(self):
        self.engine.set_model_running = Mock()
        self.assertTrue(self.api.unload_model("fake:7b")["ok"])
        self.engine.set_model_running.assert_called_once_with("fake:7b", start=False)
        self.events.wait("model_unloaded")
        self.engine.set_model_running.side_effect = RuntimeError("offline")
        self.assertEqual(self.api.unload_model("m"), {"ok": False, "error": "offline"})
        self.assertEqual(self.api._unloading, set())

    def test_active_stream_prevents_unload(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("start")
            release.wait(3)
            return "start"
        self.engine.request_ollama = request
        self.engine.set_model_running = Mock()
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hi", "m", None)["stream_id"]
        self.events.wait("token", stream)
        self.assertFalse(self.api.unload_model("m")["ok"])
        self.engine.set_model_running.assert_not_called()
        release.set()
        self.events.wait("done", stream)

    def test_warning_requires_multiple_models_known_local_capacity_and_overflow(self):
        self.api._hardware = {"server_local": True, "gpus": [{"total_bytes": 16}]}
        loaded = {"a": {"size": 9}, "b": {"size": 8}}
        self.engine.running_details = lambda: loaded
        self.api.state()
        event = self.events.wait("vram_warning")
        self.assertEqual((event["required_bytes"], event["gpu_memory_bytes"]), (17, 16))
        for models, hardware in (({"a": {"size": 20}}, self.api._hardware),
                                 ({"a": {"size": 8}, "b": {"size": 8}}, self.api._hardware),
                                 (loaded, {"server_local": False, "gpus": [{"total_bytes": 16}]}),
                                 (loaded, {"gpus": [{"total_bytes": None}]})):
            self.events.items.clear()
            self.engine.running_details = lambda: models
            self.api._hardware = hardware
            self.assertTrue(self.api.state()["ok"])
            self.assertEqual(self.events.items, [])

    def test_real_transport_serializes_keep_alive(self):
        frame = {"done": True, "message": {"content": "ok"}}
        with patch("AIassist.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(frame).encode())) as opened:
            AIassist.request_ollama([], model="m", keep_alive="7m")
        self.assertEqual(json.loads(opened.call_args.args[0].data)["keep_alive"], "7m")


if __name__ == "__main__":
    unittest.main()
