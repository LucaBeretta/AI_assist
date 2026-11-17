"""colibri's launcher driven from AI Assist (status, recommendations, install,
context, start and stop), plus chat backups.

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

import colibri_launcher as colibri_client


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
                                "urls": {"openai_base_url": state["url"] + "/v1"},
                                "tokens_per_second": state.get("tps") if state["server"] == "ready" else None}
        print(json.dumps(report, indent=2))
    elif args[:2] == ["setup", "--list"]:
        print(json.dumps(state["catalog"], indent=2))
    elif args[:1] == ["start"]:
        state["server"] = "ready"; save(state)
        print(json.dumps({"state": "ready"}))
    elif args[:1] == ["stop"]:
        state["server"] = "stopped"; save(state)
    elif args[:1] == ["setup"] and "--ctx" in args and "--model" not in args:
        ctx = int(args[args.index("--ctx") + 1])
        with open(os.path.join(here, "setup.json"), "w", encoding="utf-8") as handle:
            json.dump({"family": "qwen36", "ctx": ctx}, handle)
        state["server"] = "ready"; save(state)
        print(json.dumps({"config": {"ctx": ctx}}))
    elif args[:1] == ["setup"] and "--model" in args:
        model = args[args.index("--model") + 1]
        ctx = int(args[args.index("--ctx") + 1]) if "--ctx" in args else None
        for done in (0, 50, 100):
            state["install"] = {"phase": "download", "done": done, "total": 100, "running": True,
                                "rate": 25, "eta_seconds": (100 - done) / 25}
            save(state)
            time.sleep(state.get("step_seconds", 0.3))
        state.update(model=model, ready=True, server="ready", install={"phase": "started", "running": False})
        save(state)
        with open(os.path.join(here, "setup.json"), "w", encoding="utf-8") as handle:
            json.dump({"family": "qwen36", "ctx": ctx}, handle)
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

    def __init__(self, replies, model="qwen3-coder-30b", delay=0.0, stream_pause=0.0):
        self.replies = list(replies)
        self.stream_pause = stream_pause
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
                if payload.get("stream") and status == 200:
                    self.send_stream(reply)
                else:
                    self.send_json(status, reply)

            def send_stream(self, reply):
                """Server-sent events as colibri's gateway sends them."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                message = reply["choices"][0]["message"]
                events = [{"reasoning_content": "."}]
                text = message.get("content") or ""
                middle = len(text) // 2
                events += [{"content": text[:middle]}, {"content": text[middle:]}]
                for delta in events:
                    chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(gateway.stream_pause)
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True, name="fake-server").start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def join_workers(before, timeout=15):
    """Let the app's background workers finish before a Tk test drops the app.

    A worker that ends after the test would release the last reference to Tk
    objects from its own thread, which aborts Tcl ("deleted by the wrong thread")."""
    for thread in threading.enumerate():
        if thread is threading.main_thread() or thread in before or thread.name == "fake-server":
            continue
        thread.join(timeout)


