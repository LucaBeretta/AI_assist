"""Stop for streamed Ollama answers, and the agent's file size limit."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from coding_agent import FILE_LIMIT_CHOICES, run_agent
from test_agent_support import isolate_agent_data
from workspace_values import file_limit_label, file_limit_value, file_limit_warning, load_file_limit


def call(name, **arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}}


def done(text="Done."):
    return {"message": {"role": "assistant", "content": text}}


class FileLimitAgentTests(unittest.TestCase):


    def setUp(self):
        isolate_agent_data(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.big = self.root / "index.html"
        self.big.write_text("".join(f"<div class='row'>{index}</div>\n" for index in range(4000)), encoding="utf-8")
        self.events = []

    def run_tool(self, *responses, **options):
        with patch("coding_agent._chat", side_effect=list(responses) + [done()]):
            result = run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "go"}],
                               self.events.append, lambda action: True, **options)
        return [message["content"] for message in result["messages"] if message["role"] == "tool"]

    def test_default_limit_still_blocks_and_points_to_the_setting(self):
        self.assertGreater(self.big.stat().st_size, 64000)
        results = self.run_tool(call("read_file", path="index.html"),
                                call("edit_file", path="index.html", old_text="<div class='row'>7</div>", new_text="x"))
        self.assertIn("64,000-byte limit", results[0])
        self.assertIn("Developer > Files", results[1])

    def test_larger_limit_reads_in_parts_and_edits_blocks(self):
        results = self.run_tool(
            call("read_file", path="index.html"),
            call("read_file", path="index.html", start_line=3990, end_line=4000),
            call("edit_file", path="index.html", old_text="<div class='row'>3995</div>\n", new_text="<div class='row'>ok</div>\n"),
            file_limit=128000, context_length=4096)
        self.assertIn("Part of a large file", results[0])
        self.assertIn("the model's context is 4,096", results[0])
        self.assertLess(len(results[0]), 6000)
        self.assertIn("lines 3990-4000 of 4000", results[1])
        self.assertIn("Successfully updated index.html", results[2])
        self.assertIn("<div class='row'>ok</div>", self.big.read_text(encoding="utf-8"))
        self.assertTrue(any(event.get("type") == "status" and "reads it in parts" in event.get("text", "")
                            for event in self.events))

    def test_small_files_are_read_whole_and_limits_are_validated(self):
        (self.root / "small.css").write_text("body { color: red; }\n", encoding="utf-8")
        results = self.run_tool(call("read_file", path="small.css"), file_limit=1000000, context_length=32768)
        self.assertNotIn("Part of a large file", results[0])
        for bad in (0, True, "64KB", 10**9):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_agent("http://localhost:11434", "m", self.root, [{"role": "user", "content": "x"}],
                          lambda event: None, lambda action: True, file_limit=bad)

    def test_limit_labels_settings_and_warnings(self):
        self.assertEqual([file_limit_label(value) for value in FILE_LIMIT_CHOICES],
                         ["64 KB", "128 KB", "256 KB", "512 KB", "1 MB"])
        self.assertEqual(file_limit_value("512 KB"), 512000)
        self.assertEqual(file_limit_value("nonsense"), 64000)
        settings = self.root / "agent.settings"
        self.assertEqual(load_file_limit(settings), 64000)
        settings.write_text(json.dumps({"file_limit": 256000}))
        self.assertEqual(load_file_limit(settings), 256000)
        settings.write_text(json.dumps({"file_limit": 12345}))
        self.assertEqual(load_file_limit(settings), 64000)
        self.assertEqual(file_limit_warning(64000, 131072), "")
        warning = file_limit_warning(512000, 4096, ram_free=8 * 1024 ** 3)
        self.assertIn("about 128,000 tokens", warning)
        self.assertIn("the current context is 4,096", warning)
        self.assertIn("read and edited in parts", warning)
        self.assertIn("this computer has about 8.0 GB free", warning)
        tight = file_limit_warning(1000000, 4096, ram_free=2 * 1024 ** 3)
        self.assertIn("Keep the context smaller", tight)


if __name__ == "__main__":
    unittest.main()
