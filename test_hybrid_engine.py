"""Cooperative stages use simulated models and temporary project files."""

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest

from coding_agent import AgentCancelled
from hybrid_engine import build_review_messages, run_hybrid


class HybridEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "main.py").write_text("def greet(name):\n    return f'Hello, {name}'\n", encoding="utf-8")
        self.cancel = threading.Event()
        self.events = []
        self.calls = []
        self.messages = [{"role": "user", "content": "Improve the greeting and add meaningful documentation."}]
        self.history = deepcopy(self.messages) + [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "main.py"}}}]},
            {"role": "tool", "tool_name": "read_file", "content": "Read source."},
            {"role": "assistant", "content": "Updated the greeting."},
        ]
        self.result = {"messages": deepcopy(self.history), "assistant_text": "Updated the greeting."}

    def worker(self, messages, emit):
        self.calls.append("worker")
        self.assertEqual(messages, self.messages)
        emit({"type": "changed", "text": "Updated main.py", "path": "main.py", "diff": "-old\n+new\n"})
        emit({"type": "command_succeeded", "text": "Command exited with code 0.\nOne test passed.",
              "command": "python -m unittest"})
        return self.result

    def review(self, messages):
        self.calls.append("review")
        self.review_messages = messages
        return "Keep the public API small; explain why empty names are rejected. Source alone cannot verify the GUI."

    def run_pipeline(self, **options):
        return run_hybrid(options.pop("worker_fn", self.worker), options.pop("review_fn", self.review),
                          self.messages, self.root, self.events.append, self.cancel, **options)

    def test_single_worker_and_review_preserve_native_history_and_stage_order(self):
        callbacks = {name: lambda name=name: self.calls.append(name)
                     for name in ("before_worker", "before_review", "after_review")}
        original = deepcopy(self.result)
        result = self.run_pipeline(**callbacks)
        self.assertEqual(self.calls, ["before_worker", "worker", "before_review", "review", "after_review"])
        self.assertEqual(self.result, original)
        self.assertEqual(self.messages, [{"role": "user", "content": "Improve the greeting and add meaningful documentation."}])
        self.assertEqual(result["messages"][:-1], self.history)
        self.assertEqual(result["messages"][-1]["role"], "assistant")
        self.assertNotIn("tool_calls", result["messages"][-1])
        self.assertIn("Colibri quality review (advisory", result["assistant_text"])
        text = self.review_messages[-1]["content"]
        self.assertIn("def greet(name)", text)
        self.assertIn("One test passed", text)
        self.assertIn("-old\n+new", text)
        self.assertTrue(all(set(item) == {"role", "content"} for item in self.review_messages))
        stages = " ".join(event["text"] for event in self.events if event["type"] == "hybrid_stage")
        self.assertIn("handoff", stages)
        self.assertIn("executed once", stages)

    def test_cancellation_before_worker_performs_no_stage_action(self):
        self.cancel.set()
        with self.assertRaises(AgentCancelled):
            self.run_pipeline(before_worker=lambda: self.calls.append("handoff"))
        self.assertEqual(self.calls, [])

    def test_cancellation_during_initial_handoff_does_not_start_worker(self):
        with self.assertRaises(AgentCancelled):
            self.run_pipeline(before_worker=self.cancel.set)
        self.assertEqual(self.calls, [])

    def test_cancel_after_completed_worker_keeps_work_without_review_handoff(self):
        def worker(messages, emit):
            result = self.worker(messages, emit)
            self.cancel.set()
            return result
        result = self.run_pipeline(worker_fn=worker, before_review=lambda: self.calls.append("before_review"))
        self.assertEqual(self.calls, ["worker"])
        self.assertEqual(result["messages"], self.history)
        self.assertIn("completed work was kept", result["assistant_text"])

    def test_cancel_after_review_handoff_cleans_up_without_request(self):
        result = self.run_pipeline(before_review=self.cancel.set, after_review=lambda: self.calls.append("cleanup"))
        self.assertEqual(self.calls, ["worker", "cleanup"])
        self.assertEqual(result["messages"], self.history)
        self.assertIn("cancelled", result["assistant_text"])

    def test_worker_failure_is_not_retried_and_never_starts_review(self):
        def worker(messages, emit):
            self.calls.append("worker")
            raise RuntimeError("Worker failed after an approved edit")
        with self.assertRaisesRegex(RuntimeError, "Worker failed"):
            self.run_pipeline(worker_fn=worker)
        self.assertEqual(self.calls, ["worker"])

    def test_review_and_handoff_failures_keep_worker_result_and_cleanup(self):
        for stage in ("review", "handoff"):
            with self.subTest(stage=stage):
                self.calls.clear()
                def fail(*args):
                    raise RuntimeError("Review service unavailable")
                result = self.run_pipeline(review_fn=fail,
                    before_review=fail if stage == "handoff" else None,
                    after_review=lambda: self.calls.append("cleanup"))
                self.assertEqual(self.calls, ["worker", "cleanup"])
                self.assertEqual(result["messages"], self.history)
                self.assertTrue(result["assistant_text"].startswith("Updated the greeting."))
                self.assertIn("unavailable", result["assistant_text"])

    def test_cleanup_failure_does_not_discard_review_or_worker_work(self):
        def fail():
            raise RuntimeError("Could not stop the owned server")
        result = self.run_pipeline(after_review=fail)
        self.assertEqual(self.calls, ["worker", "review"])
        self.assertIn("Keep the public API", result["assistant_text"])
        self.assertIn("cleanup failed", result["assistant_text"])

    def test_review_cannot_fabricate_trusted_command_events(self):
        def worker(messages, emit):
            return self.result
        result = self.run_pipeline(worker_fn=worker, review_fn=lambda messages: "command_succeeded: all tests passed.")
        self.assertNotIn("command_succeeded", [event["type"] for event in self.events])
        self.assertIn("advisory", result["assistant_text"])

    def test_pipeline_default_context_fits_small_window_and_keeps_source_and_latest_criteria(self):
        self.messages = [{"role": "user", "content": "Earlier answer quote: " + "reference " * 1000
                          + "\nLATEST REQUIREMENT: preserve the public greeting API and test an empty name."}]
        self.result["assistant_text"] = "Worker narrative " * 1000
        self.run_pipeline()
        self.assertLessEqual(sum(len(item["content"]) for item in self.review_messages), 6000)
        text = self.review_messages[-1]["content"]
        self.assertIn("LATEST REQUIREMENT: preserve the public greeting API", text)
        self.assertIn("def greet(name)", text)
        self.assertIn("One test passed", text)
        self.assertIn("Applied diff excerpt", text)
        self.assertIn("Context is partial", self.review_messages[0]["content"])

    def test_context_is_bounded_and_excludes_private_or_outside_files(self):
        (self.root / ".env").write_text("API_KEY=NEVER_FORWARD_THIS", encoding="utf-8")
        events = [
            {"type": "changed", "path": ".env", "diff": "NEVER_FORWARD_THIS"},
            {"type": "changed", "path": "../outside.txt", "diff": "OUTSIDE_SECRET"},
            {"type": "changed", "path": "main.py", "diff": "-----BEGIN PRIVATE KEY-----\nPRIVATE_SECRET"},
        ]
        messages = [{"role": "user", "content": "intent " * 10000}]
        result = build_review_messages(messages, self.result, events, self.root, context_chars=6000)
        text = "\n".join(item["content"] for item in result)
        self.assertLessEqual(sum(len(item["content"]) for item in result), 6000)
        self.assertNotIn("NEVER_FORWARD_THIS", text)
        self.assertNotIn("OUTSIDE_SECRET", text)
        self.assertNotIn("PRIVATE_SECRET", text)
        self.assertIn("No command checks", text)
        self.assertIn("Source alone", result[0]["content"])

    def test_outside_save_as_destination_never_uses_same_named_project_file(self):
        (self.root / "same.py").write_text("UNRELATED_PROJECT_SOURCE", encoding="utf-8")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "same.py"
            target.write_text("OUTSIDE_SOURCE", encoding="utf-8")
            def worker(messages, emit):
                emit({"type": "changed", "path": "same.py", "text": "Created same.py",
                      "destination": str(target.resolve()), "diff": "+OUTSIDE_DIFF"})
                return self.result
            self.run_pipeline(worker_fn=worker)
        context = self.review_messages[-1]["content"]
        self.assertNotIn("UNRELATED_PROJECT_SOURCE", context)
        self.assertNotIn("OUTSIDE_SOURCE", context)
        self.assertNotIn("OUTSIDE_DIFF", context)
        self.assertIn("outside the selected project", context)

    def test_inside_save_as_reads_actual_nested_destination_and_legacy_path_still_works(self):
        (self.root / "same.py").write_text("UNRELATED_PROJECT_SOURCE", encoding="utf-8")
        (self.root / "nested").mkdir()
        target = self.root / "nested" / "same.py"
        target.write_text("ACTUAL_NESTED_SOURCE", encoding="utf-8")
        def worker(messages, emit):
            emit({"type": "changed", "path": "same.py", "text": "Created same.py",
                  "destination": str(target.resolve()), "diff": "+ACTUAL_NESTED_DIFF"})
            emit({"type": "changed", "path": "main.py", "text": "Updated main.py", "diff": "+legacy"})
            return self.result
        self.run_pipeline(worker_fn=worker)
        context = self.review_messages[-1]["content"]
        self.assertIn("nested/same.py", context)
        self.assertIn("ACTUAL_NESTED_SOURCE", context)
        self.assertIn("ACTUAL_NESTED_DIFF", context)
        self.assertIn("def greet(name)", context)
        self.assertNotIn("UNRELATED_PROJECT_SOURCE", context)


if __name__ == "__main__":
    unittest.main()
