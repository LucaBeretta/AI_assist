"""Exercise provider readers against local sockets, with no model or window."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_core import runtime
from chat_store import ChatStore
from colibri_backend import ColibriClient
from test_webui_app import Events
from webui.app import Api


class StreamHTTPTests(unittest.TestCase):
    def setUp(self):
        self.disconnected = threading.Event()
        self.started = threading.Event()
        self.silent = False
        self.defer_headers = False
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                try:
                    if not case.defer_headers:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-ndjson")
                        self.end_headers()
                    if not case.silent:
                        self.wfile.write((json.dumps({"message": {"content": "first "}, "done": False}) + "\n").encode())
                        self.wfile.flush()
                    case.started.set()
                    readable, _, _ = select.select([self.connection], [], [], 3)
                    if readable and self.connection.recv(1) == b"":
                        case.disconnected.set()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    case.disconnected.set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(Path(folder.name))
        self.events = Events()
        self.api = Api(self.events, self.store, runtime)
        self.addCleanup(self.api._close)
        self.host = patch("app_core.runtime.ollama_url", return_value=f"http://127.0.0.1:{self.server.server_port}")
        self.host.start()
        self.addCleanup(self.host.stop)

    def test_stop_disconnects_real_socket_and_saves_only_accepted_text(self):
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "test", "fake:7b", 4096)["stream_id"]
        self.events.wait("token", stream)
        self.api.stop(stream)
        self.assertTrue(self.disconnected.wait(2))
        stopped = self.events.wait("stopped", stream)
        self.assertEqual(stopped["text"], "first ")
        self.assertEqual(self.store.load(chat)["messages"][-1]["content"], "first ")
        self.assertFalse(any(e["type"] == "done" for e in self.events.items))

    def test_stop_disconnects_a_silent_reader_before_the_first_token(self):
        self.silent = True
        chat = self.api.new_chat()["chat"]["id"]
        for defer_headers in (True, False):
            with self.subTest(defer_headers=defer_headers):
                self.defer_headers = defer_headers
                self.started.clear()
                self.disconnected.clear()
                stream = self.api.send(chat, "test", "fake:7b", None)["stream_id"]
                self.assertTrue(self.started.wait(2))
                self.api.stop(stream)
                self.assertTrue(self.disconnected.wait(2))
                self.events.wait("stopped", stream)
        self.assertEqual([item["role"] for item in self.store.load(chat)["messages"]], ["user", "user"])

    def test_stop_disconnects_a_silent_colibri_stream(self):
        self.silent = True
        client = ColibriClient(f"http://127.0.0.1:{self.server.server_port}/v1")
        chat = self.api.new_chat()["chat"]["id"]
        with patch.object(self.api._colibri, "prepare", return_value=client):
            stream = self.api.send(chat, "test", "fake:7b", None, "Colibri")["stream_id"]
            self.assertTrue(self.started.wait(2))
            self.api.stop(stream)
            self.assertTrue(self.disconnected.wait(2))
            self.events.wait("stopped", stream)


if __name__ == "__main__":
    unittest.main()
