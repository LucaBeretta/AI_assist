"""Headless layout and visual-render regression checks for the small-screen UI."""

from collections import deque
import unittest

from compute_dashboard import (
    ComputeDashboard, dashboard_layout, _value_ratio, _short,
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


class FakeCanvas:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.calls = []

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))

    def create_text(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))

    def create_rectangle(self, *args, **kwargs):
        self.calls.append(("rectangle", args, kwargs))

    def create_oval(self, *args, **kwargs):
        self.calls.append(("oval", args, kwargs))

    def create_arc(self, *args, **kwargs):
        self.calls.append(("arc", args, kwargs))

    def create_line(self, *args, **kwargs):
        self.calls.append(("line", args, kwargs))


class DrawTests(unittest.TestCase):
    def render(self, size, *, values=True):
        instance = object.__new__(ComputeDashboard)
        instance.closed = False
        instance.canvas = FakeCanvas(*size)
        instance.peer_rows = [{"id": "trusted_1", "endpoint": "192.168.1.2:49381",
                               "state": "paired · live telemetry unavailable"}]
        instance.history = {
            "cpu": deque([0.1, 0.3, None, 0.5], maxlen=30),
            "gpu": deque([0.2, 0.8], maxlen=30),
            "ram": deque([0.2, 0.25], maxlen=30),
        }
        instance.latest = ({
            "cpu": {"name": "CPU", "percent": 50, "logical_cores": 16},
            "ram": {"total_bytes": 16 * 1024 ** 3,
                    "available_bytes": 12 * 1024 ** 3},
            "gpus": [{"name": "Demo GPU", "percent": 75,
                      "used_bytes": 10 * 1024 ** 3,
                      "total_bytes": 24 * 1024 ** 3}],
            "models": {"available": True, "installed": ["qwen:8b", "gemma:4b"],
                       "loaded": ["qwen:8b"]},
        } if values else {})
        instance.redraw()
        return instance.canvas.calls

    def test_compact_view_renders_all_sections_without_tk_window(self):
        for size in ((480, 230), (800, 400), (1024, 540)):
            with self.subTest(size=size):
                calls = self.render(size)
                labels = [call[2].get("text") for call in calls if call[0] == "text"]
                for label in ("CPU", "GPU", "RAM", "MODELS", "PAIRED NODES"):
                    self.assertIn(label, labels)
                self.assertTrue(any(call[0] == "arc" for call in calls))
                self.assertTrue(any(call[0] == "line" for call in calls))

    def test_unavailable_is_not_fabricated_zero_or_online(self):
        labels = [call[2].get("text") for call in self.render((480, 230), values=False)
                  if call[0] == "text"]
        self.assertNotIn("0.0%", labels)
        self.assertNotIn("ONLINE", labels)
        self.assertIn("OLLAMA UNAVAILABLE", labels)

    def test_tiny_canvas_safely_prompts_resize(self):
        labels = [call[2].get("text") for call in self.render((80, 70))
                  if call[0] == "text"]
        self.assertIn("Resize window for compute metrics", labels)


if __name__ == "__main__":
    unittest.main()
