"""Tests for manual, off-by-default LAN sharing lifecycle."""
import unittest
from unittest.mock import Mock

from sharing_control import ManualSharingController, SharingStateError


class ManualSharingTests(unittest.TestCase):
    def setUp(self):
        self.host = Mock()
        self.factory = Mock(return_value=self.host)
        self.controller = ManualSharingController(self.factory)

    def test_constructing_controller_does_not_create_host_or_open_listener(self):
        self.assertEqual(self.controller.state, "off")
        self.assertFalse(self.controller.enabled)
        self.factory.assert_not_called()
        self.host.start.assert_not_called()

    def test_default_enable_is_refused(self):
        with self.assertRaisesRegex(SharingStateError, "explicit user action"):
            self.controller.enable()
        self.factory.assert_not_called()
        with self.assertRaises(SharingStateError):
            self.controller.enable(user_initiated=False)
        self.factory.assert_not_called()

    def test_explicit_enable_starts_once(self):
        self.assertTrue(self.controller.enable(user_initiated=True))
        self.assertTrue(self.controller.enabled)
        self.assertEqual(self.controller.state, "on")
        self.factory.assert_called_once_with()
        self.host.start.assert_called_once_with()
        self.assertFalse(self.controller.enable(user_initiated=True))
        self.host.start.assert_called_once_with()

    def test_stop_releases_host_and_never_restarts(self):
        self.controller.enable(user_initiated=True)
        self.assertTrue(self.controller.disable())
        self.assertFalse(self.controller.enabled)
        self.host.stop.assert_called_once_with()
        self.assertFalse(self.controller.disable())
        self.assertFalse(self.controller.close())
        self.factory.assert_called_once_with()

    def test_app_shutdown_stops_sharing(self):
        self.controller.enable(user_initiated=True)
        self.assertTrue(self.controller.close())
        self.assertEqual(self.controller.state, "off")
        self.host.stop.assert_called_once_with()

    def test_failed_start_cleans_up_and_resets(self):
        self.host.start.side_effect = OSError("blocked by firewall")
        with self.assertRaisesRegex(OSError, "blocked"):
            self.controller.enable(user_initiated=True)
        self.assertFalse(self.controller.enabled)
        self.assertEqual(self.controller.state, "off")
        self.host.stop.assert_called_once_with()

    def test_failed_factory_leaves_sharing_off(self):
        self.factory.side_effect = RuntimeError("no interface")
        with self.assertRaisesRegex(RuntimeError, "no interface"):
            self.controller.enable(user_initiated=True)
        self.assertEqual(self.controller.state, "off")
        self.host.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
