"""Chat management and folder selection are testable without a window."""

import threading
import unittest
import zipfile

import test_webui_app as fixtures
from webui.desktop import Desktop


class ChatManagementTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_rename_search_snippet_and_delete(self):
        chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(chat)
        record["messages"] = [{"role": "user", "content": "Before\nNeedle in the message\nAfter"}]
        self.store.save(record)
        self.assertTrue(self.api.rename_chat(chat, "Custom title")["ok"])
        for query in ("custom", "needle"):
            result = self.api.search_chats(query)["chats"]
            self.assertEqual(result[0]["id"], chat)
            self.assertIn("Needle", result[0]["snippet"])
        self.assertEqual(self.api.search_chats("absent")["chats"], [])
        self.assertFalse(self.api.rename_chat(chat, " ")["ok"])
        self.assertTrue(self.api.delete_chat(chat)["ok"])
        self.assertFalse(self.api.open_chat(chat)["ok"])
        self.assertFalse(self.api.delete_chat("../escape")["ok"])

    def test_busy_chat_cannot_be_deleted_but_can_be_renamed(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("partial")
            release.wait()
            return "partial"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "hi", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        self.assertFalse(self.api.delete_chat(chat)["ok"])
        self.assertTrue(self.api.rename_chat(chat, "Named while working")["ok"])
        release.set()
        self.events.wait("done", stream)
        self.assertEqual(self.store.load(chat)["title"], "Named while working")

    def test_export_folder_dialog_is_injected_and_incognito_stays_out(self):
        chat = self.api.new_chat()["chat"]["id"]
        private = self.api.new_chat(incognito=True)["chat"]["id"]
        self.api.rename_chat(private, "Private")
        self.api._desktop = Desktop(folder_dialog=lambda: [str(self.store.directory)])
        result = self.api.export_chats()
        self.assertTrue(result["ok"], result)
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertIn(f"chats/{chat}.json", archive.namelist())
            self.assertNotIn(f"chats/{private}.json", archive.namelist())
        self.assertTrue(self.api.delete_chat(private)["ok"])
        self.api._desktop = Desktop(folder_dialog=lambda: None)
        self.assertEqual(self.api.export_chats(), {"ok": True, "cancelled": True})


if __name__ == "__main__":
    unittest.main()
