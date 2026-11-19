"""Validated web preferences are durable and cannot overwrite internal state."""

import unittest
import test_webui_app as fixtures
from webui import settings


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_welcome_completion_survives_restart_and_rejects_unknown_fields(self):
        self.api.set_idle_unload_minutes(5)
        self.assertTrue(self.api.set_web_preferences({"first_run_complete": True})["ok"])
        self.assertTrue(settings.load(self.store.directory)["first_run_complete"])
        self.assertEqual(settings.load(self.store.directory)["idle_unload_minutes"], 5)
        self.assertFalse(self.api.set_web_preferences({"first_run_complete": 1})["ok"])
        self.assertFalse(self.api.set_web_preferences({"window": {}})["ok"])


if __name__ == "__main__":
    unittest.main()
