"""Exercise the actual HTTP protocol against a local fixture server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from colibri_backend import ColibriClient, normalize_url
from coding_agent import AgentCancelled, run_agent


class ColibriTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply(None)

            def do_POST(self):
                self.reply(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

            def reply(self, payload):
                owner.requests.append((self.path, dict(self.headers), payload))
                status, body, content_type = owner.responses.pop(0)
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = ColibriClient(f"http://127.0.0.1:{self.server.server_port}", "test-key")
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def json_reply(self, body, status=200):
        self.responses.append((status, body, "application/json"))

    def stream_reply(self, chunks, done=True):
        body = "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in chunks)
        if done:
            body += "data: [DONE]\n\n"
        self.responses.append((200, body.encode(), "text/event-stream"))

    def test_models_and_auth(self):
        self.json_reply({"data": [{"id": "glm"}, {"id": "glm"}]})
        self.assertEqual(self.client.models(), ["glm"])
        path, headers, payload = self.requests[0]
        self.assertEqual(path, "/v1/models")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertIsNone(payload)

    def test_streaming_unicode_and_usage(self):
        self.stream_reply([{"choices": [{"delta": {"reasoning_content": "private reasoning"}}]},
                           {"choices": [{"delta": {"content": "Ciao café"}}]},
                           {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                           {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 3}}])
        chunks, metrics = [], []
        result = self.client.chat([{"role": "user", "content": "hello"}], "glm", chunks.append, metrics.append)
        self.assertEqual(result, "Ciao café")
        self.assertEqual(chunks, [result])
        self.assertEqual(metrics[0]["eval_count"], 3)
        payload = self.requests[0][2]
        self.assertTrue(payload["stream"])
        self.assertNotIn("options", payload)
        self.assertNotIn("keep_alive", payload)

    def test_interrupted_stream_is_failure(self):
        self.stream_reply([{"choices": [{"delta": {"content": "partial"}}]}], done=False)
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            self.client.chat([], "glm")

    def test_truncation_is_failure(self):
        self.stream_reply([{"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}])
        with self.assertRaisesRegex(RuntimeError, "limit"):
            self.client.chat([], "glm")

    def test_unauthorized_does_not_echo_key(self):
        self.json_reply({"error": "test-key"}, status=401)
        with self.assertRaisesRegex(RuntimeError, "API key") as error:
            self.client.models()
        self.assertNotIn("test-key", str(error.exception))

    def test_agent_reviews_write_and_roundtrips_tool_id(self):
        self.json_reply({"choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call_write", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "hello.py", "content": "print('hello')\n"})}}]},
            "finish_reason": "tool_calls"}]})
        self.json_reply({"choices": [{"message": {"role": "assistant", "content": "Saved."}, "finish_reason": "stop"}]})
        approvals = []
        with tempfile.TemporaryDirectory() as directory:
            def approve(action):
                self.assertFalse(Path(directory, "hello.py").exists())
                approvals.append(action)
                return True
            result = run_agent(self.client.url, "glm", directory, [{"role": "user", "content": "Create hello.py"}],
                               lambda event: None, approve, chat_fn=self.client.agent_chat)
            self.assertEqual(Path(directory, "hello.py").read_text(), "print('hello')\n")
        self.assertEqual(len(approvals), 1)
        self.assertEqual(result["assistant_text"], "Saved.")
        messages = self.requests[1][2]["messages"]
        assistant = next(item for item in messages if item.get("tool_calls"))
        tool = next(item for item in messages if item["role"] == "tool")
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertIsInstance(assistant["tool_calls"][0]["function"]["arguments"], str)
        self.assertIsInstance(result["messages"][-3]["tool_calls"][0]["function"]["arguments"], dict)

    def test_cancelled_agent_does_not_execute_tools(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AgentCancelled):
                run_agent(self.client.url, "glm", directory, [{"role": "user", "content": "Write a file"}],
                          lambda event: None, lambda action: self.fail("No approval after cancellation"),
                          cancel_event=cancel, chat_fn=self.client.agent_chat)
        self.assertEqual(self.requests, [])

    def test_review_is_one_plain_request_without_tools(self):
        self.json_reply({"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Explain the error-handling choice with one meaningful comment."}}]})
        messages = [{"role": "user", "content": "Review this change."}]
        result = self.client.review(messages, "glm", threading.Event(), max_tokens=512, timeout=2)
        self.assertIn("meaningful comment", result)
        self.assertEqual(len(self.requests), 1)
        path, headers, payload = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload, {"model": "glm", "messages": messages, "stream": False, "max_tokens": 512})
        self.assertNotIn("tools", payload)

    def test_review_rejects_truncation_tool_calls_and_malformed_responses(self):
        samples = [
            {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "Incomplete"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Run this",
                "tool_calls": [{"function": {"name": "run_command", "arguments": "{}"}}]}}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Malformed",
                "tool_calls": {}}}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": []}}]},
            {"error": {"message": "secret-body-must-not-be-repeated"}},
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.json_reply(sample)
                with self.assertRaisesRegex(RuntimeError, "review failed") as raised:
                    self.client.review([{"role": "user", "content": "Review"}], "glm", threading.Event(), timeout=2)
                self.assertNotIn("secret-body", str(raised.exception))
        self.assertEqual(len(self.requests), len(samples))

    def test_invalid_or_cancelled_review_never_sends_a_request(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(AgentCancelled):
            self.client.review([{"role": "user", "content": "Review"}], "glm", cancel)
        for options in ({"max_tokens": 2048}, {"timeout": 121}, {"max_tokens": True}):
            with self.assertRaises(ValueError):
                self.client.review([{"role": "user", "content": "Review"}], "glm", threading.Event(), **options)
        with self.assertRaises(ValueError):
            self.client.review([{"role": "tool", "content": "Native result", "tool_call_id": "x"}],
                               "glm", threading.Event())
        self.assertEqual(self.requests, [])

    def test_review_http_error_reports_status_without_server_body(self):
        self.json_reply({"error": {"message": "private-server-detail"}}, status=403)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as raised:
            self.client.review([{"role": "user", "content": "Review"}], "glm", threading.Event(), timeout=2)
        self.assertNotIn("private-server-detail", str(raised.exception))

    def test_review_cancellation_and_deadline_disconnect_single_http_request(self):
        for stop in ("cancel", "deadline"):
            with self.subTest(stop=stop):
                cancel, reading, released = threading.Event(), threading.Event(), threading.Event()
                connection = Mock()
                connection.getresponse.return_value.status = 200
                connection.sock.shutdown.side_effect = lambda *args: released.set()
                def read(limit):
                    reading.set()
                    released.wait(2)
                    raise OSError("socket disconnected")
                connection.getresponse.return_value.read.side_effect = read
                def cancel_when_reading():
                    if reading.wait(1):
                        cancel.set()
                stopper = None
                if stop == "cancel":
                    stopper = threading.Thread(target=cancel_when_reading)
                    stopper.start()
                with patch("colibri_backend.http.client.HTTPConnection", return_value=connection):
                    expected = AgentCancelled if stop == "cancel" else RuntimeError
                    with self.assertRaises(expected):
                        self.client.review([{"role": "user", "content": "Review"}], "glm", cancel,
                                           timeout=1 if stop == "cancel" else .1)
                if stopper is not None:
                    stopper.join(timeout=1)
                connection.request.assert_called_once()
                connection.sock.shutdown.assert_called_once()
                self.assertTrue(released.is_set())
                self.assertTrue(connection.close.called)

    def test_review_preserves_https_port_path_and_header_credentials(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = json.dumps({"choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "Review feedback"}}
        ]}).encode()
        client = ColibriClient("https://review.example:8443/v1", "session-private-key")
        with patch("colibri_backend.http.client.HTTPSConnection", return_value=connection) as create:
            result = client.review([{"role": "user", "content": "Review source"}], "review-model", threading.Event())
        create.assert_called_once_with("review.example", 8443, timeout=120)
        self.assertEqual(result, "Review feedback")
        args, options = connection.request.call_args
        self.assertEqual(args, ("POST", "/v1/chat/completions"))
        self.assertEqual(options["headers"]["Authorization"], "Bearer session-private-key")
        self.assertNotIn(b"session-private-key", options["body"])

    def test_review_cancelled_while_connecting_does_not_send_headers_or_body(self):
        cancel = threading.Event()
        connection = Mock()
        connection.connect.side_effect = cancel.set
        with patch("colibri_backend.http.client.HTTPConnection", return_value=connection):
            with self.assertRaises(AgentCancelled):
                self.client.review([{"role": "user", "content": "Review"}], "glm", cancel)
        connection.request.assert_not_called()
        self.assertTrue(connection.close.called)

    def test_url_validation(self):
        self.assertEqual(normalize_url("http://localhost:8000/"), "http://localhost:8000/v1")
        self.assertEqual(normalize_url("https://host/v1/"), "https://host/v1")
        for url in ("file:///tmp", "https://user:secret@host", "http://host/v1?key=secret", "http://host/api/chat"):
            with self.assertRaises(ValueError):
                normalize_url(url)


class BackendGuiTests(unittest.TestCase):
    def test_colibri_startup_chat_and_switch_back_to_ollama(self):
        from tk_app import AIAssistantGUI
        with tempfile.TemporaryDirectory() as directory, ExitStack() as mocks:
            Path(directory, "backend.settings").write_text(json.dumps({"backend": "Colibri", "url": "http://localhost:8000/v1"}))
            mocks.enter_context(patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": directory, "COLIBRI_URL": "", "COLI_API_KEY": ""}))
            mocks.enter_context(patch("update_ui.UpdateChecker.check"))
            ensure = mocks.enter_context(patch("ollama_engine.ensure_ollama"))
            mocks.enter_context(patch("ollama_engine.list_ollama_models", return_value=["glm"]))
            mocks.enter_context(patch("ollama_engine.running_models", return_value={"glm"}))
            mocks.enter_context(patch("colibri_backend.ColibriClient.models", return_value=["glm"]))
            # No colibri folder: the server is used as it runs (no launcher control).
            mocks.enter_context(patch("colibri_launcher.find_directory", return_value=None))
            mocks.enter_context(patch("capabilities_view.fetch_model_capabilities", return_value={"capabilities": []}))

            def reply(client, messages, model, on_chunk=None, on_metrics=None):
                on_chunk("Hello from Colibri")
                return "Hello from Colibri"

            mocks.enter_context(patch.object(ColibriClient, "chat", reply))
            try:
                root = tk.Tk()
            except tk.TclError as error:
                self.skipTest(str(error))
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)
            app = AIAssistantGUI(root)

            def wait(predicate):
                until = time.monotonic() + 5
                while not predicate() and time.monotonic() < until:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(predicate())

            try:
                wait(lambda: not app.startup_pending and not app.busy())
                self.assertEqual(app.backend, "Colibri")
                ensure.assert_not_called()
                self.assertEqual(app.model_name.get(), "glm")
                for widget in (app.run_all_button, app.stop_all_button, app.context_picker):
                    self.assertEqual(str(widget.cget("state")), "disabled")
                # Find & download models stays available: its colibri tab explains how to install colibri.
                self.assertEqual(str(app.download_models_button.cget("state")), "normal")
                self.assertIn("Colibri", app.connection_label.cget("text"))
                app.start_new_chat()
                identity = app.current_chat["id"]
                app.input_box.insert("1.0", "Hi")
                app.send_prompt()
                wait(lambda: not app.is_waiting and not app.pending_saves)
                record = app.chat_store.load(identity)
                self.assertEqual(record["backend"], "Colibri")
                self.assertEqual(record["messages"][-1]["content"], "Hello from Colibri")
                app.open_backend()
                children = app.backend_window.winfo_children()
                picker = next(widget for widget in children if widget.winfo_class() == "TCombobox")
                picker.set("Ollama")
                entries = [widget for widget in children if widget.winfo_class() == "TEntry"]
                entries[-1].insert(0, "do-not-save-this-key")
                actions = children[-1]
                next(widget for widget in actions.winfo_children() if widget.cget("text") == "Connect").invoke()
                wait(lambda: not app.busy() and not app.pending_saves)
                self.assertEqual(app.backend, "Ollama")
                ensure.assert_called()
                self.assertNotEqual(str(app.download_models_button.cget("state")), "disabled")
                self.assertNotIn("do-not-save-this-key", Path(directory, "backend.settings").read_text())
                app.open_chat(identity)
                # The chat reopens on the engine it was written with, and its model returns.
                self.assertEqual(app.backend, "Colibri")
                self.assertEqual(app.engine_choice.get(), "Colibri")
                self.assertEqual(app.current_chat["backend"], "Colibri")
                wait(lambda: not app.busy() and app.model_name.get() == "glm")
                self.assertFalse(errors, errors)
                wait(lambda: not app.pending_saves)
            finally:
                app.saver.stop()
                app.capabilities_view.close()
                app.html_previews.close()
                for callback in root.tk.call("after", "info"):
                    root.after_cancel(callback)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
