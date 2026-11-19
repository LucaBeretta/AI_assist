"""Plain window preferences, independent of the native GUI and Tk appearance."""

import json
from pathlib import Path


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
    for screen in screens:
        if (screen.x <= value["x"] and screen.y <= value["y"]
                and value["x"] + value["width"] <= screen.x + screen.width
                and value["y"] + value["height"] <= screen.y + screen.height):
            return value
    return {}


def saved_theme(directory):
    path = Path(directory) / "appearance.settings"
    if not path.exists():
        path = path.with_suffix(".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        theme = value.get("theme") if isinstance(value, dict) else None
        return theme if theme in {"Night", "Day", "Dark", "Light", "Warm"} else "Night"
    except (OSError, ValueError):
        return "Night"
