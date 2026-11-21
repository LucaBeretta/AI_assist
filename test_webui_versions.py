"""Version navigation preserves complete paths and compacted-history validity."""

import unittest

import test_webui_app as fixtures
from chat_store import ChatStoreError


class VersionTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.counter = 0
        def request(*args, on_chunk, **kwargs):
            self.counter += 1
            text = "answer-" + str(self.counter)
            on_chunk(text)
            return text
        self.engine.request_ollama = request

    def send(self, chat, text):
        stream = self.api.send(chat, text, "fake:7b", 8192)["stream_id"]
        self.events.wait("done", stream)

    def test_regenerate_switch_and_edit_keep_entire_old_tail(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.send(chat, "first")
        self.events.wait("done", self.api.regenerate(chat)["stream_id"])
        self.assertEqual(self.api.open_chat(chat)["versions"], {"0": [1, 2]})
        self.send(chat, "followup")
        old_path = self.store.load(chat)["messages"]
        first = self.api.switch_version(chat, 0, 0)
        self.assertEqual(first["messages"][-1]["content"], "answer-1")
        self.assertEqual(len(first["messages"]), 2)
        self.assertEqual(self.api.switch_version(chat, 0, 1)["messages"], old_path)
        self.events.wait("done", self.api.edit_and_resend(chat, 0, "edited first")["stream_id"])
        edited = self.store.load(chat)["messages"]
        self.assertEqual(edited[0]["content"], "edited first")
        self.assertEqual(len(edited), 2)
        result = self.api.switch_version(chat, -1, 0)
        self.assertEqual(result["messages"], old_path)
        self.assertEqual(result["versions"]["-1"], [0, 2])
        self.assertEqual(self.api.switch_version(chat, -1, 1)["messages"], edited)

    def test_nested_forks_are_visible_only_for_the_matching_prefix(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.send(chat, "a")
        self.send(chat, "b")
        self.events.wait("done", self.api.regenerate(chat)["stream_id"])
        self.assertEqual(self.api.open_chat(chat)["versions"]["2"], [1, 2])
        self.events.wait("done", self.api.edit_and_resend(chat, 0, "different")["stream_id"])
        self.send(chat, "b")
        self.events.wait("done", self.api.regenerate(chat)["stream_id"])
        result = self.api.open_chat(chat)
        self.assertEqual(result["versions"]["2"], [1, 2])
        self.api.switch_version(chat, -1, 0)
        result = self.api.switch_version(chat, 2, 0)
        self.assertEqual(result["messages"][-1]["content"], "answer-2")
        self.assertEqual(result["versions"]["2"], [0, 2])

    def test_only_changes_to_covered_history_invalidate_summary(self):
        chat = self.api.new_chat()["chat"]["id"]
        for text in ("a", "b", "c", "d"):
            self.send(chat, text)
        record = self.store.load(chat)
        record["summary"] = {"text": "summary", "upto": 4, "created_at": record["created_at"]}
        record["agent_history"] = [{"role": "user", "content": "outdated native task history"}]
        self.store.save(record)
        self.events.wait("done", self.api.regenerate(chat)["stream_id"])
        self.assertIsNotNone(self.store.load(chat)["summary"])
        self.events.wait("done", self.api.edit_and_resend(chat, 0, "new")["stream_id"])
        self.assertIsNone(self.store.load(chat)["summary"])
        self.assertEqual(self.store.load(chat)["agent_history"][0]["content"], "outdated native task history")
        self.assertTrue(self.store.load(chat)["_agent_history_stale"])
        self.assertEqual(len(self.api.switch_version(chat, -1, 0)["messages"]), 8)

    def test_legacy_and_invalid_branches(self):
        record = self.store.new_record()
        self.store.save(record)
        self.assertEqual(self.api.open_chat(record["id"])["versions"], {})
        for branches in ([], {"x": []}, {"0": [[{"role": "tool", "content": "x"}]]}):
            with self.assertRaises(ChatStoreError):
                self.store.save({**record, "branches": branches})
        self.assertFalse(self.api.switch_version(record["id"], -1, 0)["ok"])
        self.assertFalse(self.api.regenerate(record["id"])["ok"])
        self.assertFalse(self.api.edit_and_resend(record["id"], True, "x")["ok"])


if __name__ == "__main__":
    unittest.main()
