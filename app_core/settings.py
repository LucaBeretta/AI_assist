"""Small JSON settings files beside the saved chats, written atomically.

Each writer used to replace ``<name>.tmp`` without syncing it, so two writers
could collide on the temporary name and a crash could leave a truncated file.
``write_json`` gives every write its own private temporary file, syncs it, and
then replaces the target.
"""

import json
import os
from pathlib import Path
import tempfile

from coding_agent import FILE_LIMIT_CHOICES

BACKENDS = ("Ollama", "Colibri")
DEFAULT_COLIBRI_URL = "http://127.0.0.1:8000/v1"


def read_json(path):
    """The JSON object in ``path``, or {} when it is missing, unreadable or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data):
    """Atomically replace ``path`` with ``data``. The new file is private to this user (0600)."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_backend(path, normalize_url, environ=None):
    """(backend, colibri_url) from backend.settings and ``COLIBRI_URL``.

    ``normalize_url`` validates a Colibri URL (colibri_backend.normalize_url);
    an invalid saved file is ignored, as the desktop app always did.
    """
    environ = os.environ if environ is None else environ
    backend, url = "Ollama", DEFAULT_COLIBRI_URL
    if path is not None:
        data = read_json(path)
        try:
            saved_url = normalize_url(data.get("url", url))
            if data.get("backend") in BACKENDS:
                backend, url = data["backend"], saved_url
        except (ValueError, TypeError, AttributeError):
            pass
    if environ.get("COLIBRI_URL"):
        url = normalize_url(environ["COLIBRI_URL"])
    return backend, url


def save_backend(path, backend, url):
    if backend not in BACKENDS:
        raise ValueError("Choose Ollama or Colibri.")
    write_json(path, {"backend": backend, "url": url})


def load_file_limit(path):
    """The agent's largest readable file, from agent.settings; 64 KB when unset or invalid."""
    value = read_json(path).get("file_limit") if path is not None else None
    return value if value in FILE_LIMIT_CHOICES else FILE_LIMIT_CHOICES[0]


def save_file_limit(path, limit):
    if limit not in FILE_LIMIT_CHOICES:
        raise ValueError("Choose one of the offered file limits.")
    write_json(path, {"file_limit": limit})


def load_hourly_report(path):
    data = read_json(path) if path is not None else {}
    enabled = data.get("enabled") is True
    feedback = data.get("feedback")
    if feedback not in ("useful", "not_useful"):
        feedback = ""
    return {"enabled": enabled, "feedback": feedback}


def save_hourly_report(path, enabled, feedback=""):
    if type(enabled) is not bool or feedback not in ("", "useful", "not_useful"):
        raise ValueError("Choose a valid hourly report setting.")
    write_json(path, {"enabled": enabled, "feedback": feedback})
