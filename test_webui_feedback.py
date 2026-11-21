"""Post-task feedback never reruns model requests or silently teaches memory."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from webui.feedback_loop import FeedbackLog
from test_webui_app import ApiTests


class PostTaskFeedbackTests(unittest.TestCase):
    def setUp(self):
        ApiTests.setUp(self)

    def test_useful_feedback_is_local_and_does_not_call_models_or_change_memory(self):
        chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(chat)
        record["messages"] = [{"role": "assistant", "content": "Completed"}]
        self.store.save(record)
        saved = self.api.record_task_feedback(chat, 0, "useful")
        self.assertTrue(saved["ok"], saved)
        self.assertFalse(saved["remembered"])
        entries = FeedbackLog(self.store.directory).read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["rating"], "useful")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.api.list_memories()["memories"], [])

    def test_improvement_only_enters_shared_memory_on_explicit_opt_in(self):
        chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(chat)
        record["messages"] = [{"role": "assistant", "content": "Completed"}]
        self.store.save(record)
        self.assertTrue(self.api.record_task_feedback(chat, 0, "not_useful", "Use short answers.")["ok"])
        self.assertEqual(self.api.list_memories()["memories"], [])
        self.assertTrue(self.api.record_task_feedback(chat, 0, "not_useful", "Use short answers.", True)["ok"])
        self.assertIn("Use short answers.", self.api.list_memories()["memories"][0]["text"])

    def test_invalid_or_incognito_feedback_does_not_persist(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.assertFalse(self.api.record_task_feedback(chat, 0, "useful")["ok"])
        incognito = self.api.new_chat(True)["chat"]["id"]
        self.assertFalse(self.api.record_task_feedback(incognito, -1, "useful", task_id="task-1")["ok"])
        self.assertFalse(self.api.record_task_feedback(chat, -1, "not_useful", "x" * 1201, task_id="task-1")["ok"])
        self.assertEqual(FeedbackLog(self.store.directory).read(), [])

    def test_developer_feedback_and_symlink_protection(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.assertTrue(self.api.record_task_feedback(chat, -1, "not_useful", "Run checks.", False, "task-1")["ok"])
        self.assertEqual(FeedbackLog(self.store.directory).read()[0]["task_id"], "task-1")
        path = self.store.directory / "task-feedback.json"
        path.unlink()
        target = self.store.directory / "target.txt"
        target.write_text("untouched", encoding="utf-8")
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Cannot create symbolic links on this system")
        self.assertFalse(self.api.record_task_feedback(chat, -1, "useful", task_id="task-2")["ok"])
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_corrupted_feedback_is_not_overwritten(self):
        chat = self.api.new_chat()["chat"]["id"]
        path = self.store.directory / "task-feedback.json"
        path.write_text("{invalid", encoding="utf-8")
        self.assertFalse(self.api.record_task_feedback(chat, -1, "useful", task_id="task-1")["ok"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{invalid")


if __name__ == "__main__":
    unittest.main()
