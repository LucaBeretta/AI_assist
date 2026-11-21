"""Task originals, created files, bounded retention and confined restore."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from coding_agent import run_agent
from task_checkpoints import CheckpointStore
from test_agent_support import isolate_agent_data
from test_coding_agent import tool_response, final_response
import test_webui_app as fixtures


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # The real path: macOS temporary folders sit behind the /var link and Windows can report short (8.3) names.
        base = Path(folder.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.store = CheckpointStore(base / "backups")

    def test_default_store_is_shared_app_data_and_never_inside_a_project(self):
        from chat_store import ChatStore
        data_directory = isolate_agent_data(self)
        for project in (self.root, self.root.parent / "another-project"):
            project.mkdir(exist_ok=True)
            source = project / "main.py"
            source.write_text("original")
            replies = iter([tool_response("write_file", {"path": "main.py", "content": "changed"}), final_response()])
            result = run_agent("http://localhost:11434", "m", project, [{"role": "user", "content": "task"}],
                lambda event: None, lambda action: True, chat_fn=lambda *args: next(replies))
            self.assertEqual(list(project.iterdir()), [source])
            self.assertTrue((data_directory / "checkpoints" / result["task_id"] / "manifest.json").is_file())
            self.assertEqual(len(CheckpointStore(data_directory / "checkpoints").list(project)), 1)
        self.assertEqual(ChatStore().directory, data_directory.resolve())

    def test_first_original_only_and_new_files_are_restored_after_failed_task(self):
        original = self.root / "a.py"
        original.write_bytes(b"original\r\n")
        responses = iter([tool_response("write_file", {"path": "a.py", "content": "second"}),
                          tool_response("edit_file", {"path": "a.py", "old_text": "second", "new_text": "third"}),
                          tool_response("write_file", {"path": "new/b.py", "content": "created"}),
                          final_response()])
        result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "task"}],
                           lambda event: None, lambda action: True, chat_fn=lambda *args: next(responses),
                           checkpoint_store=self.store)
        self.assertEqual(original.read_bytes(), b"third")
        task = result["task_id"]
        manifest = self.store.list(self.root)[0]
        self.assertEqual(set(manifest["files"]), {"a.py", "new/b.py"})
        self.assertTrue(manifest["files"]["new/b.py"]["created"])
        self.assertEqual(self.store.restore(task)["restored"], 2)
        self.assertEqual(original.read_bytes(), b"original\r\n")
        self.assertFalse((self.root / "new/b.py").exists())

    def test_retains_ten_per_project_and_never_records_secret_files(self):
        path = self.root / "a.py"
        path.write_text("safe")
        identities = []
        for _ in range(12):
            identity = str(uuid.uuid4())
            identities.append(identity)
            self.store.capture(identity, self.root, path, b"safe")
            self.store.prune(self.root, identity)
        records = self.store.list(self.root)
        self.assertEqual(len(records), 10)
        self.assertEqual({item["task_id"] for item in records}, set(identities[2:]))
        with self.assertRaises(ValueError):
            self.store.capture(str(uuid.uuid4()), self.root, self.root / ".env", b"token")
        with self.assertRaises(ValueError):
            self.store.capture(str(uuid.uuid4()), self.root, path, b"-----BEGIN PRIVATE KEY-----")
        self.assertFalse(self.store.capture(str(uuid.uuid4()), self.root, self.root.parent / "outside.py", b"safe"))

    def test_new_checkpoint_waits_while_windows_briefly_holds_its_folder(self):
        # Antivirus can still be scanning the files just staged, so Windows refuses the folder rename for a moment.
        real_replace = os.replace
        refused = []

        def busy_folder_once(source, target):
            if Path(source).name.startswith(".checkpoint-") and not refused:
                refused.append(target)
                raise PermissionError(13, "Access is denied")
            real_replace(source, target)

        path = self.root / "a.py"
        path.write_text("safe")
        identity = str(uuid.uuid4())
        with patch("app_core.paths._RETRY_REPLACE", True), patch("app_core.paths.time.sleep") as sleep, \
                patch("app_core.paths.os.replace", side_effect=busy_folder_once):
            self.assertTrue(self.store.capture(identity, self.root, path, b"safe"))
        self.assertEqual(len(refused), 1)
        sleep.assert_called_once()
        self.assertEqual([item["task_id"] for item in self.store.list(self.root)], [identity])

    def test_incomplete_checkpoint_does_not_block_other_edits(self):
        incomplete = str(uuid.uuid4())
        (self.store.directory / incomplete).mkdir(parents=True)
        source = self.root / "notes.txt"
        source.write_text("before")
        replies = iter([tool_response("write_file", {"path": source.name, "content": "after"}), final_response()])
        result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "Edit notes"}],
                           lambda event: None, lambda action: True, chat_fn=lambda *args: next(replies),
                           checkpoint_store=self.store)
        self.assertEqual(source.read_text(), "after")
        warnings = []
        self.assertEqual([item["task_id"] for item in self.store.list(self.root, warnings=warnings)], [result["task_id"]])
        self.assertEqual(warnings, [f"Skipped unreadable checkpoint {incomplete}."])
        with self.assertRaises(FileNotFoundError):
            self.store.restore(incomplete)

    def test_tampered_manifest_or_backup_fails_before_changing_any_file(self):
        path = self.root / "a.py"
        path.write_text("safe")
        task = str(uuid.uuid4())
        self.store.capture(task, self.root, path, b"safe")
        path.write_text("changed")
        manifest_path = self.store.directory / task / "manifest.json"
        saved = json.loads(manifest_path.read_text())
        bad = {**saved, "files": {**saved["files"], "../outside.py": {"created": True, "backup": None}}}
        manifest_path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.store.restore(task)
        self.assertEqual(path.read_text(), "changed")
        manifest_path.write_text(json.dumps(saved))
        backup = self.store.directory / task / saved["files"]["a.py"]["backup"]
        backup.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.store.restore(task)
        self.assertEqual(path.read_text(), "changed")

    def test_denied_actions_do_not_create_checkpoints(self):
        responses = iter([tool_response("write_file", {"path": "a.py", "content": "x"}), final_response()])
        run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "task"}],
                  lambda event: None, lambda action: False, chat_fn=lambda *args: next(responses), checkpoint_store=self.store)
        self.assertFalse(self.store.directory.exists())


class CheckpointApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_api_lists_and_restores_task_after_completion(self):
        project = self.store.directory / "project"
        project.mkdir()
        source = project / "a.py"
        source.write_text("original")
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "m")
        with patch("coding_agent._chat", side_effect=[tool_response("write_file", {"path": "a.py", "content": "new"}), final_response()]):
            task = self.api.run_agent(chat, "task", str(project))["task_id"]
            approval = self.events.wait("approval")
            self.api.approve_action(task, approval["approval_id"], True)
            self.events.wait("agent_done")
        self.assertEqual(self.api._developer.tasks, {})
        self.assertEqual(source.read_text(), "new")
        self.assertEqual(self.api.list_checkpoints(str(project))["checkpoints"][0]["task_id"], task)
        self.assertTrue(self.api.restore_checkpoint(task)["ok"])
        self.assertEqual(source.read_text(), "original")
        self.events.wait("checkpoint_restored")
        self.assertFalse(self.api.restore_checkpoint("../escape")["ok"])


if __name__ == "__main__":
    unittest.main()
