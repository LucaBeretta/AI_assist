"""Reviewed hunk application preserves originals, denials and race protection."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import diff_hunks
from coding_agent import run_agent
from task_checkpoints import CheckpointStore
from test_coding_agent import final_response, tool_response
import test_webui_app as fixtures


class HunkTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "project"
        self.root.mkdir()
        self.store = CheckpointStore(Path(folder.name) / "checkpoints")
        self.original = "".join(f"line {n}\r\n" for n in range(25))
        self.proposed = self.original.replace("line 1\r\n", "first\r\n").replace("line 22\r\n", "last\r\n")

    def test_partial_all_and_none_and_checkpoint(self):
        for selected in (["h1"], ["h1", "h2"], [], None):
            with self.subTest(selected=selected):
                source = self.root / "a.py"
                source.write_bytes(self.original.encode())
                actions = []
                def approve(action):
                    actions.append(action)
                    return True if selected is None else {"approved": True, "hunks": selected}
                with patch("coding_agent._chat", side_effect=[tool_response("write_file", {"path": "a.py", "content": self.proposed}), final_response()]):
                    result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "edit"}],
                        lambda event: None, approve, checkpoint_store=self.store)
                self.assertEqual([item["id"] for item in actions[0]["hunks"]], ["h1", "h2"])
                expected = self.original if selected == [] else self.original.replace("line 1\r\n", "first\r\n") if selected == ["h1"] else self.proposed
                self.assertEqual(source.read_bytes(), expected.encode())
                if selected:
                    text = next(item["content"] for item in result["messages"] if item["role"] == "tool")
                    self.assertIn("Applied blocks:", text)
                    self.assertIn("Rejected blocks:", text)
                    self.store.restore(result["task_id"])
                    self.assertEqual(source.read_bytes(), self.original.encode())

    def test_file_changed_during_partial_review_is_not_overwritten(self):
        source = self.root / "a.py"
        source.write_text(self.original, encoding="utf-8", newline="")
        def approve(action):
            source.write_text("external change")
            return {"approved": True, "hunks": ["h1"]}
        with patch("coding_agent._chat", side_effect=[tool_response("write_file", {"path": "a.py", "content": self.proposed}), final_response()]):
            result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "edit"}],
                lambda event: None, approve, checkpoint_store=self.store)
        self.assertEqual(source.read_text(), "external change")
        self.assertIn("changed after", next(item["content"] for item in result["messages"] if item["role"] == "tool"))

    def test_insert_delete_no_final_newline_and_invalid_ids(self):
        self.assertEqual(diff_hunks.apply_selected("a\nb", "x\na\n", ["h1"]), "x\na\n")
        for selection in (["h9"], ["h1", "h1"], "h1", [True]):
            with self.assertRaises(ValueError):
                diff_hunks.apply_selected("a", "b", selection)


class HunkApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_approval_event_exposes_blocks_and_api_selects_one(self):
        project = self.store.directory / "project"
        project.mkdir()
        original = "".join(f"line {n}\n" for n in range(25))
        (project / "a.py").write_bytes(original.encode())
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "fake:7b")
        proposed = original.replace("line 1\n", "first\n").replace("line 22\n", "last\n")
        with patch("coding_agent._chat", side_effect=[tool_response("write_file", {"path": "a.py", "content": proposed}), final_response()]):
            task = self.api.run_agent(chat, "Edit", str(project))["task_id"]
            approval = self.events.wait("approval")
            self.assertEqual(len(approval["action"]["hunks"]), 2)
            self.assertFalse(self.api.approve_action(task, approval["approval_id"], True, ["invalid"])["ok"])
            self.assertTrue(self.api.approve_action(task, approval["approval_id"], True, ["h2"])["ok"])
            self.events.wait("agent_done")
        self.assertEqual((project / "a.py").read_text(), original.replace("line 22\n", "last\n"))


if __name__ == "__main__":
    unittest.main()
