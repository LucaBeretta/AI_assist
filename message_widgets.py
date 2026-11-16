"""Readable Tk message bodies with selectable, explicitly exportable code."""

import re
import tkinter as tk

from developer_tools import extract_code_blocks
from html_preview import is_html


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_DEFAULT_COLORS = {
    "surface": "#fffdf9", "user": "#f3eee4", "ink": "#26251f",
    "muted": "#7c786b", "border": "#e4dfd3", "accent": "#a14e30",
    "code_background": "#f5f2eb", "code_header": "#ece8de",
}


def _message_segments(text):
    """Keep prose around fenced blocks; extract_code_blocks owns code contents."""
    blocks = iter(extract_code_blocks(text))
    prose = []
    fence = None
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if fence is None:
            if match is not None and not (match[1][0] == "`" and "`" in match[2]):
                if prose:
                    yield "text", "".join(prose)
                    prose = []
                fence = match[1]
            else:
                prose.append(line)
        elif (match is not None and match[1][0] == fence[0]
              and len(match[1]) >= len(fence) and not match[2].strip()):
            yield "code", next(blocks)
            fence = None
    if fence is not None:
        yield "code", next(blocks)
    elif prose:
        yield "text", "".join(prose)


def _inline_text(text):
    """Remove simple inline decoration while preserving its readable content."""
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(`+)(.+?)\1", r"\2", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    return text


def _prose_rows(text):
    """Yield small presentation rows without interpreting HTML or running code."""
    for line in text.splitlines():
        if not line.strip():
            yield {"kind": "space"}
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if heading:
            yield {"kind": "heading", "text": _inline_text(heading[2]), "level": len(heading[1])}
        elif _RULE.match(line):
            yield {"kind": "rule"}
        elif bullet:
            marker = "\u2022" if bullet[2] in {"-", "+", "*"} else bullet[2]
            contents = bullet[3]
            if contents.startswith(("[ ] ", "[x] ", "[X] ")):
                marker = "\u2610" if contents.startswith("[ ] ") else "\u2611"
                contents = contents[4:]
            yield {"kind": "bullet", "text": _inline_text(contents), "marker": marker,
                   "indent": min(24, len(bullet[1].expandtabs(2)) * 4)}
        elif line.lstrip().startswith(">"):
            yield {"kind": "quote", "text": _inline_text(re.sub(r"^\s*>+\s?", "", line))}
        else:
            yield {"kind": "paragraph", "text": _inline_text(line)}


def _button(app, parent, text, command, colors):
    button = app.button(
        parent, text, command, bg=colors["code_header"], fg=colors["muted"],
        hover=colors["border"],
    )
    button.configure(font=("Segoe UI", 9), padx=9, pady=5)
    return button


def _copy_code(widget, code):
    widget.clipboard_clear()
    widget.clipboard_append(code)


def _render_code(parent, block, app, colors):
    panel = tk.Frame(parent, bg=colors["code_background"], highlightthickness=1,
                     highlightbackground=colors["border"], borderwidth=0)
    panel.pack(fill="x", pady=(5, 9))
    header = tk.Frame(panel, bg=colors["code_header"])
    header.pack(fill="x")
    actions = tk.Frame(header, bg=colors["code_header"])
    actions.pack(side="right", padx=(4, 5), pady=3)
    copy_button = _button(app, actions, "Copy", lambda: _copy_code(panel, block["code"]), colors)
    copy_button.pack(side="left", padx=(0, 2))
    preview = getattr(app, "preview_html_block", None)
    if callable(preview) and is_html(block):
        preview_button = _button(app, actions, "Preview", lambda: preview(block), colors)
        preview_button.pack(side="left", padx=(0, 2))
        if not block["complete"]:
            preview_button.configure(state="disabled", cursor="arrow")
    export = getattr(app, "export_code_block", None)
    if callable(export):
        save_button = _button(app, actions, "Save as\u2026", lambda: export(block), colors)
        save_button.pack(side="left")
        if not block["complete"]:
            save_button.configure(state="disabled", cursor="arrow")
    label = block["language"] or "code"
    if block.get("filename"):
        filename = block["filename"]
        label += "  \u00b7  " + (filename if len(filename) <= 46 else "\u2026" + filename[-45:])
    if not block["complete"]:
        label += "  \u00b7  Incomplete"
    tk.Label(header, text=label, font=("Segoe UI", 9), anchor="w",
             bg=colors["code_header"], fg=colors["muted"]).pack(side="left", fill="x", expand=True, padx=11)

    body = tk.Frame(panel, bg=colors["code_background"])
    body.pack(fill="x", padx=10, pady=(7, 1))
    body.columnconfigure(0, weight=1)
    # Text appends its own final newline. Do not count that as another visible
    # line, and keep the original code unchanged for clipboard and Save as.
    code = block["code"]
    line_count = max(1, code.count("\n") + (0 if code.endswith("\n") else 1))
    code_widget = tk.Text(
        body, width=1, height=min(14, line_count), wrap="none",
        bg=colors["code_background"], fg=colors["ink"],
        selectbackground=colors["accent"], selectforeground="#ffffff",
        font=("Consolas", 10), relief="flat", borderwidth=0,
        highlightthickness=0, padx=0, pady=3, cursor="xterm", takefocus=True,
    )
    code_widget._aiassist_code = True
    code_widget.insert("1.0", code)
    code_widget.configure(state="disabled")
    code_widget.grid(row=0, column=0, sticky="nsew")
    if line_count > 14:
        vertical = tk.Scrollbar(body, orient="vertical", command=code_widget.yview,
                                bg=colors["code_header"], troughcolor=colors["code_background"],
                                relief="flat", borderwidth=0, width=10)
        vertical.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        code_widget.configure(yscrollcommand=vertical.set)
    horizontal = tk.Scrollbar(body, orient="horizontal", command=code_widget.xview,
                              bg=colors["code_header"], troughcolor=colors["code_background"],
                              relief="flat", borderwidth=0, width=9)
    horizontal.grid(row=1, column=0, sticky="ew", pady=(3, 0))
    code_widget.configure(xscrollcommand=horizontal.set)
    return panel


def render_message(parent, text, app, role="ai"):
    """Return an unpacked message-body Frame using the app's theme and buttons.

    The caller owns message cards, complete-message copy, packing, and export
    dialogs. This widget only copies code on a click and calls an optional
    ``app.export_code_block(block)`` on explicit Save as clicks.
    """
    if not isinstance(text, str):
        raise ValueError("The message must be text.")
    colors = dict(_DEFAULT_COLORS)
    colors.update(getattr(app, "COLORS", {}))
    background = colors["user"] if role == "user" else colors["surface"]
    frame = tk.Frame(parent, bg=background)
    labels = []

    def add_prose(prose):
        for row in _prose_rows(prose):
            kind = row["kind"]
            if kind == "space":
                tk.Frame(frame, bg=background, height=6).pack(fill="x")
                continue
            if kind == "rule":
                tk.Frame(frame, bg=colors["border"], height=1).pack(fill="x", pady=8)
                continue
            holder = frame
            inset = 0
            if kind == "bullet":
                holder = tk.Frame(frame, bg=background)
                holder.pack(fill="x", padx=(row["indent"], 0), pady=1)
                tk.Label(holder, text=row["marker"], font=("Segoe UI", 11),
                         bg=background, fg=colors["muted"], width=3,
                         anchor="n").pack(side="left", anchor="n")
                inset = 34 + row["indent"]
            elif kind == "quote":
                holder = tk.Frame(frame, bg=background)
                holder.pack(fill="x", pady=3)
                tk.Frame(holder, bg=colors["border"], width=3).pack(side="left", fill="y", padx=(0, 10))
                inset = 13
            heading = kind == "heading"
            font = ("Segoe UI", 13 if heading and row["level"] == 1 else 12 if heading else 11,
                    "bold" if heading else "italic" if kind == "quote" else "normal")
            label = tk.Label(holder, text=row["text"], font=font, bg=background,
                             fg=colors["muted"] if kind == "quote" else colors["ink"],
                             anchor="w", justify="left", wraplength=max(100, 480 - inset))
            label.pack(side="left" if holder is not frame else "top", fill="x", expand=True,
                       pady=(8, 4) if heading else (1, 1))
            labels.append((label, inset))

    for kind, content in _message_segments(text):
        if kind == "code":
            _render_code(frame, content, app, colors)
        else:
            add_prose(content)

    last_width = [None]

    def resize(event):
        # Requested widths from a hidden view can grow recursively. Only use
        # actual allocated widths after this frame receives a Configure event.
        if event.width <= 1 or event.width == last_width[0]:
            return
        last_width[0] = event.width
        for label, inset in labels:
            label.configure(wraplength=max(80, event.width - inset - 2))

    frame.bind("<Configure>", resize, add="+")
    return frame
