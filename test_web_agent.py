"""The developer agent in the web workspace: every edit and command waits for a person.

A scripted model proposes tool calls; the real agent loop, approval rules,
undo copies, server and HTTP are used. Commands are harmless real ones.
"""

import json
import os
from pathlib import Path
import shlex
import socket
import sys
import tempfile
import threading
import time
import unittest

from app_core.providers import ProviderInfo
from chat_store import ChatStore
from shared_memory import SharedMemory
from webui.server import WebWorkspace, WorkspaceServer


def tool_call(name, **arguments):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


def final(text="Done."):
    return {"message": {"content": text}}


def python_command(code):
    """A command that runs this Python, written for the agent's shell on this system."""
    if os.name == "nt":
        return f"& '{sys.executable}' -c \"{code}\""
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


class AgentProvider:
    name = "Ollama"

    def __init__(self, steps, tools=True):
        self.steps = iter(steps)
        self.tools = tools

    def info(self):
        return ProviderInfo(self.name, "http://127.0.0.1:11434", True, False)

    def list_models(self):
        return ["coder:7b"]

    def running(self):
        return {"coder:7b": {}}

    def model_details(self, model):
        return {"name": model, "capabilities": ["completion", "tools"] if self.tools else ["completion"]}

    def agent_backend(self, cancel_event=None):
        return "http://127.0.0.1:11434", lambda *args: next(self.steps)


class AgentCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "main.py").write_bytes(b"print('old')\n")
        self.store = ChatStore(self.root / "data")
        self.provider = AgentProvider([])
        self.workspace = WebWorkspace(self.store, SharedMemory(self.store.directory), provider_factory=lambda: self.provider)
        self.workspace.agent.confirm_seconds = 0.3
        self.server = WorkspaceServer(self.workspace)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = self.workspace.sessions.exchange(self.workspace.sessions.new_launch_code())
        self.chat = self.store.save(self.store.new_record(model="coder:7b", mode="dev", project_path=str(self.project)))

    def call(self, method, path, body=None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        self.addCleanup(connection.close)
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {"Host": self.server.authority, "Authorization": "Bearer " + self.token,
                            "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    def start(self, text="Change main.py", chat_id=None):
        """Start a task; returns a function that reads the next event."""
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=20)
        self.addCleanup(connection.close)
        body = json.dumps({"content": text, "model": "coder:7b"}).encode()
        connection.sendall((f"POST /api/chats/{chat_id or self.chat['id']}/agent HTTP/1.1\r\nHost: {self.server.authority}\r\n"
                            f"Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        reader = connection.makefile("rb")
        self.addCleanup(reader.close)
        status = reader.readline()
        while reader.readline() not in (b"\r\n", b""):
            pass
        self.assertIn(b" 200 ", status)

        def next_event(kind=None):
            while True:
                line = reader.readline()
                if not line:
                    self.fail(f"The stream ended before {kind or 'an event'}.")
                event = json.loads(line)
                if kind is None or event["type"] == kind or event["type"] in ("done", "error"):
                    return event
        return next_event

    def wait_until_idle(self):
        deadline = time.monotonic() + 10
        while self.workspace.service.active and time.monotonic() < deadline:
            time.sleep(0.02)


class EditTests(AgentCase):
    def test_an_edit_waits_for_approval_then_can_be_undone(self):
        self.provider = AgentProvider([tool_call("write_file", path="main.py", content="print('new')\n"), final("Updated main.py.")])
        events = self.start()
        review = events("approval")
        self.assertEqual(review["type"], "approval")
        self.assertEqual((review["action"]["kind"], review["action"]["path"]), ("write_file", "main.py"))
        self.assertIn("+print('new')", review["action"]["diff"])
        self.assertNotIn("content", review["action"])
        time.sleep(0.2)
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('old')\n")  # nothing written while waiting
        self.assertEqual(self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"}), (200, {"closed": True}))
        done = events("done")
        self.assertEqual((done["type"], done["changed"], done["message"]["content"]), ("done", 1, "Updated main.py."))
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('new')\n")
        status, body = self.call("GET", f"/api/chats/{self.chat['id']}")
        self.assertTrue(body["undo"])
        self.assertIn("changed", [entry["type"] for entry in body["chat"]["activity"]])
        self.assertTrue(body["chat"]["agent_history"])
        status, undone = self.call("POST", f"/api/chats/{self.chat['id']}/undo", {})
        self.assertEqual((status, undone["message"]), (200, "Undid the AI's changes to 1 file."))
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('old')\n")
        self.assertFalse(self.call("GET", f"/api/chats/{self.chat['id']}")[1]["undo"])

    def test_a_rejected_edit_is_never_written(self):
        self.provider = AgentProvider([tool_call("write_file", path="main.py", content="print('new')\n"), final("Left it.")])
        events = self.start()
        review = events("approval")
        self.assertEqual(self.call("POST", f"/api/approvals/{review['id']}", {"choice": "reject"}), (200, {"closed": True}))
        # The first answer counts: a late approval (a second click, another tab) cannot overturn it.
        status, body = self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"})
        self.assertIn(status, (404, 409))
        self.assertEqual(events("approval_closed")["approved"], False)
        self.assertEqual(events("done")["changed"], 0)
        self.assertEqual((self.project / "main.py").read_bytes(), b"print('old')\n")


class CommandTests(AgentCase):
    def command_task(self):
        marker = self.project / "ran.txt"
        self.provider = AgentProvider([tool_call("run_command", command=python_command("open('ran.txt','w').write('x')"),
                                                 description="Write a marker"), final("Ran it.")])
        return marker, self.start("Run the marker command")

    def test_a_command_needs_both_confirmations_and_the_wait(self):
        marker, events = self.command_task()
        review = events("approval")
        self.assertEqual((review["action"]["kind"], review["action"]["cwd"]), ("run_command", str(self.project.resolve())))
        status, body = self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"})
        self.assertEqual((status, body["error"]), (409, "Confirm the command a second time first."))
        status, body = self.call("POST", f"/api/approvals/{review['id']}", {"choice": "second_check"})
        self.assertEqual(status, 200)
        self.assertGreater(body["seconds_left"], 0)
        status, body = self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"})
        self.assertEqual(status, 409)
        self.assertIn("Wait", body["error"])
        self.assertFalse(marker.exists())
        time.sleep(0.35)
        self.assertEqual(self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"}), (200, {"closed": True}))
        done = events("done")
        self.assertEqual(done["type"], "done", done)
        self.assertTrue(marker.exists())
        activity = self.call("GET", f"/api/chats/{self.chat['id']}")[1]["chat"]["activity"]
        self.assertIn("command_succeeded", [entry["type"] for entry in activity])

    def test_a_rejected_command_never_runs(self):
        marker, events = self.command_task()
        review = events("approval")
        self.call("POST", f"/api/approvals/{review['id']}", {"choice": "second_check"})
        self.call("POST", f"/api/approvals/{review['id']}", {"choice": "reject"})
        self.assertEqual(events("done")["type"], "done")
        self.assertFalse(marker.exists())

    def test_stop_declines_the_waiting_review(self):
        marker, events = self.command_task()
        review = events("approval")
        request = next(iter(self.workspace.requests))
        self.assertEqual(self.call("POST", f"/api/requests/{request}/cancel", {})[0], 200)
        done = events("done")
        self.assertEqual((done["type"], done["stopped"]), ("done", True))
        self.wait_until_idle()
        self.assertEqual(self.call("POST", f"/api/approvals/{review['id']}", {"choice": "approve"})[0], 404)
        self.assertFalse(marker.exists())


class RefusalTests(AgentCase):
    def test_only_developer_chats_with_a_chosen_folder_run_the_agent(self):
        ordinary = self.store.save(self.store.new_record(model="coder:7b"))
        status, body = self.call("POST", f"/api/chats/{ordinary['id']}/agent", {"content": "Hi", "model": "coder:7b"})
        self.assertEqual(status, 409)
        lost = self.store.save(self.store.new_record(model="coder:7b", mode="dev", project_path=str(self.root / "gone")))
        status, body = self.call("POST", f"/api/chats/{lost['id']}/agent", {"content": "Hi", "model": "coder:7b"})
        self.assertEqual(status, 409)
        self.assertIn("desktop app", body["error"])
        for bad in ({"content": "", "model": "coder:7b"}, {"content": "Hi", "model": ""}):
            self.assertEqual(self.call("POST", f"/api/chats/{self.chat['id']}/agent", bad)[0], 400)

    def test_a_model_without_tools_is_refused_before_anything_runs(self):
        self.provider = AgentProvider([], tools=False)
        events = self.start()
        error = events("error")
        self.assertEqual(error["type"], "error")
        self.assertIn("does not advertise tool calling", error["error"])
        self.assertEqual(error["help"]["title"], "This model cannot use tools")

    def test_answers_must_be_known_choices_for_open_reviews(self):
        self.assertEqual(self.call("POST", "/api/approvals/0123456789abcdef", {"choice": "approve"})[0], 404)
        self.assertEqual(self.call("POST", "/api/approvals/0123456789abcdef", {"choice": "yes"})[0], 400)

    def test_a_new_task_uses_the_same_project_folder(self):
        status, body = self.call("POST", f"/api/projects/{self.chat['id']}/chats", {})
        self.assertEqual(status, 200)
        self.assertEqual((body["chat"]["mode"], body["chat"]["project_path"]), ("dev", str(self.project)))
        self.assertNotEqual(body["chat"]["id"], self.chat["id"])


if __name__ == "__main__":
    unittest.main()
