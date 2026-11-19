"""Validated web preferences are durable and cannot overwrite internal state."""

import unittest
import test_webui_app as fixtures
from webui import settings


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_welcome_completion_survives_restart_and_rejects_unknown_fields(self):
        self.api.set_idle_unload_minutes(5)
        self.assertTrue(self.api.set_web_preferences({"first_run_complete": True})["ok"])
        self.assertTrue(settings.load(self.store.directory)["first_run_complete"])
        self.assertEqual(settings.load(self.store.directory)["idle_unload_minutes"], 5)
        self.assertFalse(self.api.set_web_preferences({"first_run_complete": 1})["ok"])
        self.assertFalse(self.api.set_web_preferences({"window": {}})["ok"])

    def test_zoom_validation_and_thinking_duration_are_saved(self):
        self.assertTrue(self.api.set_web_preferences({"zoom": 1.2})["ok"])
        self.assertEqual(settings.load(self.store.directory)["zoom"], 1.2)
        for zoom in (True, .5, 2, float("nan")):
            self.assertFalse(self.api.set_web_preferences({"zoom": zoom})["ok"])
        def request(*args, on_chunk, **kwargs):
            on_chunk("<think>Reasoning")
            on_chunk("</think>Answer")
            return "<think>Reasoning</think>Answer"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hello", "fake:7b", None)["stream_id"]
        self.events.wait("done", stream)
        message = self.store.load(chat)["messages"][-1]
        self.assertGreaterEqual(message["thinking_seconds"], 0)
        self.assertEqual(message["content"], "<think>Reasoning</think>Answer")


if __name__ == "__main__":
    unittest.main()