def completion(content="", tool_calls=None, reasoning=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return 200, {"id": "x", "object": "chat.completion",
                 "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


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
        settings = self.folder / "chats" / "colibri.settings"
        saved = colibri_client.save_directory(self.colibri, settings)
        self.assertEqual(colibri_client.load_settings(settings)["directory"], saved)
        self.assertEqual(colibri_client.find_directory(saved), saved)
        self.assertEqual(colibri_client.load_settings(self.folder / "missing"), {})

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
        self.assertEqual(colibri_client.server_url(report), "http://127.0.0.1:8123/v1")
        self.assertTrue(colibri_client.is_local_url("http://127.0.0.1:8123/v1"))
        self.assertFalse(colibri_client.is_local_url("http://192.168.1.20:8000/v1"))
        self.assertIn("start --json --no-browser", self.calls())
        colibri_client.stop_server(self.colibri)
        self.assertEqual(colibri_client.server_state(colibri_client.status(self.colibri)), "stopped")

    def test_install_reports_progress_and_configures(self):
        progress = []
        cancel = threading.Event()
        colibri_client.install_model(self.colibri, "qwen3-coder-30b", lambda *item: progress.append(item),
                                     cancel, poll_seconds=0.05)
        self.assertIn("setup --model qwen3-coder-30b --yes --json --no-browser", self.calls())
        self.assertTrue(any(phase == "download" and total == 100 and rate == 25 and eta >= 0
                            for phase, done, total, rate, eta in progress))
        report = colibri_client.status(self.colibri)
        self.assertEqual(report["setup"]["model"]["id"], "qwen3-coder-30b")
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.install_model(self.colibri, "--yes", lambda *item: None, cancel)

    def test_context_on_install_and_change(self):
        colibri_client.install_model(self.colibri, "qwen3-coder-30b", lambda *item: None, threading.Event(),
                                     poll_seconds=0.05, ctx=16384)
        self.assertIn("setup --model qwen3-coder-30b --yes --json --no-browser --ctx 16384", self.calls())
        self.assertEqual(colibri_client.status(self.colibri)["ctx"], 16384)
        report = colibri_client.set_context(self.colibri, 0)
        calls = self.calls()
        self.assertLess(calls.index("stop"), calls.index("setup --ctx 0 --yes --json --no-browser"))
        self.assertIsNone(report["ctx"])  # 0 = automatic
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.set_context(self.colibri, -5)
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.install_model(self.colibri, "x", lambda *item: None, threading.Event(), ctx=True)

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


class OutputTests(unittest.TestCase):
    def test_json_output_skips_banner(self):
        self.assertEqual(colibri_client.parse_json_output("colibri v2\n  {not json\n[1, 2]\n"), [1, 2])
        with self.assertRaises(colibri_client.ColibriError):
            colibri_client.parse_json_output("no json here")
        self.assertEqual(colibri_client.server_url({"server": {"urls": {"openai_base_url": "http://0.0.0.0:8001/v1"}}}),
                         "http://127.0.0.1:8001/v1")
        self.assertIsNone(colibri_client.server_url({}))
        self.assertEqual(colibri_client.speed_text({"server": {"tokens_per_second": 3.44}}), "~3.4 tok/s on the last answer")
        self.assertEqual(colibri_client.speed_text({"server": {"tokens_per_second": 21.2}}), "~21 tok/s on the last answer")


class ChatBackupTests(unittest.TestCase):
    def test_backup_zip_holds_restorable_json_and_markdown(self):
        import zipfile
        from chat_store import ChatStore
        with tempfile.TemporaryDirectory() as folder:
            store = ChatStore(Path(folder) / "chats")
            first = store.create(model="alpha:8b")
            first.update(title="Piano / sito", messages=[{"role": "user", "content": "ciao"},
                                                         {"role": "assistant", "content": "Ciao!"}])
            store.save(first)
            second = store.create(model="qwen3-coder-30b")
            second.update(title="Piano / sito", backend="Colibri")
            store.save(second)
            (Path(folder) / "chats" / "00000000-0000-0000-0000-000000000000.json").write_text("{broken", encoding="utf-8")
            target = Path(folder) / "backup.zip"
            unsent = store.new_record(model="alpha:8b")
            unsent.update(title="Not on disk yet", messages=[{"role": "user", "content": "in coda"}])
            self.assertEqual(store.export_all(target, unsaved={unsent["id"]: unsent}, deleted={second["id"]}), 2)
            self.assertEqual(len(store.errors), 1)
            with zipfile.ZipFile(target) as archive:
                names = sorted(archive.namelist())
                self.assertIn(f"chats/{first['id']}.json", names)
                self.assertIn("readable/Piano  sito.md", names)
                self.assertIn("readable/Not on disk yet.md", names)
                self.assertNotIn(f"chats/{second['id']}.json", names)  # being deleted
                self.assertIn("README.txt", names)
                markdowns = [archive.read(name).decode("utf-8") for name in names if name.startswith("readable/")]
                restored = json.loads(archive.read(f"chats/{first['id']}.json"))
            self.assertTrue(any("## AI Assist\n\nCiao!" in text and "(Ollama)" in text for text in markdowns))
            self.assertTrue(any("in coda" in text for text in markdowns))
            self.assertEqual(restored["messages"][1]["content"], "Ciao!")
            # Restoring is copying the JSON back into a chats folder.
            fresh = ChatStore(Path(folder) / "restored")
            (Path(folder) / "restored" / f"{first['id']}.json").write_text(json.dumps(restored), encoding="utf-8")
            self.assertEqual(fresh.load(first["id"])["title"], "Piano / sito")
            self.assertEqual(list(Path(folder).glob(".backup.zip.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
