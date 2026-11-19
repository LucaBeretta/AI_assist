import tempfile
from pathlib import Path, PurePosixPath
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

    def test_the_child_gate_loader_is_checked_but_other_subfolders_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "AIassist.py").write_bytes(b"app")
            Path(folder, "child_gate").mkdir()
            Path(folder, "child_gate", "sitecustomize.py").write_bytes(b"old")
            result = self.check(folder, {"AIassist.py": b"app", "child_gate/sitecustomize.py": b"new",
                                         "docs/tool.py": b"not shipped"})
        self.assertEqual(result["differences"], ["child_gate/sitecustomize.py"])

    def test_the_apps_packages_and_web_page_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "AIassist.py").write_bytes(b"app")
            Path(folder, "webui", "static").mkdir(parents=True)
            Path(folder, "webui", "static", "app.js").write_bytes(b"old")
            result = self.check(folder, {"AIassist.py": b"app", "app_core/chat_service.py": b"new",
                                         "webui/server.py": b"new", "webui/static/app.js": b"new",
                                         "webui/static/styles.css": b"new", "webui/tests/markdown.test.mjs": b"test",
                                         "app_core/test_x.py": b"test", "webui/static/notes.md": b"doc"})
        self.assertEqual(sorted(result["differences"]), ["app_core/chat_service.py", "webui/server.py",
                                                        "webui/static/app.js", "webui/static/styles.css"])

    def test_every_shipped_file_in_this_copy_is_recognised(self):
        from update_checker import is_shipped
        here = Path(__file__).resolve().parent
        for folder, pattern in (("app_core", "*.py"), ("webui", "*.py"), ("webui/static", "*")):
            for path in sorted((here / folder).glob(pattern)):
                if path.is_file():
                    with self.subTest(path=path.name):
                        self.assertTrue(is_shipped(PurePosixPath(path.relative_to(here).as_posix())))

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
