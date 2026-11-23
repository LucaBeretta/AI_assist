"""Serialized, fail-closed internet approvals; only workers wait for an answer."""
from collections import OrderedDict
from dataclasses import asdict
import threading
import time
import uuid

from webui.jobs import job_window

CONFIRM_SECONDS = 3


class Approvals:
    def __init__(self, emit, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        self.lock = threading.RLock()
        self.waiting = OrderedDict()
        self.closed = False

    def _public(self, identity, item):
        return {"approval_id": identity, "request": asdict(item["request"]), "stage": item["stage"],
                "remaining_seconds": max(0, item["ready"] - self.clock()) if item["stage"] == 2 else CONFIRM_SECONDS,
                **item["destination"]}

    def pending(self, window=None):
        with self.lock:
            if not self.waiting:
                return None
            identity, item = next(iter(self.waiting.items()))
            if window is None or item["destination"].get("window", "main") == window:
                return self._public(identity, item)

    def _show(self):
        current = self.pending()
        if current and self.emit("internet_approval", **current) is False:
            self.block_pending()

    def approve(self, request):
        # A native event-loop thread must never block on a JavaScript answer.
        if threading.current_thread() is threading.main_thread():
            return False
        with self.lock:
            if self.closed:
                return False
            identity = str(uuid.uuid4())
            item = {"request": request, "done": threading.Event(), "allowed": False, "stage": 1, "ready": 0}
            window = job_window.get()
            item["destination"] = {"window": window} if window != "main" else {}
            self.waiting[identity] = item
            if len(self.waiting) == 1:
                self._show()
        item["done"].wait()
        return item["allowed"]

    def respond(self, identity, action):
        with self.lock:
            if not self.waiting or identity != next(iter(self.waiting)):
                raise ValueError("This internet request is no longer awaiting approval.")
            item = self.waiting[identity]
            if action == "continue" and item["stage"] == 1:
                item.update(stage=2, ready=self.clock() + CONFIRM_SECONDS)
                self._show()
            elif action == "block":
                self._finish(identity, False)
            elif action == "allow" and item["stage"] == 2:
                if self.clock() < item["ready"]:
                    raise ValueError("Wait for the second confirmation countdown before allowing access.")
                self._finish(identity, True)
            else:
                raise ValueError("Continue to the second confirmation or block this request.")
        return {"ok": True}

    def _finish(self, identity, allowed):
        item = self.waiting.pop(identity)
        item["allowed"] = allowed
        self.emit("internet_closed", approval_id=identity, allowed=allowed, **item["destination"])
        item["done"].set()
        self._show()

    def block_pending(self, window=None):
        with self.lock:
            first = next(iter(self.waiting), None)
            for identity, item in list(self.waiting.items()):
                if window is not None and item["destination"].get("window") != window:
                    continue
                del self.waiting[identity]
                self.emit("internet_closed", approval_id=identity, allowed=False, **item["destination"])
                item["done"].set()
            if first is not None and first not in self.waiting and self.waiting:
                self._show()

    def close(self):
        with self.lock:
            self.closed = True
            self.block_pending()
