"""Preview paths and lifetime tested with injected windows, never real UI."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit

import test_webui_app as fixtures
from webui.desktop import Desktop


class PreviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.window = SimpleNamespace(load_url=Mock(), destroy=Mock())
        self.create = Mock(return_value=self.window)
        self.api._desktop = Desktop(self.create)
        self.api._preview.desktop = self.api._desktop

    def test_project_file_uri_reuses_window_and_rejects_escapes(self):
        root = self.store.directory / "project"
        root.mkdir()
        page = root / "index.html"
        page.write_text("<h1>Preview</h1>")
        self.assertEqual(self.api.preview_file(str(root), "index.html")["uri"], page.as_uri())
        self.assertTrue(self.api.preview_file(str(root), "index.html")["ok"])
        self.create.assert_called_once()
        self.assertIsNone(self.create.call_args.kwargs["js_api"])
        self.window.load_url.assert_called_once_with(page.as_uri())
        self.assertFalse(self.api.preview_file(str(root), "../outside.html")["ok"])
        self.assertFalse(self.api.preview_file(str(root), str(page))["ok"])
        (root / "a.py").write_text("code")
        self.assertFalse(self.api.preview_file(str(root), "a.py")["ok"])

    def test_generated_file_is_under_app_data_updated_and_cleaned_on_close(self):
        result = self.api.preview_code("<h1>first</h1>")
        self.assertTrue(result["ok"], result)
        target = Path(self.api._preview.temporary.name) / "index.html"
        self.assertTrue(target.is_relative_to(self.store.directory))
        self.assertEqual(target.read_text(), "<h1>first</h1>")
        self.assertTrue(self.api.preview_code("<p>second</p>")["ok"])
        self.assertEqual(target.read_text(), "<p>second</p>")
        self.api._close()
        self.assertFalse(target.exists())
        self.window.destroy.assert_called_once()
        self.assertFalse(self.api.preview_code("later")["ok"])

    def test_failed_open_removes_owned_temp_file(self):
        self.create.side_effect = RuntimeError("No webview")
        self.assertFalse(self.api.preview_code("<p>x</p>")["ok"])
        self.assertIsNone(self.api._preview.temporary)
        self.assertEqual(list(self.store.directory.glob(".html-preview-*")), [])


if __name__ == "__main__":
    unittest.main()
