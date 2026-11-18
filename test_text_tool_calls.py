import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import coding_agent
from coding_agent import DEFAULT_MAX_STEPS, STEP_LIMIT_CHOICES, run_agent, text_tool_calls


XML_READ = (
    "Let me read it.<tool_call>\n<function=read_file>\n<parameter=path>\nmain.py\n</parameter>\n"
    "<parameter=start_line>\n1\n</parameter>\n</function>\n</tool_call>"
)


def reply(content):
    return {"message": {"role": "assistant", "content": content}}


class TextToolCallParsingTests(unittest.TestCase):
    def test_request_describes_the_command_shell_on_each_platform(self):
        for os_name, platform, expected in (("nt", "win32", "PowerShell 5.1"),
                                            ("posix", "darwin", "OS: darwin"),
                                            ("posix", "linux", "OS: linux")):
            with self.subTest(platform=platform), patch("coding_agent.os.name", os_name), \
                    patch("coding_agent.sys.platform", platform):
                messages, _ = coding_agent._request_messages([{"role": "user", "content": "Run tests"}], 4096)
                prompt = messages[0]["content"]
                self.assertIn(expected, prompt)
                self.assertIn("script files", prompt)
                if os_name == "nt":
                    self.assertIn("no &&", prompt)
                    self.assertIn("backslash quote", prompt)
                else:
                    self.assertIn("/bin/sh", prompt)

    def test_qwen_xml_call_is_recovered_with_schema_types(self):
        calls, remaining, malformed = text_tool_calls(XML_READ)
        self.assertFalse(malformed)
        self.assertEqual(remaining, "Let me read it.")
        self.assertEqual(calls, [{"function": {"name": "read_file",
                                               "arguments": {"path": "main.py", "start_line": 1}}}])

    def test_multiline_values_keep_inner_newlines_and_booleans_are_typed(self):
        text = ("<tool_call><function=edit_file><parameter=path>\na.py\n</parameter>"
                "<parameter=old_text>\nx = 1\ny = 2\n</parameter><parameter=new_text>\nx = 3\n</parameter>"
                "<parameter=replace_all>\ntrue\n</parameter></function></tool_call>")
        calls, _, malformed = text_tool_calls(text)
        self.assertFalse(malformed)
        self.assertEqual(calls[0]["function"]["arguments"],
                         {"path": "a.py", "old_text": "x = 1\ny = 2", "new_text": "x = 3", "replace_all": True})

    def test_json_call_is_recovered(self):
        calls, remaining, malformed = text_tool_calls(
            '<tool_call>{"name": "list_files", "arguments": {"limit": 5}}</tool_call>')
        self.assertFalse(malformed)
        self.assertEqual(remaining, "")
        self.assertEqual(calls, [{"function": {"name": "list_files", "arguments": {"limit": 5}}}])

    def test_cut_off_call_is_malformed(self):
        calls, remaining, malformed = text_tool_calls(
            "Fixing it:<tool_call>\n<function=edit_file>\n<parameter=path>\na.py\n</parameter>\n<parameter=old_text>\nx")
        self.assertTrue(malformed)
        self.assertEqual(calls, [])
        self.assertIn("Fixing it:", remaining)

    def test_leftover_closing_tags_are_malformed_not_a_final_answer(self):
        calls, _, malformed = text_tool_calls("</parameter>\n</function>\n</tool_call>")
        self.assertEqual(calls, [])
        self.assertTrue(malformed)

    def test_output_budget_fits_a_whole_file_at_32k(self):
        _, prediction = coding_agent._request_messages([{"role": "user", "content": "go"}], 32768)
        self.assertEqual(prediction, 8192)
        _, small = coding_agent._request_messages([{"role": "user", "content": "go"}], 4096)
        self.assertEqual(small, 1024)

    def test_agent_context_uses_choice_then_loaded_model_then_16k(self):
        from workspace_ui import agent_context_length
        loaded = lambda: {"qwen3-coder:30b": {"context_length": 32768}}
        self.assertEqual(agent_context_length(8192, "qwen3-coder:30b", loaded), 8192)
        self.assertEqual(agent_context_length(None, "qwen3-coder:30b", loaded), 32768)
        self.assertEqual(agent_context_length(None, "other", loaded), 16384)
        self.assertEqual(agent_context_length(None, "qwen3-coder:30b", None), 16384)
        def offline():
            raise RuntimeError("Ollama is not running")
        self.assertEqual(agent_context_length(None, "qwen3-coder:30b", offline), 16384)

    def test_plain_text_is_not_a_call(self):
        self.assertEqual(text_tool_calls("All tests pass."), ([], "All tests pass.", False))


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("print('hi')\n", encoding="utf-8")
        self.events = []

    def run_replies(self, replies):
        with patch("coding_agent._chat", side_effect=replies):
            return run_agent("http://localhost:11434", "qwen3-coder:30b", self.root,
                             [{"role": "user", "content": "Check main.py"}],
                             self.events.append, lambda action: True)

    def test_text_call_runs_the_tool_and_the_task_continues(self):
        result = self.run_replies([reply(XML_READ), reply("Done.")])
        self.assertEqual(result["assistant_text"], "Done.")
        tools = [message for message in result["messages"] if message["role"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertIn("print('hi')", tools[0]["content"])
        recovered = [message for message in result["messages"] if message.get("tool_calls")]
        self.assertEqual(recovered[0]["content"], "Let me read it.")

    def test_unreadable_call_asks_the_model_to_retry_instead_of_finishing(self):
        result = self.run_replies([reply("<tool_call>\n<function=edit_file>\n<parameter=path>"), reply("Done.")])
        self.assertEqual(result["assistant_text"], "Done.")
        retries = [message for message in result["messages"]
                   if message["role"] == "user" and "could not be read" in message["content"]]
        self.assertEqual(len(retries), 1)
        self.assertIn("Check main.py", retries[0]["content"])
        self.assertTrue(any("retry" in event.get("text", "") for event in self.events if isinstance(event, dict)))

    def test_repeated_test_failure_asks_for_a_trace_then_a_report(self):
        failing = ("Command exited with code 1.\n.F\n=====\nFAIL: test_format (test_m.T.test_format)\n"
                   "AssertionError: '1.' != '{}'\nFAILED (failures=1)")
        outputs = iter([failing.format("1k"), failing.format("1M"), failing.format("1k"), failing.format("1k")])
        command = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "run_command", "arguments": {"command": "python -m unittest"}}}]}}
        with patch("coding_agent._run_command", side_effect=lambda *args, **kwargs: next(outputs)):
            result = self.run_replies([command, command, command, command, reply("Stopped: [:-2] is wrong.")])
        nudges = [message["content"] for message in result["messages"] if message["role"] == "user"][1:]
        self.assertEqual(len(nudges), 2)
        self.assertIn("line by line", nudges[0])
        self.assertIn("FAIL: test_format", nudges[0])
        self.assertIn("Stop now and report", nudges[1])

    def test_failure_signature_ignores_success_and_other_tools(self):
        self.assertEqual(coding_agent._failure_signature("run_command", "Command exited with code 0.\nOK"), "")
        self.assertEqual(coding_agent._failure_signature("read_file", "Command exited with code 1."), "")
        self.assertEqual(coding_agent._failure_signature(
            "run_command", "Command exited with code 1.\nSyntaxError: invalid decimal literal"),
            "SyntaxError: invalid decimal literal")

    def test_default_step_limit_is_raised_and_offered(self):
        self.assertEqual(DEFAULT_MAX_STEPS, 20)
        self.assertIn(DEFAULT_MAX_STEPS, STEP_LIMIT_CHOICES)
        self.assertEqual(inspect.signature(run_agent).parameters["max_steps"].default, DEFAULT_MAX_STEPS)
        self.assertIn("could not be read", coding_agent._MALFORMED_CALL)


if __name__ == "__main__":
    unittest.main()
