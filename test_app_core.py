"""The UI-free application core: settings, rules, the Ollama client, providers, the lock."""

import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app_core import conversation, ollama, paths, privacy, settings
from app_core.instance_lock import AlreadyRunning, InstanceLock
from app_core.providers import ColibriProvider, OllamaProvider, configured_provider
from colibri_backend import normalize_url

HERE = Path(__file__).resolve().parent


class FakeOllama(http.server.BaseHTTPRequestHandler):
    """Just enough of Ollama's API for the client: tags, ps, and a streamed chat."""

    requests = []

    def log_message(self, *args):
        pass

    def reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self.reply({"models": [{"name": "beta:1b"}, {"name": "alpha:8b"}, {"name": " "}, "junk"]})
        elif self.path == "/api/ps":
            self.reply({"models": [{"name": "alpha:8b", "size": 100, "size_vram": 50}]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append((self.path, body))
        if self.path == "/api/ps":
            self.reply({"models": []})
            return
        frames = [{"message": {"content": "Hel"}, "done": False}, {"message": {"content": "lo"}, "done": False},
                  {"message": {"content": ""}, "done": True, "eval_count": 4, "eval_duration": 1_000_000_000}]
        self.send_response(200)
        self.end_headers()
        for frame in frames:
            self.wfile.write(json.dumps(frame).encode() + b"\n")


class OllamaServerCase(unittest.TestCase):
    def setUp(self):
        FakeOllama.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"


class PathsAndSettingsTests(unittest.TestCase):
    def test_data_directory_follows_the_override_then_the_platform(self):
        self.assertEqual(paths.data_directory({"AI_ASSIST_DATA_DIR": "/x/y"}), Path("/x/y"))
        self.assertEqual(paths.data_directory({"LOCALAPPDATA": "C:/Local"}), Path("C:/Local") / "AI Assist" / "chats")
        self.assertEqual(paths.data_directory({}), Path.home() / ".aiassist" / "chats")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_a_new_data_directory_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            created = paths.ensure_private_directory(Path(folder, "new", "chats"))
            self.assertEqual(created.stat().st_mode & 0o777, 0o700)

    def test_write_json_replaces_atomically_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, "x.settings")
            settings.write_json(target, {"a": 1})
            settings.write_json(target, {"a": 2})
            self.assertEqual(settings.read_json(target), {"a": 2})
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["x.settings"])
            with patch("app_core.settings.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    settings.write_json(target, {"a": 3})
            self.assertEqual(settings.read_json(target), {"a": 2})
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["x.settings"])

    def test_read_json_tolerates_missing_damaged_and_non_object_files(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(settings.read_json(Path(folder, "missing")), {})
            Path(folder, "bad").write_text("{not json")
            Path(folder, "list").write_text("[1]")
            self.assertEqual(settings.read_json(Path(folder, "bad")), {})
            self.assertEqual(settings.read_json(Path(folder, "list")), {})

    def test_backend_settings_round_trip_and_environment_override(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "backend.settings")
            self.assertEqual(settings.load_backend(path, normalize_url, {}), ("Ollama", settings.DEFAULT_COLIBRI_URL))
            settings.save_backend(path, "Colibri", "http://127.0.0.1:9000/v1")
            self.assertEqual(settings.load_backend(path, normalize_url, {}), ("Colibri", "http://127.0.0.1:9000/v1"))
            self.assertEqual(settings.load_backend(path, normalize_url, {"COLIBRI_URL": "http://127.0.0.1:7/v1"})[1],
                             "http://127.0.0.1:7/v1")
            path.write_text(json.dumps({"backend": "Colibri", "url": "ftp://bad"}))
            self.assertEqual(settings.load_backend(path, normalize_url, {})[0], "Ollama")
            with self.assertRaises(ValueError):
                settings.save_backend(path, "Other", "x")

    def test_file_limit_accepts_only_offered_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agent.settings")
            self.assertEqual(settings.load_file_limit(path), 64000)
            settings.save_file_limit(path, 512000)
            self.assertEqual(settings.load_file_limit(path), 512000)
            path.write_text(json.dumps({"file_limit": 999}))
            self.assertEqual(settings.load_file_limit(path), 64000)
            with self.assertRaises(ValueError):
                settings.save_file_limit(path, 5)


class ConversationRuleTests(unittest.TestCase):
    def test_title_comes_from_the_first_user_message(self):
        self.assertEqual(conversation.title_from([{"role": "assistant", "content": "hi"}]), "")
        self.assertEqual(conversation.title_from([{"role": "user", "content": "  Plan   a\ntrip "}]), "Plan a trip")
        long = conversation.title_from([{"role": "user", "content": "x" * 100}])
        self.assertEqual((len(long), long[-1]), (72, "\u2026"))

    def test_chat_messages_put_shared_memory_first(self):
        talk = [{"role": "user", "content": "q"}]
        self.assertEqual(conversation.chat_messages(talk), talk)
        self.assertEqual(conversation.chat_messages(talk, "notes")[0], {"role": "system", "content": "notes"})
        self.assertIsNot(conversation.chat_messages(talk)[0], talk[0])

    def test_developer_prompt_keeps_folder_context_and_only_recent_tool_results(self):
        activity = [{"type": "tool_result", "text": f"result {index}"} for index in range(8)] + [
            {"type": "status", "text": "ignored"}]
        prompt = conversation.developer_system_prompt("/work", "QUALITY", "PINNED", activity)
        self.assertIn("The user selected this folder for reading and writing: /work", prompt)
        self.assertIn("QUALITY", prompt)
        self.assertIn("Pinned project context:\nPINNED", prompt)
        self.assertNotIn("result 1\n", prompt)
        self.assertIn("result 7", prompt)
        self.assertNotIn("ignored", prompt)

    def test_agent_messages_resume_native_history_without_old_system_messages(self):
        history = [{"role": "system", "content": "old"}, {"role": "user", "content": "first"},
                   {"role": "assistant", "content": "", "tool_calls": []}]
        messages = conversation.agent_messages([], history, "next", "SYSTEM", "MEMORY")
        self.assertEqual([m["role"] for m in messages], ["system", "system", "user", "assistant", "user"])
        self.assertEqual((messages[0]["content"], messages[1]["content"], messages[-1]["content"]), ("SYSTEM", "MEMORY", "next"))
        fresh = conversation.agent_messages([{"role": "user", "content": "q"}], [], "q", "SYSTEM")
        self.assertEqual([m["content"] for m in fresh], ["SYSTEM", "q"])


class PrivacyTests(unittest.TestCase):
    def test_local_follows_the_internet_gate(self):
        for url in ("http://localhost:11434", "127.0.0.1:11434", "http://127.0.0.2:1", "http://[::1]:8000/v1"):
            self.assertTrue(privacy.is_local_url(url), url)
        for url in ("http://192.168.1.20:11434", "https://example.com", "", "http://[bad"):
            self.assertFalse(privacy.is_local_url(url), url)

    def test_privacy_status_texts(self):
        off, on = {"installed": False, "online": None}, {"installed": True, "online": None}
        self.assertEqual(privacy.privacy_status(off, "a", "http://localhost:1"), ("local only", "success"))
        self.assertEqual(privacy.privacy_status(on, "a", "http://localhost:1"), ("local only · internet locked", "success"))
        self.assertEqual(privacy.privacy_status(on, "a", "http://10.0.0.2:1"), ("remote server", "amber"))
        self.assertEqual(privacy.privacy_status(on, "x:cloud", "http://localhost:1")[1], "amber")
        self.assertEqual(privacy.privacy_status({"installed": True, "online": "github.com"}, "a", "")[0], "online now: github.com")
        self.assertTrue(privacy.privacy_status(on, "a", "", {"state": "on"})[0].endswith(" · firewall on"))
        lifted = privacy.privacy_status(on, "a", "", {"state": "lifted", "has_rules": True, "lift_until": time.time() + 60})
        self.assertIn("firewall lifted until", lifted[0])


class OllamaClientTests(OllamaServerCase):
    def test_base_url_normalises_listen_addresses(self):
        self.assertEqual(ollama.base_url({}), "http://localhost:11434")
        self.assertEqual(ollama.base_url({"OLLAMA_HOST": "0.0.0.0:9000"}), "http://127.0.0.1:9000")
        self.assertEqual(ollama.base_url({"OLLAMA_HOST": "http://[::]"}), "http://127.0.0.1:11434")

    def test_lists_models_and_running_details(self):
        self.assertEqual(ollama.list_models(self.base), ["alpha:8b", "beta:1b"])
        details = ollama.running_details(ollama.api_json(self.base, "/api/ps"))
        self.assertEqual(details, {"alpha:8b": {"size": 100, "size_vram": 50, "context_length": None}})
        self.assertEqual(ollama.placement(details["alpha:8b"]), "gpu 50% · cpu")
        self.assertEqual(ollama.placement({"size": 10, "size_vram": 10}), "gpu")
        self.assertEqual(ollama.placement({"size": 10, "size_vram": 0}), "cpu")

    def test_stream_chat_sends_context_and_reports_chunks_and_metrics(self):
        chunks, metrics = [], []
        answer = ollama.stream_chat(self.base, [{"role": "user", "content": "hi"}], "alpha:8b", 4096,
                                    chunks.append, metrics.append)
        self.assertEqual((answer, chunks), ("Hello", ["Hel", "lo"]))
        self.assertEqual(metrics, [{"eval_count": 4, "eval_duration": 1_000_000_000}])
        path, body = FakeOllama.requests[-1]
        self.assertEqual((path, body["options"]["num_ctx"], body["stream"]), ("/api/chat", 4096, True))

    def test_unreachable_server_is_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "Could not connect to Ollama"):
            ollama.stream_chat("http://127.0.0.1:1", [], "alpha:8b")
        with self.assertRaisesRegex(RuntimeError, "Could not load models"):
            ollama.list_models("http://127.0.0.1:1")

    def test_lifecycle_payload(self):
        self.assertEqual(ollama.lifecycle_payload("m", True, 4096), {"model": "m", "stream": False, "keep_alive": -1,
                                                                     "options": {"num_ctx": 4096}})
        self.assertEqual(ollama.lifecycle_payload("m", False, 4096), {"model": "m", "stream": False, "keep_alive": 0})

    def test_a_remote_server_is_never_started(self):
        def offline():
            raise RuntimeError("offline")
        with patch("app_core.ollama.subprocess.Popen") as started:
            with self.assertRaisesRegex(RuntimeError, "remote Ollama server is offline"):
                ollama.ensure_running("http://192.168.1.9:11434", offline)
        started.assert_not_called()


