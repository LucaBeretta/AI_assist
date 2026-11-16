import unittest

from workspace_ui import (
    POLL_ACTIVE_INTERVAL_MS, POLL_IDLE_INTERVAL_MS, _response_poll_interval,
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


if __name__ == "__main__":
    unittest.main()
