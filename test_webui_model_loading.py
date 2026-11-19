"""Loading progress uses queued events, stops on output, and never needs a window."""

import threading
import unittest
from unittest.mock import patch

import test_webui_app as fixtures
from webui.model_loading import ModelLoadProgress


class LoadingClockTests(unittest.TestCase):
    def test_ticks_and_first_output_report_elapsed_time_once(self):
        events = fixtures.Events()
        now = [20.0]
        progress = ModelLoadProgress(events.emit, "cold", threading.Event(), clock=lambda: now[0],
                                     autostart=False, stream_id="s", chat_id="a")
        progress.start(lambda: {})
        for value in (21.0, 22.0):
            now[0] = value
            progress.tick()
        now[0] = 35.0
        progress.first_output()
        progress.first_output()
        progress.tick()
        self.assertEqual([event["elapsed_seconds"] for event in events.items if event["type"] == "model_loading"], [0, 1, 2])
        self.assertEqual([event["load_seconds"] for event in events.items if event["type"] == "model_loaded"], [15])
        self.assertEqual(events.items[-1]["chat_id"], "a")

    def test_loaded_unknown_or_cancelled_state_never_claims_loading(self):
        for state in (lambda: {"warm": {}}, lambda: None,
                      lambda: (_ for _ in ()).throw(RuntimeError("offline"))):
            events = fixtures.Events()
            progress = ModelLoadProgress(events.emit, "warm", threading.Event(), autostart=False)
            progress.start(state)
            progress.tick()
            progress.first_output()
            self.assertEqual(events.items, [])
        events = fixtures.Events()
        cancel = threading.Event()
        progress = ModelLoadProgress(events.emit, "cold", cancel, autostart=False)
        progress.start(lambda: {})
        cancel.set()
        progress.tick()
        progress.first_output()
        self.assertEqual([event["type"] for event in events.items], ["model_loading"])


class LoadingApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_cold_model_reports_periodically_until_first_token(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.engine.running_details = lambda: {}
        def request(*args, on_chunk, **kwargs):
            release.wait()
            on_chunk("answer")
            return "answer"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hi", "cold", None)["stream_id"]
        with self.events.condition:
            self.assertTrue(self.events.condition.wait_for(lambda: any(
                event["type"] == "model_loading" and event["elapsed_seconds"] >= 1
                for event in self.events.items), timeout=4))
        self.assertFalse(any(event["type"] == "token" for event in self.events.items))
        progress = self.api._streams[stream]["loading"]
        release.set()
        self.events.wait("done", stream)
        loaded = self.events.wait("model_loaded", stream)
        self.assertGreaterEqual(loaded["load_seconds"], 1)
        types = [event["type"] for event in self.events.items]
        self.assertLess(types.index("model_loaded"), types.index("token"))
        count = len(self.events.items)
        progress.tick()
        self.assertEqual(len(self.events.items), count)

    def test_warm_model_and_failed_or_cancelled_requests_do_not_emit_false_loaded(self):
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hi", "fake:7b", None)["stream_id"]
        self.events.wait("done", stream)
        self.assertFalse(any(event["type"].startswith("model_load") for event in self.events.items))
        def fail(*args, **kwargs):
            raise RuntimeError("failed while loading")
        self.engine.request_ollama = fail
        stream = self.api.send(chat, "again", "cold", None)["stream_id"]
        self.events.wait("error", stream)
        self.assertFalse(any(event["type"] == "model_loaded" for event in self.events.items))
        self.events.items.clear()
        release = threading.Event()
        self.addCleanup(release.set)
        def waiting(*args, on_chunk, **kwargs):
            release.wait()
            on_chunk("discard this")
            return "discard this"
        self.engine.request_ollama = waiting
        stream = self.api.send(chat, "wait", "cold", None)["stream_id"]
        self.events.wait("model_loading", stream)
        self.api.stop(stream)
        release.set()
        self.events.wait("stopped", stream)
        self.assertFalse(any(event["type"] in {"token", "model_loaded"} for event in self.events.items))

    def test_developer_emits_loading_until_its_first_response(self):
        from test_coding_agent import final_response
        project = self.store.directory / "project"
        project.mkdir()
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "cold")
        with patch("coding_agent._chat", return_value=final_response("done")):
            self.api.run_agent(chat, "Inspect", str(project))
            self.events.wait("agent_done")
        kinds = [event["type"] for event in self.events.items]
        self.assertLess(kinds.index("model_loading"), kinds.index("model_loaded"))
        self.assertLess(kinds.index("model_loaded"), kinds.index("assistant"))


if __name__ == "__main__":
    unittest.main()
