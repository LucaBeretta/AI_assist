"""Automatic backups and the restore that never destroys (app_core.backups)."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from app_core import backups
from background_saves import BackgroundSaves
from chat_store import ChatStore
from shared_memory import SharedMemory

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)


class BackupCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = ChatStore(self.root / "chats")
        self.memory = SharedMemory(self.store.directory)

    def chat(self, text):
        record = self.store.new_record(model="m")
        record["messages"] = [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
        return self.store.save(record)


class CreateTests(BackupCase):
    def test_a_backup_holds_every_chat_and_the_shared_notes(self):
        first, second = self.chat("one"), self.chat("two")
        self.memory.save("Prefer tea.", True)
        path, count = backups.create(self.store, self.store.directory / "shared.memory", now=NOW)
        self.assertEqual((path.name, count), ("chats-20261119-120000.zip", 2))
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        self.assertTrue({f"chats/{first['id']}.json", f"chats/{second['id']}.json", backups.MEMORY_NAME} <= names)
        self.assertEqual([item["name"] for item in backups.list_backups(self.store)], [path.name])
        # The chat list ignores the backups folder.
        self.assertEqual(len(self.store.list_chats()), 2)

    def test_one_backup_a_day_and_only_the_newest_are_kept(self):
        self.chat("one")
        self.assertIsNotNone(backups.create_if_due(self.store, now=NOW))
        self.assertIsNone(backups.create_if_due(self.store, now=NOW + timedelta(hours=23)))
        self.assertIsNotNone(backups.create_if_due(self.store, now=NOW + timedelta(days=1)))
        for day in range(2, 20):
            backups.create(self.store, now=NOW + timedelta(days=day))
        kept = backups.list_backups(self.store)
        self.assertEqual(len(kept), backups.KEEP)
        self.assertEqual(kept[0]["created"], NOW + timedelta(days=19))


class RestoreTests(BackupCase):
    def make_backup(self):
        path, _ = backups.create(self.store, self.store.directory / "shared.memory", now=NOW)
        return path

    def test_missing_chats_come_back_exactly_and_chats_you_have_are_not_changed(self):
        kept, lost = self.chat("still here"), self.chat("deleted by mistake")
        self.memory.save("Prefer tea.", True)
        archive = self.make_backup()
        self.store.delete(lost["id"])
        changed = self.store.load(kept["id"])
        changed["messages"].append({"role": "user", "content": "newer than the backup"})
        self.store.save(changed)
        report = backups.restore(self.store, archive, self.memory)
        self.assertEqual((report["added"], report["kept"], report["memory"]), (1, 1, "kept yours"))
        self.assertEqual(self.store.load(lost["id"]), lost)  # title and times exactly as they were
        self.assertEqual(self.store.load(kept["id"])["messages"][-1]["content"], "newer than the backup")
        self.assertIn("1 chat added", backups.describe(report))

    def test_a_damaged_chat_is_replaced_and_the_damaged_file_kept_aside(self):
        record = self.chat("important")
        archive = self.make_backup()
        (self.store.directory / f"{record['id']}.json").write_text("{damaged", encoding="utf-8")
        report = backups.restore(self.store, archive, self.memory)
        self.assertEqual(report["replaced_damaged"], 1)
        self.assertEqual(self.store.load(record["id"])["messages"][0]["content"], "important")
        aside = list((self.store.directory / "damaged").iterdir())
        self.assertEqual([path.read_text(encoding="utf-8") for path in aside], ["{damaged"])

    def test_notes_come_back_only_when_yours_are_empty(self):
        self.memory.save("From before.", True)
        archive = self.make_backup()
        self.memory.save("", True)
        self.assertEqual(backups.restore(self.store, archive, self.memory)["memory"], "restored")
        self.assertEqual(self.memory.notes, "From before.")

    def test_a_crafted_zip_cannot_write_elsewhere_or_smuggle_records(self):
        good = self.store.new_record(model="m")
        evil = self.root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("../escaped.json", "{}")
            archive.writestr("chats/../../escaped.json", "{}")
            archive.writestr("chats/not-a-uuid.json", "{}")
            archive.writestr(f"chats/{good['id']}.json", json.dumps(dict(good, id="0" * 8 + "-0000-4000-8000-" + "0" * 12)))
            archive.writestr("chats/11111111-1111-4111-8111-111111111111.json", "not json")
            archive.writestr(f"chats/{'2' * 8}-2222-4222-8222-{'2' * 12}.json", json.dumps({"id": f"{'2' * 8}-2222-4222-8222-{'2' * 12}"}))
        report = backups.restore(self.store, evil, self.memory)
        self.assertEqual((report["added"], report["invalid"]), (0, 3))
        self.assertFalse((self.root / "escaped.json").exists())
        self.assertFalse((self.store.directory.parent / "escaped.json").exists())
        self.assertEqual(self.store.list_chats(), [])

    def test_files_that_are_not_backups_are_refused(self):
        not_zip = self.root / "notes.zip"
        not_zip.write_text("hello")
        with self.assertRaises(backups.BackupError):
            backups.restore(self.store, not_zip)
        empty = self.root / "empty.zip"
        with zipfile.ZipFile(empty, "w") as archive:
            archive.writestr("README.txt", "x")
        with self.assertRaisesRegex(backups.BackupError, "no AI Assist chats"):
            backups.restore(self.store, empty)


class OrderingTests(BackupCase):
    def test_a_backup_runs_after_the_saves_queued_before_it(self):
        saver = BackgroundSaves(self.store)
        self.addCleanup(saver.stop)
        record = self.store.new_record(model="m")
        record["messages"] = [{"role": "user", "content": "queued just before the backup"}]
        saver.submit(1, record)
        done = threading.Event()
        outcome = []
        saver.call(lambda: backups.create(self.store, now=NOW), lambda result, error: (outcome.append((result, error)), done.set()))
        self.assertTrue(done.wait(10))
        (path, count), error = outcome[0]
        self.assertIsNone(error)
        self.assertEqual(count, 1)
        with zipfile.ZipFile(path) as archive:
            self.assertIn(f"chats/{record['id']}.json", archive.namelist())

    def test_a_failing_call_reports_its_error_and_the_worker_keeps_going(self):
        saver = BackgroundSaves(self.store)
        self.addCleanup(saver.stop)
        outcome, done = [], threading.Event()
        saver.call(lambda: 1 / 0, lambda result, error: outcome.append(type(error).__name__))
        saver.call(lambda: "next", lambda result, error: (outcome.append(result), done.set()))
        self.assertTrue(done.wait(10))
        self.assertEqual(outcome, ["ZeroDivisionError", "next"])


if __name__ == "__main__":
    unittest.main()
