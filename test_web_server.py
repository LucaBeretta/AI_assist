"""The web workspace: chat service rules, the API contract, and its security boundary.

A scripted provider stands in for Ollama, so streaming, Stop and failures
are exact. The server runs for real on 127.0.0.1 and is called over HTTP.
"""

import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app_core.chat_service import MEMORY_RECEIPT, ChatService, ServiceError
from app_core.providers import ProviderInfo
from chat_store import ChatStore
from shared_memory import SharedMemory
from webui import security
from webui.server import Handler, WebWorkspace, WorkspaceServer


class ScriptedProvider:
    """Streams ``chunks``; with ``hold`` it waits (as a loading model would) until cancelled."""

    name = "Ollama"

    def __init__(self, chunks=("Hel", "lo"), error=None, hold=False):
        self.chunks, self.error, self.hold = list(chunks), error, hold
        self.calls = []
        self.started = threading.Event()

    def info(self):
        return ProviderInfo(self.name, "http://127.0.0.1:11434", True, False)

    def list_models(self):
        return ["alpha:8b", "cloudy:cloud"]

    def running(self):
        return {"alpha:8b": {"size": 10, "size_vram": 10, "context_length": 4096}}

    def model_details(self, model):
        return {"name": model, "capabilities": ["completion"], "context_length": 8192}

    def stream_chat(self, messages, model, *, context_length=None, on_chunk=None, on_metrics=None, cancel_event=None):
        self.calls.append({"messages": messages, "model": model, "context_length": context_length})
        for chunk in self.chunks:
            on_chunk(chunk)
        self.started.set()
        if self.hold:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                time.sleep(0.02)
                on_chunk("")  # A stream callback is where Stop is noticed.
            raise RuntimeError("held too long")
        if self.error:
            raise RuntimeError(self.error)
        on_metrics({"eval_count": 2, "eval_duration": 1_000_000_000})
        return "".join(self.chunks)


class ServiceCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(Path(folder.name) / "chats")
        self.memory = SharedMemory(self.store.directory)
        self.provider = ScriptedProvider()
        self.service = ChatService(self.store, lambda: self.provider, self.memory)

    def send(self, chat_id, text, model="alpha:8b", cancel=None, backend="Ollama"):
        events = []
        cancel = cancel or threading.Event()
        self.service.prepare(chat_id, text, model, backend)
        self.service.send(chat_id, text, model, backend, events.append, cancel)
        return events


