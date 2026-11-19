"""Save dialogs are injected and output is atomic UTF-8 without a BOM."""

import unittest
from unittest.mock import Mock, patch

import test_webui_app as fixtures
from webui.desktop import Desktop


class SaveCodeTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_save_unicode_and_cancel_without_any_real_dialog(self):
        target = self.store.directory / "source.py"
        dialog = Mock(return_value=[str(target)])
        self.api._desktop = Desktop(save_dialog=dialog)
        text = "# caffè\nprint('hello')\n"
        self.assertTrue(self.api.save_code(text, "source.py")["ok"])
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))
        dialog.assert_called_once_with("source.py")
        dialog.return_value = None
        self.assertTrue(self.api.save_code(text, "source.py")["cancelled"])

    def test_invalid_text_directory_and_failed_replace_do_not_destroy_the_file(self):
        target = self.store.directory / "source.py"
        target.write_text("original", encoding="utf-8")
        dialog = Mock(return_value=str(target))
        self.api._desktop = Desktop(save_dialog=dialog)
        for text, name in (("", "a.py"), (None, "a.py"), ("code", "../a.py"), ("code", "a\x00.py")):
            self.assertFalse(self.api.save_code(text, name)["ok"])
        dialog.assert_not_called()
        with patch("webui.local_files.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(self.api.save_code("replacement", "a.py")["ok"])
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(list(self.store.directory.glob("tmp*")), [])
        dialog.return_value = str(self.store.directory)
        self.assertFalse(self.api.save_code("code", "a.py")["ok"])


if __name__ == "__main__":
    unittest.main()
