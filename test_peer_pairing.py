"""Tests for opt-in LAN discovery and verified TLS pairing.

Tests make no internet connections and do not expose an inference service.
Loopback TLS tests require the optional 'cryptography' package.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from peer_pairing import (
    DISCOVERY_PORT, MAGIC, PROTOCOL, PairingError, PairingHost, PairingWindow,
    TrustStore, _json_packet, discover, pair_with_host, parse_advertisement,
    permitted_ipv4, verification_code,
)


class DiscoveryTests(unittest.TestCase):
    def test_private_ipv4_only(self):
        for address in ("10.0.0.1", "172.31.1.1", "192.168.1.2"):
            self.assertTrue(permitted_ipv4(address))
        for address in ("127.0.0.1", "8.8.8.8", "192.0.2.2", "host.local",
                        "::1", "255.255.255.255", "224.0.0.1", "0.0.0.0"):
            self.assertFalse(permitted_ipv4(address), address)
        self.assertTrue(permitted_ipv4("127.0.0.1", loopback=True))

    def test_discovery_messages_are_never_trusted(self):
        raw = _json_packet({"magic": MAGIC, "version": PROTOCOL,
                            "node_id": "node_2", "name": "Office PC", "port": 50001})
        candidate = parse_advertisement(raw, "192.168.1.23")
        self.assertEqual(candidate["address"], "192.168.1.23")
        self.assertEqual(candidate["name"], "Office PC")
        self.assertIs(candidate["verified"], False)
        self.assertIsNone(parse_advertisement(raw, "8.8.8.8"))
        self.assertIsNone(parse_advertisement(raw, "127.0.0.1"))
        self.assertIsNone(parse_advertisement(raw * 100, "192.168.1.23"))

    def test_discovery_rejects_invalid_versions_ports_and_fake_ids(self):
        packet = {"magic": MAGIC, "version": PROTOCOL,
                  "node_id": "safe", "port": 12000}
        for key, value in (("version", 2), ("version", True), ("port", "1000"),
                           ("port", 65536), ("node_id", "../bad")):
            with self.subTest(key=key):
                invalid = {**packet, key: value}
                self.assertIsNone(parse_advertisement(_json_packet(invalid), "10.0.0.2"))
        self.assertIsNone(parse_advertisement(b"nonsense", "10.0.0.2"))

    def test_discovery_timeout_validation(self):
        for timeout in (0, -1, 11):
            with self.assertRaises(ValueError):
                discover(timeout=timeout)


class PairingWindowTests(unittest.TestCase):
    def test_short_lived_single_use_pin(self):
        window = PairingWindow()
        pin = window.open()
        self.assertEqual(len(pin), 6)
        self.assertTrue(pin.isdigit())
        self.assertFalse(window.verify_once("123"))
        self.assertTrue(window.verify_once(pin))
        self.assertFalse(window.verify_once(pin))

    def test_three_wrong_attempts_close_window(self):
        window = PairingWindow()
        pin = window.open()
        wrong = "000000" if pin != "000000" else "000001"
        for _ in range(3):
            self.assertFalse(window.verify_once(wrong))
        self.assertFalse(window.verify_once(pin))

    def test_expired_window_denies_correct_pin(self):
        with patch("peer_pairing.time.monotonic", side_effect=[50.0, 52.0]):
            window = PairingWindow()
            pin = window.open(seconds=1)
            self.assertFalse(window.verify_once(pin))


class TrustStoreTests(unittest.TestCase):
    def test_client_and_server_credentials_are_separate_and_revocable(self):
        with tempfile.TemporaryDirectory() as directory:
            trust = TrustStore(directory)
            trust.save_server("client_1", "top-secret")
            self.assertTrue(trust.authenticate_client("client_1", "top-secret"))
            self.assertFalse(trust.authenticate_client("client_1", "wrong"))
            self.assertNotIn("top-secret", (Path(directory) / "paired_clients.json").read_text())
            trust.save_client("server_1", "a" * 64, "another-secret", "192.168.1.5", 50001)
            self.assertEqual(trust.client_record("server_1")["address"], "192.168.1.5")
            trust.revoke("client_1", server=True)
            trust.revoke("server_1")
            self.assertFalse(trust.authenticate_client("client_1", "top-secret"))
            self.assertIsNone(trust.client_record("server_1"))

    def test_reject_public_or_hostname_trust_record(self):
        with tempfile.TemporaryDirectory() as directory:
            trust = TrustStore(directory)
            for host in ("8.8.8.8", "example.com"):
                with self.assertRaises(PairingError):
                    trust.save_client("peer", "a" * 64, "secret", host, 443)


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "Optional TLS identity dependency missing")
class EncryptedPairingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.host = PairingHost("127.0.0.1", self.root / "server")
        self.host.start(advertise=False)
        self.addCleanup(self.host.stop)
        self.client_store = TrustStore(self.root / "client")

    def test_correct_screen_code_and_pin_pair_over_tls(self):
        pin = self.host.window.open()
        peer_id = pair_with_host("127.0.0.1", self.host.port,
                                 verification_code(self.host.fingerprint), pin,
                                 "client_1", self.client_store)
        self.assertEqual(peer_id, self.host.node_id)
        record = self.client_store.client_record(peer_id)
        self.assertEqual(record["fingerprint"], self.host.fingerprint)
        self.assertTrue(self.host.store.authenticate_client("client_1", record["token"]))
        self.assertFalse(self.host.window.verify_once(pin))

    def test_spoofed_fingerprint_stops_before_sending_pin(self):
        pin = self.host.window.open()
        wrong_code = ("F" if self.host.fingerprint[0] != "f" else "E") + self.host.fingerprint[1:16].upper()
        with self.assertRaisesRegex(PairingError, "certificate code differs"):
            pair_with_host("127.0.0.1", self.host.port, wrong_code, pin,
                           "client_1", self.client_store)
        # The correct PIN remains available, because the client never transmitted it.
        self.assertTrue(self.host.window.verify_once(pin))

    def test_wrong_pin_is_rejected_and_no_credentials_saved(self):
        pin = self.host.window.open()
        wrong = "000000" if pin != "000000" else "000001"
        with self.assertRaisesRegex(PairingError, "rejected"):
            pair_with_host("127.0.0.1", self.host.port,
                           verification_code(self.host.fingerprint), wrong,
                           "client_1", self.client_store)
        self.assertIsNone(self.client_store.client_record(self.host.node_id))
        self.assertFalse(self.host.store.authenticate_client("client_1", "anything"))

    def test_public_address_rejected_before_connect(self):
        with self.assertRaisesRegex(PairingError, "non-LAN"):
            pair_with_host("8.8.8.8", 443, "a" * 16, "123456",
                           "client_1", self.client_store)


if __name__ == "__main__":
    unittest.main()
