"""Ordered chat writes; workers never access Tk widgets."""

from copy import deepcopy
import queue
import threading


class _Call:
    def __init__(self, function, on_done):
        self.function, self.on_done = function, on_done


class BackgroundSaves:
    def __init__(self, store):
        self.store = store
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True, name="chat-saves")
        self.thread.start()

    def submit(self, version, record, delete=False):
        self.jobs.put((version, deepcopy(record), delete))

    def call(self, function, on_done):
        """Run ``function()`` in order with the queued writes, then ``on_done(result, error)``
        on this worker thread. Backups and restores use it, so they never read or replace a
        chat file while it is being saved (Windows cannot replace a file that is open)."""
        self.jobs.put(_Call(function, on_done))

    def _run(self):
        timestamps = {}
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
                if isinstance(job, _Call):
                    try:
                        result, error = job.function(), None
                    except Exception as failure:
                        result, error = None, failure
                    try:
                        job.on_done(result, error)
                    except Exception:
                        pass
                    continue
                version, record, delete = job
                try:
                    if record["id"] in timestamps and record["updated_at"] <= timestamps[record["id"]]:
                        record["updated_at"] = timestamps[record["id"]]
                    result = (self.store.delete(record["id"]) if delete else self.store.save(record))
                    if result:
                        timestamps[record["id"]] = result["updated_at"]
                    elif delete:
                        timestamps.pop(record["id"], None)
                    self.results.put((version, record["id"], result, None))
                except Exception as error:
                    self.results.put((version, record["id"], None, str(error)))
            finally:
                self.jobs.task_done()

    def stop(self):
        self.jobs.put(None)
