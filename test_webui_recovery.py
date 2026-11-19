"""Durable drafts and interrupted replies, without a window or real engine."""

import threading
import time
import unittest

import test_webui_app as fixtures
from webui.app import Api


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_draft_survives_restart_and_invalid_input_is_json(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.assertTrue(self.api.save_draft(chat, "unfinished\nλ")["ok"])
        restarted = Api(self.events, self.store, self.engine, colibri=self.colibri)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.open_chat(chat)["chat"]["draft"], "unfinished\nλ")
        self.assertFalse(self.api.save_draft(chat, None)["ok"])

    def test_partial_is_durable_during_silence_then_replaced_on_finish(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("first")
            release.wait(3)
            on_chunk(" second")
            return "first second"
        self.engine.request_ollama = request
        self.api._autosave_interval = 0.01
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hi", "m", None)["stream_id"]
        self.events.wait("token", stream)
        deadline = time.monotonic() + 2
        while not self.store.load(chat)["messages"][-1].get("partial"):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(self.api.state()["interrupted"], [])
        restarted = Api(self.events, self.store, self.engine, colibri=self.colibri)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.state()["interrupted"][0]["chat_id"], chat)
        self.api.save_draft(chat, "next question")
        release.set()
        self.events.wait("done", stream)
        saved = self.store.load(chat)
        self.assertEqual(saved["messages"][-1], {"role": "assistant", "content": "first second"})
        self.assertEqual(len(saved["messages"]), 2)
        self.assertEqual(saved["draft"], "next question")
        self.assertEqual(restarted.state()["interrupted"], [])

    def test_keep_and_discard_require_an_idle_interrupted_reply(self):
        for keep in (True, False):
            record = self.store.new_record()
            record["messages"] = [{"role": "user", "content": "hi"},
                                  {"role": "assistant", "content": "interrupted", "partial": True}]
            chat = self.store.save(record)["id"]
            self.assertFalse(self.api.send(chat, "wait", "m", None)["ok"])
            result = self.api.resolve_interrupted(chat, keep)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["chat"]["messages"]), 2 if keep else 1)
            self.assertNotIn("partial", result["chat"]["messages"][-1])
            self.assertFalse(self.api.resolve_interrupted(chat, keep)["ok"])


if __name__ == "__main__":
    unittest.main()
