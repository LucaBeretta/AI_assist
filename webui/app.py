"""Small JSON API over the existing engine and chat store."""

from copy import deepcopy
from functools import wraps
import importlib
import inspect
import logging
from pathlib import Path
import sys
import threading
import uuid

from chat_store import ChatStore
from webui.bridge import Bridge


log = logging.getLogger(__name__)


def _json_method(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception as error:
            return {"ok": False, "error": str(error)}
    # pywebview inspects getfullargspec rather than unwrapping decorators.
    guarded.__signature__ = inspect.signature(method)
    return guarded


class Api:
    def __init__(self, bridge, store=None, engine=None):
        self._bridge = bridge
        self._store = store if store is not None else ChatStore()
        self._engine = engine if engine is not None else importlib.import_module("AIassist")
        self._lock = threading.RLock()
        self._streams = {}
        self._closed = False

    @_json_method
    def state(self):
        return {"ok": True, "engine": "Ollama", "host": self._engine.ollama_url(),
                "models": self._engine.list_ollama_models(), "loaded": self._engine.running_details()}

    @_json_method
    def list_chats(self):
        with self._lock:
            return {"ok": True, "chats": self._store.list_chats()}

    @_json_method
    def open_chat(self, id):
        with self._lock:
            return {"ok": True, "chat": self._store.load(id)}

    @_json_method
    def new_chat(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("The web UI is closing.")
            return {"ok": True, "chat": self._store.create()}

    @_json_method
    def send(self, chat_id, text, model, context):
        if not isinstance(text, str) or not text.strip() or len(text) > 1000000:
            raise ValueError("Enter a message between 1 and 1,000,000 characters.")
        if not isinstance(model, str) or not model.strip() or len(model) > 300:
            raise ValueError("Choose an installed model.")
        if context is not None and (type(context) is not int or not 2048 <= context <= 1048576):
            raise ValueError("Context must be automatic or an integer between 2048 and 1048576.")
        with self._lock:
            if self._closed:
                raise RuntimeError("The web UI is closing.")
            record = self._store.load(chat_id)
            if any(item["chat_id"] == chat_id for item in self._streams.values()):
                raise ValueError("This chat already has an active stream.")
            record["model"], record["context_length"] = model, context
            record["backend"] = "Ollama"
            record["messages"].append({"role": "user", "content": text})
            record["draft"] = ""
            record = self._store.save(record)
            stream_id = str(uuid.uuid4())
            stream = {"chat_id": chat_id, "cancel": threading.Event(), "finished": threading.Event(),
                      "parts": [], "metrics": {}, "data_lock": threading.Lock()}
            self._streams[stream_id] = stream
            thread = threading.Thread(target=self._run_stream, args=(stream_id, stream, record),
                                      name="webui-chat-" + stream_id[:8], daemon=True)
            try:
                thread.start()
            except Exception:
                del self._streams[stream_id]
                raise
            return {"ok": True, "stream_id": stream_id}

    @_json_method
    def stop(self, stream_id):
        with self._lock:
            stream = self._streams.get(stream_id)
            if stream is None:
                return {"ok": True, "stream_id": stream_id, "active": False}
            # Serialize Stop against chunk acceptance and the completion decision.
            with stream["data_lock"]:
                stream["cancel"].set()
            return {"ok": True, "stream_id": stream_id, "active": True}

    def _run_stream(self, stream_id, stream, record):
        engine = self._engine
        ended = threading.Event()
        started = threading.Event()
        message_count = len(record["messages"])

        def chunk(text):
            with stream["data_lock"]:
                if stream["cancel"].is_set():
                    raise engine.StreamStopped()
                stream["parts"].append(text)
                self._bridge.emit("token", stream_id=stream_id, chat_id=stream["chat_id"], text=text)

        def metrics(value):
            with stream["data_lock"]:
                stream["metrics"] = deepcopy(value)

        def request():
            started.set()
            try:
                if stream["cancel"].is_set():
                    raise engine.StreamStopped()
                return engine.request_ollama(deepcopy(record["messages"]), model=record["model"],
                                             context_length=record["context_length"],
                                             on_chunk=chunk, on_metrics=metrics)
            except engine.StreamStopped:
                raise
            except Exception as failure:
                # Keep unexpected engine failures inside run_stoppable's error contract.
                raise RuntimeError(str(failure)) from failure
            finally:
                ended.set()

        kind, error, answer = "error", None, None
        try:
            answer, error, stopped = engine.run_stoppable(request, stream["parts"], stream["cancel"])
            # run_stoppable can return before its reader exits. Do not announce
            # disconnection or release this chat while the HTTP reader is alive.
            if started.is_set() or not ended.is_set():
                ended.wait()
            with self._lock:
                with stream["data_lock"]:
                    stopped = stopped or stream["cancel"].is_set()
                    partial = "".join(stream["parts"])
                    kind = "stopped" if stopped else ("error" if error else "done")
                    content = partial if kind != "done" else answer
                current = self._store.load(stream["chat_id"])
                if current["messages"] != record["messages"] or len(current["messages"]) != message_count:
                    raise RuntimeError("This chat changed while the reply was running; the reply was not saved.")
                if content:
                    current["messages"].append({"role": "assistant", "content": content})
                current["last_response"] = {"status": kind, "metrics": stream["metrics"]}
                if error and kind == "error":
                    current["last_response"]["error"] = error
                self._store.save(current)
                self._streams.pop(stream_id, None)
                self._bridge.emit(kind, stream_id=stream_id, chat_id=stream["chat_id"],
                                  text=content or "", metrics=stream["metrics"], error=error if kind == "error" else None)
        except Exception as failure:
            self._bridge.emit("error", stream_id=stream_id, chat_id=stream["chat_id"], error=str(failure))
        finally:
            with self._lock:
                self._streams.pop(stream_id, None)
            stream["finished"].set()

    def _ensure_engine(self):
        try:
            self._engine.ensure_ollama(lambda text: self._bridge.emit("status", text=text))
            self._bridge.emit("ready")
        except Exception as error:
            self._bridge.emit("error", error=str(error))

    def _close(self):
        with self._lock:
            self._closed = True
            streams = list(self._streams.values())
            for stream in streams:
                stream["cancel"].set()
        for stream in streams:
            stream["finished"].wait(2)


def main():
    try:
        webview = importlib.import_module("webview")
    except ImportError:
        print("The web UI needs pywebview. Run: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    bridge = None
    api = None
    try:
        # An absolute file URI does not enable pywebview's internal HTTP server.
        page = Path(__file__).resolve().parent / "static" / "index.html"
        bridge = Bridge(lambda script: window.evaluate_js(script), autostart=False)
        api = Api(bridge)
        window = webview.create_window("AI Assist — Web", page.as_uri(), js_api=api, text_select=True)
        def loaded():
            bridge.start()
        window.events.loaded += loaded
        webview.start(api._ensure_engine, http_server=False)
        return 0
    except Exception as error:
        log.exception("Could not start the web UI")
        print(f"Could not start the web UI: {error}", file=sys.stderr)
        return 1
    finally:
        if api is not None:
            api._close()
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
