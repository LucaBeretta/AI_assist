"""Generate → Infinite: requests are checked first, the clip is made as a background job and saved on request."""
import base64
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import infinite_engine
import test_webui_app

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


class InfiniteApiTests(unittest.TestCase):
    def setUp(self):
        test_webui_app.ApiTests.setUp(self)
        self.program = "/Applications/Infinite.app/Contents/MacOS/Infinite"
        self.api._find_infinite = lambda: self.program
        self.api._model_thinks = lambda model: model.startswith("qwen3")
        self.requests = []

        def request(messages, **options):
            self.requests.append((messages, options, threading.current_thread().name))
            return "```inf\ninfinite-patch 1\n```"
        self.engine.request_ollama = request
        self.made = []

        def make_clip(description, ask, program, folder, *, seconds, progress, cancel):
            self.made.append((description, program, seconds))
            ask([{"role": "user", "content": description}])
            progress("writing")
            progress("rendering", seconds=seconds)
            folder.mkdir(parents=True)
            (folder / "patch.inf").write_text("infinite-patch 1\n", encoding="utf-8")
            (folder / "clip.mp4").write_bytes(MP4)
            return {"patch": "infinite-patch 1\n", "video": str(folder / "clip.mp4"), "replies": 1,
                    "warnings": [], "sound": True, "credit": infinite_engine.CREDIT}
        replace = patch.object(infinite_engine, "make_clip", make_clip)
        replace.start()
        self.addCleanup(replace.stop)

    def test_requests_are_checked_before_anything_starts(self):
        self.program = None
        state = self.api.infinite_state()
        self.assertEqual((state["ok"], state["installed"], state["lengths"]), (True, False, [4, 8, 12]))
        missing = self.api.make_infinite_clip("a star", "qwen3.5:9b", 4)
        self.assertIn("github.com/n1m21n/Infinite", missing["error"])
        self.program = "/opt/Infinite"
        for arguments, expected in (((" ", "qwen3.5:9b", 4), "1 to 1,500"), (("x" * 1501, "qwen3.5:9b", 4), "1 to 1,500"),
                                    (("a star", "qwen3.5:9b", 5), "4, 8 or 12"),
                                    (("a star", "", 4), "runs on this computer"),
                                    (("a star", "gpt-oss:120b-cloud", 4), "Cloud models")):
            result = self.api.make_infinite_clip(*arguments)
            self.assertFalse(result["ok"])
            self.assertIn(expected, result["error"])
        self.assertEqual(self.made, [])
        self.assertEqual(self.requests, [])

    def test_a_clip_is_made_in_the_background_and_can_be_played_and_saved(self):
        started = self.api.make_infinite_clip("  a star that spins  ", "qwen3.5:9b", 8)
        self.assertTrue(started["ok"])
        done = self.events.wait("job_done")
        self.assertEqual(done["operation"], "infinite")
        progress = [event for event in self.events.items if event["type"] == "job_progress"]
        self.assertEqual([event["stage"] for event in progress], ["writing", "rendering"])
        self.assertEqual(progress[1]["seconds"], 8)
        result = done["result"]
        self.assertEqual((result["seconds"], result["sound"], result["credit"]), (8, True, infinite_engine.CREDIT))
        self.assertEqual(self.made, [("a star that spins", self.program, 8)])
        messages, options, thread = self.requests[0]
        self.assertEqual(options["model"], "qwen3.5:9b")
        self.assertEqual(options["context_length"], infinite_engine.CONTEXT_LENGTH)
        self.assertIs(options["think"], False)  # a thinking model answers without reasoning first
        self.assertNotEqual(thread, threading.current_thread().name)
        self.assertEqual(base64.b64decode(self.api.infinite_clip(result["clip"])["video"]), MP4)

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "mine.mp4"
            self.api._desktop.save_file = lambda name: (str(target),)
            self.assertEqual(self.api.save_infinite_clip(result["clip"])["path"], str(target))
            self.assertEqual(target.read_bytes(), MP4)
            self.api._desktop.save_file = lambda name: (str(Path(folder) / "mine.inf"),)
            self.api.save_infinite_patch(result["clip"])
            self.assertEqual((Path(folder) / "mine.inf").read_text(encoding="utf-8"), "infinite-patch 1\n")
            self.api._desktop.save_file = lambda name: (str(Path(folder) / "wrong.txt"),)
            self.assertIn(".inf", self.api.save_infinite_patch(result["clip"])["error"])
            self.api._desktop.save_file = lambda name: None
            self.assertTrue(self.api.save_infinite_clip(result["clip"])["cancelled"])

    def test_other_models_keep_ollamas_default_and_the_idle_setting_applies(self):
        self.api._settings["idle_unload_minutes"] = 15
        self.api.make_infinite_clip("a star", "llama3.2:3b", 4)
        self.events.wait("job_done")
        options = self.requests[0][1]
        self.assertNotIn("think", options)
        self.assertEqual(options["keep_alive"], "15m")

    def test_only_clips_made_here_can_be_read(self):
        for clip in ("../../etc", "A" * 32, "0" * 31, None, 7):
            self.assertIn("no longer available", self.api.infinite_clip(clip)["error"])
        self.assertIn("no longer available", self.api.save_infinite_patch("0" * 32)["error"])

    def test_old_clips_are_removed_when_a_new_one_starts(self):
        root = self.api._infinite_folder()
        for number in range(10):
            folder = root / f"{number:032x}"
            folder.mkdir(parents=True)
            os.utime(folder, (time.time() - 100 + number, time.time() - 100 + number))
        (root / "catalogue-2.1.0.json").write_text("{}", encoding="utf-8")
        self.api.make_infinite_clip("a star", "qwen3.5:9b", 4)
        clip = self.events.wait("job_done")["result"]["clip"]
        kept = sorted(item.name for item in root.iterdir())
        self.assertIn(clip, kept)
        self.assertIn("catalogue-2.1.0.json", kept)  # the node list stays
        self.assertEqual(len([name for name in kept if name != "catalogue-2.1.0.json"]), 8)
        self.assertNotIn(f"{0:032x}", kept)


if __name__ == "__main__":
    unittest.main()
