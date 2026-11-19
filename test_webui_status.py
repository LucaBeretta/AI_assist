"""Shared status computation and one publisher, without native UI objects."""

import threading
import unittest
from unittest.mock import patch

from status_values import snapshot_values
import test_webui_app as fixtures
from webui.status import StatusPush


class StatusTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_status_reuses_tk_values_and_exposes_numeric_placement_and_privacy(self):
        self.engine.running_details = lambda: {"fake:7b": dict(size=100, size_vram=67, context_length=8192)}
        chat = self.api.new_chat()["chat"]
        self.api.set_chat_model(chat["id"], "fake:7b")
        self.api.open_chat(chat["id"])
        value = self.api.status_snapshot()
        self.assertEqual(value["engine"], "ollama ready")
        self.assertEqual(value["gpu_fraction"], .67)
        self.assertAlmostEqual(value["cpu_fraction"], .33)
        self.assertEqual(value["vram_bytes"], 67)
        self.assertEqual(value["context_total"], 8192)
        self.assertEqual(value["privacy"], "local only")
        self.engine.ollama_url = lambda: "https://remote.example"
        self.assertEqual(self.api.status_snapshot()["privacy"], "remote server")
        self.api.set_chat_model(chat["id"], "model-cloud")
        self.assertEqual(self.api.status_snapshot()["privacy_kind"], "cloud model")
        with patch.object(self.engine, "running_details", side_effect=RuntimeError("offline")):
            self.assertEqual(self.api.status_snapshot()["engine_state"], "offline")
        shared = snapshot_values(engine="Ollama", connected=True, model="m", choice=8192,
            metrics=dict(prompt_eval_count=3000, eval_count=1100, eval_duration=2e9))
        self.assertEqual(shared["context"], "4.1k / 8.2k")
        self.assertEqual(shared["speed"], "550 tok/s")

    def test_live_agent_steps_and_metrics_reach_the_snapshot(self):
        project = self.store.directory / "project"
        project.mkdir()
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "fake:7b")
        release = threading.Event()
        self.addCleanup(release.set)
        def run(*, messages, on_event, on_context, **kwargs):
            on_context(dict(used=1000, total=16384))
            on_event(dict(type="metrics", step=3, metrics=dict(eval_count=100, eval_duration=2e9)))
            release.wait(3)
            return dict(messages=messages, assistant_text="done")
        with patch("coding_agent.run_agent", side_effect=run):
            self.api.run_agent(chat, "Inspect", str(project), max_steps=30)
            self.events.wait("metrics")
            value = self.api.status_snapshot()
            self.assertEqual((value["agent_step"], value["agent_max_steps"]), (3, 30))
            self.assertEqual(value["tokens_per_second"], 50)
            self.assertEqual(value["context_used"], 1000)
            release.set()
            self.events.wait("agent_done")

    def test_single_timer_and_close_drop_even_an_inflight_snapshot(self):
        emitted = threading.Event()
        publisher = StatusPush(lambda: {"ok": True}, lambda *args, **kw: emitted.set(), interval=.01)
        self.addCleanup(publisher.close)
        publisher.start()
        thread = publisher._thread
        publisher.start()
        self.assertIs(publisher._thread, thread)
        self.assertTrue(emitted.wait(1))
        publisher.close()
        self.assertFalse(thread.is_alive())
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        events = []
        def snapshot():
            entered.set()
            release.wait(2)
            return {"ok": True}
        publisher = StatusPush(snapshot, lambda *args, **kw: events.append(kw))
        self.addCleanup(publisher.close)
        publisher.start()
        self.assertTrue(entered.wait(1))
        publisher.close(timeout=0)
        release.set()
        publisher.close()
        publisher.tick()
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
