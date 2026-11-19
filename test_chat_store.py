"""Saved chat persistence checks using temporary directories only."""

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from chat_store import ChatStore, ChatStoreError


class ChatStoreTests(unittest.TestCase):
    def test_legacy_appearance_preferences_are_not_reported_as_a_damaged_chat(self):
        chat = self.store.create(model="demo")
        (self.directory / "appearance.json").write_text('{"theme": "Dark"}', encoding="utf-8")
        self.assertEqual([record["id"] for record in self.store.list_chats()], [chat["id"]])
        self.assertEqual(self.store.errors, [])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "chats"
        self.store = ChatStore(self.directory)

    def test_listing_again_sees_every_change_made_outside_this_store(self):
        kept = self.store.create(model="demo", title="Kept")
        changed = self.store.create(model="demo", title="Before")
        removed = self.store.create(model="demo", title="Removed")
        self.assertEqual(len(self.store.list_chats()), 3)
        other = ChatStore(self.directory)  # another writer, e.g. a restored backup
        record = other.load(changed["id"])
        record["title"], record["_manual_title"] = "After", True
        other.save(record)
        other.delete(removed["id"])
        (self.directory / f"{kept['id']}.json").write_text("{damaged", encoding="utf-8")
        listed = {chat["id"]: chat["title"] for chat in self.store.list_chats()}
        self.assertEqual(listed, {changed["id"]: "After"})
        self.assertEqual(len(self.store.errors), 1)
        self.assertEqual(len(self.store.list_chats()), 1)  # a damaged file is reported again, never remembered
        self.assertEqual(len(self.store.errors), 1)

    def test_changing_a_listed_record_does_not_change_the_next_listing(self):
        chat = self.store.create(model="demo", title="Original")
        listed = self.store.list_chats()[0]
        listed["title"] = "Changed in memory only"
        returned = self.store.save(dict(self.store.load(chat["id"]), draft="x"))
        returned["messages"].append({"role": "user", "content": "changed after saving"})
        again = self.store.list_chats()[0]
        self.assertEqual((again["title"], again["messages"]), ("Original", []))

    def test_create_persists_complete_record_in_its_own_uuid_file(self):
        chat = self.store.create(model="qwen3:8b", mode="dev", project_path="C:/Projects/demo")
        self.assertEqual(str(uuid.UUID(chat["id"])), chat["id"])
        self.assertEqual(chat["title"], "New chat")
        self.assertEqual(chat["messages"], [])
        self.assertEqual(chat["draft"], "")
        self.assertIsNone(chat["context_length"])
        self.assertEqual(chat["mode"], "dev")
        self.assertEqual(chat["model"], "qwen3:8b")
        self.assertEqual(chat["project_path"], "C:/Projects/demo")
        self.assertEqual(chat["project_files"], [])
        self.assertEqual(chat["activity"], [])
        self.assertEqual(chat["agent_history"], [])
        self.assertTrue(chat["created_at"].endswith("Z"))
        self.assertEqual(datetime.fromisoformat(chat["updated_at"]).utcoffset().total_seconds(), 0)
        self.assertEqual(list(self.directory.iterdir()), [self.directory / f"{chat['id']}.json"])
        self.assertEqual(self.store.load(chat["id"]), chat)

    def test_unicode_code_draft_and_settings_survive_a_new_store(self):
        chat = self.store.create(model="model:latest", mode="dev", project_path="C:/caffè/工具")
        messages = [
            {"role": "system", "content": "Keep all whitespace.\n"},
            {"role": "user", "content": "  Explain café ☕ and 日本語\n```python\nprint('hello')\n```\n\t"},
            {"role": "assistant", "content": "```python\nvalue = '🦊'\n\nprint(value)  \n```\n"},
        ]
        chat.update(messages=deepcopy(messages), draft="unfinished\n\t雪  ", context_length=32768,
                    project_files=["src/caffè.py", "docs/工具.md"])
        saved = self.store.save(chat)
        restored = ChatStore(self.directory).load(chat["id"])
        self.assertEqual(restored, saved)
        self.assertEqual(restored["messages"], messages)
        self.assertEqual(restored["draft"], "unfinished\n\t雪  ")
        self.assertEqual(restored["context_length"], 32768)
        self.assertEqual(restored["project_files"], ["src/caffè.py", "docs/工具.md"])
        self.assertEqual(restored["created_at"], chat["created_at"])
        self.assertIn("日本語", (self.directory / f"{chat['id']}.json").read_text(encoding="utf-8"))

    def test_title_comes_from_first_user_message_and_is_bounded(self):
        chat = self.store.create()
        prompt = "  Explain\nthis\tproject " + "猫" * 100
        chat["messages"] = [{"role": "system", "content": "Ignore for title"},
                            {"role": "user", "content": prompt},
                            {"role": "user", "content": "Second prompt"}]
        saved = self.store.save(chat)
        self.assertEqual(len(saved["title"]), 72)
        self.assertTrue(saved["title"].startswith("Explain this project "))
        self.assertTrue(saved["title"].endswith("…"))
        self.assertEqual(saved["messages"][1]["content"], prompt)
        saved["messages"][1]["content"] = "A changed first prompt"
        self.assertEqual(self.store.save(saved)["title"], saved["title"])

    def test_developer_activity_preserves_exact_text_paths_and_commands(self):
        chat = self.store.create(mode="dev")
        activity = [
            {"type": "read_file", "text": "  Reading café.py\n\t工具 🦊  ", "path": "src/café.py"},
            {"type": "run", "text": "```text\nFirst line\n\nSecond line  \n```\n",
             "command": "python -m unittest"},
            {"type": "status", "text": "Done"},
        ]
        chat["activity"] = deepcopy(activity)
        saved = self.store.save(chat)
        restored = ChatStore(self.directory).load(chat["id"])
        self.assertEqual(restored["activity"], activity)
        self.assertEqual(saved["activity"], activity)
        saved["activity"][0]["text"] = "Local mutation"
        self.assertEqual(self.store.load(chat["id"])["activity"], activity)

    def test_applied_change_diffs_survive_restart_and_invalid_diffs_do_not_overwrite(self):
        chat = self.store.create(mode="dev", project_path="C:/Projects/demo")
        diff = ("--- a/src/greeting.py\n+++ b/src/greeting.py\n@@ -1,2 +1,3 @@\n"
                "-name = 'world'\n+name = 'caf\u00e9'\n+message = f'Hello, {name}!'\n"
                " print(message)\n\\ No newline at end of file\n")
        chat["activity"] = [
            {"type": "status", "text": "Inspecting project"},
            {"type": "changed", "text": "Updated src/greeting.py", "path": "src/greeting.py", "diff": diff,
             "destination": "C:/Projects/demo/src/greeting.py"},
        ]
        self.store.save(chat)
        restored = ChatStore(self.directory).load(chat["id"])
        self.assertEqual(restored["activity"], chat["activity"])
        path = self.directory / f"{chat['id']}.json"
        original = path.read_bytes()
        for field in ("diff", "destination"):
            for invalid in (None, 42, True, [], {}):
                changed = deepcopy(restored)
                changed["activity"][1][field] = invalid
                with self.subTest(field=field, value=invalid), self.assertRaises(ChatStoreError):
                    self.store.save(changed)
                self.assertEqual(path.read_bytes(), original)

    def test_native_agent_history_preserves_tools_thinking_and_json_arguments(self):
        chat = self.store.create(mode="dev")
        history = [
            {"role": "system", "content": "Project context\n工具"},
            {"role": "user", "content": "Inspect café.py"},
            {"role": "assistant", "thinking": "  Inspect first\nthen explain  ", "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": {
                    "path": "src/café.py", "start_line": 1, "options": [None, True, 1.25, {"mode": "text"}],
                }}},
            ]},
            {"role": "tool", "tool_name": "read_file", "content": "```python\nprint('雪')  \n```\n"},
            {"role": "assistant", "content": "The project prints 雪.", "thinking": "Finished."},
        ]
        chat["agent_history"] = deepcopy(history)
        saved = self.store.save(chat)
        self.assertNotIn("content", chat["agent_history"][2], "Saving must not mutate the caller")
        history[2]["content"] = ""
        self.assertEqual(saved["agent_history"], history)
        self.assertEqual(ChatStore(self.directory).load(chat["id"])["agent_history"], history)
        saved["agent_history"][2]["tool_calls"][0]["function"]["arguments"]["path"] = "changed.py"
        self.assertEqual(self.store.load(chat["id"])["agent_history"], history)

    def test_invalid_native_agent_histories_cannot_replace_saved_data(self):
        chat = self.store.create(mode="dev")
        path = self.directory / f"{chat['id']}.json"
        original = path.read_bytes()
        invalid_histories = [
            "wrong", ["wrong"], [{"role": "other", "content": "text"}],
            [{"role": "tool"}], [{"role": "assistant", "content": None}],
            [{"role": "assistant", "thinking": []}],
            [{"role": "tool", "content": "text", "tool_name": None}],
            [{"role": "assistant", "tool_calls": {}}],
            [{"role": "assistant", "tool_calls": [None]}],
            [{"role": "assistant", "tool_calls": [{"function": {"name": 1}}]}],
            [{"role": "assistant", "tool_calls": [{"function": {"name": "read", "arguments": []}}]}],
            [{"role": "assistant", "tool_calls": [{"function": {"name": "read", "arguments": {"n": float("nan")}}}]}],
            [{"role": "assistant", "metadata": {"n": float("inf")}}],
            [{"role": "assistant", "metadata": {1: "non-text key"}}],
            [{"role": "assistant", "metadata": ("tuple",)}],
            [{"role": "assistant", "metadata": object()}],
        ]
        circular = {"role": "assistant"}
        circular["metadata"] = circular
        invalid_histories.append([circular])
        for index, history in enumerate(invalid_histories):
            with self.subTest(index=index), self.assertRaises(ChatStoreError):
                self.store.save(dict(chat, agent_history=history))
            self.assertEqual(path.read_bytes(), original)

    def test_manual_titles_survive_messages_and_restart_even_for_new_chat(self):
        chat = self.store.create()
        renamed = self.store.rename(chat["id"], "  My project  ")
        renamed["messages"] = [{"role": "user", "content": "Suggest another title"}]
        self.assertEqual(self.store.save(renamed)["title"], "My project")
        self.store.rename(chat["id"], "New chat")
        restarted = ChatStore(self.directory)
        renamed = restarted.load(chat["id"])
        renamed["messages"].append({"role": "assistant", "content": "Reply"})
        self.assertEqual(restarted.save(renamed)["title"], "New chat")

    def test_stale_message_save_preserves_a_newer_manual_rename(self):
        with patch("chat_store._utc_now", return_value="2026-01-01T10:00:00.000000Z"):
            chat = self.store.create()
        with patch("chat_store._utc_now", return_value="2026-01-02T10:00:00.000000Z"):
            self.store.rename(chat["id"], "My chosen title")
        chat["messages"] = [{"role": "user", "content": "A pending response"}]
        saved = self.store.save(chat)
        self.assertEqual(saved["title"], "My chosen title")
        self.assertEqual(saved["messages"], chat["messages"])

    def test_saved_records_are_copied_and_do_not_mutate_callers(self):
        chat = self.store.create()
        chat["messages"] = [{"role": "user", "content": "A title"}]
        before = deepcopy(chat)
        saved = self.store.save(chat)
        self.assertEqual(chat, before)
        saved["messages"][0]["content"] = "Local mutation"
        self.assertEqual(self.store.load(chat["id"])["messages"][0]["content"], "A title")

    def test_list_sorts_updates_and_filters_search_and_mode(self):
        with patch("chat_store._utc_now", return_value="2026-01-01T10:00:00.000000Z"):
            first = self.store.create(title="Café project", model="qwen3", mode="dev")
        with patch("chat_store._utc_now", return_value="2026-01-02T10:00:00.000000Z"):
            second = self.store.create(title="Weekend", model="llama3")
        self.assertEqual([chat["id"] for chat in self.store.list_chats()], [second["id"], first["id"]])
        first.update(messages=[{"role": "assistant", "content": "The NEEDLE is here"}],
                     draft="unfinished notes", project_path="C:/special-project")
        with patch("chat_store._utc_now", return_value="2026-01-03T10:00:00.000000Z"):
            self.store.save(first)
        self.assertEqual([chat["id"] for chat in self.store.list_chats()], [first["id"], second["id"]])
        for query in ("CAFÉ", "needle", "qwen3", "special-project", "unfinished"):
            with self.subTest(query=query):
                self.assertEqual([chat["id"] for chat in self.store.list_chats(query)], [first["id"]])
        self.assertEqual(self.store.list_chats("needle", mode="chat"), [])
        self.assertEqual([chat["id"] for chat in self.store.list_chats(mode="dev")], [first["id"]])
        self.assertEqual(self.store.list_chats("absent"), [])

    def test_independent_store_instances_do_not_lose_other_chats(self):
        first = self.store.create(title="First")
        another_store = ChatStore(self.directory)
        stale_first = another_store.load(first["id"])
        second = self.store.create(title="Second")
        stale_first["draft"] = "Changed by the other window"
        another_store.save(stale_first)
        self.assertEqual({chat["id"] for chat in self.store.list_chats()}, {first["id"], second["id"]})
        self.assertEqual(self.store.load(second["id"]), second)

    def test_corrupt_chat_is_listed_as_an_error_and_cannot_be_overwritten(self):
        damaged = self.store.create()
        healthy = self.store.create(title="Healthy")
        path = self.directory / f"{damaged['id']}.json"
        broken_contents = b'{"messages": the original damaged file'
        path.write_bytes(broken_contents)
        self.assertEqual([chat["id"] for chat in self.store.list_chats()], [healthy["id"]])
        self.assertTrue(any(damaged["id"] in error for error in self.store.errors))
        with self.assertRaisesRegex(ChatStoreError, "Could not load saved chat"):
            self.store.load(damaged["id"])
        with self.assertRaises(ChatStoreError):
            self.store.save(damaged)
        self.assertEqual(path.read_bytes(), broken_contents)
        self.store.delete(damaged["id"])
        self.assertEqual(len(self.store.list_chats()), 1)
        self.assertEqual(self.store.errors, [])

    def test_invalid_json_shapes_encoding_and_mismatched_ids_are_reported(self):
        chat = self.store.create()
        path = self.directory / f"{chat['id']}.json"
        wrong_id = dict(chat, id=str(uuid.uuid4()))
        for payload in (b"[]", b"\xff\xfe", json.dumps(wrong_id).encode("utf-8")):
            with self.subTest(payload=payload):
                path.write_bytes(payload)
                with self.assertRaises(ChatStoreError):
                    self.store.load(chat["id"])
                self.assertEqual(self.store.list_chats(), [])
                self.assertTrue(self.store.errors)

    def test_unsafe_ids_never_read_or_delete_outside_the_store(self):
        outside = Path(self.temporary.name) / "keep.json"
        outside.write_text("keep this", encoding="utf-8")
        chat = self.store.create()
        ids = ("../keep", "..\\keep", str(outside), "", "not-a-uuid", chat["id"].upper(), None)
        for chat_id in ids:
            with self.subTest(chat_id=chat_id):
                for action in (self.store.load, self.store.delete):
                    with self.assertRaises(ChatStoreError):
                        action(chat_id)
                with self.assertRaises(ChatStoreError):
                    self.store.rename(chat_id, "Rename")
                with self.assertRaises(ChatStoreError):
                    self.store.save(dict(chat, id=chat_id))
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep this")
        self.assertEqual(len(self.store.list_chats()), 1)

    def test_symbolic_links_are_not_followed_for_load_save_or_delete(self):
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("keep this", encoding="utf-8")
        chat = self.store.create()
        path = self.directory / f"{chat['id']}.json"
        path.unlink()
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"This environment cannot create symbolic links: {error}")
        for action in (self.store.load, self.store.delete):
            with self.assertRaises(ChatStoreError):
                action(chat["id"])
        with self.assertRaises(ChatStoreError):
            self.store.save(chat)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep this")
        self.assertTrue(path.is_symlink())

    def test_atomic_replace_failure_preserves_original_and_removes_temporary_file(self):
        chat = self.store.create()
        path = self.directory / f"{chat['id']}.json"
        original = path.read_bytes()
        chat["draft"] = "changed"
        with patch("chat_store.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(ChatStoreError, "disk unavailable"):
                self.store.save(chat)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_invalid_records_cannot_replace_a_valid_chat(self):
        chat = self.store.create()
        path = self.directory / f"{chat['id']}.json"
        original = path.read_bytes()
        invalid_changes = [
            {"title": " "}, {"model": None}, {"mode": "other"}, {"draft": 123},
            {"project_path": []}, {"project_files": "wrong"}, {"project_files": [None]},
            {"activity": "wrong"}, {"activity": ["wrong"]},
            {"activity": [{"type": "read"}]}, {"activity": [{"type": 12, "text": "read"}]},
            {"activity": [{"type": "read", "text": None}]},
            {"activity": [{"type": "read", "text": "read", "path": []}]},
            {"activity": [{"type": "run", "text": "run", "command": 10}]},
            {"activity": [{"type": "read", "text": "read", "unsupported": "value"}]},
            {"messages": "wrong"},
            {"messages": [{"role": "tool", "content": "unsupported"}]},
            {"messages": [{"role": "user", "content": None}]},
            {"context_length": True}, {"context_length": 0},
            {"created_at": "yesterday"}, {"updated_at": "2026-01-01T12:00:00"},
            {"_manual_title": "yes"},
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ChatStoreError):
                self.store.save(dict(chat, **changes))
            self.assertEqual(path.read_bytes(), original)

    def test_older_records_restore_empty_project_files_activity_and_agent_history(self):
        chat = self.store.create()
        del chat["project_files"]
        del chat["activity"]
        del chat["agent_history"]
        path = self.directory / f"{chat['id']}.json"
        path.write_text(json.dumps(chat), encoding="utf-8")
        self.assertEqual(self.store.load(chat["id"])["project_files"], [])
        self.assertEqual(self.store.load(chat["id"])["activity"], [])
        self.assertEqual(self.store.load(chat["id"])["agent_history"], [])
        self.assertEqual(self.store.save(chat)["activity"], [])

    def test_file_size_limit_protects_reads_and_writes_without_truncating(self):
        chat = self.store.create()
        path = self.directory / f"{chat['id']}.json"
        original = path.read_bytes()
        with patch("chat_store._MAX_CHAT_BYTES", len(original) - 1):
            with self.assertRaisesRegex(ChatStoreError, "file limit"):
                self.store.load(chat["id"])
        chat["draft"] = "A long draft" * 100
        with patch("chat_store._MAX_CHAT_BYTES", len(original) + 100):
            with self.assertRaisesRegex(ChatStoreError, "file limit"):
                self.store.save(chat)
        self.assertEqual(path.read_bytes(), original)

    def test_delete_is_scoped_and_missing_records_are_meaningful(self):
        first = self.store.create()
        second = self.store.create()
        self.assertIsNone(self.store.delete(first["id"]))
        self.assertIsNone(self.store.delete(first["id"]))
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            self.store.load(first["id"])
        self.assertEqual(self.store.load(second["id"]), second)

    def test_invalid_search_and_rename_arguments_are_rejected(self):
        chat = self.store.create()
        for title in ("", "\t\n", None):
            with self.subTest(title=title), self.assertRaises(ChatStoreError):
                self.store.rename(chat["id"], title)
        for arguments in ({"query": None}, {"mode": "unsupported"}, {"mode": []}):
            with self.subTest(arguments=arguments), self.assertRaises(ChatStoreError):
                self.store.list_chats(**arguments)

    def test_default_paths_use_override_local_app_data_then_home(self):
        root = Path(self.temporary.name)
        with patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(root / "override"),
                                    "LOCALAPPDATA": str(root / "local")}, clear=True):
            self.assertEqual(ChatStore().directory, (root / "override").resolve())
        with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "local")}, clear=True):
            self.assertEqual(ChatStore().directory, (root / "local" / "AI Assist" / "chats").resolve())
        with patch.dict(os.environ, {}, clear=True), patch("chat_store.Path.home", return_value=root):
            self.assertEqual(ChatStore().directory, (root / ".aiassist" / "chats").resolve())


if __name__ == "__main__":
    unittest.main()
