"""Attachment confinement, limits and native vision capability checks."""

import base64
from pathlib import Path
import json
import unittest
from unittest.mock import patch
import zipfile

import test_webui_app as fixtures
from webui.app import request_messages


def encoded(data):
    return base64.b64encode(data).decode("ascii")


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_text_is_saved_in_chat_folder_and_sent_as_context_then_exported(self):
        chat = self.api.new_chat()["chat"]["id"]
        result = self.api.attach(chat, "code.py", encoded(b"print('hello')"))
        self.assertTrue(result["ok"], result)
        item = result["attachment"]
        path = self.store.directory / chat / "attachments" / item["id"]
        self.assertEqual(path.read_bytes(), b"print('hello')")
        stream = self.api.send(chat, "explain", "m", 8192)["stream_id"]
        self.events.wait("done", stream)
        self.assertIn("[Attached file: code.py]\nprint('hello')", self.calls[0][0][-1]["content"])
        record = self.store.load(chat)
        self.assertNotIn("attachments", record)
        self.assertEqual(record["messages"][0]["content"], "explain")
        edited = self.api.edit_and_resend(chat, 0, "explain again")["stream_id"]
        self.events.wait("done", edited)
        self.assertIn("print('hello')", self.calls[-1][0][-1]["content"])
        archive_path = self.store.directory / "backup.zip"
        self.store.export_all(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read(f"chats/{chat}/attachments/{item['id']}"), path.read_bytes())

    def test_invalid_names_base64_secrets_pdf_and_limits_do_not_write(self):
        chat = self.api.new_chat()["chat"]["id"]
        for name in ("../a.txt", "C:\\a.txt", "/a.txt", "a/b.txt", "a\\b.txt", "a.txt:stream", "NUL.txt", ".env", "secret.key"):
            self.assertFalse(self.api.attach(chat, name, encoded(b"text"))["ok"], name)
        for name, data in (("a.txt", "?"), ("a.pdf", encoded(b"%PDF-1")),
                           ("a.png", encoded(b"not an image")), ("a.txt", encoded(b"x" * 64001)),
                           ("a.txt", encoded(b"-----BEGIN PRIVATE KEY-----\nsecret"))):
            self.assertFalse(self.api.attach(chat, name, data)["ok"], name)
        self.assertFalse((self.store.directory / chat).exists())
        (self.store.directory / "agent.settings").write_text(json.dumps({"file_limit": 128000}), encoding="utf-8")
        self.assertTrue(self.api.attach(chat, "larger.txt", encoded(b"x" * 64001))["ok"])

    def test_image_requires_vision_at_attach_and_send(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "vision-model")
        data = encoded(b"\x89PNG\r\n\x1a\nimage")
        with patch("webui.app.fetch_model_capabilities", return_value={"capabilities": []}):
            self.assertFalse(self.api.attach(chat, "image.png", data)["ok"])
        with patch("webui.app.fetch_model_capabilities", return_value={"capabilities": ["vision"]}) as show:
            self.assertTrue(self.api.attach(chat, "image.png", data)["ok"])
            self.events.wait("done", self.api.send(chat, "see", "vision-model", None)["stream_id"])
            show.assert_called_with("http://localhost:11434", "vision-model")
        self.assertEqual(self.calls[-1][0][-1]["images"], [data])
        before = self.store.load(chat)
        with patch("webui.app.fetch_model_capabilities", return_value={"capabilities": []}):
            self.assertFalse(self.api.send(chat, "again", "text-model", None)["ok"])
            self.assertFalse(self.api.set_chat_model(chat, "text-model")["ok"])
        self.assertEqual(self.store.load(chat), before)

    def test_incognito_attachment_has_no_file_and_failed_save_cleans_up(self):
        chat = self.api.new_chat(incognito=True)["chat"]["id"]
        self.assertTrue(self.api.attach(chat, "private.txt", encoded(b"private"))["ok"])
        self.assertEqual(list(self.store.directory.iterdir()), [])
        self.assertIn("private", request_messages({**self.api.open_chat(chat)["chat"], "messages": [
            {"role": "user", "content": "hi", "attachments": self.api.open_chat(chat)["chat"]["attachments"]}]})[-1]["content"])
        normal = self.api.new_chat()["chat"]["id"]
        with patch.object(self.store, "save", side_effect=OSError("full")):
            self.assertFalse(self.api.attach(normal, "a.txt", encoded(b"x"))["ok"])
        self.assertEqual(list((self.store.directory / normal / "attachments").iterdir()), [])

    def test_symlink_parent_is_rejected(self):
        chat = self.api.new_chat()["chat"]["id"]
        destination = self.store.directory / "elsewhere"
        destination.mkdir()
        try:
            (self.store.directory / chat).symlink_to(destination, target_is_directory=True)
        except OSError:
            self.skipTest("Directory links require privileges on this platform")
        self.assertFalse(self.api.attach(chat, "a.txt", encoded(b"x"))["ok"])
        self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
