"""Scoped memory handoff and bounded review source without native widgets."""
import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

from coding_agent import AgentCancelled
from hybrid_memory import memory_callbacks, unload_worker
from hybrid_engine import run_hybrid


class MemoryHandoffTests(unittest.TestCase):
    def test_quality_review_uses_the_app_file_limit_for_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "large.py").write_text("# Review this source\n" + "x" * 70000, encoding="utf-8")
            messages = [{"role": "user", "content": "Review the changed file"}]

            def worker(history, emit):
                emit({"type": "changed", "path": "large.py", "text": "Updated large.py"})
                return {"messages": history, "assistant_text": "Updated large.py"}

            review = Mock(return_value="Reviewed")
            run_hybrid(worker, review, messages, folder, lambda event: None,
                       threading.Event(), max_file_bytes=128000)
            self.assertIn("# Review this source", review.call_args.args[0][-1]["content"])

    def snapshot(self, url="http://127.0.0.1:8000/v1"):
        return dict(url=url, directory="known-colibri", manage_memory=True, follow_report=True)

    def test_only_selected_local_worker_and_managed_colibri_are_released(self):
        order = []
        state, before, prepare, cleanup = memory_callbacks(self.snapshot(), "http://localhost:11434", "selected-worker", threading.Event())
        report = {"server": {"urls": {"openai_base_url": "http://localhost:8123/v1"}}}
        with patch("hybrid_memory.launcher.stop_server", side_effect=lambda path: order.append(("stop", path))), \
                patch("hybrid_memory.unload_worker", side_effect=lambda host, model: order.append(("unload", host, model))), \
                patch("hybrid_memory.launcher.start_server", side_effect=lambda *args, **kwargs: (order.append(("start", args[0])), report)[1]):
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
            with patch("hybrid_memory.launcher.stop_server") as stop, patch("hybrid_memory.unload_worker") as unload, \
                    patch("hybrid_memory.launcher.start_server", return_value={}) as start:
                before()
                prepare()
                cleanup()
            stop.assert_not_called()
            unload.assert_not_called()
            self.assertEqual(start.call_count, int(snapshot["url"].startswith("http://127.0.0.1")))

    def test_cancelled_handoff_does_not_start_the_reviewer(self):
        cancel = threading.Event()
        _, _, prepare, cleanup = memory_callbacks(self.snapshot(), "http://localhost:11434", "worker", cancel)
        with patch("hybrid_memory.unload_worker", side_effect=lambda *args: cancel.set()), \
                patch("hybrid_memory.launcher.start_server") as start, patch("hybrid_memory.launcher.stop_server") as stop:
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
        with patch("hybrid_memory.urllib.request.urlopen", return_value=Response()) as request:
            unload_worker("http://localhost:11434", "one-model")
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload, {"model": "one-model", "stream": False, "keep_alive": 0})
