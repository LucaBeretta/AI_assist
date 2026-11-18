"""Exercise the real Ollama reader against local sockets; no model or window."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import tempfile
import threading
import unittest
from unittest.mock import patch

import AIassist
from chat_store import ChatStore
from test_webui_app import Events
from webui.app import Api


class StreamHTTPTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.disconnected = threading.Event()
        self.started = threading.Event()
        self.silent = False
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                def send(text):
                    self.wfile.write((json.dumps({"message": {"content": text}, "done": False}) + "\n").encode())
                    self.wfile.flush()
                try:
                    if not case.silent:
                        send("first ")
                    case.started.set()
                    case.release.wait(4)
                    send("discarded")
                    # The callback raises StreamStopped on this next chunk.
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
        self.addCleanup(self.release.set)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(Path(folder.name))
        self.events = Events()
        self.api = Api(self.events, self.store, AIassist)
        self.addCleanup(self.api._close)
        self.host = patch("AIassist.ollama_url", return_value=f"http://127.0.0.1:{self.server.server_port}")
        self.host.start()
        self.addCleanup(self.host.stop)

    def test_stop_disconnects_real_socket_and_saves_only_accepted_text(self):
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "test", "fake:7b", 4096)["stream_id"]
        self.events.wait("token", stream)
        self.api.stop(stream)
        self.release.set()
        stopped = self.events.wait("stopped", stream)
        self.assertTrue(self.disconnected.wait(3))
        self.assertEqual(stopped["text"], "first ")
        self.assertEqual(self.store.load(chat)["messages"][-1]["content"], "first ")
        self.assertFalse(any(e["type"] == "done" for e in self.events.items))

    def test_silent_reader_is_not_falsely_reported_as_disconnected(self):
        self.silent = True
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "test", "fake:7b", None)["stream_id"]
        self.assertTrue(self.started.wait(3))
        self.api.stop(stream)
        # Existing engine cancellation is callback-driven: until another chunk
        # arrives, the API must retain the stream and must not announce stopped.
        with self.api._lock:
            self.assertIn(stream, self.api._streams)
        self.assertFalse(any(e["type"] in ("stopped", "done") for e in self.events.items))
        self.release.set()
        self.events.wait("stopped", stream)
        self.assertTrue(self.disconnected.wait(3))
        self.assertEqual(len(self.store.load(chat)["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