class ChatServiceTests(ServiceCase):
    def test_an_answer_streams_and_both_messages_are_saved(self):
        chat = self.service.create("alpha:8b")
        events = self.send(chat["id"], "Hi there")
        self.assertEqual([event["type"] for event in events], ["user", "chunk", "chunk", "metrics", "done"])
        saved = self.store.load(chat["id"])
        self.assertEqual([(m["role"], m["content"]) for m in saved["messages"]], [("user", "Hi there"), ("assistant", "Hello")])
        self.assertEqual(saved["title"], "Hi there")
        self.assertIn("tokens/s", events[3]["summary"])

    def test_shared_memory_goes_first_and_other_chats_are_recalled(self):
        self.memory.add("The user prefers Python.")
        other = self.service.create("alpha:8b")
        self.send(other["id"], "Tell me about gardening tomatoes")
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "More about tomatoes please")
        system = self.provider.calls[-1]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("The user prefers Python.", system["content"])
        self.assertIn("gardening tomatoes", system["content"])

    def test_a_failed_answer_saves_no_reply(self):
        self.provider.error = "Not enough memory"
        chat = self.service.create("alpha:8b")
        events = self.send(chat["id"], "Hi")
        self.assertEqual(events[-1], {"type": "error", "error": "Not enough memory"})
        self.assertEqual([m["role"] for m in self.store.load(chat["id"])["messages"]], ["user"])

    def test_stop_keeps_the_partial_answer(self):
        self.provider = ScriptedProvider(chunks=("Part",), hold=True)
        chat = self.service.create("alpha:8b")
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        events = self.send(chat["id"], "Long story", cancel=cancel)
        self.assertEqual((events[-1]["type"], events[-1]["stopped"]), ("done", True))
        self.assertEqual(self.store.load(chat["id"])["messages"][-1], {"role": "assistant", "content": "Part"})
        self.assertEqual(sum(event["type"] in ("done", "error") for event in events), 1)

    def test_memory_commands_are_handled_locally(self):
        chat = self.service.create("alpha:8b")
        events = self.send(chat["id"], "Remember that I like tea", model="")
        self.assertEqual((events[-1]["type"], events[-1]["local"]), ("done", True))
        self.assertIn("I like tea", self.memory.notes)
        self.assertEqual(self.store.load(chat["id"])["messages"][-1]["content"], MEMORY_RECEIPT)
        self.assertEqual(self.provider.calls, [])
        self.memory.save(self.memory.notes, enabled=False)
        events = self.send(chat["id"], "Remember that I like coffee", model="")
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("disabled", events[-1]["error"])

    def test_refusals_happen_before_anything_streams(self):
        chat = self.service.create("alpha:8b")
        developer = self.store.save(self.store.new_record(model="alpha:8b", mode="dev", project_path="/p"))
        for chat_id, text, model, backend, status in (
                (chat["id"], "", "alpha:8b", "Ollama", 400), (chat["id"], "x" * 100_001, "alpha:8b", "Ollama", 400),
                (chat["id"], "Hi", "", "Ollama", 400), (developer["id"], "Hi", "alpha:8b", "Ollama", 409),
                ("00000000-0000-4000-8000-000000000000", "Hi", "alpha:8b", "Ollama", 404)):
            with self.subTest(status=status, text=text[:5]), self.assertRaises(ServiceError) as caught:
                self.service.prepare(chat_id, text, model, backend)
            self.assertEqual(caught.exception.status, status)

    def test_a_chat_continues_only_on_its_own_engine_and_one_answer_at_a_time(self):
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "Hi")
        with self.assertRaises(ServiceError) as caught:
            self.service.prepare(chat["id"], "Again", "m", "Colibri")
        self.assertEqual(caught.exception.status, 409)
        self.service.prepare(chat["id"], "Again", "alpha:8b", "Ollama")
        with self.assertRaises(ServiceError) as caught:
            self.service.prepare(chat["id"], "Twice", "alpha:8b", "Ollama")
        self.assertEqual(caught.exception.status, 409)
        self.service.release(chat["id"])
        self.service.prepare(chat["id"], "Now", "alpha:8b", "Ollama")

    def send_from(self, chat_id, text, edit_from, cancel=None):
        events = []
        self.service.prepare(chat_id, text, "alpha:8b", "Ollama", edit_from)
        self.service.send(chat_id, text, "alpha:8b", "Ollama", events.append, cancel or threading.Event(), None, edit_from)
        return events

    def conversation(self, chat_id):
        return [(m["role"], m["content"]) for m in self.store.load(chat_id)["messages"]]

    def test_editing_a_message_replaces_it_and_everything_after_it(self):
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "First question")
        self.provider = ScriptedProvider(chunks=("Second answer",))
        self.send(chat["id"], "Second question")
        self.provider = ScriptedProvider(chunks=("New answer",))
        events = self.send_from(chat["id"], "First question, better", 0)
        self.assertEqual((events[0]["type"], events[0]["index"]), ("user", 0))
        self.assertEqual(self.conversation(chat["id"]), [("user", "First question, better"), ("assistant", "New answer")])
        # The model saw only the edited conversation, not the replaced turns.
        sent = [m["content"] for m in self.provider.calls[-1]["messages"] if m["role"] != "system"]
        self.assertEqual(sent, ["First question, better"])

    def test_regenerate_answers_the_last_message_again(self):
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "Name a colour")
        self.provider = ScriptedProvider(chunks=("Blue",))
        self.send_from(chat["id"], "Name a colour", 0)
        self.assertEqual(self.conversation(chat["id"]), [("user", "Name a colour"), ("assistant", "Blue")])

    def test_a_failed_or_early_stopped_regenerate_keeps_the_earlier_answer(self):
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "Name a colour")
        before = self.conversation(chat["id"])
        self.provider = ScriptedProvider(error="Not enough memory")
        events = self.send_from(chat["id"], "Name a colour", 0)
        self.assertEqual(events[-1], {"type": "error", "error": "Not enough memory", "restored": True})
        self.assertEqual(self.conversation(chat["id"]), before)
        self.provider = ScriptedProvider(chunks=(), hold=True)
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        events = self.send_from(chat["id"], "Name a colour", 0, cancel)
        self.assertEqual((events[-1]["type"], events[-1]["stopped"], events[-1]["restored"]), ("done", True, True))
        self.assertEqual(self.conversation(chat["id"]), before)

    def test_only_the_persons_own_messages_can_be_edited(self):
        chat = self.service.create("alpha:8b")
        self.send(chat["id"], "Hi")
        for edit_from in (1, 2, -1, True, "0", 0.0):
            with self.subTest(edit_from=edit_from), self.assertRaises(ServiceError) as caught:
                self.service.prepare(chat["id"], "Hi again", "alpha:8b", "Ollama", edit_from)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.service.active, set())

    def test_pinned_chats_come_first_and_a_stale_save_keeps_the_pin(self):
        older = self.service.create("alpha:8b")
        self.service.rename(older["id"], "Older")
        newer = self.service.create("alpha:8b")
        self.service.rename(newer["id"], "Newer")
        stale = self.store.load(older["id"])
        self.service.set_pinned(older["id"], True)
        self.service.rename(newer["id"], "Newer")  # now the most recent, yet below the pinned chat
        self.assertEqual([(chat["title"], chat["pinned"]) for chat in self.service.list()], [("Older", True), ("Newer", False)])
        stale["draft"] = "typed before the pin"
        self.store.save(stale)  # an older copy, as a long answer or agent task would save
        self.assertTrue(self.store.load(older["id"])["pinned"])
        self.service.set_pinned(older["id"], False)
        self.assertNotIn("pinned", self.store.load(older["id"]))
        with self.assertRaises(ServiceError):
            self.service.set_pinned(older["id"], "yes")

    def test_list_rename_delete_and_search(self):
        first = self.service.create("alpha:8b")
        self.send(first["id"], "Bananas are yellow")
        second = self.service.create("alpha:8b")
        self.service.rename(second["id"], "Kitchen plans")
        self.assertEqual([chat["title"] for chat in self.service.list()], ["Kitchen plans", "Bananas are yellow"])
        self.assertEqual([chat["title"] for chat in self.service.list("YELLOW")], ["Bananas are yellow"])
        self.service.delete(first["id"])
        self.assertEqual([chat["title"] for chat in self.service.list()], ["Kitchen plans"])
        with self.assertRaises(ServiceError):
            self.service.rename(second["id"], " ")


