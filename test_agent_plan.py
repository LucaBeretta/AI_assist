"""Read-only planning and approval are enforced outside the model's prompt."""

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import coding_agent
from test_agent_support import isolate_agent_data
from test_coding_agent import tool_response, final_response
import test_webui_app as fixtures


class PlanTests(unittest.TestCase):
    def setUp(self):
        isolate_agent_data(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_forbidden_native_and_text_calls_are_blocked_until_edited_plan_is_approved(self):
        responses = iter([
            tool_response("write_file", {"path": "bad.py", "content": "bad"}),
            final_response('<tool_call>{"name":"run_command","arguments":{"command":"bad"}}</tool_call>'),
            tool_response("list_files", {}),
            final_response("1. Inspect.\n2. Write good.py."),
            tool_response("write_file", {"path": "good.py", "content": "good"}),
            final_response("Done"),
        ])
        captured, events = [], []
        approved = False
        def approve(plan):
            nonlocal approved
            self.assertFalse((self.root / "bad.py").exists())
            self.assertFalse((self.root / "good.py").exists())
            approved = True
            return "1. Write only good.py with good."
        def chat(*args):
            captured.append(deepcopy(args[2]))
            return next(responses)
        def action(value):
            self.assertTrue(approved)
            return True
        with patch("coding_agent._run_command", side_effect=AssertionError("command before approval")):
            result = coding_agent.run_agent("http://localhost:11434", "m", self.root,
                [{"role": "user", "content": "task"}], events.append, action,
                plan_first=True, approve_plan=approve, chat_fn=chat)
        self.assertEqual((self.root / "good.py").read_text(), "good")
        self.assertFalse((self.root / "bad.py").exists())
        self.assertIn("Approved plan:\n1. Write only good.py with good.",
                      "\n".join(item.get("content", "") for item in captured[-1]))
        self.assertTrue(any(event["type"] == "plan" for event in events))
        self.assertEqual(result["assistant_text"], "Done")

    def test_cancel_stops_before_any_action(self):
        with self.assertRaises(coding_agent.AgentCancelled), patch("coding_agent._execute_tool") as execute:
            coding_agent.run_agent("http://localhost:11434", "m", self.root,
                [{"role": "user", "content": "task"}], lambda event: None, lambda action: True,
                plan_first=True, approve_plan=lambda plan: False, chat_fn=lambda *args: final_response("1. Work."))
        execute.assert_not_called()

    def test_real_request_advertises_only_read_tools_while_planning(self):
        captured = []
        responses = iter([final_response("1. Inspect."), final_response("Done")])
        def request(value, **kwargs):
            captured.append(json.loads(value.data))
            return io.BytesIO(json.dumps(next(responses)).encode())
        with patch("coding_agent.urllib.request.urlopen", side_effect=request):
            coding_agent.run_agent("http://localhost:11434", "m", self.root,
                [{"role": "user", "content": "task"}], lambda event: None, lambda action: True,
                plan_first=True, approve_plan=lambda plan: True, keep_alive="7m")
        self.assertEqual({tool["function"]["name"] for tool in captured[0]["tools"]}, {"read_file", "list_files"})
        self.assertIn("write_file", {tool["function"]["name"] for tool in captured[1]["tools"]})
        self.assertEqual([request["keep_alive"] for request in captured], ["7m", "7m"])


class DeveloperApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.project = self.store.directory / "project"
        self.project.mkdir()

    def test_plan_waits_edited_approval_and_action_approval_are_separate(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "m")
        responses = [final_response("1. Write a.py."),
                     tool_response("write_file", {"path": "a.py", "content": "x"}), final_response("Done")]
        with patch("coding_agent._chat", side_effect=responses) as request:
            result = self.api.run_agent(chat, "task", str(self.project), plan_first=True)
            self.assertTrue(result["ok"], result)
            task = result["task_id"]
            plan = self.events.wait("plan_approval")
            self.assertEqual(self.api.developer_task_state(task)["approvals"], [plan])
            self.assertFalse((self.project / "a.py").exists())
            self.assertFalse(self.api.send(chat, "busy", "m", None)["ok"])
            self.assertTrue(self.api.approve_plan(task, "edit", "1. Approved edited plan.")["ok"])
            action = self.events.wait("approval")
            self.assertEqual(self.api.developer_task_state(task)["approvals"], [action])
            self.assertFalse((self.project / "a.py").exists())
            self.assertTrue(self.api.approve_action(task, action["approval_id"], True)["ok"])
            terminal = self.events.wait("agent_done")
            self.assertEqual(self.api.developer_task_state(task), {"ok": True, "approvals": [], "result": terminal})
        self.assertEqual((self.project / "a.py").read_text(), "x")
        self.assertEqual(self.store.load(chat)["messages"][-1]["content"], "Done")
        self.assertIn("Approved edited plan", "\n".join(item["content"] for item in request.call_args_list[1].args[2]))
        self.assertFalse(self.api.approve_plan(task, "approve")["ok"])

    def test_close_cancels_pending_plan_without_writing(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "m")
        with patch("coding_agent._chat", return_value=final_response("1. Work.")):
            result = self.api.run_agent(chat, "task", str(self.project), plan_first=True)
            self.events.wait("plan_approval")
            self.api._close()
            self.events.wait("agent_stopped")
        self.assertEqual(list(self.project.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
