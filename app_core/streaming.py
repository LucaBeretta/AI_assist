"""Streamed requests that a person can stop at any moment."""

from contextlib import contextmanager
import http.client
import queue
import socket
import threading
import urllib.error
import urllib.request


class StreamStopped(Exception):
    """Raised inside a stream callback to stop reading; the connection closes,
    which tells Ollama or Colibri to stop generating."""


@contextmanager
def open_stream(request, timeout, cancel_event=None):
    """Open a gated urllib stream whose socket can be interrupted during a read."""
    if cancel_event is None:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            yield response
        return

    finished = threading.Event()
    lock = threading.Lock()
    active_socket = None

    def check_cancelled():
        if cancel_event.is_set():
            raise StreamStopped()

    def tracked(base):
        class Connection(base):
            def connect(self):
                nonlocal active_socket
                check_cancelled()
                super().connect()
                # urllib may clear the connection's socket when the response
                # takes over its buffer. Keep a handle until reading completes.
                with lock:
                    active_socket = self.sock
                    check_cancelled()
        return Connection

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(tracked(http.client.HTTPConnection), req)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(tracked(http.client.HTTPSConnection), req, context=self._context)

    def watch_cancel():
        while not finished.wait(.05):
            if cancel_event.is_set():
                with lock:
                    if active_socket is not None:
                        try:
                            active_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                return

    check_cancelled()
    watcher = threading.Thread(target=watch_cancel, daemon=True, name="model-stream-cancel")
    watcher.start()
    try:
        # The standard opener retains proxies, redirects, TLS verification
        # and the urllib.Request audit event used by the internet gate.
        with urllib.request.build_opener(HTTPHandler(), HTTPSHandler()).open(request, timeout=timeout) as response:
            check_cancelled()
            yield response
        check_cancelled()
    except Exception as error:
        if cancel_event.is_set():
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise StreamStopped() from None
        raise
    finally:
        finished.set()
        watcher.join()


def run_stoppable(request, parts, cancel_event):
    """Run ``request()`` on its own thread. Returns (answer, error, stopped).

    On Stop the caller gets the text received so far at once. A cancellable
    request uses the same event to interrupt its reader."""
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
