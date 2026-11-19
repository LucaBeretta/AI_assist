"""Colibri discovery and chat integration use injected clients or local fixtures."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app_core import runtime
import test_webui_app as fixtures
from webui.app import Api
from webui.colibri import ColibriService
from chat_store import ChatStore


class ColibriApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.client = Mock()
        self.service = Mock(default_backend="Ollama")
        self.service.availability.return_value = dict(installed=True, running=True, models=["colibri-model"],
            host="http://localhost:8000/v1", state="ready", context_length=32768, tokens_per_second=4.5)
        self.service.prepare.return_value = self.client
        self.api._colibri = self.service

    def test_state_and_stream_reuse_colibri_and_persist_its_backend(self):
        def chat(messages, model, on_chunk, on_metrics):
            self.assertEqual(messages[-1]["content"], "hello")
            on_chunk("Colibri reply")
            on_metrics(dict(prompt_eval_count=10, eval_count=5))
            return "Colibri reply"
        self.client.chat.side_effect = chat
        chat_id = self.api.new_chat()["chat"]["id"]
        with patch.object(self.engine, "request_ollama", side_effect=AssertionError("Wrong engine")):
            stream = self.api.send(chat_id, "hello", "colibri-model", None, backend="Colibri")
            self.assertTrue(stream["ok"], stream)
            self.events.wait("done", stream["stream_id"])
        saved = self.store.load(chat_id)
        self.assertEqual(saved["backend"], "Colibri")
        self.assertEqual(saved["environment"]["backend"], "Colibri")
        self.assertEqual(saved["messages"][-1]["content"], "Colibri reply")
        self.assertEqual(self.api.state()["colibri"]["models"], ["colibri-model"])
        self.assertEqual(self.api.state()["engine"], "Colibri")
        snapshot = self.api.status_snapshot()
        self.assertEqual(snapshot["model"], "colibri-model · ram + ssd")
        self.assertEqual(snapshot["context_total"], 32768)
        self.assertEqual(snapshot["tokens_per_second"], 4.5)
        with patch.object(self.engine, "list_ollama_models", side_effect=RuntimeError("offline")):
            self.assertTrue(self.api.state()["ok"])
        self.service.availability.return_value.update(running=False, state="stopped")
        self.assertEqual(self.api.status_snapshot()["engine_state"], "stopped")

    def test_old_record_with_backend_only_in_the_environment_uses_colibri(self):
        record = self.store.new_record(model="colibri-model")
        record["environment"]["backend"] = "Colibri"
        record = self.store.save(record)
        self.assertNotIn("backend", record)
        self.client.chat.return_value = "reply"
        stream = self.api.send(record["id"], "hello", "colibri-model", None)
        self.assertTrue(stream["ok"], stream)
        self.events.wait("done", stream["stream_id"])
        self.assertEqual(self.store.load(record["id"])["backend"], "Colibri")

    def test_saved_colibri_selection_does_not_start_ollama(self):
        self.service.default_backend = "Colibri"
        self.engine.ensure_ollama = Mock()
        self.api._ensure_engine()
        self.engine.ensure_ollama.assert_not_called()
        self.assertEqual(self.api.status_snapshot()["backend"], "Colibri")
        chat = self.api.new_chat()["chat"]
        self.assertEqual(chat["backend"], "Colibri")
        self.assertEqual(self.api.status_snapshot()["chat_id"], chat["id"])
        self.assertTrue(self.api.set_chat_model(chat["id"], "fake:7b", "Ollama")["ok"])
        self.assertEqual(self.store.load(chat["id"])["backend"], "Ollama")
        self.assertFalse(self.api.set_chat_model(chat["id"], "fake:7b", "Unknown")["ok"])

    def test_stop_keeps_partial_and_errors_are_real_events(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def chat(messages, model, on_chunk, on_metrics):
            on_chunk("partial")
            release.wait(3)
            on_chunk("not accepted after Stop")
            return "partial"
        self.client.chat.side_effect = chat
        chat_id = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat_id, "hello", "colibri-model", None, backend="Colibri")["stream_id"]
        self.events.wait("token", stream)
        self.api.stop(stream)
        release.set()
        self.assertEqual(self.events.wait("stopped", stream)["text"], "partial")
        self.assertEqual(self.store.load(chat_id)["messages"][-1]["content"], "partial")
        self.client.chat.side_effect = RuntimeError("Colibri is offline")
        stream = self.api.send(chat_id, "again", "colibri-model", None)["stream_id"]
        self.assertIn("offline", self.events.wait("error", stream)["error"])
        self.assertFalse(self.api.send(chat_id, "again", "model", None, backend="wrong")["ok"])
        with self.assertRaisesRegex(ValueError, "vision"):
            self.api._require_vision("colibri-model", [{"images": ["bytes"]}], "Colibri")

    def test_launcher_follows_reported_port_and_never_exposes_the_key(self):
        (self.store.directory / "backend.settings").write_text(json.dumps({"backend": "Colibri"}), encoding="utf-8")
        (self.store.directory / "colibri.settings").write_text(json.dumps({"directory": "chosen"}), encoding="utf-8")
        report = dict(ctx=16384, server=dict(state="ready", tokens_per_second=3.1,
            urls={"openai_base_url": "http://127.0.0.1:8123/v1"}))
        with patch.dict(os.environ, {"COLI_API_KEY": "private-session-key", "COLIBRI_URL": ""}), \
                patch("webui.colibri.launcher.find_directory", return_value="chosen") as find, \
                patch("webui.colibri.launcher.status", return_value=report), \
                patch("webui.colibri.launcher.start_server", return_value=report) as start, \
                patch("colibri_backend.ColibriClient") as client:
            client.return_value.models.return_value = ["served"]
            service = ColibriService(self.store.directory)
            value = service.availability()
            self.assertTrue(value["installed"])
            self.assertTrue(value["running"])
            self.assertEqual(value["models"], ["served"])
            self.assertEqual(value["host"], "http://127.0.0.1:8123/v1")
            self.assertNotIn("private-session-key", json.dumps(value))
            self.assertIs(service.prepare(threading.Event(), lambda text: None), client.return_value)
            start.assert_called_once()
            find.assert_called_once_with("chosen")
            client.assert_called_with("http://127.0.0.1:8123/v1", "private-session-key")
        with patch.dict(os.environ, {"COLIBRI_URL": "https://remote.example/v1"}), \
                patch("webui.colibri.launcher.find_directory", return_value="chosen"), \
                patch("webui.colibri.launcher.start_server") as start:
            service = ColibriService(self.store.directory)
            service.prepare(threading.Event(), lambda text: None)
            start.assert_not_called()


class ColibriSocketTests(unittest.TestCase):
    def test_existing_client_disconnects_and_partial_is_saved_on_stop(self):
        started, release, disconnected = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                def token(text):
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode())
                    self.wfile.flush()
                try:
                    token("first ")
                    started.set()
                    release.wait(4)
                    token("discarded")
                    readable, _, _ = select.select([self.connection], [], [], 3)
                    if readable and self.connection.recv(1) == b"":
                        disconnected.set()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    disconnected.set()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store, events = ChatStore(Path(folder.name)), fixtures.Events()
        (store.directory / "backend.settings").write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/v1"}), encoding="utf-8")
        with patch.dict(os.environ, {"COLIBRI_URL": "", "COLI_API_KEY": ""}):
            service = ColibriService(store.directory)
        api = Api(events, store, runtime, colibri=service)
        self.addCleanup(api._close)
        with patch("webui.colibri.launcher.find_directory", return_value=None):
            chat = api.new_chat()["chat"]["id"]
            stream = api.send(chat, "hello", "served", None, backend="Colibri")["stream_id"]
            events.wait("token", stream)
            self.assertTrue(started.wait(1))
            api.stop(stream)
            release.set()
            self.assertEqual(events.wait("stopped", stream)["text"], "first ")
            self.assertTrue(disconnected.wait(3))
        self.assertEqual(store.load(chat)["messages"][-1]["content"], "first ")


if __name__ == "__main__":
    unittest.main()
