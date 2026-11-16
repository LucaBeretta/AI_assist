"""Message layout checks without running generated code or opening windows."""

import tkinter as tk
import unittest

from developer_tools import extract_code_blocks
from message_widgets import _message_segments, _prose_rows, render_message


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class MessageParsingTests(unittest.TestCase):
    def test_code_is_identical_to_shared_extraction_and_prose_is_retained(self):
        text = "Before\n\n````python file=main.py\n# triple ``` inside\n````\nAfter\n~~~js\nlet n = 1;"
        segments = list(_message_segments(text))
        self.assertEqual([kind for kind, value in segments], ["text", "code", "text", "code"])
        self.assertEqual([value for kind, value in segments if kind == "code"], extract_code_blocks(text))
        self.assertEqual(segments[0][1], "Before\n\n")
        self.assertEqual(segments[2][1], "After\n")

    def test_invalid_opening_and_short_closing_are_consistent_with_extractor(self):
        samples = ["```bad`label\nordinary\n", "````text\n```\n~~~\n````\n",
                   "    ```python\n    pass\n    ```\n", "````"]
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual([value for kind, value in _message_segments(text) if kind == "code"],
                                 extract_code_blocks(text))

    def test_headings_bullets_checkboxes_quotes_and_rules_are_readable(self):
        rows = list(_prose_rows("# **Overview**\n- `item`\n  2. second\n- [x] done\n> quoted\n---\n\nBody"))
        self.assertEqual([row["kind"] for row in rows],
                         ["heading", "bullet", "bullet", "bullet", "quote", "rule", "space", "paragraph"])
        self.assertEqual(rows[0]["text"], "Overview")
        self.assertEqual(rows[1]["text"], "item")
        self.assertEqual(rows[2]["marker"], "2.")
        self.assertEqual(rows[3]["marker"], "\u2611")
        self.assertEqual(rows[4]["text"], "quoted")


class FakeApp:
    COLORS = {"surface": "#fffdf9", "user": "#f3eee4"}

    def __init__(self):
        self.exports = []

    def button(self, parent, text, command, bg, fg, hover):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=hover)

    def export_code_block(self, block):
        self.exports.append(block)


class MessageWidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        self.addCleanup(self.root.destroy)
        self.app = FakeApp()

    def test_code_is_readonly_selectable_bounded_and_exported_only_on_click(self):
        code = "".join(f"print({number})\n" for number in range(30))
        block = {"language": "python", "filename": None, "code": code, "complete": True}
        frame = render_message(self.root, "# Example\n```python\n" + code + "```\n", self.app)
        self.assertEqual(frame.winfo_manager(), "")
        widgets = list(descendants(frame))
        code_widget = next(widget for widget in widgets if isinstance(widget, tk.Text))
        self.assertEqual(code_widget.get("1.0", "end-1c"), code)
        self.assertEqual(str(code_widget["state"]), "disabled")
        self.assertTrue(code_widget._aiassist_code)
        self.assertEqual(int(code_widget["width"]), 1)
        self.assertEqual(int(code_widget["height"]), 14)
        self.assertEqual(len([widget for widget in widgets if isinstance(widget, tk.Scrollbar)]), 2)
        self.assertEqual(self.app.exports, [])
        save = next(widget for widget in widgets if isinstance(widget, tk.Button) and widget["text"].startswith("Save as"))
        save.invoke()
        self.assertEqual(self.app.exports, [block])
        copy = next(widget for widget in widgets if isinstance(widget, tk.Button) and widget["text"] == "Copy")
        copy.invoke()
        self.assertEqual(self.root.clipboard_get(), code)

    def test_incomplete_block_save_is_disabled(self):
        frame = render_message(self.root, "```python\nprint(1)", self.app)
        widgets = list(descendants(frame))
        save = next(widget for widget in widgets if isinstance(widget, tk.Button) and widget["text"].startswith("Save as"))
        self.assertEqual(str(save["state"]), "disabled")
        save.invoke()
        self.assertEqual(self.app.exports, [])
        self.assertTrue(any(isinstance(widget, tk.Label) and "Incomplete" in widget["text"] for widget in widgets))

    def test_no_export_control_without_callback_and_wrap_uses_actual_width(self):
        self.app.export_code_block = None
        frame = render_message(self.root, "Paragraph with enough words to wrap into several lines.\n```py\npass\n```", self.app)
        widgets = list(descendants(frame))
        self.assertFalse(any(isinstance(widget, tk.Button) and widget["text"].startswith("Save as") for widget in widgets))
        prose = next(widget for widget in widgets if isinstance(widget, tk.Label) and widget["text"].startswith("Paragraph"))
        self.assertEqual(int(prose["wraplength"]), 480)
        frame.event_generate("<Configure>", width=320, height=200)
        self.assertEqual(int(prose["wraplength"]), 318)
        frame.event_generate("<Configure>", width=1, height=200)
        self.assertEqual(int(prose["wraplength"]), 318)


if __name__ == "__main__":
    unittest.main()
