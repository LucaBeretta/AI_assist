"""Reviewed agent actions; all network and command execution are mocked."""

import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

import coding_agent
from coding_agent import AgentCancelled, run_agent
from test_agent_support import isolate_agent_data


def tool_response(name, arguments, content=""):
    return {"message": {"role": "assistant", "content": content, "tool_calls": [
        {"function": {"name": name, "arguments": arguments}},
    ]}}


def final_response(text="Finished."):
    return {"message": {"role": "assistant", "content": text}}


class FakeProcess:
    pid = 12345

    def __init__(self, output=b"checked\n", running=False):
        self.stdout = io.BytesIO(output)
        self.returncode = None if running else 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.data_directory = isolate_agent_data(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.events = []
        self.approvals = []
        self.messages = [{"role": "user", "content": "Fix the project."}]

    def approve(self, action):
        self.approvals.append(action)
        return True

    def run_responses(self, responses, approval=None, **options):
        with patch("coding_agent._chat", side_effect=responses) as chat:
            result = run_agent("http://localhost:11434", "qwen3:8b", self.root,
                               self.messages, self.events.append,
                               approval or self.approve, **options)
        return result, chat

    def tool_results(self, result):
        return [message["content"] for message in result["messages"] if message["role"] == "tool"]

    def test_reads_and_lists_without_approval_and_preserves_input_messages(self):
        (self.root / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="")
        result, chat = self.run_responses([
            tool_response("list_files", {}),
            tool_response("read_file", {"path": "main.py", "start_line": 2, "end_line": 2}),
            final_response("Inspected."),
        ])
        self.assertEqual(result["assistant_text"], "Inspected.")
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(self.approvals, [])
        self.assertIn('"path": "main.py"', self.tool_results(result)[0])
        self.assertEqual(self.tool_results(result)[1], "File: main.py (lines 2-2 of 3)\ntwo\n")
        self.assertEqual(self.messages, [{"role": "user", "content": "Fix the project."}])
        self.assertEqual([event["type"] for event in self.events],
                         ["status", "tool_start", "tool_result", "tool_start", "tool_result", "assistant"])

    def test_approved_file_creation_has_diff_and_changed_event(self):
        result, _ = self.run_responses([
            tool_response("write_file", {"path": "src/main.py", "content": "print(1)\n"}), final_response(),
        ])
        self.assertEqual((self.root / "src/main.py").read_text(encoding="utf-8"), "print(1)\n")
        self.assertEqual(len(self.approvals), 1)
        self.assertEqual(self.approvals[0]["kind"], "write_file")
        self.assertEqual(self.approvals[0]["path"], "src/main.py")
        self.assertFalse(self.approvals[0]["exists"])
        self.assertIn("--- /dev/null", self.approvals[0]["diff"])
        self.assertIn("+print(1)", self.approvals[0]["diff"])
        self.assertIn("Successfully created", self.tool_results(result)[0])
        self.assertTrue(any(event["type"] == "changed" and event["path"] == "src/main.py" for event in self.events))

    def test_update_uses_exact_reviewed_content_and_readable_missing_newline_diff(self):
        target = self.root / "main.py"
        target.write_text("old", encoding="utf-8")
        result, _ = self.run_responses([
            tool_response("write_file", {"path": "main.py", "content": "new"}), final_response(),
        ])
        self.assertEqual(target.read_bytes(), b"new")
        self.assertTrue(self.approvals[0]["exists"])
        self.assertIn("-old\n\\ No newline", self.approvals[0]["diff"])
        self.assertIn("+new\n\\ No newline", self.approvals[0]["diff"])
        self.assertIn("Successfully updated", self.tool_results(result)[0])

    def test_user_picked_destination_gets_its_own_overwrite_review(self):
        selected = self.root.parent / "picked.py"
        selected.write_text("old\n", encoding="utf-8")
        reviews = []

        def approve(action):
            reviews.append(action)
            if not action.get("destination_selected"):
                return {"approved": True, "destination": str(selected)}
            self.assertTrue(action["exists"])
            self.assertIn("-old", action["diff"])
            self.assertEqual(selected.read_text(encoding="utf-8"), "old\n")
            return True

        result, _ = self.run_responses([tool_response("write_file", {"path": "new.py", "content": "new\n"}),
                                        final_response()], approval=approve)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(selected.read_text(encoding="utf-8"), "new\n")
        self.assertFalse((self.root / "new.py").exists())
        self.assertIn(str(selected), self.tool_results(result)[0])

    def test_denied_review_at_picked_destination_leaves_both_files_untouched(self):
        selected = self.root.parent / "picked.py"
        selected.write_text("keep", encoding="utf-8")

        def approve(action):
            if not action.get("destination_selected"):
                return {"approved": True, "destination": str(selected)}
            return False

        self.run_responses([tool_response("write_file", {"path": "new.py", "content": "replace"}),
                            final_response()], approval=approve)
        self.assertEqual(selected.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.root / "new.py").exists())

    def test_denial_and_approval_failure_never_write_or_execute(self):
        for approval in (lambda action: False, lambda action: 1,
                         lambda action: (_ for _ in ()).throw(RuntimeError("dialog closed"))):
            with self.subTest(approval=approval), patch("coding_agent.subprocess.Popen") as popen:
                result, _ = self.run_responses([
                    tool_response("write_file", {"path": "new.py", "content": "pass\n"}),
                    tool_response("run_command", {"command": "echo test"}), final_response(),
                ], approval=approval)
                self.assertFalse((self.root / "new.py").exists())
                popen.assert_not_called()
                self.assertTrue(all("No action was taken" in value or "no action was taken" in value
                                    for value in self.tool_results(result)))

    def test_file_changed_during_review_is_not_overwritten(self):
        target = self.root / "main.py"
        target.write_text("original\n", encoding="utf-8")

        def change_during_review(action):
            target.write_text("external change\n", encoding="utf-8")
            return True

        result, _ = self.run_responses([
            tool_response("write_file", {"path": "main.py", "content": "new\n"}), final_response(),
        ], approval=change_during_review)
        self.assertEqual(target.read_text(encoding="utf-8"), "external change\n")
        self.assertIn("changed after its diff", self.tool_results(result)[0])
        self.assertFalse(any(event["type"] == "changed" for event in self.events))

    def test_file_changed_while_atomic_write_is_prepared_is_not_overwritten(self):
        target = self.root / "main.py"
        target.write_text("original\n", encoding="utf-8")
        with patch("coding_agent.os.fsync", side_effect=lambda descriptor: target.write_text("external\n", encoding="utf-8")):
            result, _ = self.run_responses([
                tool_response("write_file", {"path": "main.py", "content": "new\n"}), final_response(),
            ])
        self.assertEqual(target.read_text(encoding="utf-8"), "external\n")
        self.assertIn("changed after its diff", self.tool_results(result)[0])
        self.assertEqual([path.name for path in self.root.iterdir()], ["main.py"])

    def test_traversals_secrets_and_binary_targets_fail_before_approval(self):
        outside = Path(self.temporary.name) / "outside.py"
        for path in ("../outside.py", "..\\outside.py", str(outside), "C:outside.py", ".env", ".env::$DATA", ".env.", "key.pem", "credentials.json", "node_modules/a.py", "image.png"):
            with self.subTest(path=path):
                result, _ = self.run_responses([
                    tool_response("write_file", {"path": path, "content": "new\n"}), final_response(),
                ])
                self.assertIn("Tool failed:", self.tool_results(result)[0])
        self.assertEqual(self.approvals, [])
        self.assertFalse(outside.exists())

    def test_resolved_symlink_escape_fails_without_needing_windows_link_privilege(self):
        outside = Path(self.temporary.name) / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        fake_link = self.root.resolve() / "linked.py"
        fake_link.write_text("placeholder\n", encoding="utf-8")
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return original_resolve(outside) if path == fake_link else original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            result, _ = self.run_responses([
                tool_response("write_file", {"path": "linked.py", "content": "new\n"}), final_response(),
            ])
        self.assertEqual(self.approvals, [])
        self.assertIn("inside the selected project", self.tool_results(result)[0])
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_rejects_secret_contents_and_oversized_files_before_approval(self):
        for content in ("-----BEGIN RSA PRIVATE KEY-----\nprivate", "x" * 64001, "binary\x00data"):
            with self.subTest(content_length=len(content)):
                result, _ = self.run_responses([
                    tool_response("write_file", {"path": "ordinary.txt", "content": content}), final_response(),
                ])
                self.assertIn("Tool failed:", self.tool_results(result)[0])
        self.assertEqual(self.approvals, [])
        self.assertFalse((self.root / "ordinary.txt").exists())

    def test_command_approval_describes_exact_command_cwd_and_timeout(self):
        with patch("coding_agent.subprocess.Popen", return_value=FakeProcess()) as popen:
            result, _ = self.run_responses([
                tool_response("run_command", {"command": "echo checked", "timeout_seconds": 20, "description": "Check output"}),
                final_response(),
            ])
        action = self.approvals[0]
        self.assertEqual(action, {"kind": "run_command", "command": "echo checked", "timeout_seconds": 20,
                                  "description": "Check output", "cwd": str(self.root.resolve())})
        invocation = popen.call_args.args[0]
        self.assertEqual(invocation[-1], action["command"])
        self.assertEqual(popen.call_args.kwargs["cwd"], action["cwd"])
        self.assertEqual(popen.call_args.kwargs["stdin"], coding_agent.subprocess.DEVNULL)
        if coding_agent.os.name == "nt":
            self.assertEqual(invocation[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
            self.assertIn("creationflags", popen.call_args.kwargs)
        self.assertIn("Command exited with code 0.\nchecked", self.tool_results(result)[0])
        outcome = next(event for event in self.events if event["type"] == "command_succeeded")
        self.assertEqual(outcome["command"], action["command"])
        self.assertEqual(outcome["text"], self.tool_results(result)[0])

    def test_failed_command_emits_observed_failure_without_a_success_claim(self):
        process = FakeProcess(output=b"check failed\n")
        process.returncode = 7
        with patch("coding_agent.subprocess.Popen", return_value=process):
            result, _ = self.run_responses([
                tool_response("run_command", {"command": "run-check"}), final_response(),
            ])
        outcomes = [event for event in self.events if event["type"].startswith("command_")]
        self.assertEqual(outcomes, [{"type": "command_failed", "command": "run-check",
                                    "text": "Command exited with code 7.\ncheck failed\n"}])
        self.assertEqual(outcomes[0]["text"], self.tool_results(result)[0])

    def test_denied_command_emits_no_command_outcome(self):
        with patch("coding_agent.subprocess.Popen") as popen:
            self.run_responses([tool_response("run_command", {"command": "echo denied"}), final_response()],
                               approval=lambda action: False)
        popen.assert_not_called()
        self.assertFalse(any(event["type"].startswith("command_") for event in self.events))

    def test_command_timeout_stops_own_process_tree(self):
        process = FakeProcess(running=True)
        with patch("coding_agent.subprocess.Popen", return_value=process), patch(
                "coding_agent._terminate_process_tree", side_effect=lambda child: child.kill()) as terminate, patch(
                "coding_agent.time.monotonic", side_effect=[0, 2]):
            result, _ = self.run_responses([
                tool_response("run_command", {"command": "long-command", "timeout_seconds": 1}), final_response(),
            ])
        terminate.assert_called_once_with(process)
        self.assertIn("timed out after 1 seconds", self.tool_results(result)[0])
        outcome = next(event for event in self.events if event["type"] == "command_timed_out")
        self.assertEqual(outcome["command"], "long-command")
        self.assertEqual(outcome["text"], self.tool_results(result)[0])

    def test_command_cancellation_stops_own_process_tree(self):
        cancel = threading.Event()
        process = FakeProcess(running=True)

        def start(*args, **kwargs):
            cancel.set()
            return process

        with patch("coding_agent.subprocess.Popen", side_effect=start), patch(
                "coding_agent._terminate_process_tree", side_effect=lambda child: child.kill()) as terminate:
            with self.assertRaises(AgentCancelled):
                self.run_responses([tool_response("run_command", {"command": "long-command"})], cancel_event=cancel)
        terminate.assert_called_once_with(process)
        self.assertFalse(any(event["type"].startswith("command_") for event in self.events))

    def test_cancel_during_approval_performs_no_action(self):
        cancel = threading.Event()

        def approve_then_cancel(action):
            cancel.set()
            return True

        with patch("coding_agent.subprocess.Popen") as popen:
            with self.assertRaises(AgentCancelled):
                self.run_responses([tool_response("run_command", {"command": "echo denied"})],
                                   approval=approve_then_cancel, cancel_event=cancel)
        popen.assert_not_called()

    def test_output_is_bounded_for_ui_and_model_and_read_ranges_validate(self):
        (self.root / "large.txt").write_text("x" * 60000, encoding="utf-8")
        with patch("coding_agent.subprocess.Popen", return_value=FakeProcess(b"z" * 40000)):
            result, _ = self.run_responses([
                tool_response("read_file", {"path": "large.txt"}),
                tool_response("run_command", {"command": "echo long"}),
                tool_response("read_file", {"path": "large.txt", "start_line": 2, "end_line": 1}), final_response(),
            ])
        self.assertLessEqual(len(self.tool_results(result)[0]), 8192)
        self.assertIn("narrower", self.tool_results(result)[0])
        self.assertLessEqual(len(self.tool_results(result)[1]), 8192)
        command_log = next(event["text"] for event in self.events if event["type"] == "tool_result" and event["tool"] == "run_command")
        self.assertLess(len(command_log), 20200)
        self.assertIn("20,000", command_log)
        self.assertIn("end_line", self.tool_results(result)[2])

    def test_string_arguments_unknown_tools_and_max_steps_are_handled(self):
        result, chat = self.run_responses([
            tool_response("list_files", '{"limit": 2}'), tool_response("unknown", {}),
        ], max_steps=2)
        self.assertEqual(chat.call_count, 2)
        self.assertIn("Unknown tool", self.tool_results(result)[1])
        self.assertIn("stopped after 2 steps", result["assistant_text"])
        self.assertEqual(self.approvals, [])

    def test_malformed_arguments_cannot_run_and_echoed_native_messages_remain_valid(self):
        result, _ = self.run_responses([
            tool_response("write_file", "not json"), final_response(),
        ])
        self.assertEqual(self.approvals, [])
        self.assertIn("Tool failed:", self.tool_results(result)[0])
        assistant = next(message for message in result["messages"] if message.get("tool_calls"))
        self.assertEqual(assistant["tool_calls"][0]["function"]["arguments"], {})

    def test_request_history_remains_bounded_and_retains_latest_instruction(self):
        history = [{"role": "system", "content": "x" * 8000}, {"role": "user", "content": "Fix the latest bug."}]
        for number in range(8):
            history.extend([{"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
                {"role": "tool", "tool_name": "read_file", "content": "a" * 8192}])
        messages, prediction = coding_agent._request_messages(history, 4096)
        self.assertLess(len(json.dumps(messages)), 13000)
        self.assertTrue(any(message.get("content") == "Fix the latest bug." for message in messages))
        self.assertLessEqual(prediction, 1024)

    def test_long_followup_keeps_original_request_and_recent_dialogue(self):
        history = [
            {"role": "user", "content": "Use CSV files and write everything into the selected folder."},
            {"role": "assistant", "content": "We agreed to use semicolon separators."},
            {"role": "tool", "tool_name": "read_file", "content": "x" * 20000},
            {"role": "user", "content": "Now add the export button."},
        ]
        messages, _ = coding_agent._request_messages(history, 4096)
        text = "\n".join(message.get("content", "") for message in messages)
        self.assertIn("Use CSV files", text)
        self.assertIn("semicolon separators", text)
        self.assertIn("Now add the export button.", text)
        self.assertNotIn("x" * 100, text)
        self.assertLess(len(json.dumps(messages)), 13000)

    def test_conversation_excerpts_are_bounded_and_do_not_echo_tool_calls(self):
        history = [{"role": "user", "content": "Remember CSV. " + "x" * 20000}]
        history += [{"role": "assistant", "content": "Earlier plan " * 1000,
                     "tool_calls": [{"function": {"name": "write_file", "arguments": {}}}]}] * 10
        memory = coding_agent._conversation_memory(history, 1000)
        self.assertLessEqual(len(memory[0]["content"]), 1000)
        self.assertIn("Remember CSV.", memory[0]["content"])
        self.assertNotIn("tool_calls", memory[0])

    def test_native_chat_protocol_uses_tools_options_and_bounded_http_response(self):
        with patch("coding_agent.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(final_response("Done")).encode())) as opened:
            result = run_agent("http://localhost:11434/", "model", self.root, self.messages,
                               self.events.append, self.approve)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/chat")
        payload = json.loads(request.data)
        self.assertFalse(payload["stream"])
        self.assertEqual({item["function"]["name"] for item in payload["tools"]},
                         {"list_files", "read_file", "edit_file", "write_file", "run_command"})
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(result["assistant_text"], "Done")

    def test_unsupported_models_and_malformed_responses_are_readable(self):
        error = urllib.error.HTTPError("http://localhost", 400, "Bad request", {},
                                       io.BytesIO(b'{"error":"model does not support tools"}'))
        with patch("coding_agent.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "native tool calling"):
                run_agent("http://localhost:11434", "model", self.root, self.messages,
                          self.events.append, self.approve)
        for value in ({}, {"message": {"content": []}}, {"message": {"content": ""}}):
            with self.subTest(value=value), patch("coding_agent._chat", return_value=value):
                with self.assertRaises(RuntimeError):
                    run_agent("http://localhost:11434", "model", self.root, self.messages,
                              self.events.append, self.approve)


if __name__ == "__main__":
    unittest.main()
