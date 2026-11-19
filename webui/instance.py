"""One local window per data directory, using an OS lock and a file signal."""

import logging
import os
from pathlib import Path
import tempfile
import threading
import uuid

from app_core.instance_lock import AlreadyRunning, InstanceLock


log = logging.getLogger(__name__)


class SingleInstance:
    def __init__(self, directory, interval=0.2):
        self.directory = Path(directory)
        self.signal = self.directory / "web-ui.activate"
        self.interval = interval
        self._lock = InstanceLock(self.directory, "AI Assist desktop")
        self._thread = None
        self._stop = threading.Event()
        self._seen = None

    @staticmethod
    def _regular(path):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Instance files must not be links.")

    def acquire(self):
        if self._lock.handle is not None:
            return True
        self._regular(self._lock.path)
        self._regular(self._lock.owner_path)
        try:
            self._lock.acquire()
        except AlreadyRunning:
            self._notify()
            return False
        self._seen = self._read_signal()
        return True

    def _read_signal(self):
        self._regular(self.signal)
        try:
            with self.signal.open("rb") as source:
                return source.read(64)
        except FileNotFoundError:
            return None

    def _notify(self):
        self._regular(self.signal)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as target:
                temporary = Path(target.name)
                target.write(uuid.uuid4().hex.encode("ascii"))
            os.replace(temporary, self.signal)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def start(self, notify):
        """The callback only queues a signal; it must not operate a window."""
        if self._lock.handle is None or self._thread is not None:
            return
        def poll():
            while not self._stop.is_set():
                try:
                    value = self._read_signal()
                    if value is not None and value != self._seen:
                        self._seen = value
                        notify()
                except Exception:
                    log.exception("Could not read the local activation signal")
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=poll, daemon=True, name="webui-instance")
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1)
        self._lock.release()
