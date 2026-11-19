"""LAN pairing remains manual, gated, cancellable and separate from the desktop API."""
import io
import json
import threading
import unittest
from types import SimpleNamespace
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
        self.assertEqual(self.api._paired_records()["peer"], {"address": "192.168.1.20", "port": 1234})
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
