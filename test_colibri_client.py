"""colibri support: format translation, the coli launcher, and the HTTP gateway.

A fake ``coli`` script stands in for colibri's launcher and a local HTTP server
for its OpenAI-compatible gateway, so no model, download, or network is used."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

import colibri_client
from coding_agent import AgentCancelled, run_agent


FAKE_COLI = textwrap.dedent('''\
    #!/usr/bin/env python3
    """A stand-in for colibri's coli launcher, driven by state.json beside it."""
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    state_path = os.path.join(here, "state.json")
    def load():
        with open(state_path, encoding="utf-8") as handle:
            return json.load(handle)
    def save(state):
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
    state = load()
    args = sys.argv[1:]
    with open(os.path.join(here, "calls.log"), "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\\n")
    print("colibri v2 (fake)")
    if args[:1] == ["status"]:
        report = {"configured": state["model"] is not None, "config_path": os.path.join(here, "setup.json"),
                  "setup": None, "server": None, "install": state.get("install")}
        if state["model"]:
            report["setup"] = {"status": "ready", "ready": state["ready"], "backend": "cpu",
                               "model": {"id": state["model"], "name": state["model"].upper()}}
            report["server"] = {"state": state["server"], "model_id": state["model"] if state["server"] == "ready" else None,
                                "urls": {"openai_base_url": state["url"] + "/v1"}}
        print(json.dumps(report, indent=2))
    elif args[:2] == ["setup", "--list"]:
        print(json.dumps(state["catalog"], indent=2))
    elif args[:1] == ["start"]:
        state["server"] = "ready"; save(state)
        print(json.dumps({"state": "ready"}))
    elif args[:1] == ["stop"]:
        state["server"] = "stopped"; save(state)
    elif args[:1] == ["setup"] and "--model" in args:
        model = args[args.index("--model") + 1]
        for done in (0, 50, 100):
            state["install"] = {"phase": "download", "done": done, "total": 100, "running": True}
            save(state)
            time.sleep(state.get("step_seconds", 0.3))
        state.update(model=model, ready=True, server="ready", install={"phase": "started", "running": False})
        save(state)
        with open(os.path.join(here, "setup.json"), "w", encoding="utf-8") as handle:
            json.dump({"family": "qwen36"}, handle)
        print(json.dumps({"config": {"model": {"id": model}}}))
    else:
        print("unknown command", file=sys.stderr)
        sys.exit(2)
