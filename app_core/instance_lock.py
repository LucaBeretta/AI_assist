"""One AI Assist at a time per data folder.

Two windows (or the desktop window and the web workspace) saving the same
chats would silently overwrite each other: each keeps its own copy of the open
chat. Every entry point therefore holds this lock while it runs.

The lock is an operating-system file lock (``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere), so it ends with the process, even after a crash,
and needs no stale-lock clean-up. Who holds it is written to a separate file,
because Windows forbids reading a locked byte range from another process.
"""

import json
import os
from pathlib import Path
import time

LOCK_NAME = "instance.lock"
OWNER_NAME = "instance.owner"


class AlreadyRunning(RuntimeError):
    """Another AI Assist process is using this data folder."""

    def __init__(self, owner):
        self.owner = owner or {}
        kind = self.owner.get("kind") or "AI Assist"
        pid = self.owner.get("pid")
        where = f" (process {pid})" if pid else ""
        super().__init__(f"{kind} is already open{where} and is using these saved chats. "
                         "Close it first, then start AI Assist again.")


class InstanceLock:
    """Hold a reference for as long as the lock must last: when the object is
    garbage-collected its file closes, and the operating system releases the lock."""

    def __init__(self, directory, kind="AI Assist"):
        self.directory = Path(directory)
        self.kind = kind
        self.handle = None

    @property
    def path(self):
        return self.directory / LOCK_NAME

    @property
    def owner_path(self):
        return self.directory / OWNER_NAME

    def owner(self):
        try:
            data = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def acquire(self):
        """Take the lock or raise AlreadyRunning. OSError if the folder is unusable."""
        if self.handle is not None:
            return self
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            _lock(handle)
        except OSError:
            handle.close()
            raise AlreadyRunning(self.owner()) from None
        self.handle = handle
        try:
            temporary = self.owner_path.with_name(f".{OWNER_NAME}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps({"pid": os.getpid(), "kind": self.kind, "started": time.time()}),
                                 encoding="utf-8")
            os.replace(temporary, self.owner_path)
        except OSError:
            pass  # The lock itself is what protects the chats; the owner note is a courtesy.
        return self

    def release(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            self.owner_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _unlock(handle)
        except OSError:
            pass
        handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
