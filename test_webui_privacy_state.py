"""Settings > Privacy shows a current firewall state without a Refresh button: it re-reads by itself when needed."""
import time
import unittest

import test_webui_app


class FakeLock:
    def __init__(self, summary):
        self.summary, self.busy, self.reads = summary, None, 0

    def poll(self):
        return []

    def refresh(self):
        self.reads += 1


class PrivacyStateTests(unittest.TestCase):
    def setUp(self):
        test_webui_app.ApiTests.setUp(self)

    def use(self, summary):
        self.api._firewall = FakeLock(summary)
        self.api._firewall_read_at = 0.0
        return self.api._firewall

    def test_a_known_good_state_is_not_read_again(self):
        lock = self.use({"state": "on", "lift_until": None})
        self.api.privacy_state()
        self.assertEqual(lock.reads, 0)

    def test_unknown_or_failed_reads_are_retried_at_most_once_a_minute(self):
        lock = self.use(None)
        self.api.privacy_state()
        self.assertEqual(lock.reads, 1)
        lock.summary = {"state": "error", "detail": "Could not read Windows Firewall"}
        self.api._firewall_read_at = 0.0
        self.api.privacy_state()
        self.api.privacy_state()  # the second look within a minute does not start another read
        self.assertEqual(lock.reads, 2)

    def test_an_expired_lift_is_read_again(self):
        lock = self.use({"state": "lifted", "lift_until": time.time() - 5})
        self.api.privacy_state()
        self.assertEqual(lock.reads, 1)
        lock = self.use({"state": "lifted", "lift_until": time.time() + 600})
        self.api.privacy_state()
        self.assertEqual(lock.reads, 0)


if __name__ == "__main__":
    unittest.main()
