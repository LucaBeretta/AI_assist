"""Human internet approvals are serialized, timed in Python and fail closed."""
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import network_gate
import test_webui_app as fixtures
from webui.internet import Approvals, CONFIRM_SECONDS


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.events = fixtures.Events()
        self.now = 10
        self.manager = Approvals(self.events.emit, lambda: self.now)
        self.addCleanup(self.manager.close)
        self.results = []

    def request(self, target="example.com"):
        value = network_gate.Request("Connection", target, "Read the catalog", "Model names", "test", "this task")
        thread = threading.Thread(target=lambda: self.results.append(self.manager.approve(value)))
        thread.start()
        self.addCleanup(thread.join, 2)
        self.events.wait("internet_approval")
        return thread

    def test_two_steps_and_full_server_countdown_are_required(self):
        thread = self.request()
        identity = self.manager.pending()["approval_id"]
        with self.assertRaises(ValueError):
            self.manager.respond(identity, "allow")
        self.manager.respond(identity, "continue")
        self.assertEqual(self.manager.pending()["stage"], 2)
        with self.assertRaises(ValueError):
            self.manager.respond(identity, "allow")
        self.assertTrue(thread.is_alive())
        self.now += CONFIRM_SECONDS
        self.manager.respond(identity, "allow")
        thread.join(2)
        self.assertEqual(self.results, [True])
        with self.assertRaises(ValueError):
            self.manager.respond(identity, "allow")

    def test_block_close_and_delivery_failure_never_allow(self):
        thread = self.request()
        self.manager.respond(self.manager.pending()["approval_id"], "block")
        thread.join(2)
        self.assertEqual(self.results, [False])
        thread = self.request()
        self.manager.close(); thread.join(2)
        self.assertEqual(self.results, [False, False])
        broken = Approvals(lambda *args, **kwargs: False)
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(broken.approve(network_gate.Request("x", "x", "x", "x", "x", "x"))))
        thread.start(); thread.join(2)
        self.assertEqual(outcomes, [False])

    def test_requests_are_presented_one_at_a_time_and_ui_thread_never_waits(self):
        first = self.request("first")
        identity = self.manager.pending()["approval_id"]
        second = self.request("second")
        self.assertEqual(self.manager.pending()["request"]["target"], "first")
        self.assertEqual(sum(e["type"] == "internet_approval" for e in self.events.items), 1)
        self.manager.respond(identity, "block"); first.join(2)
        self.assertEqual(self.manager.pending()["request"]["target"], "second")
        self.manager.close(); second.join(2)
        self.assertFalse(self.manager.approve(network_gate.Request("x", "x", "x", "x", "x", "x")))

    def test_installed_gate_leaves_loopback_alone_and_blocks_pull_before_transport(self):
        script = r'''
import faulthandler
faulthandler.dump_traceback_later(50, exit=True)  # if it stalls on a slow machine, show where
import network_gate as gate
gate.install()
gate._notice = lambda *args, **kwargs: None
import json, tempfile, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from webui.internet import Approvals
seen, failures, ready = [], [], threading.Event()
def emit(kind, **data):
    if kind == "internet_approval": ready.set()
manager = Approvals(emit)
gate.set_approver(manager.approve)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        seen.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        seen.append(self.path); self.send_response(200); self.end_headers()
with tempfile.TemporaryDirectory() as folder:
    gate.install(folder)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = "http://127.0.0.1:" + str(server.server_port)
    assert urllib.request.urlopen(host + "/local", timeout=20).read() == b"ok"
    assert manager.pending() is None
    def pull():
        try:
            urllib.request.urlopen(urllib.request.Request(host + "/api/pull", data=b'{"model":"test"}'), timeout=20)
        except gate.InternetBlocked:
            failures.append("blocked")
    # Daemon: if anything below fails, the error is reported instead of the thread keeping the process alive.
    thread = threading.Thread(target=pull, daemon=True); thread.start()
    assert ready.wait(10), "no approval request within 10 s"
    manager.respond(manager.pending()["approval_id"], "block")
    thread.join(10)
    assert failures == ["blocked"] and seen == ["/local"], (failures, seen)
    assert gate.recent()[-1]["decision"] == "blocked"
    manager.close(); server.shutdown(); server.server_close()
'''
        # Hosted CI machines can be several times slower than a desktop; report output if it still stalls.
        try:
            result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            self.fail(f"The gate script stalled.\nstdout: {error.stdout}\nstderr: {error.stderr}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PrivacyApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_unapproved_deployment_never_invokes_the_cli(self):
        project = self.store.directory / "project"; project.mkdir()
        (project / "index.html").write_text("<h1>Example</h1>", encoding="utf-8")
        with patch("webui.parity.network_gate.allow_external", return_value=False), patch("webui.parity.vercel_deploy.deploy_project") as deploy:
            self.assertTrue(self.api.deploy_vercel(str(project), "project")["ok"])
            self.events.wait("job_error")
            deploy.assert_not_called()

    def test_invalid_firewall_changes_and_answers_are_plain_errors(self):
        self.assertFalse(self.api.change_firewall("unknown")["ok"])
        self.assertFalse(self.api.change_firewall("lift", 7)["ok"])
        self.assertFalse(self.api.respond_internet("unknown", "allow")["ok"])
