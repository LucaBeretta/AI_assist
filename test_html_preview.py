"""Local preview HTTP and browser handoff, without launching a real browser."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from html_preview import HtmlPreviews, PreviewServer


class HtmlPreviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "index.html").write_text('<h1>Preview</h1><link rel="stylesheet" href="style.css">', encoding="utf-8")
        (self.root / "style.css").write_text("h1 { color: red; }", encoding="utf-8")

    def test_project_html_and_relative_assets_are_served_without_cache(self):
        server = PreviewServer(self.root)
        self.addCleanup(server.close)
        url = server.url("index.html")
        with urllib.request.urlopen(url) as response:
            self.assertIn(b"Preview", response.read())
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertTrue(response.headers["Content-Security-Policy"].startswith("default-src 'self'"))
        with urllib.request.urlopen(url.replace("index.html", "style.css")) as response:
            self.assertIn(b"red", response.read())
        (self.root / "index.html").write_text("Updated", encoding="utf-8")
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.read(), b"Updated")

    def test_secrets_traversal_and_directory_listings_are_blocked(self):
        (self.root / ".env").write_text("SECRET=value", encoding="utf-8")
        (self.root / "empty").mkdir()
        server = PreviewServer(self.root)
        self.addCleanup(server.close)
        base = server.url("index.html").removesuffix("index.html")
        for path in (".env", "%2e%2e/outside.html", "empty/"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        request = urllib.request.Request(base, headers={"Host": "external.example"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_generated_html_opens_in_browser_and_is_cleaned_up(self):
        previews = HtmlPreviews()
        self.addCleanup(previews.close)
        block = dict(language="html", code="<h1>Generated</h1>", complete=True)
        with patch("html_preview.webbrowser.open", return_value=True) as browser:
            url = previews.preview_block(block)
        browser.assert_called_once_with(url, new=2)
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.read(), b"<h1>Generated</h1>")
        folder = Path(previews.temporary.name)
        previews.close()
        self.assertFalse(folder.exists())

    def test_incomplete_or_non_html_blocks_are_not_written(self):
        previews = HtmlPreviews()
        self.addCleanup(previews.close)
        for block in (dict(language="html", complete=False, code="<p>partial"),
                      dict(language="python", complete=True, code="pass")):
            with self.assertRaises(ValueError):
                previews.preview_block(block)
        self.assertIsNone(previews.temporary)
