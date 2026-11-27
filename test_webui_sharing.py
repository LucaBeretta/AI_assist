"""LAN pairing remains manual, gated, cancellable and separate from the desktop API."""
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

from peer_pairing import TrustStore
import test_webui_app as fixtures
from webui.pairing_worker import serve
from webui.sharing import PairingProcess


class SharingTests(unittest.TestCase):
    setUp = fixtures.ApiTests.setUp

    def finish(self, operation):
        with self.events.condition:
            def result():
                return next((item for item in self.events.items if item.get("job_id") == operation["job_id"]
                             and item["type"] in {"job_done", "job_error", "job_stopped"}), None)
            self.assertTrue(self.events.condition.wait_for(result, 3), self.events.items)
            return result()

    def service(self):
        return Mock(details={"address": "192.168.1.10", "port": 1234, "pin": "123456",
                             "verification_code": "A" * 16, "expires_at": 9999999999}, alive=Mock(return_value=True))

    def test_reading_state_does_not_construct_a_listener_and_enabled_is_not_restored(self):
        with patch("webui.sharing.PairingProcess") as factory:
            self.assertFalse(self.api.sharing_state()["enabled"])
            self.assertFalse(self.api._sharing_directory.exists())
            factory.assert_not_called()
        service = self.service()
        with patch("webui.sharing.network_gate.allow_external", return_value=True), patch("webui.sharing.PairingProcess", return_value=service):
            event = self.finish(self.api.set_sharing(True, "192.168.1.10"))
            self.assertEqual(event["type"], "job_done")
            self.assertTrue(self.api.sharing_state()["enabled"])
            self.api._close()
        service.stop.assert_called_once()
        other = fixtures.Api(self.events, self.store, self.engine, colibri=self.colibri)
        self.addCleanup(other._close)
        self.assertFalse(other.sharing_state()["enabled"])

    def test_blocked_approval_and_invalid_address_never_start_the_service(self):
        with patch("webui.sharing.network_gate.allow_external", return_value=False), patch("webui.sharing.PairingProcess") as factory:
            self.assertFalse(self.api.set_sharing(True, "0.0.0.0")["ok"])
            event = self.finish(self.api.set_sharing(True, "192.168.1.10"))
            self.assertEqual(event["type"], "job_error")
            factory.assert_not_called()
        self.assertFalse(self.api.sharing_state()["enabled"])

    def test_cancel_during_start_stops_the_new_service(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        service = self.service()
        service.start.side_effect = lambda: (started.set(), release.wait(2))
        with patch("webui.sharing.network_gate.allow_external", return_value=True), patch("webui.sharing.PairingProcess", return_value=service):
            operation = self.api.set_sharing(True, "192.168.1.10")
            self.assertTrue(started.wait(1))
            self.api.cancel_job(operation["job_id"])
            release.set()
            self.assertEqual(self.finish(operation)["type"], "job_stopped")
        service.stop.assert_called_once()
        self.assertFalse(self.api.sharing_state()["enabled"])

    def test_trust_tokens_never_enter_the_state_or_compute_metadata(self):
        store = TrustStore(self.api._sharing_directory)
        store.save_client("peer", "a" * 64, "secret-token", "192.168.1.20", 1234)
        state = self.api.sharing_state()
        self.assertNotIn("secret-token", json.dumps(state))
        self.assertNotIn("fingerprint", json.dumps(state))
        self.assertEqual(self.api._paired_records()["peer"], {"address": "192.168.1.20", "port": 1234, "name": ""})
        with patch("webui.sharing.pair_with_host", return_value="peer") as pair, patch("webui.sharing.network_gate.allow_external", return_value=True):
            self.assertEqual(self.finish(self.api.pair_device("192.168.1.20", 1234, "A" * 16, "123456"))["type"], "job_done")
            self.assertEqual(pair.call_args.args[:4], ("192.168.1.20", 1234, "A" * 16, "123456"))

    def test_child_protocol_offers_only_pin_and_close_and_stops_on_invalid_input(self):
        host = Mock(port=1234, fingerprint="a" * 64, window=Mock(open=Mock(return_value="123456")))
        output = io.StringIO()
        serve("192.168.1.10", self.store.directory, io.StringIO('{"command":"new_pin"}\n{"command":"close"}\n'), output, factory=Mock(return_value=host))
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([item["pin"] for item in replies], ["123456", "123456"])
        host.start.assert_called_once(); host.stop.assert_called_once()
        host.reset_mock(); output = io.StringIO()
        serve("192.168.1.10", self.store.directory, io.StringIO('{"command":"run","text":"bad"}\n'), output, factory=Mock(return_value=host))
        self.assertFalse(json.loads(output.getvalue().splitlines()[-1])["ok"])
        host.stop.assert_called_once()

    def test_pairing_process_refuses_to_run_without_child_gate(self):
        process = PairingProcess("192.168.1.10", self.store.directory)
        with patch("webui.sharing.network_gate.child_environment", return_value=None), patch("webui.sharing.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "gate"):
                process.start()
            spawn.assert_not_called()

    def test_child_launch_keeps_gate_environment_and_stop_closes_private_pipes(self):
        packet = {"ok": True, "address": "192.168.1.10", "port": 1234, "pin": "123456",
                  "verification_code": "A" * 16, "expires_at": 9999999999, "token": "must-not-escape"}
        child = Mock(stdout=io.StringIO(json.dumps(packet) + "\n"), stdin=Mock(), poll=Mock(return_value=None))
        service = PairingProcess("192.168.1.10", self.store.directory)
        environment = {"AI_ASSIST_GATE": "1234:parent-token", "PYTHONPATH": "child_gate"}
        with patch("webui.sharing.network_gate.child_environment", return_value=environment), patch("webui.sharing.subprocess.Popen", return_value=child) as spawn:
            service.start()
            self.assertNotIn("token", service.details)
            arguments = spawn.call_args.args[0]
            self.assertEqual(arguments[1], "-u")
            self.assertTrue(arguments[2].endswith("pairing_worker.py"))
            self.assertEqual(spawn.call_args.kwargs["env"]["AI_ASSIST_GATE"], "1234:parent-token")
            self.assertEqual(spawn.call_args.kwargs["env"]["PYTHONPATH"], "child_gate")
            service.stop()
        child.stdin.write.assert_called_once_with('{"command":"close"}\n')
        child.stdin.close.assert_called_once()
        child.wait.assert_called_once_with(timeout=2)
        self.assertEqual(service.details, {})


class SharedAiSwitchTests(unittest.TestCase):
    setUp = fixtures.ApiTests.setUp
    finish = SharingTests.finish
    service = SharingTests.service

    def test_only_ollama_on_this_computer_can_be_shared(self):
        from webui.sharing import local_ollama
        self.assertEqual(local_ollama("http://localhost:11434"), "http://127.0.0.1:11434")
        self.assertEqual(local_ollama("http://127.0.0.1:12000/"), "http://127.0.0.1:12000")
        self.assertEqual(local_ollama("http://127.0.0.1"), "http://127.0.0.1:11434")
        for url in ("http://192.168.1.30:11434", "https://ollama.com", "", None, "http://[::1]:11434x", "file:///etc"):
            self.assertIsNone(local_ollama(url), url)

    def test_ai_sharing_is_its_own_choice_and_names_ai_answers_in_the_approval(self):
        service = self.service()
        with patch("webui.sharing.network_gate.allow_external", return_value=True) as approve, \
                patch("webui.sharing.PairingProcess", return_value=service) as factory:
            self.assertEqual(self.finish(self.api.set_sharing(True, "192.168.1.10"))["type"], "job_done")
            self.assertEqual(factory.call_args.args[3], None)  # pairing alone shares no AI
            self.assertFalse(self.api.sharing_state()["ai"])
            self.assertNotIn("AI answers", approve.call_args.args[2])
            service.share_usage, service.share_ai = False, None
            self.assertEqual(self.finish(self.api.set_sharing(True, "192.168.1.10", False, True))["type"], "job_done")
            self.assertEqual(factory.call_args.args[3], "http://127.0.0.1:11434")
            self.assertIn("AI answers", approve.call_args.args[2])
            self.assertTrue(self.api.sharing_state()["ai"])
            self.assertEqual(service.stop.call_count, 1)  # restarted so the new choice takes effect

    def test_ai_sharing_refuses_an_ollama_on_another_computer(self):
        self.engine.ollama_url = lambda: "http://192.168.1.30:11434"
        with patch("webui.sharing.PairingProcess") as factory:
            result = self.api.set_sharing(True, "192.168.1.10", False, True)
        self.assertFalse(result["ok"])
        self.assertIn("this computer", result["error"])
        factory.assert_not_called()

    def test_each_paired_device_needs_its_own_permission(self):
        store = TrustStore(self.api._sharing_directory)
        self.assertFalse(self.api.allow_shared_ai("mac", True)["ok"])  # not paired yet
        store.save_server("mac", "secret-token")
        store.save_server("laptop", "other-token")
        self.assertEqual(self.api.sharing_state()["clients"], [{"id": "laptop", "name": "", "ai": False}, {"id": "mac", "name": "", "ai": False}])
        self.assertEqual(self.api.allow_shared_ai("mac", True)["clients"], [{"id": "laptop", "name": "", "ai": False}, {"id": "mac", "name": "", "ai": True}])
        self.assertTrue(store.ai_allowed("mac"))
        self.assertNotIn("token", json.dumps(self.api.sharing_state()))
        self.api.allow_shared_ai("mac", False)
        self.assertFalse(store.ai_allowed("mac"))
        for bad in (("../mac", True), ("mac", "yes"), (None, True)):
            self.assertFalse(self.api.allow_shared_ai(*bad)["ok"])

    def test_worker_shares_ollama_only_when_asked(self):
        host = Mock(port=1234, fingerprint="a" * 64, window=Mock(open=Mock(return_value="123456")))
        for share_ai, expected in ((None, None), ("http://127.0.0.1:11434", "http://127.0.0.1:11434")):
            factory = Mock(return_value=host)
            serve("192.168.1.10", self.store.directory, io.StringIO('{"command":"close"}\n'), io.StringIO(), factory=factory,
                  share_ai=share_ai)
            self.assertEqual(factory.call_args.kwargs.get("ollama"), expected)
        child = Mock(stdout=io.StringIO(json.dumps({"ok": True, "address": "192.168.1.10", "port": 1, "pin": "1",
                                                    "verification_code": "A" * 16, "expires_at": 1}) + "\n"),
                     stdin=Mock(), poll=Mock(return_value=None))
        process = PairingProcess("192.168.1.10", self.store.directory, share_ai="http://127.0.0.1:11434")
        with patch("webui.sharing.network_gate.child_environment", return_value={}), \
                patch("webui.sharing.subprocess.Popen", return_value=child) as spawn:
            process.start()
            arguments = spawn.call_args.args[0]
            self.assertEqual(arguments[arguments.index("--share-ai") + 1], "http://127.0.0.1:11434")
            process.stop()
