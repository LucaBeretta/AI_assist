"""Chatting from this computer with a paired computer's shared AI, end to end over real TLS on loopback."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import test_webui_app
from app_core import shared_ai
from peer_pairing import PairingHost, TrustStore, pair_with_host, this_device_name, verification_code
from test_shared_ai import FakeOllama


def pair(root, client_directory, ollama_url, allow=True):
    host = PairingHost("127.0.0.1", root / "server", ollama=ollama_url)
    host.start(advertise=False)
    client_directory.mkdir(parents=True, exist_ok=True)
    (client_directory / "client.json").write_text(json.dumps({"id": "mac1"}), encoding="utf-8")
    peer = pair_with_host("127.0.0.1", host.port, verification_code(host.fingerprint), host.window.open(), "mac1",
                          TrustStore(client_directory))
    if allow:
        host.store.allow_ai("mac1")
    return host, peer


class PlainMessagesTests(unittest.TestCase):
    def test_only_text_turns_travel_and_the_newest_question_always_stays(self):
        messages = [{"role": "system", "content": "Be brief."}, {"role": "system", "content": "Notes."},
                    {"role": "user", "content": "Hi", "attachments": [{"name": "a.txt"}]},
                    {"role": "assistant", "content": "Hello", "thinking": "..."}, {"role": "tool", "content": "x"}]
        self.assertEqual(shared_ai.plain_messages(messages), [
            {"role": "system", "content": "Be brief.\n\nNotes."}, {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"}])
        long = [{"role": "user", "content": f"question {index}"} for index in range(200)]
        kept = shared_ai.plain_messages(long)
        self.assertEqual(len(kept), 64)
        self.assertEqual(kept[-1]["content"], "question 199")
        big = [{"role": "user", "content": "x" * 60000} for _ in range(40)]
        self.assertLess(len(json.dumps(shared_ai.plain_messages(big))), 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "Pictures stay"):
            shared_ai.plain_messages([{"role": "user", "content": "Look", "images": ["AAAA"]}])
        self.assertEqual(shared_ai.peer_of("peer:abc"), "abc")
        self.assertIsNone(shared_ai.peer_of("Ollama"))


class SharedAIClientTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.stop)
        self.client = self.root / "client"
        self.host, self.peer = pair(self.root, self.client, self.ollama.url)
        self.addCleanup(self.host.stop)
        self.shared = shared_ai.SharedAI(self.client)

    def test_lists_the_shared_models_and_streams_an_answer(self):
        [computer] = self.shared.computers()
        self.assertEqual((computer["state"], computer["models"], computer["backend"]),
                         ("ready", ["qwen3.5:27b"], "peer:" + self.peer))
        chunks, metrics = [], []
        answer = self.shared.chat(self.peer, [{"role": "user", "content": "Hi"}], "qwen3.5:27b",
                                  on_chunk=chunks.append, on_metrics=metrics.append)
        self.assertEqual((answer, chunks), ("Ciao Luca", ["Ciao", " Luca"]))
        self.assertEqual(len(metrics), 1)
        sent = self.ollama.seen[-1][2]
        self.assertEqual(sent["messages"], [{"role": "user", "content": "Hi"}])

    def test_states_say_why_a_computer_cannot_answer(self):
        self.host.store.allow_ai("mac1", False)
        self.assertEqual(self.shared.computers()[0]["state"], "refused")
        self.host.stop()
        self.assertEqual(self.shared.computers()[0]["state"], "offline")
        with self.assertRaisesRegex(ValueError, "no longer paired"):
            self.shared.chat("someone-else", [{"role": "user", "content": "Hi"}], "qwen3.5:27b")

    def test_ollama_errors_reach_the_person(self):
        with self.assertRaisesRegex(Exception, "not found"):
            self.shared.chat(self.peer, [{"role": "user", "content": "Hi"}], "missing:1b")


class SharedAIChatApiTests(unittest.TestCase):
    """A chat set to a paired computer streams its answer through the normal chat events."""

    def setUp(self):
        test_webui_app.ApiTests.setUp(self)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.stop)
        self.host, self.peer = pair(Path(self.store.directory), Path(self.store.directory) / "pairing", self.ollama.url)
        self.addCleanup(self.host.stop)

    def test_a_chat_runs_on_the_paired_computer(self):
        backend = "peer:" + self.peer
        self.assertEqual(self.api.shared_ai_models()["computers"][0]["models"], ["qwen3.5:27b"])
        chat = self.api.new_chat()["chat"]["id"]
        self.assertFalse(self.api.set_chat_model(chat, "qwen3.5:27b", "peer:not-paired")["ok"])
        self.assertTrue(self.api.set_chat_model(chat, "qwen3.5:27b", backend)["ok"])
        self.assertTrue(self.api.send(chat, "Hi", "qwen3.5:27b", None, backend)["ok"])
        done = self.events.wait("done")
        self.assertEqual(done["text"] if "text" in done else self.store.load(chat)["messages"][-1]["content"], "Ciao Luca")
        self.assertEqual(self.store.load(chat)["backend"], backend)
        self.assertEqual(self.calls, [])  # this computer's own Ollama was never asked
        self.assertEqual(self.api.status_snapshot(chat)["engine"], f"paired computer {(this_device_name() or '127.0.0.1').lower()} ready")

    def test_pictures_are_refused_for_a_paired_computer(self):
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "qwen3.5:27b", "peer:" + self.peer)
        result = self.api.attach(chat, "photo.png", "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        self.assertFalse(result["ok"])
        self.assertIn("Pictures stay", result["error"])


if __name__ == "__main__":
    unittest.main()


class DeviceNameTests(unittest.TestCase):
    """Paired computers show people's names for them; the names never become identity or reach discovery."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def host(self):
        host = PairingHost("127.0.0.1", self.root / "server")
        host.start(advertise=False)
        self.addCleanup(host.stop)
        return host

    def test_both_computers_learn_each_others_name_when_pairing(self):
        host, client = self.host(), TrustStore(self.root / "client")
        with patch("peer_pairing.socket.gethostname", return_value="Studio-PC.local"):
            peer = pair_with_host("127.0.0.1", host.port, verification_code(host.fingerprint), host.window.open(), "mac1",
                                  client, "Luca's MacBook\x07")
        self.assertEqual(client.client_record(peer)["name"], "Studio-PC")
        self.assertEqual(host.store.client_name("mac1"), "Luca's MacBook")  # control characters dropped
        # A new address after rediscovery keeps the name; unpairing forgets it.
        record = client.client_record(peer)
        client.save_client(peer, record["fingerprint"], record["token"], "127.0.0.1", 4321)
        self.assertEqual(client.client_record(peer)["name"], "Studio-PC")
        host.store.revoke("mac1", server=True)
        self.assertEqual(host.store.client_name("mac1"), "")

    def test_an_older_client_without_a_name_still_pairs(self):
        host, client = self.host(), TrustStore(self.root / "client")
        peer = pair_with_host("127.0.0.1", host.port, verification_code(host.fingerprint), host.window.open(), "old1", client)
        self.assertTrue(host.store.authenticate_client("old1", client.client_record(peer)["token"]))
        self.assertEqual(host.store.client_name("old1"), "")

    def test_names_are_short_printable_labels_and_discovery_keeps_the_generic_name(self):
        from peer_pairing import device_name
        self.assertEqual(device_name("x" * 100), "x" * 48)
        self.assertLessEqual(len(device_name("\U0001F600" * 48).encode("utf-8")), 96)
        self.assertEqual(device_name("  Mac\nmini  "), "Macmini")
        for value in (None, 5, ["a"], "\x00\x01"):
            self.assertEqual(device_name(value), "")
        self.assertEqual(PairingHost("127.0.0.1", self.root / "other").name, "AI Assist")
