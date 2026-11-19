"""Stop for streamed Ollama answers, and the agent's file size limit."""

from contextlib import ExitStack
import gc
import json
import os
import queue
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import tk_app
import ollama_engine
from coding_agent import FILE_LIMIT_CHOICES, run_agent
from test_agent_support import isolate_agent_data
from test_colibri_launcher import join_workers
from workspace_ui import WorkspaceMixin, file_limit_label, file_limit_value, file_limit_warning, load_file_limit


def call(name, **arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}}


def done(text="Done."):
    return {"message": {"role": "assistant", "content": text}}


class FileLimitAgentTests(unittest.TestCase):
    def test_changing_the_limit_refreshes_already_pinned_context(self):
        app = SimpleNamespace(agent_file_limit=64000, agent_settings=None, project_path=str(self.root),
                              project_selected_paths=["index.html"], model_contexts={"model": 131072},
                              model_name=SimpleNamespace(get=lambda: "model"),
                              file_limit_choice=SimpleNamespace(get=lambda: "128 KB"),
                              scan_project=Mock(), set_status=Mock())
        with patch("model_advisor._fallback_ram", return_value=(32 * 1024**3, 16 * 1024**3)):
            WorkspaceMixin.choose_file_limit(app)
        self.assertEqual(app.agent_file_limit, 128000)
        app.scan_project.assert_called_once_with()

    def test_project_worker_passes_the_selected_limit_to_pinned_context(self):
        app = SimpleNamespace(agent_file_limit=128000, workspace_events=queue.Queue())
        with patch("workspace_ui.build_project_context", return_value={"text": "", "included": [], "skipped": []}) as build:
            WorkspaceMixin.project_worker(app, 7, self.root, ["index.html"])
        build.assert_called_once_with(self.root, ["index.html"], max_total_chars=8000, max_file_bytes=128000)
        self.assertIsNone(app.workspace_events.get_nowait()[-1])

    def setUp(self):
        isolate_agent_data(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.big = self.root / "index.html"
        self.big.write_text("".join(f"<div class='row'>{index}</div>\n" for index in range(4000)), encoding="utf-8")
        self.events = []

    def run_tool(self, *responses, **options):
        with patch("coding_agent._chat", side_effect=list(responses) + [done()]):
            result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "go"}],
                               self.events.append, lambda action: True, **options)
        return [message["content"] for message in result["messages"] if message["role"] == "tool"]

    def test_default_limit_still_blocks_and_points_to_the_setting(self):
        self.assertGreater(self.big.stat().st_size, 64000)
        results = self.run_tool(call("read_file", path="index.html"),
                                call("edit_file", path="index.html", old_text="<div class='row'>7</div>", new_text="x"))
        self.assertIn("64,000-byte limit", results[0])
        self.assertIn("Developer > Files", results[1])

    def test_larger_limit_reads_in_parts_and_edits_blocks(self):
        results = self.run_tool(
            call("read_file", path="index.html"),
            call("read_file", path="index.html", start_line=3990, end_line=4000),
            call("edit_file", path="index.html", old_text="<div class='row'>3995</div>\n", new_text="<div class='row'>ok</div>\n"),
            file_limit=128000, context_length=4096)
        self.assertIn("Part of a large file", results[0])
        self.assertIn("the model's context is 4,096", results[0])
        self.assertLess(len(results[0]), 6000)
        self.assertIn("lines 3990-4000 of 4000", results[1])
        self.assertIn("Successfully updated index.html", results[2])
        self.assertIn("<div class='row'>ok</div>", self.big.read_text(encoding="utf-8"))
        self.assertTrue(any(event.get("type") == "status" and "reads it in parts" in event.get("text", "")
                            for event in self.events))

    def test_small_files_are_read_whole_and_limits_are_validated(self):
        (self.root / "small.css").write_text("body { color: red; }\n", encoding="utf-8")
        results = self.run_tool(call("read_file", path="small.css"), file_limit=1000000, context_length=32768)
        self.assertNotIn("Part of a large file", results[0])
        for bad in (0, True, "64KB", 10**9):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "x"}],
                          lambda event: None, lambda action: True, file_limit=bad)

    def test_limit_labels_settings_and_warnings(self):
        self.assertEqual([file_limit_label(value) for value in FILE_LIMIT_CHOICES],
                         ["64 KB", "128 KB", "256 KB", "512 KB", "1 MB"])
        self.assertEqual(file_limit_value("512 KB"), 512000)
        self.assertEqual(file_limit_value("nonsense"), 64000)
        settings = self.root / "agent.settings"
        self.assertEqual(load_file_limit(settings), 64000)
        settings.write_text(json.dumps({"file_limit": 256000}))
        self.assertEqual(load_file_limit(settings), 256000)
        settings.write_text(json.dumps({"file_limit": 12345}))
        self.assertEqual(load_file_limit(settings), 64000)
        self.assertEqual(file_limit_warning(64000, 131072), "")
        warning = file_limit_warning(512000, 4096, ram_free=8 * 1024 ** 3)
        self.assertIn("about 128,000 tokens", warning)
        self.assertIn("the current context is 4,096", warning)
        self.assertIn("read and edited in parts", warning)
        self.assertIn("this computer has about 8.0 GB free", warning)
        tight = file_limit_warning(1000000, 4096, ram_free=2 * 1024 ** 3)
        self.assertIn("Keep the context smaller", tight)


class StopAndLimitUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        self.threads_before = set(threading.enumerate())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.history = Path(self.temporary.name) / "history"
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(self.history), "OLLAMA_MODEL": "alpha:8b"}))
        self.mocks.enter_context(patch("update_ui.UpdateChecker.check", lambda self: None))
        self.mocks.enter_context(patch("ollama_engine.ensure_ollama"))
        self.mocks.enter_context(patch("ollama_engine.list_ollama_models", return_value=["alpha:8b"]))
        self.mocks.enter_context(patch("ollama_engine.running_models", return_value={"alpha:8b"}))
        self.mocks.enter_context(patch("ollama_engine.set_model_running"))
        self.mocks.enter_context(patch("capabilities_view.fetch_model_capabilities",
                                       return_value={"name": "alpha:8b", "capabilities": ["completion"]}))
        self.release = threading.Event()
        self.disconnected = threading.Event()

        def slow_answer(messages, model, on_chunk=None, on_metrics=None, **options):
            try:
                for word in ("Prima ", "parte ", "della ", "risposta"):
                    self.release.wait(5)
                    self.release.clear()
                    on_chunk(word)
            except ollama_engine.StreamStopped:
                self.disconnected.set()  # the stream callback stopped the read: the connection closes
                raise
            return "Prima parte della risposta"

        self.mocks.enter_context(patch("ollama_engine.request_ollama", side_effect=slow_answer))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = tk_app.AIAssistantGUI(self.root)
        self.addCleanup(self.close)
        self.wait_for(lambda: not self.app.startup_pending and not self.app.busy(), "startup")

    def close(self):
        self.release.set()
        for scheduled in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", scheduled)
        if not self.app.closed:
            self.app.close()
            deadline = time.monotonic() + 5
            while not self.app.closed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.app.saver.thread.join(timeout=2)
        join_workers(self.threads_before)
        self.assertEqual([str(error[1]) for error in self.errors], [])

    def wait_for(self, predicate, description, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.02)
        self.fail(f"Timed out waiting for {description}; status={self.app.status_label.cget('text')!r}")

    def send(self, text):
        self.app.input_box.insert("1.0", text)
        self.app.send_button.invoke()

    def test_stop_keeps_the_partial_answer_and_closes_the_stream(self):
        self.send("Ciao")
        self.assertEqual(self.app.send_button.cget("text"), "Stop  ■")
        self.release.set()
        time.sleep(.1)
        self.release.set()
        self.wait_for(lambda: self.app.stream_card is not None, "streamed words")
        self.app.send_button.invoke()  # Stop
        self.wait_for(lambda: not self.app.is_waiting, "stopped")
        self.assertEqual(self.app.conversation[-1]["role"], "assistant")
        self.assertTrue(self.app.conversation[-1]["content"].startswith("Prima "))
        self.assertIn("Stopped. The partial answer was kept.", self.app.status_label.cget("text"))
        self.release.set()
        self.assertTrue(self.disconnected.wait(3))
        self.assertEqual(self.app.send_button.cget("text"), "Send  ↑")

    def test_stop_before_any_text_and_enter_does_not_stop(self):
        self.send("Ciao")
        self.app.handle_enter(type("Event", (), {"state": 0})())
        self.assertFalse(self.app.chat_cancel.is_set())
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "stopped early")
        self.assertEqual(self.app.conversation[-1]["role"], "user")
        self.assertIn("Stopped before the model answered", self.app.status_label.cget("text"))

    def test_file_limit_picker_warns_and_remembers(self):
        self.app.nav_buttons["dev"].invoke()
        self.app.file_limit_choice.set("512 KB")
        with patch("workspace_ui.messagebox.askokcancel", return_value=False) as ask:
            self.app.choose_file_limit()
        self.assertIn("about 128,000 tokens", ask.call_args.args[1])
        self.assertEqual(self.app.agent_file_limit, 64000)
        self.assertEqual(self.app.file_limit_choice.get(), "64 KB")
        self.app.file_limit_choice.set("512 KB")
        with patch("workspace_ui.messagebox.askokcancel", return_value=True):
            self.app.choose_file_limit()
        self.assertEqual(self.app.agent_file_limit, 512000)
        self.assertEqual(json.loads((self.history / "agent.settings").read_text())["file_limit"], 512000)
        self.assertIn("up to 512 KB", self.app.status_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
