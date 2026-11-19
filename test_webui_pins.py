"""Pinned files are confined, bounded and counted in actual requests."""

import unittest
import threading
import json
from unittest.mock import patch

import coding_agent
import developer_tools
import test_webui_app as fixtures


class PinTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.project = self.store.directory / "project"
        self.project.mkdir()
        self.chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(self.chat)
        record["project_path"] = str(self.project)
        self.store.save(record)

    def test_changing_the_limit_refreshes_already_pinned_context(self):
        source = "# Selected source\n" + "x" * 70000
        (self.project / "large.py").write_text(source, encoding="utf-8", newline="\n")
        self.assertFalse(self.api.pin_file(self.chat, "large.py")["ok"])
        (self.store.directory / "agent.settings").write_text(json.dumps({"file_limit": 128000}), encoding="utf-8")
        self.assertTrue(self.api.pin_file(self.chat, "large.py")["ok"])
        self.assertEqual(self.api.open_chat(self.chat)["pinned_files"][0]["size_bytes"], len(source))

    def test_project_worker_passes_the_selected_limit_to_pinned_context(self):
        (self.project / "a.py").write_text("source", encoding="utf-8")
        self.api.pin_file(self.chat, "a.py")
        (self.store.directory / "agent.settings").write_text(json.dumps({"file_limit": 128000}), encoding="utf-8")
        with patch("developer_tools.build_project_context", wraps=developer_tools.build_project_context) as build:
            stream = self.api.send(self.chat, "Inspect", "fake:7b", None)["stream_id"]
            self.events.wait("done", stream)
        self.assertTrue(build.called)
        self.assertTrue(all(call.kwargs["max_file_bytes"] == 128000 for call in build.call_args_list))

    def test_pin_open_usage_send_and_unpin(self):
        (self.project / "a.py").write_text("important source\n" * 100)
        (self.project / ".env").write_text("secret")
        files = self.api.list_project_files(str(self.project))["files"]
        self.assertEqual([item["path"] for item in files], ["a.py"])
        self.assertGreater(files[0]["estimated_tokens"], 0)
        before = self.api.context_usage(self.chat)["used"]
        self.assertTrue(self.api.pin_file(self.chat, "a.py")["ok"])
        self.assertTrue(self.api.pin_file(self.chat, "a.py")["ok"])
        opened = self.api.open_chat(self.chat)
        self.assertEqual(opened["chat"]["environment"]["pinned_paths"], ["a.py"])
        self.assertEqual(opened["pinned_files"][0]["path"], "a.py")
        self.assertGreater(self.api.context_usage(self.chat)["used"], before)
        stream = self.api.send(self.chat, "Inspect", "fake:7b", None)["stream_id"]
        self.events.wait("done", stream)
        self.assertIn("important source", self.calls[-1][0][0]["content"])
        self.assertTrue(self.api.unpin_file(self.chat, "a.py")["ok"])
        self.assertEqual(self.api.open_chat(self.chat)["pinned_files"], [])
        for path in ("../outside.py", ".env"):
            self.assertFalse(self.api.pin_file(self.chat, path)["ok"])

    def test_folder_can_be_set_before_first_task_and_old_pins_are_not_retargeted(self):
        (self.project / "a.py").write_text("source", encoding="utf-8")
        fresh = self.api.new_chat(project_path=str(self.project))["chat"]
        self.assertEqual(fresh["messages"], [])
        self.assertTrue(self.api.pin_file(fresh["id"], "a.py")["ok"])
        self.assertTrue(self.api.set_chat_project(fresh["id"], str(self.project))["ok"])
        self.assertEqual(self.store.load(fresh["id"])["environment"]["pinned_paths"], ["a.py"])
        other = self.store.directory / "other"
        other.mkdir()
        result = self.api.set_chat_project(fresh["id"], str(other))
        self.assertEqual(result["chat"]["environment"]["pinned_paths"], [])
        self.assertFalse(self.api.set_chat_project(fresh["id"], str(other / "missing"))["ok"])
        self.assertFalse(self.api.new_chat(project_path=str(other / "missing"))["ok"])

    def test_folder_change_is_refused_during_a_stream(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("partial")
            release.wait(3)
            return "partial"
        self.engine.request_ollama = request
        stream = self.api.send(self.chat, "hello", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        self.assertFalse(self.api.set_chat_project(self.chat, str(self.project))["ok"])
        release.set()
        self.events.wait("done", stream)

    def test_new_chat_same_environment_preserves_the_folder_behind_pins(self):
        (self.project / "a.py").write_text("source for the new chat", encoding="utf-8")
        self.assertTrue(self.api.pin_file(self.chat, "a.py")["ok"])
        clone = self.api.new_chat_same_environment(self.chat)["chat"]
        self.assertEqual(clone["project_path"], str(self.project))
        self.assertEqual(clone["project_files"], ["a.py"])
        self.assertEqual(self.api.open_chat(clone["id"])["pinned_files"][0]["path"], "a.py")
        stream = self.api.send(clone["id"], "Inspect", "fake:7b", None)["stream_id"]
        self.events.wait("done", stream)
        self.assertIn("source for the new chat", self.calls[-1][0][0]["content"])

    def test_large_pins_refused_but_bounded_line_ranges_work(self):
        source = "".join(f"line {n}\n" for n in range(15000))
        (self.project / "large.py").write_bytes(source.encode())
        result = self.api.pin_file(self.chat, "large.py")
        self.assertFalse(result["ok"])
        self.assertIn("limit", result["error"])
        selected = developer_tools.read_project_file(self.project, "large.py", start_line=10000, end_line=10002)
        self.assertEqual(selected, "line 9999\nline 10000\nline 10001\n")
        result = coding_agent._read_file(self.project, {"path": "large.py", "start_line": 10000, "end_line": 10002}, lambda event: None)
        self.assertIn(selected, result)
        for start, end in ((0, 1), (5, 2), (True, 3)):
            with self.assertRaises(ValueError):
                developer_tools.read_project_file(self.project, "large.py", start_line=start, end_line=end)
        with self.assertRaises(ValueError):
            developer_tools.read_project_file(self.project, "large.py", start_line=1, end_line=15000)
        (self.project / "bad.py").write_bytes(b"-----BEGIN PRIVATE KEY-----\n" + b"x\n" * 40000)
        with self.assertRaises(ValueError):
            developer_tools.read_project_file(self.project, "bad.py", start_line=2, end_line=4)


if __name__ == "__main__":
    unittest.main()
