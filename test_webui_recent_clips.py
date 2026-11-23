"""Exercise Recent clips using local files and a simulated ComfyUI render."""

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import test_webui_app as fixtures
from test_webui_save_animation import MP4_HEADER, WEBM_HEADER
from webui.desktop import Desktop
from webui.recent_clips import MAX_RECENT, RecentClips


def encoded(data):
    return base64.b64encode(data).decode("ascii")


class RecentClipsTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.path = self.store.directory / "my clip.webm"
        self.dialog = Mock(return_value=[str(self.path)])
        self.api._desktop = Desktop(save_dialog=self.dialog)
        self.api._desktop.open_file = Mock()

    def test_saved_clip_can_be_reopened_and_resaved_without_duplicate(self):
        original = self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")
        self.assertTrue(original["ok"])
        first = self.api.list_recent_clips()["clips"][0]
        updated = WEBM_HEADER + b"new video"
        self.api.save_quick_animation(encoded(updated), "video/webm")

        self.api._recent_clips = RecentClips(self.store.directory)
        clips = self.api.list_recent_clips()["clips"]
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["id"], first["id"])
        self.assertEqual(clips[0]["name"], self.path.name)
        self.assertEqual(clips[0]["kind"], "quick")
        self.assertNotIn("path", clips[0])
        self.assertTrue(clips[0]["available"])
        self.assertEqual(self.api.load_recent_clip(first["id"]), {
            "ok": True, "video": encoded(updated), "mime_type": "video/webm",
        })
        self.assertEqual(self.api.open_recent_clip(first["id"]), {"ok": True})
        self.api._desktop.open_file.assert_called_once_with(self.path.resolve())

    def test_camera_clip_overwrites_quick_history_at_the_same_path(self):
        self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")
        first = self.api.list_recent_clips()["clips"][0]
        recording = WEBM_HEADER + b"camera clip"

        self.dialog.return_value = None
        self.assertEqual(self.api.save_camera_clip(encoded(recording), "video/webm"),
                         {"ok": True, "cancelled": True})
        self.dialog.assert_called_with("camera-scene.webm")
        self.assertEqual(self.api.list_recent_clips()["clips"], [first])
        self.assertEqual(self.path.read_bytes(), WEBM_HEADER)

        self.dialog.return_value = [str(self.path)]
        self.assertEqual(self.api.save_camera_clip(encoded(recording), "video/webm"),
                         {"ok": True, "path": str(self.path)})
        self.api._recent_clips = RecentClips(self.store.directory)
        [item] = self.api.list_recent_clips()["clips"]
        self.assertEqual(item["id"], first["id"])
        self.assertEqual(item["kind"], "camera")
        self.assertEqual(item["prompt"], "")
        self.assertIs(item["portrait"], False)
        self.assertEqual(self.api.load_recent_clip(item["id"]), {
            "ok": True, "video": encoded(recording), "mime_type": "video/webm",
        })

    def test_cancelled_save_and_missing_file(self):
        self.dialog.return_value = None
        self.assertTrue(self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")["cancelled"])
        self.assertEqual(self.api.list_recent_clips()["clips"], [])
        self.dialog.return_value = str(self.path)
        self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")
        identity = self.api.list_recent_clips()["clips"][0]["id"]
        self.path.unlink()
        self.assertFalse(self.api.list_recent_clips()["clips"][0]["available"])
        self.assertFalse(self.api.load_recent_clip(identity)["ok"])
        self.assertFalse(self.api.open_recent_clip(identity)["ok"])
        self.assertFalse(self.api.load_recent_clip(str(self.path))["ok"])
        self.api._desktop.open_file.assert_not_called()

    def test_renamed_nonvideo_and_symlink_are_refused(self):
        self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")
        identity = self.api.list_recent_clips()["clips"][0]["id"]
        self.path.write_bytes(b"not really a video")
        self.assertFalse(self.api.load_recent_clip(identity)["ok"])
        self.assertFalse(self.api.open_recent_clip(identity)["ok"])
        self.path.unlink()
        target = self.path.with_name("other clip.webm")
        target.write_bytes(WEBM_HEADER)
        try:
            self.path.symlink_to(target)
        except OSError:
            return  # Windows may deny the ability to create a symlink.
        self.assertFalse(self.api.list_recent_clips()["clips"][0]["available"])
        self.assertFalse(self.api.load_recent_clip(identity)["ok"])
        self.api._desktop.open_file.assert_not_called()

    def test_wan_render_survives_comfyui_and_concurrent_completion(self):
        job = str(uuid4())
        generation = {"ready": True, "video": encoded(MP4_HEADER), "name": f"{job}_00001_.mp4"}
        with patch("app_core.photo_video.start", return_value=job):
            self.assertEqual(self.api.start_photo_video("photo", " Turn left. ", True)["job"], job)
        with patch("app_core.photo_video.status", return_value={"ready": False}):
            self.assertFalse(self.api.photo_video_status(job)["ready"])
            self.assertEqual(self.api.list_recent_clips()["clips"], [])
        with patch("app_core.photo_video.status", side_effect=lambda _: dict(generation)):
            with ThreadPoolExecutor(2) as threads:
                replies = list(threads.map(self.api.photo_video_status, [job, job]))
            self.assertTrue(all(reply["ok"] and reply["ready"] for reply in replies))

        self.api._recent_clips = RecentClips(self.store.directory)
        clips = self.api.list_recent_clips()["clips"]
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["kind"], "wan")
        self.assertEqual(clips[0]["prompt"], "Turn left.")
        self.assertIs(clips[0]["portrait"], True)
        self.assertEqual(base64.b64decode(self.api.load_recent_clip(job)["video"]), MP4_HEADER)
        self.assertEqual((self.store.directory / "generated-clips" / f"{job}.mp4").read_bytes(), MP4_HEADER)

    def test_history_failure_keeps_ready_video(self):
        job = str(uuid4())
        with patch("app_core.photo_video.start", return_value=job):
            self.api.start_photo_video("photo", "Turn left.")
        generation = {"ready": True, "video": encoded(MP4_HEADER), "name": "generated.mp4"}
        with (patch("app_core.photo_video.status", return_value=generation),
              patch.object(self.api._recent_clips, "remember_wan", side_effect=OSError("disk full"))):
            with self.assertLogs("webui.app", "WARNING"):
                result = self.api.photo_video_status(job)
        self.assertTrue(result["ready"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["history_error"])
        self.assertEqual(result["video"], encoded(MP4_HEADER))

    def test_retaining_recent_entries_keeps_older_saved_files(self):
        clips = RecentClips(self.store.directory)
        for index in range(MAX_RECENT + 2):
            path = Path(self.store.directory) / f"{index}.mp4"
            path.write_bytes(MP4_HEADER)
            clips.remember_recording(path, kind="quick")
        self.assertEqual(len(clips.list()), MAX_RECENT)
        self.assertEqual(clips.list()[0]["name"], f"{MAX_RECENT + 1}.mp4")
        self.assertTrue((self.store.directory / "0.mp4").is_file())

    def test_dropped_ai_video_copy_is_deleted_but_saved_files_stay(self):
        clips = RecentClips(self.store.directory)
        job = str(uuid4())
        clips.remember_wan(job, {"video": encoded(MP4_HEADER), "name": "render.mp4"}, "Turn left.", False)
        copy = self.store.directory / "generated-clips" / f"{job}.mp4"
        self.assertTrue(copy.is_file())
        for index in range(MAX_RECENT):
            path = Path(self.store.directory) / f"{index}.mp4"
            path.write_bytes(MP4_HEADER)
            clips.remember_recording(path, kind="camera")
        self.assertNotIn(job, [clip["id"] for clip in clips.list()])
        self.assertFalse(copy.exists())
        self.assertTrue((self.store.directory / "0.mp4").is_file())

    def test_ai_video_copy_is_removed_when_the_list_cannot_be_saved(self):
        clips = RecentClips(self.store.directory)
        generation = {"video": encoded(MP4_HEADER), "name": "render.mp4"}
        with patch("webui.recent_clips.write_utf8", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                clips.remember_wan(str(uuid4()), generation, "Turn left.", False)
        self.assertEqual(list((self.store.directory / "generated-clips").iterdir()), [])
        self.assertEqual(clips.list(), [])

    def test_open_failure_is_explained_plainly(self):
        self.api.save_quick_animation(encoded(WEBM_HEADER), "video/webm")
        identity = self.api.list_recent_clips()["clips"][0]["id"]
        del self.api._desktop.open_file
        with (patch("webui.desktop.os.startfile", side_effect=OSError("no app"), create=True),
              patch("webui.desktop.subprocess.run", side_effect=subprocess.CalledProcessError(1, "open")),
              patch("webui.desktop.subprocess.Popen", side_effect=OSError("no xdg-open")),
              self.assertLogs("webui.desktop", "WARNING")):
            result = self.api.open_recent_clip(identity)
        self.assertEqual(result, {"ok": False, "error": "Could not open the file in your default app."})
