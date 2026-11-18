"""Cooperative settings, scoped memory handoff, and reviewed UI execution."""

import json
from pathlib import Path
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from coding_agent import AgentCancelled
from hybrid_ui import HybridMixin, memory_callbacks, unload_worker
import test_workspace as workspace_tests


class MemoryHandoffTests(unittest.TestCase):
    def test_quality_review_uses_the_app_file_limit_for_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "large.py").write_text("# Review this source\n" + "x" * 70000, encoding="utf-8")
            messages = [{"role": "user", "content": "Review the changed file"}]

            def worker(history, emit):
                emit({"type": "changed", "path": "large.py", "text": "Updated large.py"})
                return {"messages": history, "assistant_text": "Updated large.py"}

            with patch("hybrid_ui.memory_callbacks", return_value=(
                    {"url": "http://reviewer.example/v1"}, Mock(), Mock(), Mock())), \
                    patch("hybrid_ui.ColibriClient.review", return_value="Reviewed") as review:
                HybridMixin.run_with_quality_review(
                    SimpleNamespace(agent_file_limit=128000), worker, messages, folder, lambda event: None,
                    threading.Event(), "http://localhost:11434", "worker", {"key": "", "review_model": "reviewer"})
            self.assertIn("# Review this source", review.call_args.args[0][-1]["content"])

    def snapshot(self, url="http://127.0.0.1:8000/v1"):
        return dict(url=url, directory="known-colibri", manage_memory=True, follow_report=True)

    def test_only_selected_local_worker_and_managed_colibri_are_released(self):
        order = []
        state, before, prepare, cleanup = memory_callbacks(self.snapshot(), "http://localhost:11434", "selected-worker", threading.Event())
        report = {"server": {"urls": {"openai_base_url": "http://localhost:8123/v1"}}}
        with patch("hybrid_ui.launcher.stop_server", side_effect=lambda path: order.append(("stop", path))), \
                patch("hybrid_ui.unload_worker", side_effect=lambda host, model: order.append(("unload", host, model))), \
                patch("hybrid_ui.launcher.start_server", side_effect=lambda *args, **kwargs: (order.append(("start", args[0])), report)[1]):
            before()
            prepare()
            cleanup()
        self.assertEqual(order, [("stop", "known-colibri"), ("unload", "http://localhost:11434", "selected-worker"),
                                 ("unload", "http://localhost:11434", "selected-worker"),
                                 ("start", "known-colibri"), ("stop", "known-colibri")])
        self.assertEqual(state["url"], "http://localhost:8123/v1")

    def test_remote_servers_are_never_stopped_or_unloaded(self):
        for snapshot, host in ((self.snapshot("https://reviewer.example/v1"), "http://localhost:11434"),
                               (self.snapshot(), "https://worker.example")):
            _, before, prepare, cleanup = memory_callbacks(snapshot, host, "worker", threading.Event())
            with patch("hybrid_ui.launcher.stop_server") as stop, patch("hybrid_ui.unload_worker") as unload, \
                    patch("hybrid_ui.launcher.start_server", return_value={}) as start:
                before()
                prepare()
                cleanup()
            stop.assert_not_called()
            unload.assert_not_called()
            self.assertEqual(start.call_count, int(snapshot["url"].startswith("http://127.0.0.1")))

    def test_cancelled_handoff_does_not_start_the_reviewer(self):
        cancel = threading.Event()
        _, _, prepare, cleanup = memory_callbacks(self.snapshot(), "http://localhost:11434", "worker", cancel)
        with patch("hybrid_ui.unload_worker", side_effect=lambda *args: cancel.set()), \
                patch("hybrid_ui.launcher.start_server") as start, patch("hybrid_ui.launcher.stop_server") as stop:
            with self.assertRaises(AgentCancelled):
                prepare()
            cleanup()
        start.assert_not_called()
        stop.assert_not_called()

    def test_unload_request_targets_one_model_with_keep_alive_zero(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, maximum): return b'{"done": true}'
        with patch("hybrid_ui.urllib.request.urlopen", return_value=Response()) as request:
            unload_worker("http://localhost:11434", "one-model")
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload, {"model": "one-model", "stream": False, "keep_alive": 0})


class HybridWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.case = workspace_tests.WorkspaceIntegrationTests("test_settings_contains_update_check_and_replaces_sidebar_link")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.app = self.case.app

    def test_settings_preserve_draft_and_never_persist_the_api_key(self):
        self.case.set_input("Keep this draft")
        self.app.colibri_key = "session-key-never-save"
        self.app.open_hybrid()
        window = self.app.hybrid_window
        window.enabled.set(True)
        window.memory.set(False)
        window.reviewer.set("reviewer")
        window.save_button.invoke()
        saved = self.app.hybrid_settings.read_text(encoding="utf-8")
        self.assertEqual(json.loads(saved), dict(enabled=True, review_model="reviewer", manage_memory=False))
        self.assertNotIn("session-key", saved)
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "Keep this draft")

    def test_missing_reviewer_does_not_consume_request_or_edit(self):
        self.case.enter_project()
        self.app.hybrid_config = dict(enabled=True, review_model="", manage_memory=False)
        self.case.set_input("Improve main.py")
        with patch("workspace_ui.run_agent") as worker:
            self.app.send_button.invoke()
        worker.assert_not_called()
        self.assertFalse(self.app.is_waiting)
        self.assertEqual(self.app.conversation, [])
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "Improve main.py")

    def test_reviewer_discovery_finishes_without_a_chat_request(self):
        self.app.open_hybrid()
        window = self.app.hybrid_window
        window.memory.set(False)
        prepare, cleanup = Mock(), Mock()
        with patch("hybrid_ui.memory_callbacks", return_value=({"url": "http://reviewer.example/v1"}, Mock(), prepare, cleanup)), \
                patch("hybrid_ui.ColibriClient.models", return_value=["reviewer"]):
            window.discover.invoke()
            self.case.wait_for(lambda: not self.app.hybrid_preparing, "reviewer discovery")
        self.assertEqual(window.reviewer.get(), "reviewer")
        prepare.assert_called_once_with()
        cleanup.assert_called_once_with()
        self.assertEqual(self.case.request.call_count, 0)
        self.assertIsNone(self.app.current_chat)

    def test_one_approved_worker_then_one_read_only_review(self):
        self.case.enter_project()
        self.app.colibri_url = "https://reviewer.example/v1"
        self.app.hybrid_config = dict(enabled=True, review_model="reviewer", manage_memory=True)
        snapshots = []
        self.case.schedule_review(True, snapshots)
        replacement = "# Explain the non-obvious choice.\nprint('professional')\n"

        def worker(host, model, root, messages, emit, approve, **kwargs):
            action = dict(kind="write_file", path="main.py", exists=True, content=replacement,
                          diff="--- main.py\n+++ main.py\n+" + replacement)
            self.assertTrue(approve(action))
            Path(root, "main.py").write_text(replacement, encoding="utf-8")
            emit(dict(type="changed", text="Updated main.py", path="main.py", diff=action["diff"]))
            return dict(assistant_text="Updated main.py.", messages=messages + [
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "main.py", "content": replacement}}}]},
                {"role": "tool", "tool_name": "write_file", "content": "Successfully updated main.py."},
                {"role": "assistant", "content": "Updated main.py."}])

        def review(messages, model, cancel, **kwargs):
            self.assertEqual(model, "reviewer")
            self.assertEqual([item["role"] for item in messages], ["system", "user"])
            self.assertIn("professional", messages[1]["content"])
            self.assertNotIn("tool_calls", messages[1])
            return "Consider a focused test. Visual appearance still needs a manual check."

        with patch("workspace_ui.run_agent", side_effect=worker) as run, \
                patch("hybrid_ui.ColibriClient.review", side_effect=review) as advise:
            self.case.set_input("Make this professional with useful comments")
            self.app.send_button.invoke()
            self.case.wait_for(lambda: not self.app.is_waiting, "cooperative result")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(advise.call_count, 1)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual((self.case.project / "main.py").read_text(encoding="utf-8"), replacement)
        self.assertIn("Colibri quality review", self.app.conversation[-1]["content"])
        self.assertTrue(any(item["role"] == "tool" for item in self.app.agent_history))
        self.assertIn("advisory", self.app.agent_history[-1]["content"])
        self.assertIn("No command checks", self.app.verification_label.cget("text"))
        saved = self.case.load_saved(self.app.current_chat["id"])
        self.assertTrue(any(item["type"] == "hybrid_review" for item in saved["activity"]))
        refine = next(widget for widget in workspace_tests.descendants(self.app.message_frame)
                      if widget.winfo_class() == "Button" and widget.cget("text") == "Use review feedback")
        with patch("workspace_ui.run_agent") as rerun:
            refine.invoke()
            rerun.assert_not_called()
        draft = self.app.input_box.get("1.0", "end-1c")
        self.assertIn("Consider a focused test", draft)
        self.assertIn("Inspect the current project", draft)


if __name__ == "__main__":
    unittest.main()
