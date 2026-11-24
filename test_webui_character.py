"""The first-launch character step: choices are checked first and Blender runs as a background job."""
import json
import threading
import unittest

import test_webui_app

PNG = b"\x89PNG\r\n\x1a\nfake"


class CharacterApiTests(unittest.TestCase):
    def setUp(self):
        test_webui_app.ApiTests.setUp(self)
        self.builds = []
        self.blender = "/opt/blender/blender"
        self.api._find_blender = lambda: self.blender

        def build(choices, folder, blender):
            self.builds.append((choices, blender, threading.current_thread().name))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "portrait.png").write_bytes(PNG)
            (folder / "character.json").write_text(json.dumps({**choices, "skin": "#d9a07a"}), encoding="utf-8")
            return {"portrait": str(folder / "portrait.png")}
        self.api._build_character = build

    def test_state_without_blender_offers_the_step_but_cannot_build(self):
        self.blender = None
        state = self.api.character_state()
        self.assertTrue(state["ok"])
        self.assertFalse(state["blender"])
        self.assertIsNone(state["portrait"])
        self.assertIn("headphones", state["options"]["accessory"])
        result = self.api.build_character({"name": "Luca"})
        self.assertFalse(result["ok"])
        self.assertIn("blender.org", result["error"])
        self.assertEqual(self.builds, [])

    def test_bad_choices_are_refused_before_blender_starts(self):
        for choices in ({"hair_style": "mohawk"}, {"top_color": "red"}, {"script": "x"}, "not an object"):
            self.assertFalse(self.api.build_character(choices)["ok"])
        self.assertEqual(self.builds, [])

    def test_build_runs_in_the_background_and_returns_the_portrait(self):
        started = self.api.build_character({"name": "Luca", "skin": "medium", "accessory": "headphones"})
        self.assertTrue(started["ok"])
        done = self.events.wait("job_done")
        self.assertEqual(done["operation"], "character")
        self.assertTrue(done["result"]["portrait"].startswith("data:image/png;base64,"))
        choices, blender, thread = self.builds[0]
        self.assertEqual(blender, "/opt/blender/blender")
        self.assertNotEqual(thread, threading.current_thread().name)
        state = self.api.character_state()
        self.assertEqual(state["choices"]["name"], "Luca")
        self.assertEqual(state["choices"]["skin"], "medium")  # the saved colour maps back to its tone
        self.assertEqual(state["portrait"], done["result"]["portrait"])
        self.assertFalse(state["building"])


if __name__ == "__main__":
    unittest.main()
