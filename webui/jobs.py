"""Cancellable background operations with plain progress/result bridge events."""
import threading
import uuid
import json


class Jobs:
    def __init__(self, emit):
        self.emit, self.lock = emit, threading.RLock()
        self.jobs = {}
        self.closed = False

    def busy(self, key):
        with self.lock:
            return any(item["key"] == key for item in self.jobs.values())

    def start(self, operation, work, key=None, *, finish_on_close=False):
        with self.lock:
            if self.closed or (key and self.busy(key)):
                raise ValueError("This operation is already running or the app is closing.")
            identity = str(uuid.uuid4())
            job = {"key": key, "cancellable": True, "finish_on_close": finish_on_close, "cancel": threading.Event(), "finished": threading.Event()}
            self.jobs[identity] = job
            def progress(**values):
                with self.lock:
                    if values.get("cancellable") is False:
                        if job["cancel"].is_set():
                            raise InterruptedError("Operation stopped.")
                        job["cancellable"] = False
                    if not self.closed:
                        self.emit("job_progress", job_id=identity, operation=operation, **values)
            def run():
                result, error = None, None
                try:
                    if not job["cancel"].is_set():
                        result = work(progress, job["cancel"])
                        json.dumps(result, allow_nan=False)
                except Exception as failure:
                    result, error = None, str(failure)
                with self.lock:
                    self.jobs.pop(identity, None)
                    if not self.closed:
                        self.emit("job_stopped" if job["cancel"].is_set() else "job_error" if error else "job_done",
                                  job_id=identity, operation=operation, result=result, error=error)
                    job["finished"].set()
            try:
                threading.Thread(target=run, daemon=not finish_on_close, name="webui-" + operation).start()
            except Exception:
                self.jobs.pop(identity, None)
                raise
        return {"ok": True, "job_id": identity, "operation": operation}

    def cancel(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if job and job["cancellable"]:
                job["cancel"].set()
            return {"ok": True, "active": job is not None}

    def durable_jobs(self):
        with self.lock:
            return [job["finished"] for job in self.jobs.values() if job["finish_on_close"]]

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                if job["cancellable"]:
                    job["cancel"].set()
            return [job["finished"] for job in self.jobs.values()]
