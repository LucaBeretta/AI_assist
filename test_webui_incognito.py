"""Incognito streams and compaction must never reach persistent storage."""

import unittest
from unittest.mock import patch
import zipfile

import test_webui_app as fixtures
from chat_store import ChatStoreError
from webui.app import Api


class IncognitoTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_send_draft_compact_and_duplicate_remain_in_memory(self):
        with patch.object(self.store, "save", side_effect=AssertionError("disk write")), \
             patch("shared_memory.memory_note", side_effect=AssertionError("shared memory")):
            record = self.api.new_chat(incognito=True)["chat"]
            chat = record["id"]
            self.assertTrue(self.api.save_draft(chat, "private draft")["ok"])
            self.assertEqual(self.api.open_chat(chat)["chat"]["draft"], "private draft")
            for index in range(4):
                stream = self.api.send(chat, str(index), "fake:7b", 8192)["stream_id"]
                self.events.wait("done", stream)
            stream = self.api.compact(chat)["stream_id"]
            self.events.wait("compact_done", stream)
            saved = self.api.open_chat(chat)["chat"]
            self.assertEqual(len(saved["messages"]), 8)
            self.assertEqual(saved["summary"]["upto"], 4)
            duplicate = self.api.new_chat_same_environment(chat)["chat"]
            self.assertTrue(duplicate["incognito"])
            self.assertEqual(duplicate["environment"]["memory_ids"], [])
            self.assertEqual(len(self.api.list_chats()["chats"]), 2)
        self.assertEqual(list(self.store.directory.iterdir()), [])
        with self.assertRaises(ChatStoreError):
            self.store.save(saved)
        path = self.store.directory / "backup.zip"
        self.assertEqual(self.store.export_all(path, unsaved={chat: saved}), 0)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ["README.txt"])
        restarted = Api(self.events, self.store, self.engine)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.list_chats()["chats"], [])
        self.api._close()
        self.assertFalse(self.api.open_chat(chat)["ok"])
        self.assertEqual(self.api._incognito, {})

    def test_incognito_flag_must_be_boolean(self):
        self.assertFalse(self.api.new_chat(incognito="yes")["ok"])


if __name__ == "__main__":
    unittest.main()
