"""Undo for the developer agent's file changes (app_core.checkpoints)."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_core import approvals
from app_core.checkpoints import CheckpointError, CheckpointStore
from coding_agent import run_agent


def write_call(path, content):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": "write_file",
            "arguments": {"path": path, "content": content}}}]}}


class CheckpointCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.project = Path(folder.name, "project")
        self.project.mkdir()
        self.store = CheckpointStore(Path(folder.name, "data", "checkpoints"))

    def run_agent_writes(self, *writes, checkpoint=None):
        """Run the real agent loop; every write is approved."""
        calls = iter([write_call(path, content) for path, content in writes] + [{"message": {"content": "Done."}}])

        def approve(decision):
            approvals.resolve(decision, True)
        return run_agent("http://127.0.0.1:11434", "m", self.project, [{"role": "user", "content": "go"}],
                         lambda event: None, approvals.approver(approve, threading.Event()),
                         chat_fn=lambda *args: next(calls), checkpoint=checkpoint)


class UndoTests(CheckpointCase):
    def test_undo_restores_edited_files_and_removes_created_ones(self):
        (self.project / "main.py").write_bytes(b"print('original')\n")
        point = self.store.begin("chat-1", self.project)
        self.run_agent_writes(("main.py", "print('first')\n"), ("main.py", "print('second')\n"),
                              ("new/helper.py", "x = 1\n"), checkpoint=point)
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('second')\n")
        latest = self.store.latest_undoable("chat-1")
        self.assertEqual(latest["id"], point.id)
        self.assertEqual(len(latest["files"]), 2)  # main.py once, with its earliest original
        report = self.store.undo(point.id)
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('original')\n")
        self.assertFalse((self.project / "new" / "helper.py").exists())
        self.assertEqual((len(report["restored"]), len(report["removed"]), report["kept"]), (1, 1, []))
        self.assertIsNone(self.store.latest_undoable("chat-1"))
        with self.assertRaisesRegex(CheckpointError, "already undone"):
            self.store.undo(point.id)

    def test_undo_keeps_files_changed_after_the_ai(self):
        (self.project / "a.py").write_bytes(b"a = 0\n")
        (self.project / "b.py").write_bytes(b"b = 0\n")
        point = self.store.begin("chat-1", self.project)
        self.run_agent_writes(("a.py", "a = 1\n"), ("b.py", "b = 1\n"), checkpoint=point)
        (self.project / "b.py").write_bytes(b"b = 2  # my own edit\n")
        report = self.store.undo(point.id)
        self.assertEqual((self.project / "a.py").read_bytes(), b"a = 0\n")
        self.assertEqual((self.project / "b.py").read_bytes(), b"b = 2  # my own edit\n")
        self.assertEqual([(Path(path).name, reason) for path, reason in report["kept"]],
                         [("b.py", "changed after the AI wrote it")])

    def test_a_created_file_you_edited_or_deleted_is_left_alone(self):
        point = self.store.begin("chat-1", self.project)
        self.run_agent_writes(("x.py", "x = 1\n"), ("y.py", "y = 1\n"), checkpoint=point)
        (self.project / "x.py").write_bytes(b"x = 'mine'\n")
        (self.project / "y.py").unlink()
        report = self.store.undo(point.id)
        self.assertEqual((self.project / "x.py").read_bytes(), b"x = 'mine'\n")
        self.assertEqual(sorted(Path(path).name for path, _ in report["kept"]), ["x.py", "y.py"])

    def test_without_an_undo_copy_nothing_is_written(self):
        (self.project / "main.py").write_bytes(b"keep me\n")
        point = self.store.begin("chat-1", self.project)
        with patch("app_core.checkpoints.ensure_private_directory", side_effect=OSError("disk full")):
            result = self.run_agent_writes(("main.py", "changed\n"), checkpoint=point)
        tool_results = [m["content"] for m in result["messages"] if m["role"] == "tool"]
        self.assertTrue(tool_results[0].startswith("Tool failed: Could not save an undo copy of main.py"), tool_results)
        self.assertEqual((self.project / "main.py").read_bytes(), b"keep me\n")
        self.assertEqual([path.name for path in self.project.iterdir()], ["main.py"])  # no temporary file left

    def test_tasks_without_changes_leave_no_undo_point(self):
        self.store.begin("chat-1", self.project)
        self.assertEqual(self.store.list(), [])

    def test_undo_points_belong_to_their_chat_and_old_ones_are_pruned(self):
        store = CheckpointStore(self.store.root, keep=2)
        for index in range(3):
            (self.project / "f.py").write_bytes(f"v{index}\n".encode())
            point = store.begin(f"chat-{index}", self.project)
            self.run_agent_writes(("f.py", f"ai{index}\n"), checkpoint=point)
        store.prune()
        self.assertEqual(sorted(item["chat_id"] for item in store.list()), ["chat-1", "chat-2"])
        self.assertIsNone(store.latest_undoable("chat-0"))

    def test_damaged_or_unknown_points_are_refused(self):
        for bad in ("../x", "", "not-hex", "0" * 31):
            with self.subTest(bad=bad), self.assertRaises(CheckpointError):
                self.store.undo(bad)


if __name__ == "__main__":
    unittest.main()
