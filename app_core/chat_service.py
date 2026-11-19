"""Saved chats and streamed answers for interfaces without a Tk window.

``ChatService`` applies the desktop app's rules to ordinary chats:

- Explicit memory commands ("Remember that ...") are handled locally and
  confirmed only after the note is saved.
- Shared memory (notes and bounded excerpts of other chats) goes first.
- The user's message is saved before the request; the answer is saved only
  when it completed, or, after Stop, as the partial text received so far.
- A failed request saves no answer.

Developer chats are read-only here: running the agent needs its reviewed
approvals, which stay in the desktop window for now.
"""

from copy import deepcopy
import threading

from app_core.conversation import chat_messages
from app_core.streaming import StreamStopped, run_stoppable
from chat_store import ChatStoreError
from response_metrics import metrics_summary, response_metrics
from shared_memory import memory_note

MAX_MESSAGE_CHARS = 100_000
MEMORY_RECEIPT = "Saved to shared memory. This note is available in other chats."


class ServiceError(Exception):
    """A request the service refuses. ``status`` follows HTTP conventions."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class _Emitter:
    """Forwards events until the terminal one; a late chunk from a stopped
    stream's reader thread is dropped instead of arriving after "done"."""

    def __init__(self, sink):
        self.sink = sink
        self.lock = threading.Lock()
        self.closed = False

    def __call__(self, event, terminal=False):
        with self.lock:
            if self.closed:
                return
            if terminal:
                self.closed = True
        self.sink(event)


