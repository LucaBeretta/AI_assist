"""Headless tests for dashboard monitor restore and fallback behavior."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dashboard_placement import (
    DashboardPlacement, _screen, choose_restored_geometry,
    connected_screens, geometry_string, select_saved_screen,
)


PRIMARY = _screen("mac-main", 0, 0, 1440, 900, True)
EXTERNAL = _screen("mac-external", -800, 100, 800, 480, False)


def saved_for(screen=EXTERNAL, x=-790, y=110, w=780, h=440):
    return {"version": 1, "screen": screen,
            "window": {"x": x, "y": y, "width": w, "height": h}}


class MonitorPlacementTests(unittest.TestCase):
    def test_restores_exact_secondary_display_with_negative_x(self):
        rect = choose_restored_geometry(saved_for(), [PRIMARY, EXTERNAL])
        self.assertEqual(rect, {"x": -790, "y": 110, "width": 776, "height": 426})
        self.assertEqual(geometry_string(rect), "776x426-790+110")

    def test_missing_external_falls_back_visible_on_primary(self):
        rect = choose_restored_geometry(saved_for(), [PRIMARY])
        self.assertGreaterEqual(rect["x"], PRIMARY["x"])
        self.assertGreaterEqual(rect["y"], PRIMARY["y"])
        self.assertLessEqual(rect["x"] + rect["width"], PRIMARY["width"])
        self.assertLessEqual(rect["y"] + rect["height"], PRIMARY["height"])
        self.assertGreaterEqual(rect["width"], 480)
        self.assertGreaterEqual(rect["height"], 320)

    def test_reconnected_display_matches_by_geometry(self):
        replacement = {**EXTERNAL, "id": "changed-after-reboot"}
        self.assertIs(select_saved_screen(EXTERNAL, [PRIMARY, replacement]), replacement)
        rect = choose_restored_geometry(saved_for(), [PRIMARY, replacement])
        self.assertLess(rect["x"], 0)

    def test_unique_screen_resolution_handles_monitor_rearrangement(self):
        moved = {**EXTERNAL, "id": "renamed", "x": 1440, "y": -100}
        rect = choose_restored_geometry(saved_for(), [PRIMARY, moved])
        self.assertGreaterEqual(rect["x"], 1440)
        self.assertGreaterEqual(rect["y"], -100)

    def test_ambiguous_monitor_does_not_guess(self):
        twin = {**EXTERNAL, "id": "twin", "x": -1600}
        unknown = {**EXTERNAL, "id": "disconnected", "x": -2000}
        self.assertIsNone(select_saved_screen(unknown, [PRIMARY, EXTERNAL, twin]))

    def test_corrupt_display_info_never_crashes(self):
        bad = saved_for()
        bad["screen"]["x"] = "not-a-coordinate"
        rect = choose_restored_geometry(bad, [PRIMARY])
        self.assertGreaterEqual(rect["x"], 0)

    def test_empty_screen_list_does_not_restore(self):
        self.assertIsNone(choose_restored_geometry(saved_for(), []))

    def test_smaller_monitor_clamps_window(self):
        tiny = _screen("tiny", 0, 0, 500, 350, True)
        saved = saved_for(tiny, x=300, y=300, w=2500, h=1500)
        rect = choose_restored_geometry(saved, [tiny])
        self.assertLessEqual(rect["x"] + rect["width"], 500)
        self.assertLessEqual(rect["y"] + rect["height"], 350)


class PlacementPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "monitor" / "placement.json"
        self.prefs = DashboardPlacement(self.path)

    def test_save_then_restore_on_same_monitor(self):
        window = Mock()
        window.state.return_value = "normal"
        window.winfo_x.return_value = -750
        window.winfo_y.return_value = 120
        window.winfo_width.return_value = 600
        window.winfo_height.return_value = 350
        with patch("dashboard_placement.connected_screens", return_value=[PRIMARY, EXTERNAL]):
            self.assertTrue(self.prefs.save(window, Mock()))
            restored = self.prefs.restore(Mock())
        self.assertEqual(restored["x"], -750)
        self.assertEqual(restored["y"], 120)
        self.assertEqual(restored["width"], 600)
        self.assertEqual(restored["height"], 350)

    def test_fullscreen_or_hidden_window_not_saved(self):
        window = Mock()
        for state in ("withdrawn", "iconic", "zoomed"):
            window.state.return_value = state
            self.assertFalse(self.prefs.save(window, Mock()))
        self.assertFalse(self.path.exists())

    def test_offscreen_window_not_saved(self):
        window = Mock()
        window.state.return_value = "normal"
        window.winfo_x.return_value = -3000
        window.winfo_y.return_value = 500
        window.winfo_width.return_value = 600
        window.winfo_height.return_value = 350
        with patch("dashboard_placement.connected_screens", return_value=[PRIMARY, EXTERNAL]):
            self.assertFalse(self.prefs.save(window, Mock()))
        self.assertFalse(self.path.exists())

    def test_corrupt_or_oversize_settings_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{", encoding="utf-8")
        self.assertIsNone(self.prefs.load())
        self.path.write_bytes(b"a" * 5000)
        self.assertIsNone(self.prefs.load())

    def test_untrusted_invalid_coordinate_ignored(self):
        self.path.parent.mkdir(parents=True)
        bad = saved_for()
        bad["window"]["x"] = True
        self.path.write_text(json.dumps(bad), encoding="utf-8")
        self.assertIsNone(self.prefs.load())

    def test_fallback_to_tk_when_native_discovery_fails(self):
        root = Mock()
        root.winfo_vrootx.return_value = 0
        root.winfo_vrooty.return_value = 0
        root.winfo_vrootwidth.return_value = 1920
        root.winfo_vrootheight.return_value = 1080
        with patch("dashboard_placement.platform.system", return_value="Darwin"), patch(
            "dashboard_placement._mac_screens", side_effect=OSError("display disconnected")
        ):
            screens = connected_screens(root)
        self.assertEqual(screens[0]["id"], "tk-desktop")


if __name__ == "__main__":
    unittest.main()
