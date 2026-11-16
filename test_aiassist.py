import unittest

from workspace_ui import (
    POLL_ACTIVE_INTERVAL_MS, POLL_IDLE_INTERVAL_MS, _response_poll_interval, _history_matches,
)


class ResponsePollingTests(unittest.TestCase):
    def test_idle_polling_uses_lower_frequency(self):
        self.assertEqual(_response_poll_interval(False, False, False, False), POLL_IDLE_INTERVAL_MS)

    def test_active_work_keeps_responsive_polling(self):
        for state in (
            (True, False, False, False),
            (False, True, False, False),
            (False, False, True, False),
            (False, False, False, True),
        ):
            with self.subTest(state=state):
                self.assertEqual(_response_poll_interval(*state), POLL_ACTIVE_INTERVAL_MS)


class HistorySearchTests(unittest.TestCase):
    def test_searches_fields_and_unicode_messages(self):
        record = dict(title="Plan", model="alpha", project_path="C:/project", draft="Unsent",
                      messages=[dict(content="Straße"), dict(content="Later response")])
        for query in ("", "plan", "alpha", "project", "unsent", "strasse", "response"):
            with self.subTest(query=query):
                self.assertTrue(_history_matches(record, query))
        self.assertFalse(_history_matches(record, "missing"))

    def test_title_match_does_not_scan_messages(self):
        record = dict(title="Plan", model="alpha", messages=[{}])
        self.assertTrue(_history_matches(record, "plan"))


if __name__ == "__main__":
    unittest.main()
