"""The AI network map and the choice of computer, with fake peers and one real TLS peer."""

import http.client
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_network
import compute_routing as routing
import peer_pairing
from peer_pairing import PairingHost, TrustStore, pair_with_host, verification_code
from test_shared_ai import FakeOllama

GIB = 2 ** 30
STUDIO = {"address": "192.168.1.20", "port": 5000, "token": "t" * 43, "fingerprint": "a" * 64}
LAPTOP = {"address": "192.168.1.31", "port": 5000, "token": "t" * 43, "fingerprint": "b" * 64}
PI = {"address": "192.168.1.40", "port": 5000, "token": "t" * 43, "fingerprint": "c" * 64}
SCAN = [{"address": "192.168.1.7", "name": "This Mac", "this": True, "ai_assist": True, "ollama": "0.40.17"},
        {"address": "192.168.1.20", "name": "Studio PC", "ai_assist": True, "ollama": "0.40.17"},
        {"address": "192.168.1.50", "name": "Old desktop", "ollama": "0.39.0"},
        {"address": "192.168.1.60", "name": "Printer"}]


def fake_models(record, client_id):
    return {"192.168.1.20": (("deepseek-r1:32b", "qwen3.5:27b"), "Ready"),
            "192.168.1.31": ((), "Not sharing its AI"),
            "192.168.1.40": (None, "Offline")}[record["address"]]


def fake_usage(record, client_id):
    if record["address"] == "192.168.1.31":
        raise peer_pairing.UsageUnavailable("off")
    return {"cpu": {"percent": 12.0}, "ram": {"total_bytes": 64 * GIB, "available_bytes": 40 * GIB},
            "gpus": [{"name": "NVIDIA GeForce RTX 4090", "total_bytes": 24 * GIB, "used_bytes": 4 * GIB}]}


def network(**extra):
    return ai_network.survey(["qwen3.5:9b"], {"ram": {"total_bytes": 16 * GIB, "available_bytes": 8 * GIB}},
                             {"studio": STUDIO, "laptop": LAPTOP, "pi": PI}, "mac1", scanned=SCAN,
                             fetch_usage=fake_usage, fetch_models=fake_models, **extra)


class MapTests(unittest.TestCase):
    def test_the_map_shows_each_computer_in_plain_words(self):
        nodes = {node.node_id: node for node in network().nodes}
        self.assertEqual(nodes["this-computer"].status, "Ready")
        studio = nodes["peer-studio"]
        self.assertEqual((studio.name, studio.status, studio.models), ("Studio PC", "Ready", ("deepseek-r1:32b", "qwen3.5:27b")))
        self.assertEqual((studio.gpu, studio.gpu_free_bytes), ("NVIDIA GeForce RTX 4090", 20 * GIB))
        self.assertEqual((nodes["peer-laptop"].status, nodes["peer-laptop"].shares_ai), ("Not sharing its AI", False))
        self.assertEqual((nodes["peer-pi"].online, nodes["peer-pi"].status), (False, "Offline"))

    def test_a_peer_that_stalls_or_sends_an_odd_list_does_not_break_the_map(self):
        class Reply:
            def __init__(self, data=None, error=None):
                self.data, self.error = data, error

            def read(self, _limit):
                if self.error:
                    raise self.error
                return self.data

            def close(self):
                pass

        cases = ((Reply(error=TimeoutError("timed out")), (None, "Offline")),
                 (Reply(error=http.client.IncompleteRead(b"{")), (None, "Offline")),
                 (Reply(b'{"models": 5}'), ((), "Shares its AI but has no models")),
                 (Reply(b"[" * 200000 + b"]" * 200000), ((), "Sent an unreadable model list")))
        for reply, expected in cases:
            with self.subTest(expected=expected), patch("peer_pairing.open_shared_ai", return_value=reply):
                self.assertEqual(ai_network.peer_models(STUDIO, "mac1"), expected)

    def test_unpaired_ai_computers_from_the_scan_are_offered_to_join(self):
        self.assertEqual([device["name"] for device in network().can_join], ["Old desktop"])


class ChoiceTests(unittest.TestCase):
    def test_local_is_the_default_and_never_leaves_this_computer(self):
        choice = ai_network.choose(network(), "qwen3.5:9b")
        self.assertEqual(choice.node.node_id, "this-computer")
        self.assertIn("stays here", choice.explanation)
        with self.assertRaisesRegex(routing.RoutingError, "This device cannot run"):
            ai_network.choose(network(), "qwen3.5:27b")

    def test_auto_uses_a_paired_computer_that_shares_the_model_and_says_so(self):
        choice = ai_network.choose(network(), "qwen3.5:27b", mode="auto")
        self.assertEqual(choice.node.name, "Studio PC")
        self.assertIn("Studio PC on your home network", choice.explanation)
        self.assertIn("encrypted", choice.explanation)

    def test_computers_that_do_not_share_or_are_offline_are_never_chosen(self):
        for model, pinned in (("qwen3.5:27b", "peer-laptop"), ("qwen3.5:27b", "peer-pi")):
            with self.subTest(pinned=pinned), self.assertRaisesRegex(routing.RoutingError, "unavailable"):
                ai_network.choose(network(), model, mode="pinned", pinned_node_id=pinned)
        with self.assertRaisesRegex(routing.RoutingError, "No authorized device"):
            ai_network.choose(network(), "llama-not-anywhere:7b", mode="auto")

    def test_auto_prefers_this_computer_unless_a_peer_is_measured_much_faster(self):
        both = ai_network.survey(["qwen3.5:27b"], {}, {"studio": STUDIO}, "mac1", scanned=SCAN,
                                 fetch_usage=fake_usage, fetch_models=fake_models)
        self.assertEqual(ai_network.choose(both, "qwen3.5:27b", mode="auto").node.node_id, "this-computer")
        measured = ai_network.with_speed(ai_network.with_speed(both, "this-computer", 9.0), "peer-studio", 41.0)
        self.assertEqual(ai_network.choose(measured, "qwen3.5:27b", mode="auto").node.node_id, "peer-studio")
        close = ai_network.with_speed(measured, "peer-studio", 10.0)
        self.assertEqual(ai_network.choose(close, "qwen3.5:27b", mode="auto").node.node_id, "this-computer")


class RealPeerTests(unittest.TestCase):
    """peer_models over real pinned TLS and PIN pairing, against a stand-in Ollama."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.stop)
        self.host = PairingHost("127.0.0.1", root / "server", ollama=self.ollama.url)
        self.host.start(advertise=False)
        self.addCleanup(self.host.stop)
        store = TrustStore(root / "client")
        peer = pair_with_host("127.0.0.1", self.host.port, verification_code(self.host.fingerprint),
                              self.host.window.open(), "mac1", store)
        self.record = store.client_record(peer)

    def test_a_paired_computer_reports_its_real_state(self):
        self.assertEqual(ai_network.peer_models(self.record, "mac1"), ((), "Hasn't allowed this computer to use its AI"))
        self.host.store.allow_ai("mac1")
        self.assertEqual(ai_network.peer_models(self.record, "mac1"), (("qwen3.5:27b",), "Ready"))
        self.host.ollama = None
        self.assertEqual(ai_network.peer_models(self.record, "mac1"), ((), "Not sharing its AI"))
        self.host.stop()
        self.assertEqual(ai_network.peer_models(self.record, "mac1", timeout=1), (None, "Offline"))


if __name__ == "__main__":
    unittest.main()
