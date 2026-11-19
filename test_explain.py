"""Errors in plain words (app_core.explain)."""

import unittest

from app_core.explain import ACTION_LABELS, explain


class ExplainTests(unittest.TestCase):
    def test_common_problems_get_a_title_help_and_one_fix(self):
        cases = {
            "Ollama is not installed. Install Ollama, then click Refresh models.": ("Ollama is not installed", "get_started"),
            "Could not connect to Ollama at http://localhost:11434. Start Ollama or run 'ollama serve', then try again.":
                ("Ollama is not answering", "refresh"),
            "Could not load models from http://localhost:11434. Start Ollama, then click Refresh.": ("Ollama is not answering", "refresh"),
            "Ollama could not complete the response: model requires more system memory (12 GiB) than is available (6 GiB)":
                ("The model needs more memory than is free", "find_model"),
            "Ollama returned HTTP 404: model 'qwen3:99b' not found": ("This model is not available", "find_model"),
            "This model does not advertise tool calling. Select Qwen Coder or another tool-capable model in the sidebar.":
                ("This model cannot use tools", "find_model"),
            "Internet access was not approved, so it was blocked (Internet connection: example.com).":
                ("Blocked: going online was not approved", None),
            "The Ollama request timed out.": ("The model took too long", "find_model"),
            "The chat's document folder could not be used: The document folder is missing or cannot be read.":
                ("The chat's document folder could not be read", "documents"),
            "Could not reach Colibri. Start coli serve and check the server URL.": ("Colibri is not answering", "refresh"),
        }
        for error, (title, action) in cases.items():
            with self.subTest(error=error[:40]):
                explained = explain(error)
                self.assertEqual((explained["title"], explained["action"]), (title, action))
                self.assertTrue(explained["help"].endswith("."))
                if action:
                    self.assertIn(action, ACTION_LABELS)

    def test_unknown_errors_are_not_guessed(self):
        for error in ("Something unexpected", "", None, "Could not save chat 123: disk full"):
            self.assertIsNone(explain(error))


if __name__ == "__main__":
    unittest.main()
