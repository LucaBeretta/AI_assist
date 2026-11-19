"""Memory receipts reflect a frozen request, including opt-out and incognito."""

import threading
import unittest
from unittest.mock import patch

from shared_memory import SharedMemory
import test_webui_app as fixtures


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.memory = SharedMemory(self.store.directory)
        self.memory.save("Prefer Python\nKeep exact numbers")

    def test_stable_ids_forget_and_done_receipt_match_request(self):
        listed = self.api.list_memories()["memories"]
        self.assertEqual(len(listed), 2)
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hello", "fake:7b", None)["stream_id"]
        done = self.events.wait("done", stream)
        self.assertEqual(done["memories_used"], listed)
        self.assertIn("Prefer Python", self.calls[-1][0][0]["content"])
        self.assertTrue(self.api.forget_memory(listed[0]["id"])["ok"])
        remaining = self.api.list_memories()["memories"]
        self.assertEqual(remaining, [listed[1]])
        stream = self.api.send(chat, "again", "fake:7b", None)["stream_id"]
        self.assertEqual(self.events.wait("done", stream)["memories_used"], remaining)
        self.assertNotIn("Prefer Python", self.calls[-1][0][0]["content"])
        self.assertFalse(self.api.forget_memory("missing")["ok"])

    def test_forget_during_generation_does_not_change_which_notes_were_sent(self):
        listed = self.api.list_memories()["memories"]
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("part")
            release.wait()
            return "part"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hello", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        self.api.forget_memory(listed[0]["id"])
        release.set()
        self.assertEqual(self.events.wait("done", stream)["memories_used"], listed)

    def test_disabled_selected_and_incognito_memory(self):
        listed = self.api.list_memories()["memories"]
        chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(chat)
        record["environment"]["memory_ids"] = [listed[1]["id"]]
        self.store.save(record)
        stream = self.api.send(chat, "hello", "fake:7b", None)["stream_id"]
        self.assertEqual(self.events.wait("done", stream)["memories_used"], [listed[1]])
        self.memory.save("Do not use", enabled=False)
        stream = self.api.send(chat, "again", "fake:7b", None)["stream_id"]
        self.assertEqual(self.events.wait("done", stream)["memories_used"], [])
        private = self.api.new_chat(incognito=True)["chat"]["id"]
        with patch("webui.memories.SharedMemory", side_effect=AssertionError("Incognito must not read notes")):
            stream = self.api.send(private, "private", "fake:7b", None)["stream_id"]
            self.assertEqual(self.events.wait("done", stream)["memories_used"], [])


if __name__ == "__main__":
    unittest.main()
