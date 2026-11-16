"""Save ordering, isolation, and recovery using real atomic chat files."""

from copy import deepcopy
import tempfile
import threading
import unittest
from unittest.mock import patch

from background_saves import BackgroundSaves
from chat_store import ChatStore


class BackgroundSaveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(folder.name)
        self.saver = BackgroundSaves(self.store)
        self.addCleanup(self.close_worker)

    def close_worker(self):
        self.saver.stop()
        self.saver.thread.join(3)
        self.assertFalse(self.saver.thread.is_alive())

    def test_saves_and_renames_are_ordered_and_delete_stays_deleted(self):
        record = self.store.new_record()
        self.saver.submit(1, record)
        record.update(title="First rename", _manual_title=True)
        self.saver.submit(2, record)
        record["title"] = "Second rename"
        self.saver.submit(3, record)
        record["draft"] = "Latest draft"
        self.saver.submit(4, record)
        self.saver.jobs.join()
        saved = self.store.load(record["id"])
        self.assertEqual((saved["title"], saved["draft"]), ("Second rename", "Latest draft"))
        self.saver.submit(5, record, delete=True)
        self.saver.jobs.join()
        self.assertFalse(self.store.list_chats())

    def test_submit_returns_while_disk_is_blocked_and_copies_its_input(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.store.save

        def slow_save(record):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test timed out")
            return original(record)

        record = self.store.new_record()
        record["messages"] = [dict(role="user", content="Original")]
        expected = deepcopy(record["messages"])
        with patch.object(self.store, "save", side_effect=slow_save):
            self.saver.submit(1, record)
            self.assertTrue(entered.wait(1))
            record["messages"][0]["content"] = "Changed after submission"
            release.set()
            self.saver.jobs.join()
        self.assertEqual(self.store.load(record["id"])["messages"], expected)

    def test_failed_write_is_reported_and_worker_can_retry(self):
        record = self.store.new_record()
        with patch.object(self.store, "save", side_effect=OSError("disk full")):
            self.saver.submit(1, record)
            self.saver.jobs.join()
        self.assertIn("disk full", self.saver.results.get_nowait()[3])
        self.saver.submit(2, record)
        self.saver.jobs.join()
        self.assertIsNone(self.saver.results.get_nowait()[3])
        self.assertEqual(self.store.load(record["id"])["id"], record["id"])
