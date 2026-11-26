"""Ordered, batched worker events with one JavaScript delivery owner."""

from copy import deepcopy
import json
import logging
import threading
from collections import deque


log = logging.getLogger(__name__)


class Bridge:
    def __init__(self, evaluate, interval=0.03, autostart=True, on_flush=None, evaluate_compute=None, on_compute_failure=None):
        self._evaluate = evaluate
        self._evaluate_compute = evaluate_compute
        self._on_compute_failure = on_compute_failure
        self._interval = interval
        self._events = deque()
        self._lock = threading.Lock()
        self._delivery = threading.Lock()
        self._closed = threading.Event()
        self._thread = None
        self._on_flush = on_flush
        if autostart:
            self.start()

    def start(self):
        with self._lock:
            if self._closed.is_set() or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="webui-events")
            self._thread.start()

    def emit(self, type, **data):
        with self._lock:
            if self._closed.is_set():
                return False
            try:
                event = deepcopy({**data, "type": type})
                if not isinstance(type, str) or (type == "token" and not isinstance(event.get("text"), str)):
                    raise ValueError("Events need a text type and tokens need text content.")
                json.dumps(event, ensure_ascii=False, allow_nan=False)
            except Exception:
                log.exception("Discarded an invalid web UI event")
                return False
            self._events.append(event)
            return True

    def flush(self):
        # Explicit test flushes and the timer must never evaluate concurrently.
        with self._delivery:
            with self._lock:
                if self._closed.is_set():
                    return
                pending = list(self._events)
                self._events.clear()
            if self._on_flush is not None:
                try:
                    self._on_flush()
                except Exception:
                    log.exception("Could not deliver a desktop action")
            if not pending:
                return
            batch = []
            for event in pending:
                if (batch and event.get("type") == "token" and batch[-1].get("type") == "token"
                        and event.get("stream_id") == batch[-1].get("stream_id")
                        and event.get("chat_id") == batch[-1].get("chat_id")):
                    batch[-1]["text"] += event["text"]
                else:
                    batch.append(event)
            compute = [event for event in batch if event.get("window") == "compute"]
            main = [event for event in batch if event.get("window") != "compute"]
            self._deliver(main, self._evaluate)
            if compute:
                self._deliver(compute, self._evaluate_compute, self._on_compute_failure)

    def _deliver(self, batch, evaluate, on_failure=None):
        if not batch or self._closed.is_set():
            return
        try:
            if evaluate is None:
                raise RuntimeError("No Mission Control event receiver.")
            payload = json.dumps(batch, ensure_ascii=False, allow_nan=False)
            payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
            evaluate("app.onEvent(" + payload + ")")
        except Exception:
            log.exception("Could not deliver web UI events")
            if on_failure and any(event["type"] == "internet_approval" for event in batch):
                on_failure()

    def _run(self):
        while not self._closed.wait(self._interval):
            self.flush()

    def close(self, timeout=1):
        with self._lock:
            self._closed.set()
            self._events.clear()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                log.warning("Web UI delivery is still finishing an in-flight evaluation")
