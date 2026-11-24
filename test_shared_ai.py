"""Shared AI over real TLS on loopback: a paired, token-holding client that pinned the host
certificate can list models and chat with the host's Ollama, and do nothing else."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import peer_inference_contract
import peer_pairing
from peer_pairing import PairingHost, TrustStore, open_shared_ai, pair_with_host, verification_code


class FakeOllama:
    """Ollama on this computer, as far as the shared-AI forwarder can tell."""

    def __init__(self):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.seen.append(("GET", self.path, None))
                if self.path == "/api/tags":
                    self.reply(200, json.dumps({"models": [{"name": "qwen3.5:27b"}]}).encode())
                else:
                    self.reply(200, b'{"version":"0.40.17"}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append(("POST", self.path, body))
                if body.get("model") == "missing:1b":
                    self.reply(404, b'{"error":"model \\"missing:1b\\" not found"}')
                    return
                lines = [{"message": {"content": "Ciao"}, "done": False},
                         {"message": {"content": " Luca"}, "done": False}, {"done": True}]
                self.reply(200, b"".join(json.dumps(line).encode() + b"\n" for line in lines), "application/x-ndjson")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class SharedAITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.stop)
        self.host = PairingHost("127.0.0.1", self.root / "server", ollama=self.ollama.url)
        self.host.start(advertise=False)
        self.addCleanup(self.host.stop)
        self.store = TrustStore(self.root / "client")
        pin = self.host.window.open()
        self.peer = pair_with_host("127.0.0.1", self.host.port, verification_code(self.host.fingerprint), pin, "client1", self.store)
        self.record = self.store.client_record(self.peer)
        # Pairing alone is not enough: this computer allows client1 to use its AI.
        self.host.store.allow_ai("client1")

    def chat(self, model="qwen3.5:27b", **extra):
        return {"request_id": "req_1", "model": model, "messages": [{"role": "user", "content": "Hi"}], **extra}

    def read(self, response):
        try:
            return response.read()
        finally:
            response.close()

    def test_a_paired_device_lists_models_and_chats_with_a_streamed_answer(self):
        tags = json.loads(self.read(open_shared_ai(self.record, "client1", "GET", "/api/tags")))
        self.assertEqual(tags["models"], [{"name": "qwen3.5:27b"}])
        response = open_shared_ai(self.record, "client1", "POST", "/api/chat", self.chat())
        lines = [json.loads(line) for line in self.read(response).splitlines()]
        self.assertEqual("".join(line.get("message", {}).get("content", "") for line in lines), "Ciao Luca")
        # This computer writes Ollama's request itself: model, plain messages, streaming, nothing else.
        self.assertEqual(self.ollama.seen[-1], ("POST", "/api/chat", {
            "model": "qwen3.5:27b", "messages": [{"role": "user", "content": "Hi"}], "stream": True}))

    def test_only_listing_and_chatting_are_shared(self):
        for method, path in (("POST", "/api/pull"), ("DELETE", "/api/delete"), ("POST", "/api/create"), ("POST", "/api/generate")):
            with self.subTest(path=path):
                with self.assertRaisesRegex(peer_pairing.PairingError, "Only listing models and chatting"):
                    open_shared_ai(self.record, "client1", method, path, {"model": "x"})
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.host.port, timeout=5, context=context)
        headers = {"X-AI-Assist-Client": "client1", "Authorization": "Bearer " + self.record["token"],
                   "Content-Type": "application/json"}
        conn.request("POST", "/ollama/api/pull", body=b'{"model":"qwen3.5:27b"}', headers=headers)
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()
        self.assertFalse(any(path == "/api/pull" for _, path, _ in self.ollama.seen))

    def test_cloud_models_are_refused_and_ollama_errors_come_back(self):
        with self.assertRaisesRegex(peer_pairing.PairingError, "Cloud models are not shared"):
            open_shared_ai(self.record, "client1", "POST", "/api/chat", self.chat("gpt-oss:120b-cloud"))
        with self.assertRaisesRegex(peer_pairing.PairingError, "not found"):
            open_shared_ai(self.record, "client1", "POST", "/api/chat", self.chat("missing:1b"))
        self.assertFalse(any(body and body.get("model") == "gpt-oss:120b-cloud" for _, _, body in self.ollama.seen))

    def test_strangers_and_a_wrong_certificate_never_reach_ollama(self):
        for record, client in ((dict(self.record, token="x" * 43), "client1"), (self.record, "stranger")):
            with self.subTest(client=client):
                with self.assertRaisesRegex(peer_pairing.PairingError, "HTTP 403"):
                    open_shared_ai(record, client, "GET", "/api/tags")
        impostor = dict(self.record, fingerprint="0" * 64)
        with patch.object(self.host.store, "authenticate_client", side_effect=AssertionError("token was sent")):
            with self.assertRaisesRegex(peer_pairing.PairingError, "certificate changed"):
                open_shared_ai(impostor, "client1", "GET", "/api/tags")
        self.assertEqual(self.ollama.seen, [])

    def test_only_plain_messages_cross_and_tools_never_do(self):
        for body in (self.chat(tools=[{"type": "function"}]), self.chat(options={"num_ctx": 999999}),
                     dict(self.chat(), messages=[{"role": "tool", "content": "x"}])):
            with self.subTest(body=str(body)[:60]), self.assertRaises(peer_inference_contract.InvalidInferenceRequest):
                open_shared_ai(self.record, "client1", "POST", "/api/chat", body)
        # A client that skips its own check is refused by this computer too.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.host.port, timeout=5, context=context)
        conn.request("POST", "/ollama/api/chat", body=json.dumps(self.chat(tools=[])).encode(), headers={
            "X-AI-Assist-Client": "client1", "Authorization": "Bearer " + self.record["token"], "Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("Only request_id, model and messages", json.loads(response.read())["error"])
        conn.close()
        with self.assertRaises(peer_inference_contract.InvalidInferenceRequest):
            open_shared_ai(self.record, "client1", "POST", "/api/show", {"model": "qwen3.5:27b", "verbose": True})
        self.assertEqual(self.ollama.seen, [])

    def test_malformed_requests_get_a_clear_refusal(self):
        # A client that skips its own check: a non-text role, and JSON nested too deeply to read.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        headers = {"X-AI-Assist-Client": "client1", "Authorization": "Bearer " + self.record["token"],
                   "Content-Type": "application/json"}
        for body, reason in ((json.dumps(dict(self.chat(), messages=[{"role": [], "content": "x"}])).encode(), "roles"),
                             (b"[" * 200000 + b"]" * 200000, "not valid JSON")):
            with self.subTest(reason=reason):
                conn = http.client.HTTPSConnection("127.0.0.1", self.host.port, timeout=5, context=context)
                conn.request("POST", "/ollama/api/chat", body=body, headers=headers)
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn(reason, json.loads(response.read())["error"])
                conn.close()
        self.assertEqual(self.ollama.seen, [])

    def test_a_paired_device_needs_its_own_permission_and_loses_it_when_unpaired(self):
        self.host.store.allow_ai("client1", False)
        with self.assertRaisesRegex(peer_pairing.PairingError, "has not allowed this device"):
            open_shared_ai(self.record, "client1", "GET", "/api/tags")
        self.host.store.allow_ai("client1")
        self.assertTrue(self.host.store.ai_allowed("client1"))
        self.host.store.revoke("client1", server=True)
        self.assertFalse(self.host.store.ai_allowed("client1"))
        with self.assertRaisesRegex(peer_pairing.PairingError, "Pair this device"):
            self.host.store.allow_ai("client1")
        self.assertEqual(self.ollama.seen, [])

    def test_sharing_off_and_ollama_not_running(self):
        self.host.ollama = None
        with self.assertRaisesRegex(peer_pairing.AIUnavailable, "not sharing"):
            open_shared_ai(self.record, "client1", "GET", "/api/tags")
        self.host.ollama = "http://127.0.0.1:9"
        with self.assertRaisesRegex(peer_pairing.PairingError, "Ollama is not running"):
            open_shared_ai(self.record, "client1", "GET", "/api/tags")

    def test_the_host_forwards_only_to_ollama_on_this_computer(self):
        for url in ("http://192.168.1.20:11434", "https://127.0.0.1:11434", "http://example.com:11434"):
            with self.subTest(url=url), self.assertRaisesRegex(peer_pairing.PairingError, "this computer"):
                PairingHost("127.0.0.1", self.root / "other", ollama=url)


if __name__ == "__main__":
    unittest.main()
