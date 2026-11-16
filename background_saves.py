"""Ordered chat writes; workers never access Tk widgets."""

from copy import deepcopy
import queue
import threading


class BackgroundSaves:
    def __init__(self, store):
        self.store = store
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True, name="chat-saves")
        self.thread.start()

    def submit(self, version, record, delete=False):
        self.jobs.put((version, deepcopy(record), delete))

    def _run(self):
        timestamps = {}
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
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
