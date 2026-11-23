"""Atomically write local files."""

import os
from pathlib import Path
import tempfile


def write_utf8(path, text):
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path, data):
    path = Path(path)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("The destination must not be a link.")
    if path.exists() and not path.is_file():
        raise ValueError("Choose a regular file destination, not a directory.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
