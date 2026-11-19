"""Unit tests for trusted LAN availability notifications. No real LAN used."""
import hashlib
import http.client
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

from paired_node_watch import (
    AvailabilityTransitions, NodeUnavailable, mac_notification,
    private_peer_record, verify_peer_health, watch_paired_nodes,
)
from peer_pairing import TrustStore


CERT = b"fake-pinned-certificate"
FP = hashlib.sha256(CERT).hexdigest()
ID = hashlib.sha256(FP.encode("ascii")).hexdigest()[:24]
TOKEN = "a" * 48
RECORD = {"fingerprint": FP, "token": TOKEN, "address": "192.168.1.3", "port": 49381}


class TransitionTests(unittest.TestCase):
    def test_two_successful_polls_before_first_online_notice(self):
        obj = AvailabilityTransitions()
        self.assertIsNone(obj.update(ID, False))
        self.assertIsNone(obj.update(ID, True))
        self.assertEqual(obj.update(ID, True), "online")
        for _ in range(10):
            self.assertIsNone(obj.update(ID, True))

    def test_oscillation_does_not_spam(self):
        obj = AvailabilityTransitions()
        for value in (True, False, True, False, True):
            self.assertIsNone(obj.update(ID, value))
        self.assertEqual(obj.update(ID, True), "online")
        self.assertIsNone(obj.update(ID, False))
        self.assertIsNone(obj.update(ID, True))
        self.assertIsNone(obj.update(ID, False))
        self.assertEqual(obj.update(ID, False), "offline")
        self.assertIsNone(obj.update(ID, True))
        self.assertEqual(obj.update(ID, True), "online")

    def test_each_peer_has_independent_state(self):
        obj = AvailabilityTransitions()
        self.assertIsNone(obj.update("a", True))
        self.assertIsNone(obj.update("b", True))
        self.assertEqual(obj.update("a", True), "online")
        self.assertEqual(obj.update("b", True), "online")


class ValidationTests(unittest.TestCase):
    def test_reject_unpaired_public_and_hostname_addresses(self):
        for address in ("8.8.8.8", "example.com", "127.0.0.1", "::1"):
            with self.subTest(address=address), self.assertRaises(NodeUnavailable):
                private_peer_record(ID, {**RECORD, "address": address})

    def test_reject_bad_identity_cert_and_secret(self):
        for peer, record in (
            ("../hidden", RECORD), (ID, {**RECORD, "fingerprint": "x"}),
            (ID, {**RECORD, "token": ""}), (ID, {"address": "192.168.1.3"}),
        ):
            with self.subTest(peer=peer, record=record), self.assertRaises(NodeUnavailable):
                private_peer_record(peer, record)

    def test_invalid_settings_fail_before_network_access(self):
        with patch("paired_node_watch.http.client.HTTPSConnection") as request:
            for client in ("", "../bad", "bad space"):
                with self.assertRaises(NodeUnavailable):
                    verify_peer_health(ID, RECORD, client)
            with self.assertRaises(NodeUnavailable):
                verify_peer_health(ID, RECORD, "mac_1", port=0)
            request.assert_not_called()


class FakeSocket:
    def getpeercert(self, *, binary_form=False):
        return CERT


class FakeResponse:
    status = 200
    def __init__(self, obj=None):
        import json
        self.data = json.dumps(obj or {"node_id": ID, "service": "inference-only",
                                       "sharing": True}).encode()
    def getheader(self, name, default=""):
        return "application/json" if name == "Content-Type" else default
    def read(self, count):
        return self.data[:count]


class FakeConnection:
    def __init__(self, *args, **kwargs):
        self.sock = FakeSocket()
        self.sent = []
        self.response = FakeResponse()
    def connect(self):
        pass
    def request(self, *args, **kwargs):
        self.sent.append((args, kwargs))
    def getresponse(self):
        return self.response
    def close(self):
        pass


