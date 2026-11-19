"""Saved prompts and chat instructions survive restart and affect inference."""

import unittest
from unittest.mock import patch

import test_webui_app as fixtures
from webui.app import Api


class PromptTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_save_replace_list_delete_and_restart_use_the_same_store(self):
        self.assertEqual(self.api.list_prompts()["prompts"], [])
        self.assertTrue(self.api.save_prompt("review", "Inspect the diff: caffè")["ok"])
        self.assertTrue(self.api.save_prompt("review", "Updated prompt")["ok"])
        restarted = Api(self.events, self.store, self.engine)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.list_prompts()["prompts"], [{"name": "review", "text": "Updated prompt"}])
        self.assertTrue(restarted.delete_prompt("review")["ok"])
        self.assertEqual(self.api.list_prompts()["prompts"], [])
        self.assertFalse(self.api.delete_prompt("missing")["ok"])
        self.assertTrue(any(event["type"] == "prompts_changed" for event in self.events.items))

    def test_invalid_inputs_and_failed_save_preserve_the_previous_prompts(self):
        for name, text in ((" ", "text"), ("name", " "), ("name", None), ("bad\nname", "text")):
            self.assertFalse(self.api.save_prompt(name, text)["ok"])
        self.api.save_prompt("review", "Original")
        with patch("webui.local_files.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(self.api.save_prompt("review", "Lost")["ok"])
        self.assertEqual(self.api.list_prompts()["prompts"][0]["text"], "Original")
        (self.store.directory / "saved.prompts").write_text("broken", encoding="utf-8")
        self.assertFalse(self.api.list_prompts()["ok"])
        self.assertFalse(self.api.save_prompt("review", "Do not overwrite corrupt data")["ok"])

    def test_per_chat_instructions_are_used_and_can_be_cleared(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.assertTrue(self.api.set_chat_instructions(chat, "Use exact numbers")["ok"])
        self.assertTrue(self.store.load(chat)["_agent_history_stale"])
        stream = self.api.send(chat, "hello", "fake:7b", None)["stream_id"]
        self.events.wait("done", stream)
        self.assertEqual(self.calls[-1][0][0], {"role": "system", "content": "Use exact numbers"})
        self.assertTrue(self.api.set_chat_instructions(chat, "")["ok"])
        self.assertFalse(self.api.set_chat_instructions(chat, None)["ok"])


if __name__ == "__main__":
    unittest.main()
