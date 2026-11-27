"""Small atomically replaced web preferences, separate from saved chat JSON."""

import json
from pathlib import Path
from app_core.settings import write_json


DEFAULTS = {"idle_unload_minutes": 0, "first_run_complete": False, "zoom": 1.0, "language": "system", "look": "classic"}


def validate(values):
    minutes = values.get("idle_unload_minutes", 0)
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError("Idle unload must be 0 (off) or 1 to 1440 minutes.")
    if type(values.get("first_run_complete", False)) is not bool:
        raise ValueError("First run completion must be true or false.")
    zoom = values.get("zoom", 1.0)
    if type(zoom) not in (int, float) or not .7 <= zoom <= 1.5:
        raise ValueError("UI zoom must be between 0.7 and 1.5.")
    if values.get("language", "system") not in ("system", "en", "it"):
        raise ValueError("Choose the system language, English or Italian.")
    if values.get("look", "classic") not in ("classic", "cockpit", "browser", "viewport"):
        raise ValueError("Choose the classic look, the 3D cockpit, the browser look or the 3D viewport.")
    return {**DEFAULTS, **values}


def load(directory):
    path = Path(directory) / "webui.settings"
    if not path.exists():
        return dict(DEFAULTS)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Web settings must be an object.")
    return validate(value)


def save(directory, values):
    values = validate(values)
    target = Path(directory) / "webui.settings"
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise ValueError("Web settings must not be a link.")
    write_json(target, values)
    return values
