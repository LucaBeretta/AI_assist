"""Streamed requests that a person can stop at any moment."""

import queue
import threading


class StreamStopped(Exception):
    """Raised inside a stream callback to stop reading; the connection closes,
    which tells Ollama or Colibri to stop generating."""


def run_stoppable(request, parts, cancel_event):
    """Run ``request()`` on its own thread. Returns (answer, error, stopped).

    On Stop the caller gets the text received so far at once; the reader
    thread ends at its next chunk, when the stream callback raises."""
    if cancel_event is None:
        try:
            return request(), None, False
        except (RuntimeError, ValueError) as error:
            return None, str(error), False
    finished = queue.Queue(maxsize=1)

    def worker():
        try:
            finished.put((request(), None))
        except StreamStopped:
            finished.put((None, "stopped"))
        except (RuntimeError, ValueError, OSError) as error:
            finished.put((None, str(error)))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        if cancel_event.is_set():
            return "".join(parts), None, True
        try:
            answer, error = finished.get(timeout=.1)
        except queue.Empty:
            continue
        if error == "stopped" or cancel_event.is_set():
            return "".join(parts), None, True
        return answer, error, False
