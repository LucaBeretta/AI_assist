"""The internet gate blocks internet traffic unless a person approves it."""

import collections
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import urllib.error

import network_gate as gate

INET = SimpleNamespace(family=socket.AF_INET)
INET6 = SimpleNamespace(family=socket.AF_INET6)
HERE = Path(__file__).resolve().parent


class GateTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.asked = []
        self.answer = False
        for name, value in (("_approver", self.approve), ("_log_directory", self.folder), ("_denied", {}),
                            ("_pending", {}), ("_noticed", {}), ("_model_checks", {}),
                            ("_recent", collections.deque(maxlen=300)), ("_last_allowed", None),
                            ("_local", threading.local())):
            patcher = patch.object(gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, options in (("_notice", {}), ("_resolve", {"side_effect": self.resolve})):
            patcher = patch.object(gate, name, **options)
            self.mocks = getattr(self, "mocks", {})
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def approve(self, request):
        self.asked.append(request)
        return self.answer

    @staticmethod
    def resolve(name):
        if name == "example.com":
            return {"93.184.215.14"}
        return {gate._ip_text(name)} if gate._ip_text(name) else set()

    def blocked(self, event, *args):
        with self.assertRaises(gate.InternetBlocked) as caught:
            gate._hook(event, args)
        return str(caught.exception)

    def decisions(self):
        return [(entry["decision"], entry["target"]) for entry in gate.recent()]


class ClassifyTests(unittest.TestCase):
    def test_this_computer_local_network_and_internet(self):
        for host in (None, "", "localhost", "LOCALHOST.", "127.0.0.1", "127.8.9.1", "::1", "[::1]",
                     "0.0.0.0", "::ffff:127.0.0.1"):
            self.assertEqual(gate.classify(host), gate.LOCAL, host)
        for host in ("192.168.1.20", "10.0.0.5", "172.16.4.4", "172.31.255.1", "169.254.1.1", "fe80::1%eth0",
                     "fd00::1", "<broadcast>", "255.255.255.255", "::ffff:192.168.0.2", b"10.1.1.1"):
            self.assertEqual(gate.classify(host), gate.LAN, host)
        # Names other than localhost may be resolved by a DNS server outside this network.
        for host in ("example.com", "printer.local", "router", "app.localhost", "8.8.8.8", "172.32.0.1",
                     "100.64.0.1", "2606:4700::1111", "::ffff:8.8.8.8"):
            self.assertEqual(gate.classify(host), gate.INTERNET, host)


class HookTests(GateTestCase):
    def test_this_computer_and_local_network_need_no_approval(self):
        gate._hook("socket.getaddrinfo", ("localhost", 11434, 0, 0, 0))
        gate._hook("socket.connect", (INET, ("127.0.0.1", 11434)))
        gate._hook("socket.connect", (INET6, ("::1", 8000, 0, 0)))
        gate._hook("socket.sendto", (INET, ("192.168.1.255", 5000)))
        gate._hook("socket.sendmsg", (INET, None))
        gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/tags", None, {}, "GET"))
        gate._hook("socket.gethostbyaddr", ("127.0.0.1",))
        gate._hook("open", ("anything",))
        self.assertEqual(self.asked, [])
        self.assertEqual(gate.recent(), [])

    def test_internet_is_blocked_when_no_one_can_approve(self):
        with patch.object(gate, "_approver", None):
            self.blocked("socket.getaddrinfo", "example.com", 443, 0, 0, 0)
            self.blocked("socket.connect", INET, ("8.8.8.8", 53))
            self.blocked("urllib.Request", "https://api.github.com/repos", None, {}, "GET")
        self.assertEqual([decision for decision, _ in self.decisions()], ["blocked: no approval window"] * 3)
        lines = (self.folder / "network.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["target"] for line in lines], ["example.com", "8.8.8.8 port 53", "api.github.com"])
        self.mocks["_notice"].assert_called()

    def test_a_denied_request_is_not_asked_again_at_once(self):
        message = self.blocked("urllib.Request", "https://example.com/", None, {}, "GET")
        self.assertIn("not approved", message)
        self.blocked("urllib.Request", "https://example.com/other", None, {}, "GET")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.decisions(), [("blocked", "example.com"), ("blocked: denied moments ago", "example.com")])

    def test_an_approval_covers_that_host_and_its_addresses_only(self):
        self.answer = True
        gate._hook("urllib.Request", ("https://example.com/x", None, {}, "GET"))
        gate._hook("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))
        gate._hook("socket.connect", (INET, ("93.184.215.14", 443)))
        self.assertEqual(len(self.asked), 1)
        request = self.asked[0]
        self.assertEqual((request.kind, request.target, request.purpose), ("Internet connection", "example.com", ""))
        self.assertTrue(request.source.startswith("test_network_gate.py line "))
        self.answer = False
        self.blocked("socket.connect", INET, ("1.1.1.1", 443))
        self.assertEqual(self.asked[1].target, "1.1.1.1 port 443")
        self.assertEqual(gate.status()["online"], "example.com")

    def test_rotated_dns_answers_are_checked_again(self):
        self.answer = True
        gate._hook("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))
        self.mocks["_resolve"].side_effect = lambda name: {"93.184.215.99"} if name == "example.com" else set()
        gate._hook("socket.connect", (INET, ("93.184.215.99", 443)))
        self.assertEqual(len(self.asked), 1)

    def test_approvals_belong_to_the_thread_that_asked(self):
        self.answer = True
        gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
        errors = []

        def other():
            self.answer = False
            try:
                gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
            except gate.InternetBlocked as error:
                errors.append(error)

        worker = threading.Thread(target=other)
        worker.start()
        worker.join(5)
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(len(errors), 1)

    def test_a_task_declares_the_purpose_and_ends_its_approvals(self):
        self.answer = True
        with gate.task("Check for updates", sends="Nothing personal"):
            gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
            gate._hook("urllib.Request", ("https://example.com/again", None, {}, "GET"))
        self.assertEqual(len(self.asked), 1)
        request = self.asked[0]
        self.assertEqual((request.purpose, request.sends, request.scope),
                         ("Check for updates", "Nothing personal", "until this task finishes"))
        gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.asked[1].purpose, "")

    def test_approvals_outside_a_task_expire(self):
        self.answer = True
        with patch.object(gate, "GRANT_SECONDS", -1):
            gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
            gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
        self.assertEqual(len(self.asked), 2)

    def test_connections_the_gate_cannot_check_are_blocked(self):
        self.blocked("socket.connect", SimpleNamespace(family=999), ("whatever",))
        self.blocked("socket.connect", INET, "not-a-tuple")
        self.assertEqual(self.asked, [])

    def test_connect_by_name_and_reverse_lookups_need_approval(self):
        self.blocked("socket.connect", INET, ("example.com", 80))
        self.assertEqual(self.asked[-1].target, "example.com")
        self.blocked("socket.gethostbyaddr", "192.168.1.5")
        self.assertEqual(self.asked[-1].kind, "Reverse DNS lookup")

    def test_asyncio_windows_connections_are_never_loaded(self):
        self.blocked("import", "_overlapped", None, [], [], [])
        gate._hook("import", ("json", None, [], [], []))

    def test_an_error_inside_the_gate_blocks(self):
        with patch.object(gate, "_check_name", side_effect=ValueError("broken")):
            message = self.blocked("socket.getaddrinfo", "localhost", 80, 0, 0, 0)
        self.assertIn("could not check", message)

    def test_identical_requests_from_two_threads_share_one_review(self):
        release, entered = threading.Event(), threading.Event()

        def slow(request):
            self.asked.append(request)
            entered.set()
            release.wait(5)
            return True

        errors = []

        def request():
            try:
                gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
            except gate.InternetBlocked as error:
                errors.append(error)

        with patch.object(gate, "_approver", slow):
            first = threading.Thread(target=request)
            first.start()
            self.assertTrue(entered.wait(5))
            second = threading.Thread(target=request)
            second.start()
            second.join(0.3)
            self.assertTrue(second.is_alive())
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual((len(self.asked), errors), (1, []))


class DeclaredHostsTests(GateTestCase):
    def lookup(self, host):
        gate._hook("socket.getaddrinfo", (host, 443, 0, 0, 0))

    def test_one_approval_covers_the_sites_a_task_declares_and_nothing_else(self):
        self.answer = True
        with gate.task("Read model lists", "Model list pages.", hosts=("ollama.com", "HuggingFace.co.", "localhost")):
            self.lookup("ollama.com")
            self.lookup("huggingface.co")
            self.lookup("ollama.com")
            self.assertEqual(len(self.asked), 1)
            self.assertEqual(self.asked[0].target, "huggingface.co and ollama.com")
            self.assertEqual(self.asked[0].purpose, "Read model lists")
            self.assertEqual(self.asked[0].scope, "until this task finishes")
            self.lookup("example.com")
            self.assertEqual([request.target for request in self.asked[1:]], ["example.com"])
            with socket.socket() as connection:
                gate._hook("socket.connect", (connection, ("93.184.215.14", 443)))
            self.assertEqual(len(self.asked), 2)
        # The approval ended with its task.
        self.lookup("huggingface.co")
        self.assertEqual(self.asked[-1].target, "huggingface.co")

    def test_refusing_the_shared_request_blocks_every_declared_site(self):
        with gate.task("Read model lists", hosts=("ollama.com", "huggingface.co")):
            with self.assertRaises(gate.InternetBlocked):
                self.lookup("huggingface.co")
            with self.assertRaises(gate.InternetBlocked):
                self.lookup("ollama.com")
        self.assertEqual(len(self.asked), 1)


class OllamaTests(GateTestCase):
    def chat(self, model):
        body = json.dumps({"model": model, "messages": []}).encode("utf-8")
        gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/chat", body, {}, "POST"))

    def test_downloads_through_ollama_need_approval(self):
        self.answer = True
        body = json.dumps({"model": "llama3:8b", "stream": True}).encode("utf-8")
        gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/pull", body, {}, "POST"))
        gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/pull", body, {}, "POST"))
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0].kind, "Ollama model download")
        self.assertIn("llama3:8b", self.asked[0].target)
        self.assertIn("online registry", self.asked[0].target)

    def test_hugging_face_downloads_name_hugging_face_and_still_need_approval(self):
        self.answer = True
        for name in ("hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M", "HuggingFace.co/org/model-GGUF"):
            body = json.dumps({"model": name, "stream": True}).encode("utf-8")
            gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/pull", body, {}, "POST"))
        self.assertEqual(len(self.asked), 2)
        for request in self.asked:
            self.assertEqual(request.kind, "Ollama model download")
            self.assertIn("from Hugging Face", request.target)
            self.assertIn("Hugging Face", request.sends)
        self.assertTrue(gate.hugging_face_model(" hf.co/a/b "))
        for name in ("llama3:8b", "hf.co", "myhf.co/a/b", None):
            self.assertFalse(gate.hugging_face_model(name))

    def test_cloud_models_need_approval_and_local_models_do_not(self):
        with patch.object(gate, "_remote_model", return_value=False):
            self.chat("qwen3:8b")
        self.assertEqual(self.asked, [])
        self.answer = True
        self.chat("gpt-oss:120b-cloud")
        with patch.object(gate, "_remote_model", return_value=True):
            self.chat("my-alias")
        self.assertEqual([request.kind for request in self.asked], ["Ollama cloud model"] * 2)
        self.assertIn("conversation", self.asked[0].sends)

    def test_requests_whose_model_cannot_be_confirmed_are_blocked(self):
        with patch.object(gate, "_remote_model", return_value=None):
            with self.assertRaisesRegex(gate.InternetBlocked, "could not confirm"):
                self.chat("qwen3:8b")
        with self.assertRaisesRegex(gate.InternetBlocked, "could not read"):
            gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/chat", b"not json", {}, "POST"))
        with self.assertRaisesRegex(gate.InternetBlocked, "could not read"):
            gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/generate", io.BytesIO(b"{}"), {}, "POST"))
        gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/chat", b"{}", {}, "POST"))
        self.assertEqual(self.asked, [])

    def test_a_firewall_lock_refuses_ollama_internet_without_asking(self):
        def locked(kind):
            return "The firewall lock is on." if kind.startswith("Ollama") else None

        with patch.object(gate, "_lock_check", locked):
            with self.assertRaisesRegex(gate.InternetBlocked, "firewall lock is on"):
                gate._hook("urllib.Request", ("http://127.0.0.1:11434/api/pull", b'{"model": "x"}', {}, "POST"))
            with self.assertRaisesRegex(gate.InternetBlocked, "firewall lock is on"):
                self.chat("gpt-oss:120b-cloud")
            self.answer = True
            gate._hook("urllib.Request", ("https://example.com/", None, {}, "GET"))
        self.assertEqual([request.kind for request in self.asked], ["Internet connection"])
        self.assertEqual([decision for decision, _ in self.decisions()],
                         ["blocked: firewall lock", "blocked: firewall lock", "allowed"])

    def test_commands_outside_the_gate_are_logged_and_announced(self):
        with patch.object(gate, "_installed", False):
            gate.note_outside("Developer agent command", "npm install")
        self.assertEqual(gate.recent(), [])
        with patch.object(gate, "_installed", True):
            gate.note_outside("Developer agent command", "npm\ninstall", "Install packages")
        entry = gate.recent()[0]
        self.assertEqual((entry["decision"], entry["target"], entry["purpose"]),
                         ("ran outside the gate", "npm install", "Install packages"))
        self.mocks["_notice"].assert_called_once()
        self.assertIn("cannot check", self.mocks["_notice"].call_args[0][0])

    def response(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    def test_ollama_reports_which_models_run_remotely(self):
        base = "http://127.0.0.1:11434"
        with patch("network_gate.urllib.request.urlopen", side_effect=[
                self.response({"remote_host": "https://ollama.com:443", "remote_model": "gpt-oss:120b"}),
                self.response({"model_info": {}})]) as urlopen:
            self.assertTrue(gate._remote_model(base, "alias"))
            self.assertFalse(gate._remote_model(base, "qwen3:8b"))
            self.assertTrue(gate._remote_model(base, "alias"))
        self.assertEqual(urlopen.call_count, 2)
        missing = urllib.error.HTTPError(base, 404, "Not Found", {}, io.BytesIO(b""))
        with patch("network_gate.urllib.request.urlopen", side_effect=missing):
            self.assertFalse(gate._remote_model(base, "missing"))
        with patch("network_gate.urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            self.assertIsNone(gate._remote_model(base, "offline"))
        self.assertTrue(gate._remote_model(base, "anything:cloud"))


class LinkAndProgramTests(GateTestCase):
    def test_internet_links_need_approval(self):
        for target in ("http://127.0.0.1:8123/index.html", "file:///C:/project/index.html",
                       r"C:\Users\someone\page.html", "/home/someone/page.html"):
            gate._hook("webbrowser.open", (target,))
            gate._hook("os.startfile", (target, "open"))
        self.assertEqual(self.asked, [])
        for target in ("https://ollama.com/library", r"\\files.example\share\doc.txt", "mailto:someone@example.com"):
            self.blocked("webbrowser.open", target)
        self.assertEqual([request.kind for request in self.asked], ["Open outside AI Assist"] * 3)

    def test_programs_that_go_online_ask_first(self):
        with patch.object(gate, "_installed", False):
            self.assertTrue(gate.allow_external("colibri model download", "m1"))
        self.assertEqual(self.asked, [])
        with patch.object(gate, "_installed", True):
            self.assertFalse(gate.allow_external("colibri model download", "m1", "files", purpose="Install m1"))
            self.answer = True
            self.assertTrue(gate.allow_external("colibri model download", "m2", "files", purpose="Install m2"))
        self.assertEqual([(request.target, request.purpose) for request in self.asked],
                         [("m1", "Install m1"), ("m2", "Install m2")])

    def test_linux_without_notify_send_uses_dbus_with_quoted_text(self):
        with patch.object(gate.os, "name", "posix"), patch.object(gate.sys, "platform", "linux"), \
                patch("network_gate.shutil.which", side_effect=lambda tool: "/usr/bin/gdbus" if tool == "gdbus" else None):
            command, environment = gate.notification_command("It's blocked", "C:\\path \\' end")
        self.assertIsNone(environment)
        self.assertEqual(command[:2], ["gdbus", "call"])
        self.assertIn("'It\\'s blocked'", command)
        self.assertIn("'C:\\\\path \\\\\\' end'", command)

    @unittest.skipUnless(os.name == "nt", "Windows notifications")
    def test_notification_text_never_becomes_part_of_the_command(self):
        command, environment = gate.notification_command("Title'; calc", "Body\n$(evil)")
        self.assertNotIn("calc", " ".join(command))
        self.assertEqual(environment["AI_ASSIST_NOTICE_TITLE"], "Title'; calc")
        self.assertEqual(environment["AI_ASSIST_NOTICE_BODY"], "Body $(evil)")


class ListeningTests(GateTestCase):
    """AI Assist itself only accepts connections from this computer."""

    def test_loopback_and_local_sockets_may_listen(self):
        gate._hook("socket.bind", (INET, ("127.0.0.1", 0)))
        gate._hook("socket.bind", (INET, ("127.5.6.7", 8080)))
        gate._hook("socket.bind", (INET6, ("::1", 0, 0, 0)))
        gate._hook("socket.bind", (INET, ("localhost", 0)))
        if hasattr(socket, "AF_UNIX"):
            gate._hook("socket.bind", (SimpleNamespace(family=socket.AF_UNIX), "/tmp/x.sock"))
        self.assertEqual(gate.recent(), [])

    def test_every_interface_and_network_addresses_are_refused_and_logged(self):
        for sock, address, shown in ((INET, ("0.0.0.0", 8765), "0.0.0.0"), (INET, ("", 8765), "every network interface"),
                                     (INET6, ("::", 0, 0, 0), "::"), (INET, ("192.168.1.20", 80), "192.168.1.20"),
                                     (INET, ("example.com", 80), "example.com"),
                                     (SimpleNamespace(family=-1), ("x",), "an address the gate cannot check")):
            with self.subTest(address=address):
                message = self.blocked("socket.bind", sock, address)
                self.assertIn("only accepts connections from this computer", message)
                self.assertEqual(gate.recent()[-1]["decision"], "blocked: listening beyond this computer")
                self.assertEqual(gate.recent()[-1]["target"], shown)
        self.assertEqual(self.asked, [])

    def test_programs_ai_assist_starts_keep_their_own_settings(self):
        with patch.object(gate, "_parent", (1, "token", "colibri")):
            gate._hook("socket.bind", (INET, ("0.0.0.0", 8000)))

    def test_requests_from_the_apps_packages_name_their_module(self):
        namespace = {"gate": gate}
        exec(compile("result = gate._source()", str(HERE / "app_core" / "probe.py"), "exec"), namespace)
        self.assertEqual(namespace["result"], "app_core/probe.py line 1")
        # The private Python in runtime/ is inside the app folder but is not AI Assist's code:
        # its frame is skipped and the app code that called it is named instead.
        exec(compile("result = gate._source()", str(HERE / "runtime" / "python" / "Lib" / "http.py"), "exec"), namespace)
        self.assertTrue(namespace["result"].startswith("test_network_gate.py line "), namespace["result"])

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_a_new_connection_log_is_private(self):
        gate._record("blocked", gate.Request("k", "t", "", "", "", ""))
        self.assertEqual((self.folder / "network.log").stat().st_mode & 0o777, 0o600)

    def test_the_log_keeps_one_line_ending_per_entry(self):
        gate._record("blocked", gate.Request("k", "a", "", "", "", ""))
        gate._record("blocked", gate.Request("k", "b", "", "", "", ""))
        raw = (self.folder / "network.log").read_bytes()
        self.assertNotIn(b"\r\r\n", raw)
        self.assertEqual(len(raw.splitlines()), 2)


class InstalledGateTests(unittest.TestCase):
    def test_the_app_process_cannot_listen_beyond_this_computer(self):
        script = r"""
import json, socket
import network_gate
network_gate._notice = lambda *args: None
network_gate.install()
result = {}
with socket.socket() as local:
    local.bind(("127.0.0.1", 0))
    result["loopback"] = "listening"
for name, host in (("all", "0.0.0.0"), ("empty", "")):
    try:
        with socket.socket() as wide:
            wide.bind((host, 0))
            result[name] = "listening"
    except network_gate.InternetBlocked:
        result[name] = "refused"
print(json.dumps(result))
"""
        with tempfile.TemporaryDirectory() as folder:
            completed = subprocess.run([sys.executable, "-c", script], cwd=HERE, env=dict(os.environ, AI_ASSIST_DATA_DIR=folder),
                                       capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout.strip().splitlines()[-1]),
                         {"loopback": "listening", "all": "refused", "empty": "refused"})

    def test_a_process_with_the_gate_reaches_this_computer_but_not_the_internet(self):
        script = r"""
import http.server, json, socket, threading, urllib.request
import network_gate
network_gate._notice = lambda *args: None
network_gate.install()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
result = {"local": urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5).read().decode()}
try:
    urllib.request.urlopen("http://example.com/", timeout=5)
    result["internet"] = "reached"
except network_gate.InternetBlocked:
    result["internet"] = "blocked"
try:
    socket.socket().connect(("1.1.1.1", 80))
    result["raw"] = "reached"
except network_gate.InternetBlocked:
    result["raw"] = "blocked"
try:
    import asyncio
    result["asyncio"] = "loaded"
except network_gate.InternetBlocked:
    result["asyncio"] = "blocked"
print(json.dumps(result))
"""
        with tempfile.TemporaryDirectory() as folder:
            environment = dict(os.environ, AI_ASSIST_DATA_DIR=folder)
            completed = subprocess.run([sys.executable, "-c", script], cwd=HERE, env=environment,
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            log = [json.loads(line) for line in Path(folder, "network.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result["local"], "ok")
        self.assertEqual((result["internet"], result["raw"]), ("blocked", "blocked"))
        self.assertEqual(result["asyncio"], "blocked" if os.name == "nt" else "loaded")
        self.assertEqual([entry["target"] for entry in log][:2], ["example.com", "1.1.1.1 port 80"])
        self.assertTrue(all(entry["decision"] == "blocked: no approval window" for entry in log))


CHILD = r"""
import asyncio, http.server, json, sys, threading, urllib.request
gate = sys.modules.get("_ai_assist_network_gate")
if gate is not None:
    gate._notice = lambda *args: None
result = {"gate": gate is not None}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
result["local"] = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5).read().decode()

def outcome(error):
    return "blocked" if "not approved" in str(error) else repr(error)

try:
    urllib.request.urlopen("http://gate-test.invalid/", timeout=5)
    result["web"] = "reached"
except OSError as error:
    result["web"] = outcome(error)

async def connect():
    await asyncio.wait_for(asyncio.open_connection("192.0.2.1", 80), 5)

try:
    asyncio.run(connect())
    result["asyncio"] = "reached"
except OSError as error:
    result["asyncio"] = outcome(error)
try:
    import hf_xet
    result["native"] = "loaded"
except ImportError:
    result["native"] = "refused"
print(json.dumps(result))
"""

PARENT = r"""
import json, subprocess, sys
import network_gate
network_gate._notice = lambda *args: None
network_gate.install()
asked = []
network_gate.set_approver(lambda request: asked.append([request.kind, request.target, request.source]) and False)
done = subprocess.run([sys.executable, "-c", sys.argv[1]], env=network_gate.child_environment("test child"),
                      capture_output=True, text=True, timeout=60)
print(json.dumps({"child": done.stdout.strip(), "stderr": done.stderr[-800:], "asked": asked,
                  "log": [[entry["decision"], entry["target"]] for entry in network_gate.recent()]}))
"""


class ChildProgramTests(unittest.TestCase):
    """Python programs AI Assist starts send their internet requests back to the app.
    Internet targets here can never resolve or route (.invalid, 192.0.2.0/24)."""

    def run_python(self, code, *arguments, env=None):
        completed = subprocess.run([sys.executable, "-c", code, *arguments], cwd=HERE, env=env,
                                   capture_output=True, text=True, timeout=120)
        return completed

    def test_a_child_asks_the_app_and_is_blocked_when_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            completed = self.run_python(PARENT, CHILD, env=dict(os.environ, AI_ASSIST_DATA_DIR=folder))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        child = json.loads(report["child"].splitlines()[-1]) if report["child"] else {}
        self.assertEqual(child, {"gate": True, "local": "ok", "web": "blocked", "asyncio": "blocked", "native": "refused"},
                         report["stderr"])
        self.assertEqual([target for _, target, _ in report["asked"]], ["gate-test.invalid", "192.0.2.1 port 80"])
        self.assertTrue(all(source.startswith("test child (process ") for _, _, source in report["asked"]))
        self.assertEqual(report["log"], [["blocked", "gate-test.invalid"], ["blocked", "192.0.2.1 port 80"]])

    def child_environment(self, folder, gate):
        return dict(os.environ, PYTHONPATH=str(HERE / "child_gate"), AI_ASSIST_GATE=gate,
                    AI_ASSIST_GATE_LABEL="orphan", AI_ASSIST_GATE_LOG=str(Path(folder, "network.log")))

    def test_without_the_app_a_child_is_blocked_and_logs_it(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as folder:
            completed = self.run_python(CHILD, env=self.child_environment(folder, f"{port}:token"))
            log = [json.loads(line) for line in Path(folder, "network.log").read_text(encoding="utf-8").splitlines()]
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual((result["web"], result["asyncio"]), ("blocked", "blocked"), completed.stderr)
        self.assertTrue(log and all(entry["decision"] == "blocked: AI Assist is not running to approve it" for entry in log))

    def test_a_child_whose_gate_cannot_start_does_not_run(self):
        with tempfile.TemporaryDirectory() as folder:
            completed = self.run_python("print('ran')", env=self.child_environment(folder, "not-a-port"))
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("ran", completed.stdout)
        self.assertIn("could not start", completed.stderr)

    def test_no_child_environment_without_the_gate(self):
        with patch.object(gate, "_installed", False):
            self.assertIsNone(gate.child_environment("colibri"))


if __name__ == "__main__":
    unittest.main()
