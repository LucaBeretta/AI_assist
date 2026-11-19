"""Headless layout and visual-render regression checks for the small-screen UI."""

from collections import deque
import unittest

from app_core.compute_values import (
    dashboard_layout, _value_ratio, _short,
)


class GeometryTests(unittest.TestCase):
    def test_all_five_panels_fit_without_scroll(self):
        for width, height in ((480, 230), (600, 260), (800, 400),
                              (1024, 540), (1920, 980)):
            with self.subTest(size=(width, height)):
                layout = dashboard_layout(width, height)
                self.assertEqual(set(layout), {"cpu", "gpu", "ram", "models", "peers"})
                for key, (x, y, w, h) in layout.items():
                    self.assertGreater(w, 0, key)
                    self.assertGreater(h, 0, key)
                    self.assertGreaterEqual(x, 0, key)
                    self.assertGreaterEqual(y, 0, key)
                    self.assertLessEqual(x + w, width, key)
                    self.assertLessEqual(y + h, height, key)
                for key1, (x1, y1, w1, h1) in layout.items():
                    for key2, (x2, y2, w2, h2) in layout.items():
                        if key1 >= key2:
                            continue
                        intersects = (max(x1, x2) < min(x1 + w1, x2 + w2)
                                      and max(y1, y2) < min(y1 + h1, y2 + h2))
                        self.assertFalse(intersects, (key1, key2, width, height))

    def test_gauges_distinguish_missing_from_zero(self):
        self.assertIsNone(_value_ratio(None, 100))
        self.assertIsNone(_value_ratio(200, 100))
        self.assertIsNone(_value_ratio(-2, 100))
        self.assertIsNone(_value_ratio(2, 0))
        self.assertEqual(_value_ratio(0, 100), 0.0)
        self.assertEqual(_value_ratio(50, 100), .5)

    def test_titles_are_safely_shortened(self):
        self.assertEqual(_short("long  model  name", 100), "long model name")
        self.assertEqual(_short("a long model", 5), "a lo…")




if __name__ == "__main__":
    unittest.main()
