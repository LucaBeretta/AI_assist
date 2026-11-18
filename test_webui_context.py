"""Environment/schema and compaction checks; no window or real model."""

from copy import deepcopy
import json
import threading
import unittest
import zipfile

from chat_store import ChatStoreError
import test_webui_app as fixtures
from webui.app import estimate_tokens


class ContextTests(unittest.TestCase):
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
