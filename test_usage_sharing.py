"""Paired usage sharing over real TLS on loopback: only paired, token-holding clients
that pinned the host certificate can read CPU/RAM/GPU, and nothing else is exposed."""
import http.client
import ssl
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import peer_pairing
from app_core.compute_values import normalize_peer_rows
from app_core import settings
from peer_pairing import PairingHost, PeerOffline, TrustStore, UsageUnavailable, fetch_usage, pair_with_host, verification_code
import test_webui_app as fixtures

USAGE = {"name": "Studio", "system": "Darwin", "time": 1.0,
         "cpu": {"name": "Apple M2 Ultra", "percent": 37.5},
         "ram": {"total_bytes": 128 * 2 ** 30, "available_bytes": 64 * 2 ** 30},
         "gpus": [{"name": "Apple M2 Ultra GPU", "percent": 12.0, "used_bytes": 2 ** 30, "total_bytes": 96 * 2 ** 30}],
         "loaded": ["qwen3.5:27b"], "chats": ["secret"], "path": "/Users/someone"}


class UsageSharingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.host = PairingHost("127.0.0.1", self.root / "server", usage=lambda: dict(USAGE))
        self.host.start(advertise=False)
        self.addCleanup(self.host.stop)
        self.store = TrustStore(self.root / "client")
        pin = self.host.window.open()
        self.peer = pair_with_host("127.0.0.1", self.host.port, verification_code(self.host.fingerprint), pin, "client1", self.store)
        self.record = self.store.client_record(self.peer)

    def test_paired_client_reads_only_whitelisted_usage(self):
        usage = fetch_usage(self.record, "client1")
        self.assertEqual(usage["cpu"]["percent"], 37.5)
        self.assertEqual(usage["gpus"][0]["percent"], 12.0)
        self.assertEqual(usage["loaded"], ["qwen3.5:27b"])
        self.assertNotIn("chats", usage)
        self.assertNotIn("/Users", repr(usage))

    def test_requests_without_the_right_token_or_client_are_refused(self):
        time.sleep(peer_pairing.USAGE_MIN_INTERVAL)
        for record, client in ((dict(self.record, token="x" * 43), "client1"), (self.record, "stranger")):
            with self.assertRaisesRegex(peer_pairing.PairingError, "HTTP 403"):
                fetch_usage(record, client)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.host.port, timeout=3, context=context)
        conn.request("GET", "/usage"); self.assertEqual(conn.getresponse().status, 403); conn.close()

    def test_a_different_certificate_is_refused_before_the_token_is_sent(self):
        impostor = dict(self.record, fingerprint="0" * 64)
        with patch.object(self.host.store, "authenticate_client", side_effect=AssertionError("token was sent")):
            with self.assertRaisesRegex(peer_pairing.PairingError, "certificate changed"):
                fetch_usage(impostor, "client1")

    def test_rate_limit_and_usage_off_and_offline(self):
        fetch_usage(self.record, "client1")
        with self.assertRaisesRegex(peer_pairing.PairingError, "HTTP 429"):
            fetch_usage(self.record, "client1")
        self.host.usage = None
        with self.assertRaises(UsageUnavailable):
            fetch_usage(self.record, "client1")
        self.host.stop()
        with self.assertRaises(PeerOffline):
            fetch_usage(self.record, "client1", timeout=.5)

    def test_bad_reports_never_become_zero(self):
        clean = peer_pairing.sanitize_usage({"cpu": {"percent": 250}, "ram": {"total_bytes": 1, "available_bytes": 5},
                                             "gpus": [{"percent": float("nan"), "used_bytes": 9, "total_bytes": 1}]})
        self.assertIsNone(clean["cpu"]["percent"])
        self.assertIsNone(clean["ram"]["total_bytes"])
        self.assertEqual((clean["gpus"][0]["percent"], clean["gpus"][0]["used_bytes"]), (None, None))


class ComputeRowsTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_compute_shows_live_usage_for_paired_hosts_only_when_asked(self):
        host = PairingHost("127.0.0.1", self.store.directory / "host", usage=lambda: dict(USAGE))
        host.start(advertise=False)
        self.addCleanup(host.stop)
        directory = self.api._sharing_directory
        directory.mkdir(parents=True, exist_ok=True)
        settings.write_json(directory / "client.json", {"id": "client1"})
        pair_with_host("127.0.0.1", host.port, verification_code(host.fingerprint), host.window.open(), "client1", TrustStore(directory))
        rows = normalize_peer_rows(self.api._peer_status())
        self.assertEqual(rows[0]["state"], "live")
        self.assertEqual(rows[0]["usage"]["cpu"]["percent"], 37.5)
        self.assertNotIn("token", repr(rows))
        host.usage = None
        self.api._peer_usage_time = 0
        self.assertEqual(normalize_peer_rows(self.api._peer_status())[0]["state"], "not sharing usage")


if __name__ == "__main__":
    unittest.main()
