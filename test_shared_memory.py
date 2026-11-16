"""Persistent shared notes, recall boundaries, and opt-out behavior."""

import tempfile
import unittest

from chat_store import ChatStore
from shared_memory import MAX_NOTES, SharedMemory, memory_note


class SharedMemoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ChatStore(folder.name)
        self.memory = SharedMemory(folder.name)

    def test_notes_survive_restart_without_polluting_chat_listing(self):
        self.memory.remember("Remember that I prefer Python and UTF-8.")
        restored = SharedMemory(self.store.directory)
        self.assertEqual(restored.notes, "I prefer Python and UTF-8.")
        self.assertIn("I prefer Python", restored.context("hello", []))
        self.assertEqual(self.store.list_chats(), [])
        self.assertEqual(self.store.errors, [])

    def test_add_to_shared_memory_wording_accepts_inline_notes_and_previous_reply(self):
        for prompt in ("Add to shared memory: I prefer Python", "Please save to shared memory: I prefer Python",
                       "Store this in my shared memory: I prefer Python"):
            with self.subTest(prompt=prompt):
                self.assertEqual(memory_note(prompt), "I prefer Python")
        self.assertEqual(memory_note("Add this to shared memory", [dict(role="assistant", content="A useful plan")]),
                         "A useful plan")
        with self.assertRaisesRegex(ValueError, "Include the note"):
            memory_note("add to shared memory")
        self.assertIsNone(memory_note("Explain how shared memory works"))
        self.assertEqual(memory_note("Add to shared memory: Keep punctuation."), "Keep punctuation.")

    def test_multiline_note_is_not_added_twice(self):
        self.memory.add("First line\nSecond line")
        self.memory.add("First line\nSecond line")
        self.assertEqual(self.memory.notes, "First line\nSecond line")

    def test_disabled_memory_neither_recalls_nor_adds_notes(self):
        self.memory.save("Existing note", False)
        self.assertFalse(self.memory.remember("Remember that I use CSV."))
        self.assertEqual(self.memory.context("CSV", []), "")
        self.assertFalse(SharedMemory(self.store.directory).enabled)

    def test_retrieves_other_chats_and_excludes_current_and_unrelated_chats(self):
        first = self.store.new_record(title="CSV export")
        first["messages"] = [dict(role="user", content="Use semicolon separators for CSV exports.")]
        unrelated = self.store.new_record(title="Gardening")
        unrelated["messages"] = [dict(role="user", content="Plant roses in spring.")]
        text = self.memory.context("Continue the CSV export", [first, unrelated])
        self.assertIn("semicolon", text)
        self.assertNotIn("roses", text)
        self.assertEqual(self.memory.context("CSV", [first], first["id"]), "")

    def test_clear_notes_and_limit_do_not_silently_discard_data(self):
        self.memory.save("Keep me")
        with self.assertRaises(ValueError):
            self.memory.save("x" * (MAX_NOTES + 1))
        self.assertEqual(SharedMemory(self.store.directory).notes, "Keep me")
        self.memory.save("")
        self.assertEqual(SharedMemory(self.store.directory).notes, "")

    def test_recall_is_bounded_and_does_not_include_tool_output(self):
        records = []
        for index in range(20):
            record = self.store.new_record(title=f"Python {index}")
            record["messages"] = [dict(role="user", content="Python " + "a" * 20000),
                                  dict(role="tool", content="Secret tool result")]
            records.append(record)
        text = self.memory.context("Python", records)
        self.assertLess(len(text), 3000)
        self.assertNotIn("Secret tool result", text)