# ------------------------------------------------------------------ HTTP

class ServerCase(ServiceCase):
    def setUp(self):
        super().setUp()
        self.workspace = WebWorkspace(self.store, self.memory, provider_factory=lambda: self.provider)
        self.server = WorkspaceServer(self.workspace)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = self.workspace.sessions.exchange(self.workspace.sessions.new_launch_code())

    def call(self, method, path, body=None, headers=None, token=True, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        self.addCleanup(connection.close)
        sent = {"Host": self.server.authority}
        if token:
            sent["Authorization"] = "Bearer " + self.token
        if body is not None or raw is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        connection.request(method, path, data, {key: value for key, value in sent.items() if value is not None})
        response = connection.getresponse()
        payload = response.read()
        kind = response.getheader("Content-Type", "")
        if "ndjson" in kind:
            return response, [json.loads(line) for line in payload.decode().splitlines()]
        return response, json.loads(payload) if kind.startswith("application/json") else payload


class SecurityTests(ServerCase):
    def test_only_loopback_addresses_can_be_served(self):
        for host in ("0.0.0.0", "", "::", "192.168.1.5", "localhost"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                WorkspaceServer(self.workspace, host)

    def test_the_page_carries_a_strict_policy(self):
        response, page = self.call("GET", "/", token=False)
        self.assertEqual(response.status, 200)
        self.assertIn(b"app.js", page)
        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        for name, value in security.COMMON_HEADERS:
            self.assertEqual(response.getheader(name), value)

    def test_only_the_page_files_are_served(self):
        for path in ("/server.py", "/../server.py", "/%2e%2e/server.py", "/static/app.js", "/tests/x.js", "/APP.JS", "/app.js/"):
            with self.subTest(path=path):
                response, _ = self.call("GET", path, token=False)
                self.assertEqual(response.status, 404)
        response, _ = self.call("POST", "/app.js", token=False)
        self.assertEqual(response.status, 405)

    def test_a_wrong_host_is_refused_against_dns_rebinding(self):
        for host in ("evil.example", f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port + 1}"):
            with self.subTest(host=host):
                response, body = self.call("GET", "/api/status", headers={"Host": host})
                self.assertEqual(response.status, 421)
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=10) as connection:
            connection.sendall(f"GET /api/status HTTP/1.1\r\nAuthorization: Bearer {self.token}\r\n\r\n".encode())
            self.assertTrue(connection.recv(64).startswith(b"HTTP/1.1 421"))

    def test_other_sites_are_refused_and_never_get_cors(self):
        port = self.server.server_port
        for headers in ({"Origin": "http://evil.example"}, {"Origin": f"http://127.0.0.1:{port + 1}"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                response, body = self.call("GET", "/api/status", headers=headers)
                self.assertEqual(response.status, 403)
                self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        response, _ = self.call("OPTIONS", "/api/chats", headers={"Origin": f"http://127.0.0.1:{port}"})
        self.assertEqual(response.status, 405)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        response, _ = self.call("GET", "/api/status", headers={"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status, 200)

    def test_every_api_call_needs_a_session(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": self.token}, {"Authorization": "Basic x"}):
            with self.subTest(headers=headers):
                response, body = self.call("GET", "/api/chats", headers=headers, token=False)
                self.assertEqual(response.status, 401)
        self.assertNotIn("Set-Cookie", dict(self.call("GET", "/api/status")[0].getheaders()))

    def test_launch_codes_work_once_and_expire(self):
        code = self.workspace.sessions.new_launch_code()
        response, body = self.call("POST", "/api/session", {"launch": code}, token=False)
        self.assertEqual(response.status, 200)
        self.assertTrue(self.workspace.sessions.valid("Bearer " + body["token"]))
        response, _ = self.call("POST", "/api/session", {"launch": code}, token=False)
        self.assertEqual(response.status, 401)
        clock = [0.0]
        sessions = security.Sessions(clock=lambda: clock[0])
        late = sessions.new_launch_code()
        clock[0] = security.LAUNCH_SECONDS + 1
        self.assertIsNone(sessions.exchange(late))
        self.assertIsNone(sessions.exchange(None))

    def test_bodies_must_be_small_json_objects(self):
        response, _ = self.call("POST", "/api/chats", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)
        response, _ = self.call("POST", "/api/chats", raw=b"[]")
        self.assertEqual(response.status, 400)
        response, _ = self.call("POST", "/api/chats", raw=b"{nope")
        self.assertEqual(response.status, 400)
        response, _ = self.call("POST", "/api/chats", raw=b"{}", headers={"Content-Length": str(2 * 1024 * 1024)})
        self.assertEqual(response.status, 413)

    def test_the_api_has_no_direct_command_write_or_internet_endpoints(self):
        # Adding an endpoint is a security decision: update this list deliberately. Edits and commands
        # happen only through the agent's reviewed approvals (/agent, /approvals); none runs directly.
        self.assertEqual(sorted((method, pattern) for method, pattern, _, _ in Handler.ROUTES), sorted([
            ("POST", r"/api/session"), ("GET", r"/api/status"), ("GET", r"/api/models"), ("GET", r"/api/models/details"),
            ("GET", r"/api/hardware"), ("GET", r"/api/chats"), ("POST", r"/api/chats"),
            ("GET", r"/api/chats/(?P<chat>[0-9a-f-]{36})"), ("PATCH", r"/api/chats/(?P<chat>[0-9a-f-]{36})"),
            ("DELETE", r"/api/chats/(?P<chat>[0-9a-f-]{36})"), ("POST", r"/api/chats/(?P<chat>[0-9a-f-]{36})/messages"),
            ("POST", r"/api/chats/(?P<chat>[0-9a-f-]{36})/agent"), ("POST", r"/api/approvals/(?P<approval>[0-9a-f]{16})"),
            ("POST", r"/api/chats/(?P<chat>[0-9a-f-]{36})/undo"), ("POST", r"/api/projects/(?P<chat>[0-9a-f-]{36})/chats"),
            ("POST", r"/api/requests/(?P<request>[0-9a-f]{16})/cancel"), ("GET", r"/api/memory"), ("PUT", r"/api/memory"),
            ("POST", r"/api/memory/notes"), ("GET", r"/api/projects"), ("GET", r"/api/projects/(?P<chat>[0-9a-f-]{36})/files"),
            ("GET", r"/api/projects/(?P<chat>[0-9a-f-]{36})/file"), ("GET", r"/api/log"), ("PUT", r"/api/settings/engine"),
            ("GET", r"/api/backups"), ("POST", r"/api/backups"), ("POST", r"/api/backups/restore")]))
        self.assertEqual([name for _, _, name, public in Handler.ROUTES if not public], ["session"])

    def test_unexpected_errors_reveal_no_details(self):
        with patch.object(WebWorkspace, "status", side_effect=KeyError("C:/secret/path")):
            response, body = self.call("GET", "/api/status")
        self.assertEqual(response.status, 500)
        self.assertNotIn("secret", json.dumps(body))


class ApiTests(ServerCase):
    def test_status_says_what_this_interface_cannot_do(self):
        response, body = self.call("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["capabilities"], {"chat": True, "developer_agent": True, "internet_approvals": False,
                                                "project_files": "read-only", "new_project_folders": False})
        self.assertEqual((body["engine"], body["endpoint_local"]), ("Ollama", True))

    def test_models_report_placement_and_cloud_models(self):
        _, body = self.call("GET", "/api/models")
        alpha, cloud = body["models"]
        self.assertEqual((alpha["running"], alpha["placement"], cloud["cloud"]), (True, "gpu", True))
        _, details = self.call("GET", "/api/models/details?name=alpha:8b")
        self.assertEqual(details["context_length"], 8192)

    def test_an_engine_that_does_not_answer_is_a_503_with_its_reason(self):
        with patch.object(ScriptedProvider, "list_models", side_effect=RuntimeError("Start Ollama, then click Refresh.")):
            response, body = self.call("GET", "/api/models")
        self.assertEqual((response.status, body["error"]), (503, "Start Ollama, then click Refresh."))

    def test_chat_lifecycle(self):
        _, created = self.call("POST", "/api/chats", {"model": "alpha:8b"})
        chat_id = created["chat"]["id"]
        response, events = self.call("POST", f"/api/chats/{chat_id}/messages", {"content": "Hi", "model": "alpha:8b",
                                                                                 "context_length": 8192})
        self.assertEqual(response.status, 200)
        self.assertEqual([event["type"] for event in events], ["start", "user", "chunk", "chunk", "metrics", "done"])
        self.assertEqual(events[-1]["message"], {"role": "assistant", "content": "Hello"})
        self.assertEqual(events[-1]["chat"]["message_count"], 2)
        self.assertEqual(self.provider.calls[-1]["context_length"], 8192)
        _, listed = self.call("GET", "/api/chats?query=hi")
        self.assertEqual([chat["id"] for chat in listed["chats"]], [chat_id])
        _, renamed = self.call("PATCH", f"/api/chats/{chat_id}", {"title": "Greeting"})
        self.assertEqual(renamed["chat"]["title"], "Greeting")
        _, pinned = self.call("PATCH", f"/api/chats/{chat_id}", {"pinned": True})
        self.assertEqual((pinned["chat"]["pinned"], pinned["chat"]["title"]), (True, "Greeting"))
        for bad in ({}, {"pinned": "yes"}):
            self.assertEqual(self.call("PATCH", f"/api/chats/{chat_id}", bad)[0].status, 400)
        # Regenerate: the same text from the last message replaces the answer.
        self.provider = ScriptedProvider(chunks=("Hi again",))
        _, events = self.call("POST", f"/api/chats/{chat_id}/messages", {"content": "Hi", "model": "alpha:8b", "edit_from": 0})
        self.assertEqual((events[1]["index"], events[-1]["message"]["content"], events[-1]["chat"]["message_count"]),
                         (0, "Hi again", 2))
        for bad, status in ((1, 409), ("0", 400), (True, 400)):
            with self.subTest(edit_from=bad):
                response, _ = self.call("POST", f"/api/chats/{chat_id}/messages", {"content": "Hi", "model": "alpha:8b", "edit_from": bad})
                self.assertEqual(response.status, status)
        _, fetched = self.call("GET", f"/api/chats/{chat_id}")
        self.assertEqual(len(fetched["chat"]["messages"]), 2)
        response, _ = self.call("DELETE", f"/api/chats/{chat_id}")
        self.assertEqual(response.status, 200)
        response, _ = self.call("GET", f"/api/chats/{chat_id}")
        self.assertEqual(response.status, 404)

    def test_send_refusals_are_json_before_any_stream(self):
        _, created = self.call("POST", "/api/chats", {"model": "alpha:8b"})
        chat_id = created["chat"]["id"]
        for body, status in (({"content": "", "model": "alpha:8b"}, 400), ({"content": "Hi", "model": "alpha:8b", "context_length": 5}, 400),
                             ({"content": "Hi", "model": "alpha:8b", "context_length": True}, 400)):
            with self.subTest(body=body):
                response, payload = self.call("POST", f"/api/chats/{chat_id}/messages", body)
                self.assertEqual(response.status, status)
                self.assertIn("error", payload)
        developer = self.store.save(self.store.new_record(model="alpha:8b", mode="dev", project_path="/p"))
        response, payload = self.call("POST", f"/api/chats/{developer['id']}/messages", {"content": "Hi", "model": "alpha:8b"})
        self.assertEqual(response.status, 409)
        self.assertIn("developer agent", payload["error"])

    def stream_with_stop(self, stop):
        self.provider = ScriptedProvider(chunks=("Partial",), hold=True)
        _, created = self.call("POST", "/api/chats", {"model": "alpha:8b"})
        chat_id = created["chat"]["id"]
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=20)
        body = json.dumps({"content": "Tell me a long story", "model": "alpha:8b"}).encode()
        connection.sendall((f"POST /api/chats/{chat_id}/messages HTTP/1.1\r\nHost: {self.server.authority}\r\n"
                            f"Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        reader = connection.makefile("rb")
        while reader.readline() not in (b"\r\n", b""):
            pass
        start = json.loads(reader.readline())
        self.assertEqual(start["type"], "start")
        self.assertTrue(self.provider.started.wait(5))
        events = stop(connection, reader, start["request"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.workspace.service.active:
            time.sleep(0.05)
        reader.close()
        connection.close()
        return chat_id, events

    def test_stop_ends_the_stream_and_keeps_the_partial_answer(self):
        def stop(connection, reader, request):
            response, body = self.call("POST", f"/api/requests/{request}/cancel", {})
            self.assertEqual(response.status, 200)
            return [json.loads(line) for line in reader]
        chat_id, events = self.stream_with_stop(stop)
        self.assertEqual((events[-1]["type"], events[-1]["stopped"]), ("done", True))
        self.assertEqual(events[-1]["message"]["content"], "Partial")
        self.assertEqual(self.store.load(chat_id)["messages"][-1]["content"], "Partial")
        response, _ = self.call("POST", "/api/requests/0123456789abcdef/cancel", {})
        self.assertEqual(response.status, 404)

    def test_closing_the_page_stops_the_answer(self):
        def close(connection, reader, request):
            connection.shutdown(socket.SHUT_RDWR)
            return []
        with patch.object(Handler, "HEARTBEAT_SECONDS", 0.1):
            chat_id, _ = self.stream_with_stop(close)
        self.assertEqual(self.workspace.service.active, set())
        self.assertEqual(self.store.load(chat_id)["messages"][-1]["content"], "Partial")

    def test_shared_memory_endpoints(self):
        _, body = self.call("PUT", "/api/memory", {"notes": "Use metric units.", "enabled": True})
        self.assertEqual(body["notes"], "Use metric units.")
        _, body = self.call("POST", "/api/memory/notes", {"note": "Prefers tea."})
        self.assertEqual(body["notes"], "Use metric units.\nPrefers tea.")
        response, body = self.call("PUT", "/api/memory", {"notes": "x" * 5000, "enabled": True})
        self.assertEqual(response.status, 400)
        response, _ = self.call("PUT", "/api/memory", {"notes": "ok", "enabled": "yes"})
        self.assertEqual(response.status, 400)

    def test_engine_switch_is_saved_like_the_desktop_app(self):
        response, _ = self.call("PUT", "/api/settings/engine", {"backend": "Colibri"})
        self.assertEqual(response.status, 200)
        saved = json.loads((self.store.directory / "backend.settings").read_text())
        self.assertEqual(saved["backend"], "Colibri")
        response, _ = self.call("PUT", "/api/settings/engine", {"backend": "Cloud"})
        self.assertEqual(response.status, 400)

    def test_backups_can_be_made_and_restored_from_the_data_folder_only(self):
        _, created = self.call("POST", "/api/chats", {"model": "alpha:8b"})
        self.call("POST", f"/api/chats/{created['chat']['id']}/messages", {"content": "Keep me", "model": "alpha:8b"})
        response, body = self.call("POST", "/api/backups", {})
        self.assertEqual(response.status, 200)
        self.assertIn("Backed up 1 chat", body["message"])
        name = body["backups"][0]["name"]
        self.call("DELETE", f"/api/chats/{created['chat']['id']}")
        response, body = self.call("POST", "/api/backups/restore", {"name": name})
        self.assertEqual((response.status, body["report"]["added"]), (200, 1))
        _, listed = self.call("GET", "/api/chats")
        self.assertEqual([chat["title"] for chat in listed["chats"]], ["Keep me"])
        for bad in ("../chats-20260101-000000.zip", "C:/Windows/win.ini", "chats-x.zip", None, 7):
            with self.subTest(bad=bad):
                response, _ = self.call("POST", "/api/backups/restore", {"name": bad})
                self.assertEqual(response.status, 404)

    def test_gate_log_shows_recent_decisions(self):
        log = self.store.directory / "network.log"
        log.write_text("cut line}\n" + json.dumps({"time": "t", "decision": "blocked", "kind": "Internet connection",
                                                    "target": "example.com", "purpose": "", "source": "x.py"}) + "\n")
        with patch("webui.server.network_gate.log_path", return_value=log):
            _, body = self.call("GET", "/api/log")
        self.assertEqual([(entry["decision"], entry["target"]) for entry in body["entries"]], [("blocked", "example.com")])


class ProjectFileTests(ServerCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.project = Path(folder.name, "project")
        (self.project / "src").mkdir(parents=True)
        (self.project / "src" / "main.py").write_bytes(b"print('hi')\n")
        (self.project / ".env").write_text("TOKEN=secret\n")
        (self.project / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "dep.js").write_text("x")
        Path(folder.name, "outside.txt").write_text("outside")
        self.developer = self.store.save(self.store.new_record(model="alpha:8b", mode="dev", project_path=str(self.project)))

    def test_files_of_a_chosen_project_are_listed_without_secrets(self):
        _, body = self.call("GET", f"/api/projects/{self.developer['id']}/files")
        self.assertEqual([item["path"] for item in body["files"]], ["src/main.py"])
        _, body = self.call("GET", f"/api/projects/{self.developer['id']}/file?path=src/main.py")
        self.assertEqual(body["content"], "print('hi')\n")
        _, projects = self.call("GET", "/api/projects")
        self.assertEqual([item["chat_id"] for item in projects["projects"]], [self.developer["id"]])

    def test_reads_cannot_leave_the_project_or_reach_secrets(self):
        for path in ("../outside.txt", ".env", "server.pem", "node_modules/dep.js", str(self.project / "src" / "main.py"),
                     "src/../../outside.txt", ""):
            with self.subTest(path=path):
                response, body = self.call("GET", f"/api/projects/{self.developer['id']}/file?path={path}")
                self.assertEqual(response.status, 404)
                self.assertNotIn("secret", json.dumps(body))

    def test_only_folders_chosen_for_developer_chats_are_readable(self):
        chat = self.service.create("alpha:8b")
        response, _ = self.call("GET", f"/api/projects/{chat['id']}/files")
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
