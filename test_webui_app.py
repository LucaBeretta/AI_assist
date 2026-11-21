"""JSON API tests using temporary chats and injected engine functions, never a window."""

from copy import deepcopy
import inspect
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app_core.runtime import StreamStopped, run_stoppable
from chat_store import ChatStore
from webui.app import Api


class Events:
    def __init__(self):
        self.items = []
        self.condition = threading.Condition()

    def emit(self, type, **data):
        with self.condition:
            self.items.append({"type": type, **deepcopy(data)})
            self.condition.notify_all()

    def wait(self, type, stream_id=None):
        with self.condition:
            def match():
                return next((e for e in self.items if e["type"] == type
                             and (stream_id is None or e.get("stream_id") == stream_id)), None)
            if not self.condition.wait_for(lambda: match() is not None, timeout=4):
                raise AssertionError(f"Missing {type}: {self.items}")
            return match()


class ApiTests(unittest.TestCase):
    def test_failed_final_save_keeps_recoverable_text_and_never_emits_done(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("recover this reply")
            release.wait(3)
            return "recover this reply"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "Hi", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        with patch.object(self.store, "save", side_effect=OSError("disk full")):
            release.set()
            failure = self.events.wait("error", stream)
        self.assertFalse(failure["saved"])
        self.assertEqual(failure["text"], "recover this reply")
        self.assertEqual(self.api.open_chat(chat)["unsaved_replies"][0]["text"], "recover this reply")
        self.assertEqual(len(self.store.load(chat)["messages"]), 1)
        self.assertFalse(any(e["type"] == "done" for e in self.events.items))

    def test_external_chat_edit_is_not_overwritten_by_a_late_reply(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("late reply")
            release.wait(3)
            return "late reply"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "Hi", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        record = self.store.load(chat)
        record["messages"].append({"role": "user", "content": "external edit"})
        self.store.save(record)
        release.set()
        self.assertIn("changed", self.events.wait("error", stream)["error"])
        self.assertEqual(self.store.load(chat)["messages"][-1]["content"], "external edit")
        self.assertEqual(self.api.open_chat(chat)["unsaved_replies"][0]["text"], "late reply")

    def test_close_rejects_new_requests_and_prevents_late_tokens(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(*args, on_chunk, **kwargs):
            on_chunk("partial")
            release.wait(3)
            on_chunk("late")
            return "unexpected"
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]["id"]
        stream = self.api.send(chat, "Hi", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream)
        self.api._close(timeout=0)
        self.assertFalse(self.api.new_chat()["ok"])
        self.assertFalse(self.api.send(chat, "again", "fake:7b", None)["ok"])
        release.set()
        self.events.wait("stopped", stream)
        self.assertEqual([e["text"] for e in self.events.items if e["type"] == "token"], ["partial"])

    def test_api_signatures_remain_visible_to_pywebview(self):
        self.assertEqual(inspect.getfullargspec(Api.send).args,
                         ["self", "chat_id", "text", "model", "context", "backend"])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(Path(folder.name))
        self.events = Events()
        self.calls = []
        def request(messages, model, context_length, on_chunk, on_metrics, cancel_event=None):
            self.calls.append((messages, model, context_length, threading.current_thread().name))
            on_chunk("hello")
            on_chunk(" world")
            on_metrics({"eval_count": 2})
            return "hello world"
        self.engine = SimpleNamespace(request_ollama=request, run_stoppable=run_stoppable,
                                      StreamStopped=StreamStopped, ollama_url=lambda: "http://localhost:11434",
                                      list_ollama_models=lambda: ["fake:7b"],
                                      running_details=lambda: {"fake:7b": {"context_length": 8192}},
                                      ensure_ollama=lambda callback: None)
        self.colibri = SimpleNamespace(default_backend="Ollama", availability=lambda: {
            "installed": False, "running": False, "models": [], "host": "http://127.0.0.1:8000/v1",
            "state": "offline", "context_length": None, "tokens_per_second": None})
        self.api = Api(self.events, self.store, self.engine, colibri=self.colibri)
        self.addCleanup(self.api._close)

    def test_state_and_chat_methods_return_plain_data(self):
        state = self.api.state()
        self.assertEqual(state["models"], ["fake:7b"])
        self.assertEqual(state["loaded"]["fake:7b"]["context_length"], 8192)
        record = self.api.new_chat()["chat"]
        self.assertEqual(self.api.open_chat(record["id"])["chat"], record)
        self.assertEqual(self.api.list_chats()["chats"][0]["id"], record["id"])

    def test_send_streams_then_persists_before_done(self):
        chat = self.api.new_chat()["chat"]
        result = self.api.send(chat["id"], "Hi", "fake:7b", 8192)
        done = self.events.wait("done", result["stream_id"])
        self.assertEqual(done["metrics"], {"eval_count": 2})
        self.assertEqual([e["text"] for e in self.events.items if e["type"] == "token"], ["hello", " world"])
        saved = self.store.load(chat["id"])
        self.assertEqual(saved["messages"], [{"role": "user", "content": "Hi"},
                                            {"role": "assistant", "content": "hello world"}])
        self.assertEqual(saved["last_response"]["status"], "done")
        self.assertEqual(self.calls[0][1:3], ("fake:7b", 8192))
        self.assertNotEqual(self.calls[0][3], threading.current_thread().name)

    def test_stop_closes_the_request_before_stopped_and_keeps_partial(self):
        release = threading.Event()
        closed = threading.Event()
        self.addCleanup(release.set)
        def request(messages, model, context_length, on_chunk, on_metrics, cancel_event=None):
            try:
                on_chunk("partial")
                release.wait(3)
                on_chunk("must not appear")
                return "must not be saved"
            finally:
                closed.set()
        self.engine.request_ollama = request
        chat = self.api.new_chat()["chat"]
        stream_id = self.api.send(chat["id"], "Long answer", "fake:7b", None)["stream_id"]
        self.events.wait("token", stream_id)
        self.assertTrue(self.api.stop(stream_id)["active"])
        release.set()
        stopped = self.events.wait("stopped", stream_id)
        self.assertTrue(closed.is_set())
        self.assertEqual(stopped["text"], "partial")
        self.assertEqual(self.store.load(chat["id"])["messages"][-1]["content"], "partial")
        self.assertFalse(any(e["type"] == "done" for e in self.events.items))
        self.assertFalse(self.api.stop(stream_id)["active"])

    def test_engine_error_is_an_event_and_never_a_completed_answer(self):
        def fail(*args, **kwargs):
            raise RuntimeError("engine offline")
        self.engine.request_ollama = fail
        chat = self.api.new_chat()["chat"]
        stream_id = self.api.send(chat["id"], "Hi", "fake:7b", None)["stream_id"]
        self.assertIn("engine offline", self.events.wait("error", stream_id)["error"])
        saved = self.store.load(chat["id"])
        self.assertEqual(len(saved["messages"]), 1)
        self.assertEqual(saved["last_response"]["status"], "error")

    def test_two_chats_do_not_share_messages_or_cancellation(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def request(messages, model, context_length, on_chunk, on_metrics, cancel_event=None):
            text = messages[-1]["content"]
            on_chunk(text)
            release.wait(3)
            on_chunk("!")
            return text + "!"
        self.engine.request_ollama = request
        a, b = [self.api.new_chat()["chat"]["id"] for _ in range(2)]
        sa = self.api.send(a, "a", "fake:7b", 4096)["stream_id"]
        sb = self.api.send(b, "b", "fake:7b", 8192)["stream_id"]
        self.events.wait("token", sa)
        self.events.wait("token", sb)
        self.assertFalse(self.api.send(a, "duplicate", "fake:7b", None)["ok"])
        self.assertEqual(self.api.open_chat(b)["chat"]["messages"][-1]["content"], "b")
        self.api.stop(sa)
        release.set()
        self.events.wait("stopped", sa)
        self.events.wait("done", sb)
        self.assertEqual(self.store.load(a)["messages"][-1]["content"], "a")
        self.assertEqual(self.store.load(b)["messages"][-1]["content"], "b!")

    def test_invalid_inputs_and_disk_errors_are_json_errors(self):
        self.assertFalse(self.api.open_chat("../invalid")["ok"])
        chat = self.api.new_chat()["chat"]["id"]
        for text, model, context in (("", "m", None), ("Hi", "", None), ("Hi", "m", True)):
            self.assertFalse(self.api.send(chat, text, model, context)["ok"])
        with patch.object(self.store, "save", side_effect=OSError("disk full")):
            self.assertIn("disk full", self.api.send(chat, "Hi", "m", None)["error"])
        with patch.object(self.engine, "list_ollama_models", side_effect=RuntimeError("offline")):
            self.assertEqual(self.api.state(), {"ok": False, "error": "offline"})


if __name__ == "__main__":
    unittest.main()
