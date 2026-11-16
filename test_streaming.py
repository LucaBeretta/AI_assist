"""Streamed answers and the Stop button, for Ollama and colibri."""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import AIassist
import colibri_client
import streaming
from test_colibri_client import FakeGateway, completion, join_workers


class FakeOllama:
    """Streams NDJSON chunks like Ollama's /api/chat, one every ``pause`` seconds."""

    def __init__(self, pieces, pause=0.0, thinking=("hmm",)):
        self.requests = []
        self.disconnected = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                chunks = [{"message": {"role": "assistant", "content": "", "thinking": text}, "done": False} for text in thinking]
                chunks += [{"message": {"role": "assistant", "content": text}, "done": False} for text in pieces]
                chunks.append({"message": {"role": "assistant", "content": ""}, "done": True})
                try:
                    for chunk in chunks:
                        self.wfile.write((json.dumps(chunk) + "\n").encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(pause)
                except (BrokenPipeError, ConnectionResetError):
                    owner.disconnected.set()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True, name="fake-server").start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class StreamClientTests(unittest.TestCase):
    def ollama(self, *args, **kwargs):
        server = FakeOllama(*args, **kwargs)
        self.addCleanup(server.close)
        patcher = patch.dict(os.environ, {"OLLAMA_HOST": server.url})
        patcher.start()
        self.addCleanup(patcher.stop)
        return server

    def test_ollama_streams_pieces_in_order(self):
        server = self.ollama(["Ciao", ", come ", "stai?"])
        pieces = []
        answer = AIassist.request_ollama([{"role": "user", "content": "hi"}], "m", context_length=4096,
                                         on_delta=lambda kind, text: pieces.append((kind, text)))
        self.assertEqual(answer, "Ciao, come stai?")
        self.assertEqual(pieces, [("thinking", "hmm"), ("content", "Ciao"), ("content", ", come "), ("content", "stai?")])
        self.assertTrue(server.requests[0]["stream"])
        self.assertEqual(server.requests[0]["options"], {"num_ctx": 4096})

    def test_ollama_stop_keeps_partial_and_disconnects(self):
        server = self.ollama([f"word{index} " for index in range(40)], pause=0.05)
        cancel = threading.Event()
        pieces = []

        def on_delta(kind, text):
            pieces.append(text)
            if len(pieces) == 4:
                cancel.set()

        started = time.monotonic()
        with self.assertRaises(streaming.StreamCancelled) as stopped:
            AIassist.request_ollama([{"role": "user", "content": "hi"}], "m", on_delta=on_delta, cancel_event=cancel)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(stopped.exception.partial.startswith("word0 word1 word2"))
        # Closing the connection is how Ollama learns to stop generating.
        self.assertTrue(server.disconnected.wait(3))

    def test_ollama_errors_stay_readable(self):
        self.ollama([])
        with self.assertRaisesRegex(RuntimeError, "did not contain a message"):
            AIassist.request_ollama([], "m", on_delta=lambda kind, text: None)
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://127.0.0.1:9"}):
            with self.assertRaisesRegex(RuntimeError, "Could not connect to Ollama"):
                AIassist.request_ollama([], "m", on_delta=lambda kind, text: None)

    def test_colibri_streams_and_stops(self):
        gateway = FakeGateway([completion("Una risposta piuttosto lunga da colibri."),
                               completion("x" * 40)], stream_pause=0.3)
        self.addCleanup(gateway.close)
        pieces = []
        answer = colibri_client.stream_chat(gateway.url, [{"role": "user", "content": "ciao"}], "m",
                                            lambda kind, text: pieces.append(kind))
        self.assertEqual(answer, "Una risposta piuttosto lunga da colibri.")
        self.assertEqual(pieces, ["thinking", "content", "content"])
        self.assertTrue(gateway.requests[0]["stream"])
        cancel = threading.Event()
        threading.Timer(0.45, cancel.set).start()
        with self.assertRaises(streaming.StreamCancelled) as stopped:
            colibri_client.stream_chat(gateway.url, [{"role": "user", "content": "ciao"}], "m",
                                       lambda kind, text: None, cancel)
        self.assertEqual(stopped.exception.partial, "x" * 20)  # the half sent before Stop

    def test_colibri_stream_http_error(self):
        gateway = FakeGateway([(400, {"error": {"message": "context_length_exceeded"}})])
        self.addCleanup(gateway.close)
        with self.assertRaisesRegex(RuntimeError, "HTTP 400: context_length_exceeded"):
            colibri_client.stream_chat(gateway.url, [{"role": "user", "content": "x"}], "m", lambda kind, text: None)


