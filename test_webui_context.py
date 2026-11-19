"""Environment/schema and compaction checks; no window or real model."""

from copy import deepcopy
import json
import io
import threading
import unittest
import zipfile
from unittest.mock import patch

from chat_store import ChatStoreError
import test_webui_app as fixtures
from webui.app import estimate_tokens
import coding_agent


class ContextTests(unittest.TestCase):
    def test_long_developer_history_counts_the_same_bounded_prompt_as_the_wire(self):
        record = self.history(count=2, context=32768)
        record["mode"] = "dev"
        record["agent_history"] = [
            {"role": "system", "content": "Pinned project context: main.py\n" + "source line\n" * 100
                + "Shared memory: keep the existing API.\nConversation summary: preserve the decisions."},
            {"role": "user", "content": "Task card: implement and test the feature.\n" * 50}]
        for step in range(20):
            record["agent_history"].extend([
                {"role": "assistant", "content": f"Inspect step {step}", "thinking": "Check the actual result.",
                 "tool_calls": [{"function": {"name": "run_command", "arguments": {"command": "python -m unittest", "description": "Verify step"}}}]},
                {"role": "tool", "tool_name": "run_command", "content": f"Step {step} output\n" + "verified line\n" * 1000}])
        record = self.store.save(record)
        response = {"message": {"content": "done"}}
        with patch("coding_agent.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as opened:
            coding_agent._chat("http://localhost:11434", "fake:7b", record["agent_history"], 32768, None)
        payload = json.loads(opened.call_args.args[0].data)
        prompt = {key: payload[key] for key in ("messages", "tools")}
        expected = estimate_tokens(json.dumps(prompt, ensure_ascii=False))
        usage = self.api.context_usage(record["id"])
        self.assertEqual((usage["used"], usage["total"]), (expected, 32768))
        self.assertGreater(usage["used"], 1000)
        self.assertLess(usage["used"], estimate_tokens(json.dumps(record["agent_history"])))
        self.assertIn("Pinned project context", json.dumps(prompt))
        self.assertIn("Shared memory", json.dumps(prompt))
        # Stored IDs/paths that were never put in a request must not invent usage.
        record["environment"]["pinned_paths"] = ["not-read-" * 1000]
        record["environment"]["memory_ids"] = ["not-used-" * 1000]
        self.store.save(record)
        self.assertEqual(self.api.context_usage(record["id"])["used"], expected)

    def test_stale_native_path_uses_instructions_summary_and_only_the_remaining_messages(self):
        from webui.app import request_messages
        record = self.history(count=8, context=32768)
        record.update(mode="dev", _agent_history_stale=True,
                      agent_history=[{"role": "user", "content": "old tool path" * 10000}])
        record["summary"] = {"text": "Keep these decisions", "upto": 4, "created_at": record["created_at"]}
        record = self.store.save(record)
        messages = request_messages(record)
        self.assertEqual([item["content"] for item in messages[:2]], ["Project rules", "Keep these decisions"])
        self.assertEqual(self.api.context_usage(record["id"])["used"],
                         coding_agent.request_context_usage(messages, 32768)["used"])

    def test_live_developer_usage_updates_after_a_tool_and_keeps_the_chosen_context(self):
        from test_coding_agent import tool_response, final_response
        project = self.store.directory / "project"
        project.mkdir()
        (project / "source.py").write_text("source information\n" * 100, encoding="utf-8")
        record = self.history(count=0, context=32768)
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def chat(*args):
            calls.append(args)
            if len(calls) == 1:
                return tool_response("read_file", {"path": "source.py"})
            reached.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release the request")
            return final_response()
        with patch("coding_agent._chat", side_effect=chat):
            task = self.api.run_agent(record["id"], "Inspect", str(project), context=16384)
            self.assertTrue(task["ok"], task)
            self.assertTrue(reached.wait(3))
            snapshots = [item for item in self.events.items if item["type"] == "context_usage"]
            self.assertGreater(snapshots[-1]["used"], snapshots[0]["used"])
            usage = self.api.context_usage(record["id"])
            self.assertEqual((usage["used"], usage["total"]), (snapshots[-1]["used"], 16384))
            release.set()
            self.events.wait("agent_done")
        self.assertEqual(self.store.load(record["id"])["context_length"], 16384)

    def test_empty_oversized_and_failed_summaries_do_not_change_history(self):
        for answer in ("", "x" * 4000, None):
            record = self.history()
            def request(*args, **kwargs):
                if answer is None:
                    raise RuntimeError("summary unavailable")
                if answer:
                    kwargs["on_chunk"](answer)
                return answer
            self.engine.request_ollama = request
            stream = self.api.compact(record["id"])["stream_id"]
            self.events.wait("error",stream)
            self.assertEqual(self.store.load(record["id"]),record)

    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def history(self, count=8, context=8192):
        record = self.store.new_record(model="fake:7b")
        record["context_length"] = context
        record["environment"].update(context_length=context, instructions="Project rules", pinned_paths=["src/a.py"], memory_ids=["note-1"])
        record["messages"] = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index}"} for index in range(count)]
        return self.store.save(record)

    def test_old_records_load_with_environment_and_summary_defaults(self):
        record = self.history()
        del record["environment"], record["summary"]
        self.store._path(record["id"]).write_text(json.dumps(record), encoding="utf-8")
        loaded = self.store.load(record["id"])
        self.assertIsNone(loaded["summary"])
        self.assertEqual(loaded["environment"], {"backend":"Ollama", "model":"fake:7b", "context_length":8192,
                                               "instructions":"", "pinned_paths":[], "memory_ids":[]})

    def test_invalid_optional_fields_never_replace_a_good_record(self):
        good = self.history()
        changes = [{"environment":None}, {"environment":{}},
                   {"summary":{"text":"x", "upto":True, "created_at":good["created_at"]}},
                   {"summary":{"text":"x", "upto":9, "created_at":good["created_at"]}},
                   {"summary":{"text":"", "upto":2, "created_at":good["created_at"]}},
                   {"summary":{"text":"x", "upto":2, "created_at":"invalid"}}]
        for field, invalid in (("backend","Other"), ("model",12), ("instructions",[]),
                               ("context_length",True), ("pinned_paths","a.py"), ("memory_ids",[1])):
            env = deepcopy(good["environment"])
            env[field] = invalid
            changes.append({"environment":env})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ChatStoreError):
                self.store.save({**good, **change})
        self.assertEqual(self.store.load(good["id"]), good)

    def test_usage_estimate_and_exact_thresholds(self):
        record = self.history(count=0, context=1000)
        record["environment"]["instructions"] = ""
        for used, level in ((799,"ok"), (800,"warn"), (949,"warn"), (950,"full"), (1001,"full")):
            record["messages"] = [{"role":"user", "content":"x" * (used * 7 // 2)}]
            self.store.save(record)
            usage = self.api.context_usage(record["id"])
            self.assertEqual((usage["used"],usage["total"],usage["level"]), (used,1000,level))
        record["context_length"] = None
        self.store.save(record)
        self.assertEqual(self.api.context_usage(record["id"])["total"],8192)
        self.assertEqual(estimate_tokens(""),0)
        self.assertEqual(estimate_tokens("xxxx"),2)

    def test_compaction_keeps_archive_and_changes_next_send_and_export(self):
        record = self.history()
        captured = []
        def answer(messages, **kwargs):
            captured.append(deepcopy(messages))
            text = "Keep decisions and file a.py" if "Summarize" in messages[0]["content"] else "reply"
            kwargs["on_chunk"](text)
            return text
        self.engine.request_ollama = answer
        stream = self.api.compact(record["id"])["stream_id"]
        done = self.events.wait("compact_done",stream)
        compacted = self.store.load(record["id"])
        self.assertEqual(compacted["messages"],record["messages"])
        self.assertEqual(compacted["summary"]["upto"],4)
        self.assertEqual(done["usage"],self.api.context_usage(record["id"]))
        stream = self.api.send(record["id"],"next","fake:7b",8192)["stream_id"]
        self.events.wait("done",stream)
        self.assertEqual(captured[-1], [{"role":"system","content":"Project rules"},
                                      {"role":"system","content":"Keep decisions and file a.py"}] + record["messages"][4:] + [{"role":"user","content":"next"}])
        path = self.store.directory / "backup.zip"
        self.store.export_all(path)
        with zipfile.ZipFile(path) as archive:
            restored = json.loads(archive.read("chats/"+record["id"]+".json"))
            self.assertEqual(restored["summary"]["upto"],4)
            markdown = next(archive.read(name).decode() for name in archive.namelist() if name.startswith("readable/"))
            self.assertIn("## Conversation summary",markdown)
            self.assertIn("Keep decisions and file a.py",markdown)
            self.assertIn("message-0",markdown)

    def test_stop_during_compaction_preserves_original_record(self):
        record = self.history()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def answer(*args, on_chunk, **kwargs):
            on_chunk("partial summary")
            started.set()
            release.wait(3)
            on_chunk(" remainder")
            return "not saved"
        self.engine.request_ollama = answer
        stream = self.api.compact(record["id"])["stream_id"]
        self.assertTrue(started.wait(2))
        self.assertFalse(self.api.send(record["id"],"busy","m",8192)["ok"])
        self.api.stop(stream)
        release.set()
        self.events.wait("stopped",stream)
        self.assertEqual(self.store.load(record["id"]),record)

    def test_compaction_refuses_an_active_stream_and_stale_result(self):
        record = self.history()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def answer(*args, on_chunk, **kwargs):
            on_chunk("result")
            started.set()
            release.wait(3)
            return "result"
        self.engine.request_ollama = answer
        stream = self.api.send(record["id"],"new","m",8192)["stream_id"]
        self.assertTrue(started.wait(2))
        self.assertFalse(self.api.compact(record["id"])["ok"])
        self.api.stop(stream)
        release.set()
        self.events.wait("stopped",stream)
        started.clear(); release.clear()
        stream = self.api.compact(record["id"])["stream_id"]
        self.assertTrue(started.wait(2))
        changed = self.store.load(record["id"])
        changed["environment"]["instructions"] = "edited externally"
        self.store.save(changed)
        release.set()
        self.events.wait("error",stream)
        self.assertIsNone(self.store.load(record["id"])["summary"])

    def test_new_chat_copies_only_environment(self):
        source = self.history()
        source["backend"] = "Colibri"
        source["environment"]["backend"] = "Colibri"
        source["summary"] = {"text":"old summary","upto":2,"created_at":source["created_at"]}
        source["draft"] = "do not copy"
        source = self.store.save(source)
        result = self.api.new_chat_same_environment(source["id"])["chat"]
        self.assertEqual(result["environment"],source["environment"])
        self.assertEqual((result["model"],result["backend"],result["context_length"]), (source["model"],"Colibri",8192))
        self.assertNotEqual(result["id"],source["id"])
        self.assertEqual(result["messages"],[])
        self.assertEqual(result["draft"],"")
        self.assertEqual(result["agent_history"],[])
        self.assertIsNone(result["summary"])


if __name__ == "__main__":
    unittest.main()
