"""Small atomically replaced web preferences, separate from saved chat JSON."""

import json
import os
from pathlib import Path
import tempfile


DEFAULTS = {"idle_unload_minutes": 0}


def validate(values):
    minutes = values.get("idle_unload_minutes", 0)
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError("Idle unload must be 0 (off) or 1 to 1440 minutes.")
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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(values, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return values
