"""One stoppable status publisher; callbacks and events carry plain values."""

import logging
import threading


log = logging.getLogger(__name__)


class StatusPush:
    def __init__(self, snapshot, emit, interval=2):
        self.snapshot, self.emit, self.interval = snapshot, emit, interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="webui-status")
            self._thread.start()

    def tick(self):
        if self._stop.is_set():
            return
        try:
            value = self.snapshot()
            if not self._stop.is_set():
                self.emit("status", **value)
        except Exception:
            log.exception("Could not publish status")

    def _run(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def close(self, timeout=1):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
