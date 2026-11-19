"""The agent's edit_file tool: exact block replacement through the reviewed write path."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from coding_agent import run_agent
from test_agent_support import isolate_agent_data


def call(name, **arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}}


def done(text="Done."):
    return {"message": {"role": "assistant", "content": text}}


class EditFileTests(unittest.TestCase):
    def setUp(self):
        isolate_agent_data(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "app.py"
        self.file.write_text("def greet():\n    return 'ciao'\n\nprint(greet())\n", encoding="utf-8")
        self.reviews = []

    def run_tool(self, *responses, approve=True):
        def approval(action):
            self.reviews.append(action)
            return approve
        with patch("coding_agent._chat", side_effect=list(responses) + [done()]):
            result = run_agent("http://localhost:11434", "m", self.root,
                               [{"role": "user", "content": "edit"}], lambda event: None, approval)
        return [message["content"] for message in result["messages"] if message["role"] == "tool"]

    def test_replaces_one_block_with_a_small_reviewed_diff(self):
        results = self.run_tool(call("edit_file", path="app.py", old_text="    return 'ciao'\n",
                                     new_text="    return 'ciao, Luigi'\n"))
        self.assertIn("Successfully updated app.py", results[0])
        self.assertEqual(self.file.read_text(encoding="utf-8"),
                         "def greet():\n    return 'ciao, Luigi'\n\nprint(greet())\n")
        diff = self.reviews[0]["diff"]
        self.assertIn("-    return 'ciao'", diff)
        self.assertIn("+    return 'ciao, Luigi'", diff)
        self.assertEqual(self.reviews[0]["kind"], "write_file")

    def test_missing_ambiguous_and_declined_edits_change_nothing(self):
        original = self.file.read_text(encoding="utf-8")
        results = self.run_tool(
            call("edit_file", path="app.py", old_text="return 'hello'", new_text="x"),
            call("edit_file", path="app.py", old_text="greet", new_text="salute"),
            call("edit_file", path="new.py", old_text="a", new_text="b"),
        )
        self.assertIn("was not found", results[0])
        self.assertIn("appears 2 times", results[1])
        self.assertIn("Use write_file", results[2])
        self.assertEqual(self.reviews, [])
        results = self.run_tool(call("edit_file", path="app.py", old_text="'ciao'", new_text="'hi'"), approve=False)
        self.assertIn("declined", results[0])
        self.assertEqual(self.file.read_text(encoding="utf-8"), original)

    def test_replace_all_and_crlf_files(self):
        self.run_tool(call("edit_file", path="app.py", old_text="greet", new_text="salute", replace_all=True))
        self.assertEqual(self.file.read_text(encoding="utf-8").count("salute"), 2)
        windows = self.root / "win.txt"
        windows.write_bytes(b"first\r\nsecond\r\nthird\r\n")
        self.run_tool(call("edit_file", path="win.txt", old_text="first\nsecond\n", new_text="one\ntwo\n"))
        self.assertEqual(windows.read_bytes(), b"one\r\ntwo\r\nthird\r\n")

    def test_bad_arguments_are_reported_to_the_model(self):
        results = self.run_tool(call("edit_file", path="app.py", old_text="", new_text="x"),
                                call("edit_file", path="../outside.py", old_text="a", new_text="b"),
                                call("edit_file", path="app.py", old_text="greet", new_text="x", replace_all="yes"))
        self.assertIn("old_text copied exactly", results[0])
        self.assertIn("inside the selected project", results[1])
        self.assertIn("replace_all must be true or false", results[2])


if __name__ == "__main__":
    unittest.main()
