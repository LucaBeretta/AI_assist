"""Read a streamed HTTP answer line by line, off the caller's thread, so a
cancel returns at once even while the server is still thinking.

The reader thread hands each line to ``handle_line``. On cancel the caller
returns immediately; the reader closes the connection at its next line, which
also tells Ollama or colibri to stop generating."""

import http.client
import queue
import threading
import urllib.error
import urllib.request


class StreamCancelled(RuntimeError):
    """The user stopped the answer. ``partial`` keeps the text received so far."""

    def __init__(self, message="Stopped.", partial=""):
        super().__init__(message)
        self.partial = partial


STOP = object()


def run_stream(request, handle_line, cancel_event=None, timeout=1800, describe_http_error=None,
               unreachable="Could not reach the model server."):
    """Run ``request`` and feed decoded lines to ``handle_line`` until it returns STOP
    or the body ends. Raises StreamCancelled, or RuntimeError with a readable message."""
    finished = queue.Queue(maxsize=1)
    cancel_event = cancel_event or threading.Event()

    def reader():
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    if cancel_event.is_set():
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line and handle_line(line) is STOP:
                        break
            finished.put(None)
        except urllib.error.HTTPError as error:
            try:
                detail = error.read(4000).decode("utf-8", errors="replace")
            finally:
                error.close()
            message = describe_http_error(error.code, detail) if describe_http_error else f"HTTP {error.code}: {detail}"
            finished.put(RuntimeError(message))
        except RuntimeError as error:
            finished.put(error)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as error:
            finished.put(RuntimeError(f"{unreachable} ({error})"))

    threading.Thread(target=reader, daemon=True).start()
    while True:
        if cancel_event.is_set():
            raise StreamCancelled()
        try:
            error = finished.get(timeout=0.1)
        except queue.Empty:
            continue
        if cancel_event.is_set():
            raise StreamCancelled()
        if error is not None:
            raise error
        return
