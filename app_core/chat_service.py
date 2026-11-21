"""Own chat sessions, drafts and stream persistence independently of the desktop."""

from copy import deepcopy
import logging
import shutil
import threading
import time
import uuid

import chat_attachments
import chat_versions
from chat_store import _utc_now
from app_core.model_loading import ModelLoadProgress
import network_gate


log = logging.getLogger(__name__)


class ChatService:
    """Share the application lock so engine changes and new streams are atomic."""

    def __init__(self, store, engine, lock, emit, *, prepare_colibri, require_vision, context_usage):
        self._store, self._engine, self._lock, self._emit = store, engine, lock, emit
        self._prepare_colibri = prepare_colibri
        self._require_vision = require_vision
        self._context_usage = context_usage
        self._streams = {}
        self._unsaved = {}
        self._incognito = {}
        self._incognito_ids = set()
        self._closed = False
        self.autosave_interval = 2

    def load(self, chat_id):
        if chat_id in self._incognito_ids:
            if chat_id not in self._incognito:
                raise ValueError("This incognito chat has been closed.")
            return deepcopy(self._incognito[chat_id])
        return self._store.load(chat_id)

    def save(self, record):
        chat_versions.sync(record)
        if record["id"] in self._incognito_ids:
            if self._closed:
                raise ValueError("This incognito chat has been closed.")
            record = self._store._validate(record)
            record["updated_at"] = _utc_now()
            self._incognito[record["id"]] = deepcopy(record)
            return record
        return self._store.save(record)

    def create(self, record):
        if record.get("incognito"):
            self._incognito_ids.add(record["id"])
        return self.save(record)

    def summaries(self, project_id=None):
        rows = self._store.list_summaries(project_id)
        rows.extend(self._store.summary(record) for record in self._incognito.values()
                    if project_id is None or record.get("project_id") == project_id)
        return sorted(rows, key=lambda row: row["updated_at"], reverse=True)

    def records(self):
        return self._store.list_chats() + list(self._incognito.values())

    def interrupted(self):
        active = {stream["chat_id"] for stream in self._streams.values()}
        return [{"chat_id": row["id"], "title": row["title"], "message_index": row["interrupted_index"]}
                for row in self._store.list_summaries()
                if row["interrupted_index"] is not None and row["id"] not in active]

    def unsaved_replies(self, chat_id):
        return deepcopy([item for item in self._unsaved.values() if item["chat_id"] == chat_id])

    def delete(self, chat_id):
        record = self.load(chat_id)
        directory = self._store.directory / chat_id
        if not record.get("incognito") and directory.exists():
            chat_attachments.folder(self._store.directory, chat_id)
            if not directory.is_dir() or directory.resolve().parent != self._store.directory:
                raise ValueError("Invalid chat attachment folder.")
        warnings = []
        if record.get("incognito"):
            self._incognito.pop(chat_id, None)
        else:
            self._store.delete(chat_id)
            if directory.exists():
                try:
                    shutil.rmtree(directory)
                except OSError as error:
                    warnings.append(f"Chat deleted, but attachment cleanup failed: {error}")
        self._unsaved = {key: item for key, item in self._unsaved.items() if item["chat_id"] != chat_id}
        return warnings

    def save_draft(self, chat_id, text):
        if not isinstance(text, str) or len(text) > 1000000:
            raise ValueError("Draft must be text of at most 1,000,000 characters.")
        with self._lock:
            if self._closed:
                raise RuntimeError("The web UI is closing.")
            record = self.load(chat_id)
            record["draft"] = text
            self.save(record)
        return {"ok": True}

    def active_streams(self):
        with self._lock:
            return tuple(self._streams.values())

    def busy(self, chat_id):
        return any(item["chat_id"] == chat_id for item in self.active_streams())

    def launch(self, record, keep_alive, **operation):
        stream_id = str(uuid.uuid4())
        stream = {"chat_id": record["id"], "cancel": threading.Event(), "finished": threading.Event(),
                  "parts": [], "metrics": {}, "data_lock": threading.Lock(), "record": record,
                  "partial_message": None}
        stream.update(operation)
        stream["keep_alive"] = keep_alive
        self._streams[stream_id] = stream
        thread = threading.Thread(target=self._run_stream, args=(stream_id, stream, record),
                                  name="chat-" + stream_id[:8], daemon=True)
        try:
            if stream["kind"] == "chat":
                threading.Thread(target=self._autosave, args=(stream_id, stream), daemon=True,
                                 name="chat-recovery-" + stream_id[:8]).start()
            thread.start()
        except Exception:
            self._streams.pop(stream_id, None)
            stream["cancel"].set()
            stream["finished"].set()
            raise
        return {"ok": True, "stream_id": stream_id}

    def _stream_record(self, stream):
        current = self.load(stream["chat_id"])
        expected = stream["record"]
        messages = current["messages"]
        if stream["partial_message"] is not None:
            if not messages or messages[-1] != stream["partial_message"]:
                raise RuntimeError("This chat changed while the reply was running; the reply was not saved.")
            current["messages"] = messages[:-1]
        fields = ("messages", "summary", "environment", "model", "backend", "context_length")
        if any(current.get(key) != expected.get(key) for key in fields):
            raise RuntimeError("This chat changed while the reply was running; the reply was not saved.")
        return current

    def _persist_partial(self, stream_id, stream):
        with self._lock:
            if stream_id not in self._streams or stream["kind"] != "chat":
                return
            with stream["data_lock"]:
                text = "".join(stream["parts"])
            if not text:
                return
            message = {"role": "assistant", "content": text, "partial": True}
            if message == stream["partial_message"]:
                return
            current = self._stream_record(stream)
            current["messages"].append(message)
            self.save(current)
            stream["partial_message"] = message

    def _autosave(self, stream_id, stream):
        while not stream["finished"].wait(self.autosave_interval):
            try:
                self._persist_partial(stream_id, stream)
            except Exception:
                log.exception("Could not save interrupted reply for %s", stream["chat_id"])

    def stop(self, stream_id):
        with self._lock:
            stream = self._streams.get(stream_id)
            if stream is None:
                return False
            # Serialize Stop against chunk acceptance and the completion decision.
            with stream["data_lock"]:
                stream["cancel"].set()
                if "loading" in stream:
                    stream["loading"].close()
            return True

    def close(self):
        with self._lock:
            self._closed = True
            for stream_id, stream in self._streams.items():
                self.stop(stream_id)
                try:
                    self._persist_partial(stream_id, stream)
                except Exception:
                    log.exception("Could not save reply while closing")
            return [stream["finished"] for stream in self._streams.values()]

    def clear_private(self):
        with self._lock:
            self._incognito.clear()
            self._unsaved = {key: item for key, item in self._unsaved.items()
                             if item["chat_id"] not in self._incognito_ids}

    def _run_stream(self, stream_id, stream, record):
        engine = self._engine
        ended = threading.Event()
        compact = stream["kind"] == "compact"
        if compact:
            self._emit("compact_started", stream_id=stream_id, chat_id=stream["chat_id"], operation="compact")

        def chunk(text):
            with stream["data_lock"]:
                if stream["cancel"].is_set():
                    raise engine.StreamStopped()
                if compact and sum(map(len, stream["parts"])) + len(text) > stream["summary_chars"]:
                    raise RuntimeError("The model summary exceeded the requested size; no compaction was saved.")
                stream["parts"].append(text)
                combined = "".join(stream["parts"])
                if "<think>" in combined and "thinking_started" not in stream:
                    stream["thinking_started"] = time.monotonic()
                if "</think>" in combined and "thinking_started" in stream and "thinking_seconds" not in stream:
                    stream["thinking_seconds"] = max(0, time.monotonic() - stream["thinking_started"])
                if text and "loading" in stream:
                    stream["loading"].first_output()
                if not compact:
                    self._emit("token", stream_id=stream_id, chat_id=stream["chat_id"], text=text)

        def metrics(value):
            with stream["data_lock"]:
                stream["metrics"] = deepcopy(value)

        def request():
            try:
                if stream["cancel"].is_set():
                    raise engine.StreamStopped()
                if not stream.get("vision_checked"):
                    self._require_vision(record["model"], stream["messages"], record.get("backend", record["environment"]["backend"]))
                if record.get("backend", record["environment"]["backend"]) == "Colibri":
                    client = self._prepare_colibri(stream["cancel"],
                        lambda text: self._emit("status", text=text, chat_id=record["id"]))
                    if stream["cancel"].is_set():
                        raise engine.StreamStopped()
                    with network_gate.task("Send a message through Colibri", "Your message, conversation, shared notes and attached or pinned context."):
                        return client.chat(deepcopy(stream["messages"]), model=record["model"], on_chunk=chunk,
                                           on_metrics=metrics, cancel_event=stream["cancel"])
                loading = ModelLoadProgress(self._emit, record["model"], stream["cancel"],
                                           stream_id=stream_id, chat_id=stream["chat_id"])
                with stream["data_lock"]:
                    stream["loading"] = loading
                loading.start(engine.running_details)
                if stream["cancel"].is_set():
                    raise engine.StreamStopped()
                options = {"keep_alive": f"{stream['keep_alive']}m"} if stream["keep_alive"] else {}
                return engine.request_ollama(deepcopy(stream["messages"]), model=record["model"],
                                             context_length=stream["context"],
                                             on_chunk=chunk, on_metrics=metrics, cancel_event=stream["cancel"], **options)
            except engine.StreamStopped:
                raise
            except Exception as failure:
                # Keep unexpected engine failures inside run_stoppable's error contract.
                raise RuntimeError(str(failure)) from failure
            finally:
                if "loading" in stream:
                    stream["loading"].close()
                ended.set()

        kind, error, answer = "error", None, None
        try:
            answer, error, stopped = engine.run_stoppable(request, stream["parts"], stream["cancel"])
            # run_stoppable can return before its reader exits. Do not announce
            # disconnection or release this chat while the HTTP reader is alive.
            ended.wait()
            with self._lock:
                with stream["data_lock"]:
                    stopped = stopped or stream["cancel"].is_set()
                    partial = "".join(stream["parts"])
                    kind = "stopped" if stopped else ("error" if error else "done")
                    content = partial if kind != "done" else answer
                current = self._stream_record(stream)
                if compact:
                    if kind == "done":
                        if not isinstance(answer, str) or not answer.strip() or len(answer) > stream["summary_chars"]:
                            raise ValueError("The model returned an empty or oversized summary; no compaction was saved.")
                        current["summary"] = {"text": answer.strip(), "upto": stream["upto"], "created_at": _utc_now()}
                        usage = self._context_usage(current, stream["context"])
                        self.save(current)
                        kind = "compact_done"
                    else:
                        usage = None
                    self._streams.pop(stream_id, None)
                    self._emit(kind, stream_id=stream_id, chat_id=stream["chat_id"], operation="compact",
                                      usage=usage, error=error if kind == "error" else None)
                    return
                if content:
                    message = {"role": "assistant", "content": content}
                    if "thinking_started" in stream:
                        message["thinking_seconds"] = stream.get("thinking_seconds", max(0, time.monotonic() - stream["thinking_started"]))
                    current["messages"].append(message)
                current["last_response"] = {"status": kind, "kind": "chat", "metrics": stream["metrics"]}
                current["last_response"]["memories_used"] = deepcopy(stream.get("memories_used", []))
                if error and kind == "error":
                    current["last_response"]["error"] = error
                self.save(current)
                self._streams.pop(stream_id, None)
                self._emit(kind, stream_id=stream_id, chat_id=stream["chat_id"],
                                  text=content or "", metrics=stream["metrics"], memories_used=stream.get("memories_used", []),
                                  error=error if kind == "error" else None)
        except Exception as failure:
            with stream["data_lock"]:
                partial = "".join(stream["parts"])
            if kind == "done" and isinstance(answer, str):
                partial = answer
            with self._lock:
                recovered = {"stream_id": stream_id, "chat_id": stream["chat_id"], "text": partial,
                             "metrics": deepcopy(stream["metrics"]), "error": str(failure), "saved": False}
                if not compact and not (self._closed and record.get("incognito")):
                    self._unsaved[stream_id] = recovered
                recovered["operation"] = stream["kind"]
                self._emit("error", **recovered)
        finally:
            with self._lock:
                self._streams.pop(stream_id, None)
            stream["finished"].set()