class ChatService:
    def __init__(self, store, provider_factory, shared_memory=None):
        self.store = store
        self.provider_factory = provider_factory
        self.shared_memory = shared_memory
        self.lock = threading.RLock()
        self.active = set()
        self._records = None

    # ------------------------------------------------------------ records

    def records(self):
        """Every readable chat, newest first. Cached: the instance lock guarantees
        that this process is the only writer, so its own writes keep it current."""
        with self.lock:
            if self._records is None:
                self._records = self.store.list_chats()
            return list(self._records)

    def reload(self):
        """Forget the cached list, after files changed outside the service (a restore)."""
        with self.lock:
            self._records = None

    def _remember(self, record):
        with self.lock:
            if self._records is not None:
                self._records = [record] + [item for item in self._records if item["id"] != record["id"]]

    def _forget(self, chat_id):
        with self.lock:
            if self._records is not None:
                self._records = [item for item in self._records if item["id"] != chat_id]

    @staticmethod
    def summary(record):
        return {key: record.get(key) for key in ("id", "title", "mode", "model", "backend", "updated_at",
                                                    "created_at", "project_path", "context_length")} | {
            "backend": record.get("backend", "Ollama"), "message_count": len(record.get("messages", []))}

    def list(self, query="", mode=None):
        query = (query or "").casefold().strip()
        if mode not in (None, "chat", "dev"):
            raise ServiceError("Mode must be chat or dev.")
        result = []
        for record in self.records():
            if mode and record["mode"] != mode:
                continue
            if query:
                fields = [record["title"], record["model"], record.get("project_path", ""), record.get("draft", "")]
                if not (any(query in field.casefold() for field in fields)
                        or any(query in message["content"].casefold() for message in record["messages"])):
                    continue
            result.append(self.summary(record))
        return result

    def get(self, chat_id):
        try:
            return self.store.load(chat_id)
        except FileNotFoundError:
            raise ServiceError("This chat no longer exists.", 404) from None
        except ChatStoreError as error:
            raise ServiceError(str(error), 404 if "canonical" in str(error) else 500) from None

    def create(self, model="", backend="Ollama"):
        with self.lock:
            record = self.store.new_record(model=model, mode="chat")
            record["backend"] = backend
            saved = self.store.save(record)
            self._remember(saved)
            return saved

    def rename(self, chat_id, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ServiceError("Enter a title of up to 200 characters.")
        with self.lock:
            self.get(chat_id)
            saved = self.store.rename(chat_id, title)
            self._remember(saved)
            return saved

    def delete(self, chat_id):
        with self.lock:
            if chat_id in self.active:
                raise ServiceError("Stop the answer in progress before deleting this chat.", 409)
            self.get(chat_id)
            self.store.delete(chat_id)
            self._forget(chat_id)

    def _save(self, record):
        saved = self.store.save(record)
        self._remember(saved)
        return saved

    # ------------------------------------------------------------ sending

    def prepare(self, chat_id, text, model, backend):
        """Validate a send before any answer streams; raises ServiceError."""
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("Write a message first.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ServiceError(f"Messages are limited to {MAX_MESSAGE_CHARS:,} characters.")
        record = self.get(chat_id)
        if record["mode"] != "chat":
            raise ServiceError("Developer chats run in the desktop app, where every edit and command is reviewed.", 409)
        if record.get("backend", "Ollama") != backend and record["messages"]:
            raise ServiceError(f"This chat was written with {record.get('backend', 'Ollama')}. "
                               f"Switch the engine to it in Settings to continue it.", 409)
        try:
            local = memory_note(text.strip(), record["messages"]) is not None
        except ValueError:
            local = True  # A malformed memory command; send() reports why.
        if not local and (not isinstance(model, str) or not model.strip()):
            raise ServiceError("Choose a model first.")
        with self.lock:
            if chat_id in self.active:
                raise ServiceError("This chat is already answering.", 409)
            self.active.add(chat_id)
        return record

    def release(self, chat_id):
        """Undo ``prepare`` when the answer will not be sent after all."""
        with self.lock:
            self.active.discard(chat_id)

    def send(self, chat_id, text, model, backend, on_event, cancel_event, context_length=None):
        """Stream one answer. ``prepare`` must have succeeded for this chat.

        Events: ``user``, ``chunk``, ``metrics``, then exactly one ``done`` or
        ``error``. ``done`` carries the saved record (``stopped`` when the person
        pressed Stop)."""
        emit = _Emitter(on_event)
        try:
            self._send(chat_id, text.strip(), model, backend, emit, cancel_event, context_length)
        except ServiceError as error:
            emit({"type": "error", "error": str(error)}, terminal=True)
        except (ChatStoreError, OSError, ValueError) as error:
            emit({"type": "error", "error": f"Could not save this chat: {error}"}, terminal=True)
        finally:
            with self.lock:
                self.active.discard(chat_id)

    def _send(self, chat_id, text, model, backend, emit, cancel_event, context_length):
        with self.lock:
            record = self.get(chat_id)
            try:
                note = memory_note(text, record["messages"])
            except ValueError as error:
                raise ServiceError(str(error)) from None
            if note is not None:
                if self.shared_memory is None:
                    raise ServiceError("Shared memory is unavailable. Check the saved-history folder.")
                try:
                    self.shared_memory.add(note)
                except ValueError as error:
                    raise ServiceError(f"Could not add to shared memory: {error}") from None
                record["messages"] += [{"role": "user", "content": text},
                                       {"role": "assistant", "content": MEMORY_RECEIPT}]
                saved = self._save(record)
                emit({"type": "user", "message": record["messages"][-2]})
                emit({"type": "done", "chat": saved, "stopped": False, "local": True}, terminal=True)
                return
            record["messages"].append({"role": "user", "content": text})
            record.update(model=model, backend=backend, draft="")
            if context_length is not None:
                record["context_length"] = context_length
            record = self._save(record)
            others = self.records()
        emit({"type": "user", "message": record["messages"][-1]})
        shared = (self.shared_memory.context(text, others, chat_id) if self.shared_memory else "")
        messages = chat_messages(record["messages"], shared)
        provider = self.provider_factory()
        parts = []

        def chunk(piece):
            if cancel_event.is_set():
                raise StreamStopped()
            if piece:
                parts.append(piece)
                emit({"type": "chunk", "text": piece})

        def metrics(data):
            values = response_metrics(data)
            emit({"type": "metrics", "metrics": values, "summary": metrics_summary(values)})

        answer, error, stopped = run_stoppable(
            lambda: provider.stream_chat(messages, model, context_length=context_length, on_chunk=chunk,
                                         on_metrics=metrics, cancel_event=cancel_event),
            parts, cancel_event)
        if error and not stopped:
            emit({"type": "error", "error": error}, terminal=True)
            return
        if stopped and not (answer or "").strip():
            emit({"type": "done", "chat": deepcopy(record), "stopped": True}, terminal=True)
            return
        with self.lock:
            latest = self.get(chat_id)
            latest["messages"].append({"role": "assistant", "content": answer})
            saved = self._save(latest)
        emit({"type": "done", "chat": saved, "stopped": stopped}, terminal=True)
