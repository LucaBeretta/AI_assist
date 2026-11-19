"""Standalone dashboard bootstrap tests; no real window or network listener."""
import unittest
from unittest.mock import MagicMock, patch

import launch_compute_dashboard as entry


class StandaloneLauncherTests(unittest.TestCase):
    def test_launch_opens_only_dashboard_and_cleans_up(self):
        root = MagicMock()
        dashboard = MagicMock()
        dashboard.window = MagicMock()
        dashboard.closed = False
        with patch.object(entry.network_gate, "install") as gate, patch.object(
            entry, "create_root", return_value=root
        ), patch.object(entry, "ComputeDashboard", return_value=dashboard) as ctor, patch.object(
            entry, "saved_peers", return_value={}
        ):
            entry.main()
        gate.assert_called_once()
        root.withdraw.assert_called_once()
        ctor.assert_called_once()
        root.wait_window.assert_called_once_with(dashboard.window)
        dashboard.close.assert_called_once()
        root.destroy.assert_called_once()

    def test_failed_window_still_destroys_root(self):
        root = MagicMock()
        with patch.object(entry.network_gate, "install"), patch.object(
            entry, "create_root", return_value=root
        ), patch.object(entry, "ComputeDashboard", side_effect=RuntimeError("window failed")):
            with self.assertRaisesRegex(RuntimeError, "window failed"):
                entry.main()
        root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
