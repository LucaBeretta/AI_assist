"""Read-only five-second telemetry publisher; sampling never runs on a UI caller."""
from copy import deepcopy
import json
import threading
import time

from app_core.compute_values import format_snapshot, normalize_peer_rows

POLL_SECONDS = 5


class ComputePoller:
    def __init__(self, sampler, emit, peers=lambda: {}, clock=time.monotonic, autostart=True):
        self.sampler, self.emit, self.peers, self.clock = sampler, emit, peers, clock
        self.autostart = autostart
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.active = False
        self.sampling = False
        self.last_started = None
        self.latest = None

    def state(self):
        with self.lock:
            return {"ok": True, "active": self.active, "busy": self.sampling, "data": deepcopy(self.latest)}

    def set_active(self, active):
        if type(active) is not bool:
            raise ValueError("Compute visibility must be true or false.")
        with self.lock:
            if self.stop.is_set():
                raise ValueError("The compute publisher is closed.")
            self.active = active
            if active and self.autostart and self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="webui-compute")
                self.thread.start()
            return self.state()

    def tick(self):
        with self.lock:
            if (self.stop.is_set() or not self.active or self.sampling or
                    self.last_started is not None and self.clock() - self.last_started < POLL_SECONDS):
                return False
            self.sampling = True
            self.last_started = self.clock()
        try:
            snapshot = self.sampler.sample()
            data = {"snapshot": snapshot, "values": format_snapshot(snapshot), "peers": normalize_peer_rows(self.peers()), "error": ""}
            json.dumps(data, allow_nan=False)
        except Exception as error:
            data = {"snapshot": {}, "values": format_snapshot({}), "peers": [], "error": str(error)}
        with self.lock:
            self.sampling = False
            if not self.stop.is_set():
                self.latest = data
                if self.active:
                    self.emit("compute", data=data)
        return True

    def _run(self):
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(.2)

    def close(self):
        self.stop.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1)