class ProviderTests(OllamaServerCase):
    def test_ollama_provider_lists_streams_and_describes_itself(self):
        provider = OllamaProvider(self.base, auto_start=False)
        self.assertEqual(provider.list_models(), ["alpha:8b", "beta:1b"])
        self.assertEqual(provider.stream_chat([], "alpha:8b"), "Hello")
        info = provider.info()
        self.assertEqual((info.name, info.local, info.endpoint), ("Ollama", True, self.base))

    def test_colibri_provider_is_managed_only_for_a_local_server_with_a_folder(self):
        self.assertTrue(ColibriProvider("http://127.0.0.1:8000/v1", directory="/c").info().manages_lifecycle)
        self.assertFalse(ColibriProvider("http://192.168.0.4:8000/v1", directory="/c").info().manages_lifecycle)
        self.assertFalse(ColibriProvider("http://127.0.0.1:8000", directory=None).manageable())
        self.assertEqual(ColibriProvider("http://127.0.0.1:8000").url, "http://127.0.0.1:8000/v1")

    def test_configured_provider_follows_backend_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertIsInstance(configured_provider(directory, {}), OllamaProvider)
            settings.save_backend(directory / "backend.settings", "Colibri", "http://127.0.0.1:8001/v1")
            with patch("colibri_launcher.find_directory", return_value=None):
                provider = configured_provider(directory, {})
            self.assertEqual((provider.name, provider.url), ("Colibri", "http://127.0.0.1:8001/v1"))


