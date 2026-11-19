"""One local window per data directory, using an OS lock and a file signal."""

import logging
import os
from pathlib import Path
import tempfile
import threading
import uuid


log = logging.getLogger(__name__)


class SingleInstance:
    def __init__(self, directory, interval=0.2):
        self.directory = Path(directory)
        self.signal = self.directory / "web-ui.activate"
        self.interval = interval
        self._file = None
        self._thread = None
        self._stop = threading.Event()
        self._seen = None

    @staticmethod
    def _regular(path):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Instance files must not be links.")

    def acquire(self):
        if self._file is not None:
            return True
        path = self.directory / "web-ui.lock"
        self._regular(path)
        source = path.open("a+b")
        try:
            source.seek(0)
            if not source.read(1):
                source.write(b"\0")
                source.flush()
            source.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(source.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(source, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            source.close()
            if error.errno not in (11, 13, 35, 36):
                raise
            self._notify()
            return False
        self._file = source
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
        if self._file is None or self._thread is not None:
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
        if self._file is not None:
            source, self._file = self._file, None
            try:
                source.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(source.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(source, fcntl.LOCK_UN)
            finally:
                source.close()
        # Keep the inode: deleting a lock file races other processes opening it.