''')

CATALOG = [
    {"id": "qwen3-coder-30b", "name": "Qwen3-Coder-30B-A3B", "family": "qwen36", "download_gb": 19.4,
     "ram_min_gb": 8, "ram_good_gb": 18, "dense_gb": 2.5, "kind": "text", "runs": "from RAM",
     "summary": "Coding model.", "status": "tight", "fits": True, "reason": "runs with a small cache",
     "recommended": True, "license_note": "", "doc": "docs/qwen36.md"},
    {"id": "glm-5.2", "name": "GLM-5.2", "family": "glm", "download_gb": 429.3, "ram_min_gb": 16,
     "ram_good_gb": 64, "dense_gb": 9.9, "kind": "text", "runs": "streams experts from the SSD",
     "summary": "744B MoE.", "status": "needs-ram", "fits": False, "reason": "needs 16 GB of RAM",
     "recommended": False, "license_note": "", "doc": ""},
    "not a row",
]


def make_fake_colibri(folder, url, **state):
    (folder / "c").mkdir(parents=True)
    coli = folder / "c" / "coli"
    coli.write_text(FAKE_COLI, encoding="utf-8")
    values = {"model": None, "ready": False, "server": "stopped", "url": url, "catalog": CATALOG}
    values.update(state)
    (folder / "c" / "state.json").write_text(json.dumps(values), encoding="utf-8")
    (folder / "c" / "setup.json").write_text(json.dumps({"family": "qwen36"}), encoding="utf-8")
    return folder


class FakeGateway:
    """A minimal colibri gateway: /v1/models and scripted /v1/chat/completions."""

    def __init__(self, replies, model="qwen3-coder-30b", delay=0.0):
        self.replies = list(replies)
        self.requests = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/models":
                    self.send_json(200, {"object": "list", "data": [
                        {"id": model, "object": "model", "capabilities": ["chat", "systemone"],
                         "input_modalities": ["text"], "context_length": 16384}]})
                else:
                    self.send_json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                gateway.requests.append(payload)
                time.sleep(delay)
                if not gateway.replies:
                    self.send_json(500, {"error": {"message": "no scripted reply"}})
                    return
                status, reply = gateway.replies.pop(0)
                self.send_json(status, reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def completion(content="", tool_calls=None, reasoning=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return 200, {"id": "x", "object": "chat.completion",
                 "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


class TranslationTests(unittest.TestCase):
    def test_tool_history_gets_ids_and_string_arguments(self):
        history = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "", "thinking": "plan", "tool_calls": [
                {"function": {"name": "list_files", "arguments": {"limit": 5}}},
                {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "tool_name": "read_file", "content": "print(1)"},
            {"role": "tool", "tool_name": "list_files", "content": "[]"},
            {"role": "assistant", "content": "done"},
        ]
        converted = colibri_client.to_openai_messages(history)
        calls = converted[2]["tool_calls"]
        self.assertEqual([call["function"]["name"] for call in calls], ["list_files", "read_file"])
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"limit": 5})
        self.assertEqual(converted[2]["reasoning_content"], "plan")
        # Results are matched by tool name, not only by position.
        self.assertEqual(converted[3], {"role": "tool", "tool_call_id": calls[1]["id"], "content": "print(1)"})
        self.assertEqual(converted[4]["tool_call_id"], calls[0]["id"])
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])
        self.assertEqual(converted[5], {"role": "assistant", "content": "done"})

    def test_orphaned_tool_result_becomes_a_note(self):
        converted = colibri_client.to_openai_messages([
            {"role": "user", "content": "go"},
            {"role": "tool", "tool_name": "run_command", "content": "exit 0"}])
        self.assertEqual(converted[1]["role"], "user")
        self.assertIn("run_command", converted[1]["content"])

    def test_response_becomes_native_message(self):
        _, reply = completion("", [{"id": "c1", "type": "function",
                                     "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}], "why")
        native = colibri_client.from_openai_response(reply)["message"]
        self.assertEqual(native["tool_calls"], [{"function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}}])
        self.assertEqual(native["thinking"], "why")
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.from_openai_response({"choices": []})
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.from_openai_response({"error": {"message": "context_length_exceeded"}})

    def test_json_output_skips_banner_and_host_normalizes(self):
        self.assertEqual(colibri_client.parse_json_output("colibri v2\n  {not json\n[1, 2]\n"), [1, 2])
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.parse_json_output("no json here")
        self.assertEqual(colibri_client.normalize_host("0.0.0.0:8000/v1/"), "http://127.0.0.1:8000")
        self.assertEqual(colibri_client.normalize_host("https://box.local:9000"), "https://box.local:9000")
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.normalize_host("ftp://x")


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        patcher = patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(self.folder / "chats")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("COLIBRI_HOST", None)
        os.environ.pop("COLIBRI_DIR", None)
        self.colibri = make_fake_colibri(self.folder / "colibri-main", "http://127.0.0.1:8123")

    def calls(self):
        return (self.colibri / "c" / "calls.log").read_text(encoding="utf-8").splitlines()

    def test_find_and_remember_directory(self):
        with patch.dict(os.environ, {"COLIBRI_DIR": str(self.colibri)}):
            self.assertEqual(colibri_client.find_directory(), str(self.colibri.resolve()))
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.save_directory(self.folder)
        saved = colibri_client.save_directory(self.colibri)
        self.assertEqual(colibri_client.load_saved_directory(), saved)
        colibri_client.save_setting("engine", "colibri")
        self.assertEqual(colibri_client.load_setting("engine"), "colibri")
        self.assertEqual(colibri_client.load_saved_directory(), saved)
        # Settings live in a dot-folder that ChatStore's *.json listing ignores.
        self.assertTrue(colibri_client.settings_path().is_relative_to(self.folder / "chats" / ".settings"))

    def test_status_recommendations_start_and_stop(self):
        report = colibri_client.status(self.colibri)
        self.assertFalse(report["configured"])
        self.assertEqual(colibri_client.server_state(report), "not configured")
        rows = colibri_client.recommendations(self.colibri)
        self.assertEqual([row["id"] for row in rows], ["qwen3-coder-30b", "glm-5.2"])
        self.assertTrue(rows[0]["tools"] and rows[0]["recommended"] and rows[0]["fits"])
        self.assertFalse(rows[1]["fits"])
        with self.assertRaisesRegex(colibri_client.ColibriError, "no model set up"):
            colibri_client.start_server(self.colibri)
        state = json.loads((self.colibri / "c" / "state.json").read_text())
        state.update(model="qwen3-coder-30b", ready=True)
        (self.colibri / "c" / "state.json").write_text(json.dumps(state))
        report = colibri_client.start_server(self.colibri)
        self.assertEqual(colibri_client.server_state(report), "ready")
        self.assertEqual(report["family"], "qwen36")
        self.assertEqual(colibri_client.server_host(report), "http://127.0.0.1:8123")
        with patch.dict(os.environ, {"COLIBRI_HOST": "127.0.0.1:9999"}):
            self.assertEqual(colibri_client.server_host(report), "http://127.0.0.1:9999")
        self.assertIn("start --json --no-browser", self.calls())
        colibri_client.stop_server(self.colibri)
        self.assertEqual(colibri_client.server_state(colibri_client.status(self.colibri)), "stopped")

    def test_install_reports_progress_and_configures(self):
        progress = []
        cancel = threading.Event()
        colibri_client.install_model(self.colibri, "qwen3-coder-30b", lambda *item: progress.append(item),
                                     cancel, poll_seconds=0.05)
        self.assertIn("setup --model qwen3-coder-30b --yes --json --no-browser", self.calls())
        self.assertTrue(any(phase == "download" and total == 100 for phase, done, total in progress))
        report = colibri_client.status(self.colibri)
        self.assertEqual(report["setup"]["model"]["id"], "qwen3-coder-30b")
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.install_model(self.colibri, "--yes", lambda *item: None, cancel)

    def test_install_cancel_stops_the_process(self):
        state = json.loads((self.colibri / "c" / "state.json").read_text())
        state["step_seconds"] = 5
        (self.colibri / "c" / "state.json").write_text(json.dumps(state))
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(colibri_client.ColibriCancelled):
            colibri_client.install_model(self.colibri, "glm-5.2", lambda *item: None, cancel)
        self.assertLess(time.monotonic() - started, 8)
        self.assertIsNone(colibri_client.status(self.colibri)["setup"])

    def test_missing_launcher_and_bad_output(self):
        with self.assertRaisesRegex(colibri_client.ColibriError, "not found"):
            colibri_client.status(self.folder)
        (self.colibri / "c" / "coli").write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
        with self.assertRaisesRegex(colibri_client.ColibriError, "exit 3.*boom"):
            colibri_client.status(self.colibri)


class GatewayTests(unittest.TestCase):
    def gateway(self, replies, **options):
        gateway = FakeGateway(replies, **options)
        self.addCleanup(gateway.close)
        return gateway

    def test_models_capabilities_and_chat(self):
        gateway = self.gateway([completion("Ciao!")])
        self.assertEqual(colibri_client.list_models(gateway.url), ["qwen3-coder-30b"])
        metadata = colibri_client.capabilities(gateway.url, "qwen3-coder-30b", "qwen36")
        self.assertEqual(metadata["capabilities"], ["completion", "tools"])
        self.assertEqual(metadata["context_length"], 16384)
        self.assertNotIn("tools", colibri_client.capabilities(gateway.url, "qwen3-coder-30b", "inkling")["capabilities"])
        answer = colibri_client.request_chat(gateway.url, [{"role": "user", "content": "hi"}], "qwen3-coder-30b")
        self.assertEqual(answer, "Ciao!")
        self.assertEqual(gateway.requests[0]["model"], "qwen3-coder-30b")
        self.assertFalse(gateway.requests[0]["stream"])

    def test_http_errors_are_readable(self):
        gateway = self.gateway([(400, {"error": {"message": "context_length_exceeded"}})])
        with self.assertRaisesRegex(colibri_client.ColibriError, "HTTP 400: context_length_exceeded"):
            colibri_client.request_chat(gateway.url, [{"role": "user", "content": "hi"}], "m")
        with self.assertRaisesRegex(colibri_client.ColibriError, "Could not reach colibri"):
            colibri_client.list_models("http://127.0.0.1:9")

    def test_agent_round_trip_through_colibri(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        (project / "main.py").write_text("print('hello')\n", encoding="utf-8")
        gateway = self.gateway([
            completion("", [{"id": "srv_1", "type": "function",
                             "function": {"name": "read_file", "arguments": "{\"path\": \"main.py\"}"}}], "look first"),
            completion("main.py prints hello."),
        ])
        events = []
        result = run_agent(gateway.url, "qwen3-coder-30b", project, [{"role": "user", "content": "What does main.py do?"}],
                           events.append, lambda action: False, context_length=8192, backend="colibri")
        self.assertEqual(result["assistant_text"], "main.py prints hello.")
        first, second = gateway.requests
        self.assertEqual([tool["function"]["name"] for tool in first["tools"]],
                         ["list_files", "read_file", "write_file", "run_command"])
        self.assertIn("max_tokens", first)
        assistant = next(message for message in second["messages"] if message.get("tool_calls"))
        tool = next(message for message in second["messages"] if message["role"] == "tool")
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertIn("print('hello')", tool["content"])
        # The saved native history keeps Ollama's shape: object arguments, tool_name.
        native_call = result["messages"][1]["tool_calls"][0]["function"]
        self.assertEqual(native_call["arguments"], {"path": "main.py"})
        self.assertEqual(result["messages"][2]["tool_name"], "read_file")

    def test_agent_cancel_and_backend_validation(self):
        gateway = self.gateway([completion("late")], delay=3)
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(AgentCancelled):
                run_agent(gateway.url, "m", folder, [{"role": "user", "content": "x"}], lambda event: None,
                          lambda action: False, cancel_event=cancel, backend="colibri")
            with self.assertRaises(ValueError):
                run_agent(gateway.url, "m", folder, [{"role": "user", "content": "x"}], lambda event: None,
                          lambda action: False, backend="lmstudio")


if __name__ == "__main__":
    unittest.main()


class ChatStoreBackendTests(unittest.TestCase):
    def test_backend_is_saved_and_old_chats_default_to_ollama(self):
        from chat_store import ChatStore, ChatStoreError
        with tempfile.TemporaryDirectory() as folder:
            store = ChatStore(folder)
            chat = store.create(model="qwen3-coder-30b", backend="colibri")
            self.assertEqual(store.load(chat["id"])["backend"], "colibri")
            path = Path(folder) / f"{chat['id']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["backend"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(store.load(chat["id"])["backend"], "ollama")
            data["backend"] = "lmstudio"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ChatStoreError):
                store.load(chat["id"])