HOLD_LOCK = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from app_core.instance_lock import InstanceLock
held = InstanceLock(sys.argv[1], kind="test holder").acquire()  # kept: the lock lives as long as the object
print("held", flush=True)
time.sleep(30)
"""


class InstanceLockTests(unittest.TestCase):
    def test_one_holder_at_a_time_in_one_process(self):
        with tempfile.TemporaryDirectory() as folder:
            first = InstanceLock(folder, kind="first").acquire()
            with self.assertRaises(AlreadyRunning) as caught:
                InstanceLock(folder).acquire()
            self.assertIn("first is already open", str(caught.exception))
            first.release()
            with InstanceLock(folder):
                pass
            self.assertFalse(Path(folder, "instance.owner").exists())

    def test_another_process_holds_the_lock_until_it_ends(self):
        with tempfile.TemporaryDirectory() as folder:
            holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, folder, str(HERE)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                contender = InstanceLock(folder)
                self.addCleanup(contender.release)
                with self.assertRaises(AlreadyRunning) as caught:
                    contender.acquire()
                self.assertEqual(caught.exception.owner.get("pid"), holder.pid)
                self.assertIn("test holder is already open", str(caught.exception))
            finally:
                holder.kill()
                holder.wait(timeout=10)
                holder.stdout.close()
            # The operating system released the lock with the process, even though it never cleaned up.
            with InstanceLock(folder):
                pass


class BoundaryTests(unittest.TestCase):
    def test_the_core_and_the_web_server_load_no_ui_toolkit(self):
        code = ("import sys; import app_core.providers, app_core.privacy, app_core.settings, app_core.instance_lock; "
                "print(sorted(name for name in sys.modules if name.split('.')[0] in ('tkinter', '_tkinter')))")
        completed = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "[]")

    def test_core_modules_never_import_tkinter_directly(self):
        statement = re.compile(r"^\s*(?:import|from)\s+_?tkinter\b", re.MULTILINE)
        for path in sorted((HERE / "app_core").glob("*.py")) + sorted((HERE / "webui").glob("*.py")):
            self.assertIsNone(statement.search(path.read_text(encoding="utf-8")), path.name)


if __name__ == "__main__":
    unittest.main()
