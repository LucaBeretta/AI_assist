"""Where AI Assist keeps its data.

network_gate.log_path() repeats this rule on purpose: the gate must load in
Python programs AI Assist starts, by file path, without this package.
"""

import os
from pathlib import Path


def data_directory(environ=None):
    """``AI_ASSIST_DATA_DIR``, else ``%LOCALAPPDATA%\\AI Assist\\chats`` on Windows,
    else ``~/.aiassist/chats``. Not resolved or created here."""
    environ = os.environ if environ is None else environ
    override, local_data = environ.get("AI_ASSIST_DATA_DIR"), environ.get("LOCALAPPDATA")
    if override:
        return Path(override).expanduser()
    if local_data:
        return Path(local_data) / "AI Assist" / "chats"
    return Path.home() / ".aiassist" / "chats"


def ensure_private_directory(directory):
    """Create ``directory`` (and parents) if missing. A folder created here is
    readable only by this user on macOS and Linux; an existing folder is left as it is."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory
