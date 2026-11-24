"""Exercise quick animation export through an injected save dialog."""

import base64
import unittest
from unittest.mock import Mock, patch

import test_webui_app as fixtures
from webui.desktop import Desktop


MP4_HEADER = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
WEBM_HEADER = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm"


class SaveAnimationTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.dialog = Mock()
        self.api._desktop = Desktop(save_dialog=self.dialog)

    def test_save_uses_video_extension_and_preserves_recording_bytes(self):
        for mime, data in (("video/mp4", MP4_HEADER), ("video/webm", WEBM_HEADER)):
            with self.subTest(mime=mime):
                extension = mime.split("/")[1]
                filename = f"quick-animation.{extension}"
                target = self.store.directory / filename
                self.dialog.return_value = [str(target)]
                result = self.api.save_quick_animation(base64.b64encode(data).decode("ascii"), mime)
                self.assertEqual(result, {"ok": True, "path": str(target)})
                self.assertEqual(target.read_bytes(), data)
                self.dialog.assert_called_with(filename)

    def test_invalid_or_oversized_recordings_do_not_open_a_dialog(self):
        mp4 = base64.b64encode(MP4_HEADER).decode("ascii")
        for data, mime in (("not base64!", "video/mp4"), (mp4, "video/webm"), (mp4, "text/html")):
            self.assertFalse(self.api.save_quick_animation(data, mime)["ok"])
        with patch("app_core.photo_video.MAX_VIDEO_BYTES", len(MP4_HEADER) - 1):
            self.assertFalse(self.api.save_quick_animation(mp4, "video/mp4")["ok"])
        self.dialog.assert_not_called()

    def test_cancel_and_unexpected_extension_leave_files_untouched(self):
        mp4 = base64.b64encode(MP4_HEADER).decode("ascii")
        self.dialog.return_value = None
        self.assertEqual(self.api.save_quick_animation(mp4, "video/mp4"), {"ok": True, "cancelled": True})
        target = self.store.directory / "different.webm"
        target.write_bytes(WEBM_HEADER)
        self.dialog.return_value = str(target)
        self.assertFalse(self.api.save_quick_animation(mp4, "video/mp4")["ok"])
        self.assertEqual(target.read_bytes(), WEBM_HEADER)


if __name__ == "__main__":
    unittest.main()
