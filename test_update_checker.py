import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from update_checker import blob_hash, check_update, github_json
import urllib.error


class UpdateTests(unittest.TestCase):


    def check(self, directory, files, truncated=False):
        responses = [[{"sha": "a" * 40, "commit": {"tree": {"sha": "b" * 40}}}],
                     {"tree": [{"path": name, "type": "blob", "sha": blob_hash(data)}
                               for name, data in files.items()], "truncated": truncated}]
        with patch("update_checker.github_json", side_effect=responses):
            return check_update(directory)

    def test_zip_without_git_and_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "AIassist.py").write_bytes(b"print('hello')\r\n")
            result = self.check(folder, {"AIassist.py": b"print('hello')\n", "test_demo.py": b"test"})
            self.assertEqual(result["differences"], [])
            self.assertTrue(result["download"].endswith("a" * 40 + ".zip"))

    def test_missing_and_changed_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "AIassist.py").write_bytes(b"old")
            result = self.check(folder, {"AIassist.py": b"new", "new_module.py": b"new"})
            self.assertEqual(result["differences"], ["AIassist.py", "new_module.py"])

    def test_incomplete_tree_is_not_reported_as_current(self):
        with self.assertRaises(ValueError):
            self.check(".", {"AIassist.py": b"app"}, truncated=True)

    def test_rate_limit_has_helpful_message(self):
        error = urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        with patch("update_checker.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "limit"):
                github_json("/commits?per_page=1")


if __name__ == "__main__":
    unittest.main()
