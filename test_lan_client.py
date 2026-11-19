"""Loopback integration tests for authenticated Mac -> Windows LAN inference.

No models, real network peers, or internet access are required. Real TLS tests
are skipped when the optional cryptography dependency is unavailable.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_core.streaming import StreamStopped
from lan_client import LANInferenceError, PairedLANProvider
from peer_pairing import PairingHost, TrustStore, pair_with_host, verification_code


def free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ValidationTests(unittest.TestCase):
    def test_missing_pairing_fails_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            with patch("lan_client.http.client.HTTPSConnection") as connection:
                with self.assertRaisesRegex(LANInferenceError, "not been paired"):
                    PairedLANProvider("not_paired", "mac_1", store=store)
                connection.assert_not_called()

    def test_saved_peer_id_must_match_pinned_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            store.save_client("wrongid", "a" * 64, "x" * 40, "192.168.1.3", 49381)
            with self.assertRaisesRegex(LANInferenceError, "certificate do not match"):
                PairedLANProvider("wrongid", "mac_1", store=store)

    def test_public_peer_is_never_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrustStore(directory)
            fingerprint = "b" * 64
            peer_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
            store.save_client(peer_id, fingerprint, "x" * 40, "127.0.0.1", 49381)
            record = store._read("paired_servers.json")
            record[peer_id]["address"] = "8.8.8.8"
            from peer_pairing import _atomic_json
            _atomic_json(Path(directory) / "paired_servers.json", record)
            with patch("lan_client.http.client.HTTPSConnection") as connection:
                with self.assertRaisesRegex(LANInferenceError, "private LAN"):
                    PairedLANProvider(peer_id, "mac_1", store=store)
                connection.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "Optional cryptography dependency missing")
class EncryptedInferenceTests(unittest.TestCase):
    def setUp(self):
        from lan_inference import InferenceNode
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.server_directory = base / "windows"
        self.store = TrustStore(base / "mac")
        self.host = PairingHost("127.0.0.1", self.server_directory)
        self.host.start(advertise=False)
        self.addCleanup(self.host.stop)
        pin = self.host.window.open()
        self.peer_id = pair_with_host(
            "127.0.0.1", self.host.port,
            verification_code(self.host.fingerprint), pin, "mac_1", self.store)
        self.node = InferenceNode("127.0.0.1", self.server_directory, port=free_local_port())
        self.node.start()
        self.addCleanup(self.node.stop)
        # Production allows only private LAN IPv4 peers. The test uses
        # loopback deliberately to avoid exposing a listener to the LAN.
        def local_record(peer_id, record):
            return "127.0.0.1", record["fingerprint"], record["token"]

        self.loopback = patch("lan_client.private_peer_record", side_effect=local_record)
        self.loopback.start()
        self.addCleanup(self.loopback.stop)
        self.client = PairedLANProvider(
            self.peer_id, "mac_1", store=self.store, port=self.node.port)

    def test_health_and_models_use_pinned_authenticated_tls(self):
        self.assertTrue(self.client.health()["sharing"])
        with patch("lan_inference.local_models", return_value=["qwen3-coder:30b", "x:cloud"]):
            self.assertEqual(self.client.list_models(), ["qwen3-coder:30b"])

    def test_wrong_identity_stops_before_sending_credentials(self):
        original = self.client.fingerprint
        self.client.fingerprint = "f" * 64
        with self.assertRaisesRegex(LANInferenceError, "certificate changed"):
            self.client.health()
        self.client.fingerprint = original
        self.assertTrue(self.client.health()["sharing"])

    def test_wrong_client_id_cannot_authenticate(self):
        wrong_client = PairedLANProvider(
            self.peer_id, "different_mac", store=self.store, port=self.node.port)
        with self.assertRaisesRegex(LANInferenceError, "HTTP 401"):
            wrong_client.health()

    def test_remote_chat_streams_back_chunks_and_metrics(self):
        upstream = [
            {"message": {"role": "assistant", "content": "Hello "}, "done": False},
            {"message": {"role": "assistant", "content": "from Windows"}, "done": False},
            {"done": True, "eval_count": 2, "eval_duration": 1000000000},
        ]
        raw = b"".join(json.dumps(item).encode() + b"\n" for item in upstream)
        pieces, metrics = [], []
        with patch("lan_inference.model_is_local", return_value=True), patch(
            "lan_inference.urllib.request.urlopen", return_value=io.BytesIO(raw)
        ):
            answer = self.client.stream_chat(
                [{"role": "user", "content": "Hello"}], "qwen3-coder:30b",
                on_chunk=pieces.append, on_metrics=metrics.append)
        self.assertEqual(answer, "Hello from Windows")
        self.assertEqual(pieces, ["Hello ", "from Windows"])
        self.assertEqual(metrics[0]["eval_count"], 2)

    def test_web_chat_service_routes_to_windows_provider_and_saves_on_mac(self):
        from chat_store import ChatStore
        from webui.server import WebWorkspace
        with tempfile.TemporaryDirectory() as folder:
            store = ChatStore(Path(folder) / "mac-chats")
            workspace = WebWorkspace(store, provider_factory=lambda: self.client)
            response = [
                {"message": {"role": "assistant", "content": "RTX says READY"}, "done": False},
                {"done": True, "eval_count": 4, "eval_duration": 1000000000},
            ]
            raw = b"".join(json.dumps(item).encode() + b"\\n" for item in response)
            with patch("lan_inference.local_models", return_value=["qwen3-coder:30b"]), patch(
                "lan_inference.ollama.api_json", return_value={"models": []}
            ), patch("lan_inference.model_is_local", return_value=True), patch(
                "lan_inference.urllib.request.urlopen", return_value=io.BytesIO(raw)
            ):
                self.assertEqual(workspace.models()["engine"], "Paired LAN")
                chat = workspace.service.create("qwen3-coder:30b", backend="Paired LAN")
                events = []
                stop = threading.Event()
                workspace.service.prepare(chat["id"], "Say READY", "qwen3-coder:30b", "Paired LAN")
                workspace.service.send(chat["id"], "Say READY", "qwen3-coder:30b",
                                       "Paired LAN", events.append, stop)
            saved = store.load(chat["id"])
            self.assertEqual(saved["messages"][-1]["content"], "RTX says READY")
            self.assertEqual(events[-1]["type"], "done")

    def test_cancel_before_chat_sends_no_prompt(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(self.client, "_response") as response:
            with self.assertRaises(StreamStopped):
                self.client.stream_chat(
                    [{"role": "user", "content": "secret"}], "qwen3-coder:30b",
                    cancel_event=cancelled)
            response.assert_not_called()

    def test_service_does_not_expose_commands(self):
        with self.assertRaisesRegex(LANInferenceError, "HTTP 404"):
            self.client._response("GET", "/v1/commands")


if __name__ == "__main__":
    unittest.main()