class StreamingUITests(unittest.TestCase):
    def setUp(self):
        self.threads_before = set(threading.enumerate())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {
            "AI_ASSIST_DATA_DIR": str(Path(self.temporary.name) / "history"), "OLLAMA_MODEL": "alpha:8b",
            "AI_ASSIST_ENGINE": "ollama"}))
        self.mocks.enter_context(patch("AIassist.ensure_ollama"))
        self.mocks.enter_context(patch("AIassist.list_ollama_models", return_value=["alpha:8b"]))
        self.mocks.enter_context(patch("AIassist.running_models", return_value={"alpha:8b"}))
        self.mocks.enter_context(patch("AIassist.set_model_running"))
        self.mocks.enter_context(patch("capabilities_view.fetch_model_capabilities",
                                       return_value={"name": "alpha:8b", "capabilities": ["completion"]}))
        self.release = threading.Event()
        self.seen = []

        def slow_answer(messages, model, on_delta=None, cancel_event=None, **options):
            on_delta("thinking", "...")
            for word in ("Prima ", "parte ", "della ", "risposta"):
                self.release.wait(5)
                self.release.clear()
                if cancel_event.is_set():
                    raise streaming.StreamCancelled(partial="".join(self.seen))
                self.seen.append(word)
                on_delta("content", word)
            return "".join(self.seen)

        self.mocks.enter_context(patch("AIassist.request_ollama", side_effect=slow_answer))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = AIassist.AIAssistantGUI(self.root)
        self.addCleanup(self.close)
        self.wait_for(lambda: not self.app.startup_pending and not self.app.busy(), "startup")

    def close(self):
        self.release.set()
        for scheduled in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", scheduled)
        if not self.app.closed:
            self.app.close()
        join_workers(self.threads_before)
        self.assertEqual([str(error[1]) for error in self.errors], [])

    def wait_for(self, predicate, description, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.02)
        self.fail(f"Timed out waiting for {description}; status={self.app.status_label.cget('text')!r}")

    def send(self, text):
        self.app.input_box.insert("1.0", text)
        self.app.send_button.invoke()

    def test_text_appears_while_streaming_then_formats(self):
        self.send("Ciao")
        self.wait_for(lambda: self.app.live is not None and self.app.live["state"].cget("text") == "thinking…", "thinking")
        self.assertEqual(self.app.send_button.cget("text"), "Stop  ■")
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")
        self.release.set()
        self.wait_for(lambda: self.app.live is not None and self.app.live["body"].cget("text") == "Prima ", "first words")
        self.assertEqual(self.app.live["state"].cget("text"), "writing…")
        self.release.set()
        self.wait_for(lambda: self.app.live is not None and self.app.live["body"].cget("text") == "Prima parte ", "more words")
        for _ in range(2):
            self.release.set()
            time.sleep(.05)
        self.wait_for(lambda: not self.app.is_waiting, "answer end")
        self.assertIsNone(self.app.live)
        self.assertEqual(self.app.conversation[-1], {"role": "assistant", "content": "Prima parte della risposta"})
        self.assertEqual(self.app.send_button.cget("text"), "Send  ↑")

    def test_stop_keeps_the_partial_answer(self):
        self.send("Ciao")
        self.release.set()
        self.wait_for(lambda: self.app.live is not None and self.app.live["body"].cget("text") == "Prima ", "first words")
        self.app.send_button.invoke()  # Stop
        self.release.set()
        self.wait_for(lambda: not self.app.is_waiting, "stopped")
        self.assertEqual(self.app.conversation[-1], {"role": "assistant", "content": "Prima "})
        self.assertIn("Stopped", self.app.status_label.cget("text"))
        saved = self.app.chat_store.load(self.app.current_chat["id"])
        self.assertEqual(saved["messages"][-1]["content"], "Prima ")
        # The next message works normally.
        self.seen.clear()
        self.send("Ancora")
        for _ in range(5):
            self.release.set()
            time.sleep(.05)
        self.wait_for(lambda: not self.app.is_waiting, "second answer")
        self.assertEqual(self.app.conversation[-1]["content"], "Prima parte della risposta")

    def test_stop_before_any_text(self):
        self.send("Ciao")
        self.wait_for(lambda: self.app.live is not None, "live card")
        self.app.send_button.invoke()
        self.release.set()
        self.wait_for(lambda: not self.app.is_waiting, "stopped early")
        self.assertEqual(self.app.conversation[-1]["role"], "user")
        self.assertIn("Stopped before the model answered", self.app.status_label.cget("text"))

    def test_enter_does_not_stop(self):
        self.send("Ciao")
        self.wait_for(lambda: self.app.live is not None, "live card")
        event = type("Event", (), {"state": 0})()
        self.app.handle_enter(event)
        self.assertFalse(self.app.chat_cancel.is_set())
        self.release.set()
        for _ in range(5):
            self.release.set()
            time.sleep(.05)
        self.wait_for(lambda: not self.app.is_waiting, "answer end")


if __name__ == "__main__":
    unittest.main()
