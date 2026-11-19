"""Worker-only model loading progress; no window references or UI calls."""

import logging
import threading
import time


log = logging.getLogger(__name__)


class ModelLoadProgress:
    def __init__(self, emit, model, cancel, *, clock=None, autostart=True, **identity):
        self._emit, self._model, self._cancel = emit, model, cancel
        self._identity = identity
        self._clock = clock or time.monotonic
        self._autostart = autostart
        self._lock = threading.RLock()
        self._finished = threading.Event()
        self._started = None

    def start(self, running_details):
        if self._cancel.is_set() or self._finished.is_set():
            return
        try:
            loaded = running_details()
        except Exception:
            # Unknown server state is not evidence that a model needs loading.
            log.debug("Could not inspect model residency", exc_info=True)
            return
        with self._lock:
            if (not isinstance(loaded, dict) or self._model in loaded
                    or self._cancel.is_set() or self._finished.is_set()):
                return
            self._started = self._clock()
            self.tick()
            if self._autostart:
                threading.Thread(target=self._pump, daemon=True, name="webui-model-loading").start()

    def tick(self):
        with self._lock:
            if self._cancel.is_set():
                self.close()
            if self._started is not None and not self._finished.is_set():
                self._emit("model_loading", model=self._model, **self._identity,
                           elapsed_seconds=max(0, self._clock() - self._started))

    def first_output(self):
        with self._lock:
            if self._started is not None and not self._finished.is_set() and not self._cancel.is_set():
                self._emit("model_loaded", model=self._model, **self._identity,
                           load_seconds=max(0, self._clock() - self._started))
            self.close()

    def _pump(self):
        while not self._finished.wait(1):
            self.tick()

    def close(self):
        with self._lock:
            self._finished.set()
