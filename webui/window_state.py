"""Plain window preferences, independent of the native GUI and Tk appearance."""

import json
from pathlib import Path

from appearance import DEFAULT_THEME


def rectangle(value):
    if (not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}
            or any(type(item) is not int for item in value.values())
            or not (320 <= value["width"] <= 32000 and 240 <= value["height"] <= 32000)
            or abs(value["x"]) > 100000 or abs(value["y"]) > 100000):
        raise ValueError("Window position and size must be a valid integer rectangle.")
    return dict(value)


def visible_rectangle(value, screens):
    try:
        value = rectangle(value)
    except ValueError:
        return {}
    areas = []
    for screen in screens:
        # pywebview's Windows Screen.frame is WinForms WorkingArea, excluding
        # the taskbar. Other providers can supply a plain work_area rectangle.
        frame = getattr(screen, "work_area", None) or getattr(screen, "frame", None)
        if frame is not None and all(hasattr(frame, key) for key in ("X", "Y", "Width", "Height")):
            area = dict(zip(("x", "y", "width", "height"), (frame.X, frame.Y, frame.Width, frame.Height)))
        elif isinstance(frame, dict) and set(frame) == {"x", "y", "width", "height"}:
            area = frame
        else:
            area = {key: getattr(screen, key) for key in ("x", "y", "width", "height")}
        if all(type(item) is int for item in area.values()) and area["width"] > 0 and area["height"] > 0:
            areas.append(area)
    if not areas:
        return {}
    def score(area):
        width = max(0, min(value["x"] + value["width"], area["x"] + area["width"]) - max(value["x"], area["x"]))
        height = max(0, min(value["y"] + value["height"], area["y"] + area["height"]) - max(value["y"], area["y"]))
        distance = ((value["x"] + value["width"] / 2 - area["x"] - area["width"] / 2) ** 2
                    + (value["y"] + value["height"] / 2 - area["y"] - area["height"] / 2) ** 2)
        return width * height, -distance
    area = max(areas, key=score)
    width, height = min(value["width"], area["width"]), min(value["height"], area["height"])
    return dict(width=width, height=height,
                x=min(max(value["x"], area["x"]), area["x"] + area["width"] - width),
                y=min(max(value["y"], area["y"]), area["y"] + area["height"] - height))


def saved_theme(directory):
    path = Path(directory) / "appearance.settings"
    if not path.exists():
        path = path.with_suffix(".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        theme = value.get("theme") if isinstance(value, dict) else None
        return theme if theme in {"Night", "Day", "Dark", "Light", "Warm"} else DEFAULT_THEME
    except (OSError, ValueError):
        return DEFAULT_THEME