class PinningTests(unittest.TestCase):
    def test_success_sends_token_only_after_certificate_check(self):
        conn = FakeConnection()
        with patch("paired_node_watch.http.client.HTTPSConnection", return_value=conn):
            self.assertTrue(verify_peer_health(ID, RECORD, "mac_1"))
        self.assertEqual(conn.sent[0][0], ("GET", "/v1/health"))
        self.assertEqual(conn.sent[0][1]["headers"]["Authorization"], "Bearer " + TOKEN)

    def test_spoofed_certificate_never_receives_token(self):
        conn = FakeConnection()
        conn.sock.getpeercert = lambda **kwargs: b"imposter certificate"
        with patch("paired_node_watch.http.client.HTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(NodeUnavailable, "TLS identity changed"):
                verify_peer_health(ID, RECORD, "mac_1")
        self.assertEqual(conn.sent, [])

    def test_wrong_node_id_is_refused(self):
        conn = FakeConnection()
        conn.response = FakeResponse({"node_id": "imposter", "sharing": True,
                                      "service": "inference-only"})
        with patch("paired_node_watch.http.client.HTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(NodeUnavailable, "identity"):
                verify_peer_health(ID, RECORD, "mac_1")

    def test_cannot_follow_plain_http_or_dns_redirect(self):
        conn = FakeConnection()
        conn.response.status = 302
        with patch("paired_node_watch.http.client.HTTPSConnection", return_value=conn):
            with self.assertRaises(NodeUnavailable):
                verify_peer_health(ID, RECORD, "mac_1")
        self.assertEqual(len(conn.sent), 1)


class WatchTests(unittest.TestCase):
    def test_notification_once_when_online_and_once_when_reconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            store.save_client(ID, FP, TOKEN, "192.168.1.3", 49381)
            stop = threading.Event()
            notifications = []
            status = [True, True, True, False, False, True, True]
            calls = []
            def check(*_args, **_kwargs):
                calls.append(True)
                value = status[len(calls) - 1]
                if len(calls) == len(status):
                    stop.set()
                if not value:
                    raise NodeUnavailable("offline")
                return True
            with patch("paired_node_watch.verify_peer_health", side_effect=check):
                watch_paired_nodes(client_id="mac_1", stop_event=stop,
                                   notify=lambda *args: notifications.append(args),
                                   interval=1, store=store)
            self.assertEqual(len(notifications), 3)
            self.assertIn("PC disponibile", notifications[0][0])
            self.assertIn("non disponibile", notifications[1][0])
            self.assertIn("PC disponibile", notifications[2][0])
            self.assertEqual(sum("non disponibile" in title for title, _ in notifications), 1)
            self.assertTrue(all("nodo" in message.lower() or "dispositivo" in message.lower()
                                for _, message in notifications))

    def test_offline_at_start_never_generates_disconnection_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            store.save_client(ID, FP, TOKEN, "192.168.1.3", 49381)
            stop = threading.Event()
            sent = []
            checks = [False, False, False, False]
            def unavailable(*_args, **_kwargs):
                checks.pop()
                if not checks:
                    stop.set()
                raise NodeUnavailable("Windows sharing off")
            with patch("paired_node_watch.verify_peer_health", side_effect=unavailable):
                watch_paired_nodes(client_id="mac_1", stop_event=stop,
                                   store=store, interval=1,
                                   notify=lambda *args: sent.append(args))
            self.assertEqual(sent, [])

    def test_interrupted_single_probe_does_not_generate_offline_notice(self):
        obj = AvailabilityTransitions()
        obj.update(ID, True)
        self.assertEqual(obj.update(ID, True), "online")
        self.assertIsNone(obj.update(ID, False))
        self.assertIsNone(obj.update(ID, True))
        self.assertIsNone(obj.update(ID, False))
        self.assertEqual(obj.update(ID, False), "offline")
        for _ in range(6):
            self.assertIsNone(obj.update(ID, False))

    def test_watcher_does_nothing_until_explicitly_started(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            stop = threading.Event()
            stop.set()
            with patch("paired_node_watch.verify_peer_health") as check:
                watch_paired_nodes(client_id="mac_1", stop_event=stop, store=store)
            check.assert_not_called()

    def test_mac_notification_uses_arguments_not_interpolated_script(self):
        with patch("paired_node_watch.platform.system", return_value="Darwin"), patch(
            "paired_node_watch.subprocess.run", return_value=Mock(returncode=0)
        ) as run:
            self.assertTrue(mac_notification("Title", 'bad "line" & script'))
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ["bad \"line\" & script", "Title"])
        self.assertNotIn("bad", args[2])


if __name__ == "__main__":
    unittest.main()
