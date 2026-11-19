"""Local saved chats, stored as independent, atomically replaced JSON files."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
import zipfile

from app_core.conversation import NEW_CHAT_TITLE, title_from
from app_core.paths import data_directory, ensure_private_directory


_MAX_CHAT_BYTES = 64 * 1024 * 1024
_CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MODES = {"chat", "dev"}
_ROLES = {"user", "assistant", "system"}


class ChatStoreError(ValueError):
    """A saved chat is invalid, unreadable, or could not be safely written."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp(value, field):
    if not isinstance(value, str):
        raise ChatStoreError(f"Chat {field} must be a UTC timestamp.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ChatStoreError(f"Chat {field} must be a UTC timestamp.") from error
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ChatStoreError(f"Chat {field} must be a UTC timestamp.")
    return result


def _validate_json(value):
    """Reject values that JSON would silently change or encode nonstandardly."""
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise ChatStoreError("Native agent history must contain only finite JSON values and text object keys.")


class ChatStore:
    """Read and write saved chats without an index shared by all conversations.

    ``errors`` contains diagnostics from the last listing and subsequent failed
    reads. A damaged chat is skipped by ``list_chats`` and never overwritten by
    ``save``. Callers should retain the fresh record returned by writes.

    The optional ``_manual_title`` flag preserves an explicit rename, including
    a rename back to "New chat", across application restarts.

    Each chat read is remembered with its file's stamp (modification time,
    size and file id), so listing again reads only files that changed. Records
    returned by ``list_chats`` share their message lists with that memory:
    treat them as read-only, and use ``load`` for a chat to change.
    """

    def __init__(self, directory=None):
        if directory is None:
            directory = data_directory()
        self.directory = Path(directory).expanduser().resolve()
        self.errors = []
        # Saves run on a worker thread while the window lists chats: the memory needs a lock.
        self._cache = {}
        self._cache_lock = threading.Lock()
        try:
            # A folder created here is private to this user on macOS and Linux.
            ensure_private_directory(self.directory)
        except OSError as error:
            raise ChatStoreError(f"Could not create the saved chats folder: {error}") from error

    def _path(self, chat_id):
        if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id):
            raise ChatStoreError("Chat ID must be a canonical lowercase UUID.")
        path = self.directory / f"{chat_id}.json"
        # Reject links, including dangling links, before any read or mutation.
        # Resolving the chosen directory in __init__ still allows users to put
        # their entire chat folder on a different volume.
        if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                or path.resolve().parent != self.directory):
            raise ChatStoreError(f"Saved chat {chat_id} points outside the chats folder.")
        return path

    def _note_error(self, message):
        if message not in self.errors:
            self.errors.append(message)

    def _validate(self, chat, expected_id=None):
        if not isinstance(chat, dict):
            raise ChatStoreError("A saved chat must be a JSON object.")
        chat_id = chat.get("id")
        self._path(chat_id)
        if expected_id is not None and chat_id != expected_id:
            raise ChatStoreError("The saved chat ID does not match its filename.")
        for field in ("title", "model", "draft", "project_path"):
            if not isinstance(chat.get(field), str):
                raise ChatStoreError(f"Chat {field} must be text.")
        if not isinstance(chat.get("library_path", ""), str):
            raise ChatStoreError("Chat library_path must be text.")
        project_files = chat.get("project_files", [])
        if not isinstance(project_files, list) or not all(isinstance(path, str) for path in project_files):
            raise ChatStoreError("Chat project_files must be a list of text paths.")
        activity = chat.get("activity", [])
        if not isinstance(activity, list):
            raise ChatStoreError("Chat activity must be a list.")
        for event in activity:
            if (not isinstance(event, dict)
                    or not isinstance(event.get("type"), str)
                    or not isinstance(event.get("text"), str)
                    or not set(event).issubset({"type", "text", "path", "command", "diff", "destination"})
                    or any(not isinstance(value, str) for value in event.values())):
                raise ChatStoreError("Each chat activity entry needs text type/text fields and optional text path/command/diff/destination fields.")
        agent_history = chat.get("agent_history", [])
        if not isinstance(agent_history, list):
            raise ChatStoreError("Chat agent_history must be a list.")
        try:
            _validate_json(agent_history)
        except RecursionError as error:
            raise ChatStoreError("Native agent history is nested too deeply or contains a circular value.") from error
        for message in agent_history:
            if (not isinstance(message, dict)
                    or not isinstance(message.get("role"), str)
                    or message["role"] not in _ROLES | {"tool"}
                    or not isinstance(message.get("content", "" if message.get("role") == "assistant" else None), str)):
                raise ChatStoreError("Each native agent message needs a user, assistant, system, or tool role and text content.")
            for field in ("thinking", "tool_name"):
                if field in message and not isinstance(message[field], str):
                    raise ChatStoreError(f"Native agent message {field} must be text.")
            if "tool_calls" in message:
                if not isinstance(message["tool_calls"], list):
                    raise ChatStoreError("Native agent tool_calls must be a list.")
                for call in message["tool_calls"]:
                    function = call.get("function") if isinstance(call, dict) else None
                    if (not isinstance(function, dict) or not isinstance(function.get("name"), str)
                            or not isinstance(function.get("arguments", {}), dict)):
                        raise ChatStoreError("Each native tool call needs a function name and JSON object arguments.")
        if not chat["title"].strip():
            raise ChatStoreError("A chat title cannot be empty.")
        if not isinstance(chat.get("mode"), str) or chat["mode"] not in _MODES:
            raise ChatStoreError("Chat mode must be 'chat' or 'dev'.")
        if not isinstance(chat.get("messages"), list):
            raise ChatStoreError("Chat messages must be a list.")
        for message in chat["messages"]:
            if (not isinstance(message, dict)
                    or not isinstance(message.get("role"), str)
                    or message["role"] not in _ROLES
                    or not isinstance(message.get("content"), str)):
                raise ChatStoreError("Each chat message needs a user, assistant, or system role and text content.")
        context_length = chat.get("context_length")
        if context_length is not None and (type(context_length) is not int or context_length < 1):
            raise ChatStoreError("Chat context_length must be a positive integer or null.")
        for field in ("created_at", "updated_at"):
            _timestamp(chat.get(field), field)
        if "_manual_title" in chat and type(chat["_manual_title"]) is not bool:
            raise ChatStoreError("Chat _manual_title must be true or false.")
        if "pinned" in chat and type(chat["pinned"]) is not bool:
            raise ChatStoreError("Chat pinned must be true or false.")
        record = deepcopy(chat)
        record.setdefault("project_files", [])
        record.setdefault("activity", [])
        record.setdefault("agent_history", [])
        for message in record["agent_history"]:
            if message["role"] == "assistant":
                message.setdefault("content", "")
        record.setdefault("context_length", None)
        return record

    def list_chats(self, query="", mode=None):
        """Return matching records, most recently updated first.

        Search covers the title, model, project path, draft, and message text.
        Invalid files are skipped and reported in ``errors``.
        """
        if not isinstance(query, str):
            raise ChatStoreError("The chat search query must be text.")
        if mode is not None and (not isinstance(mode, str) or mode not in _MODES):
            raise ChatStoreError("Chat mode must be 'chat' or 'dev'.")
        self.errors.clear()
        try:
            paths = sorted(path for path in self.directory.iterdir()
                           if path.suffix == ".json" and path.name != "appearance.json")
        except OSError as error:
            message = f"Could not list saved chats: {error}"
            self._note_error(message)
            raise ChatStoreError(message) from error
        query = query.casefold().strip()
        chats = []
        present = {path.stem for path in paths}
        with self._cache_lock:
            for identity in [identity for identity in self._cache if identity not in present]:
                del self._cache[identity]
        for path in paths:
            previous_errors = len(self.errors)
            try:
                chat = dict(self._remembered(path.stem))
            except (ChatStoreError, FileNotFoundError) as error:
                if len(self.errors) == previous_errors:
                    self._note_error(f"Skipped {path.name}: {error}")
                continue
            if mode is not None and chat["mode"] != mode:
                continue
            if query:
                searchable = [chat["title"], chat["model"], chat["project_path"], chat["draft"]]
                searchable.extend(message["content"] for message in chat["messages"])
                if not any(query in text.casefold() for text in searchable):
                    continue
            chats.append(chat)
        return sorted(chats, key=lambda chat: (_timestamp(chat["updated_at"], "updated_at"), chat["id"]),
                      reverse=True)

    def create(self, model="", mode="chat", title=NEW_CHAT_TITLE, project_path=""):
        """Create and persist an empty conversation."""
        return self.save(self.new_record(model, mode, title, project_path))

    def new_record(self, model="", mode="chat", title=NEW_CHAT_TITLE, project_path=""):
        """Build an empty record without disk I/O for queued persistence."""
        now = _utc_now()
        chat = {
            "id": str(uuid.uuid4()),
            "title": title,
            "model": model,
            "mode": mode,
            "messages": [],
            "draft": "",
            "project_path": project_path,
            "project_files": [],
            "activity": [],
            "agent_history": [],
            "context_length": None,
            "created_at": now,
            "updated_at": now,
            "_manual_title": title != NEW_CHAT_TITLE,
        }
        return chat

    def export_all(self, destination, unsaved=None, deleted=(), extras=None):
        """Write every readable chat to one .zip: the JSON files (restore them with
        Chat options > Restore chats from a backup) and a Markdown copy of each.

        ``unsaved`` maps chat ids to records not yet on disk; ``deleted`` lists
        ids being deleted; ``extras`` maps archive names to other files to include
        when they exist (shared memory). Damaged files are left out and listed in
        ``errors``. Returns the number of chats written."""
        extras = dict(extras or {})
        destination = Path(destination).expanduser()
        unsaved = dict(unsaved or {})
        deleted = set(deleted)
        chats = {chat["id"]: chat for chat in self.list_chats()}
        for identity, record in unsaved.items():
            chats[identity] = self._validate(record, expected_id=identity)
        chats = [chat for identity, chat in chats.items() if identity not in deleted]
        chats.sort(key=lambda chat: (chat["updated_at"], chat["id"]), reverse=True)
        used_names = set()
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("README.txt", (
                    "AI Assist chat backup\n\n"
                    "chats/ holds the saved chats. To restore, use Chat options > Restore chats from a backup\n"
                    "in AI Assist: it adds missing chats and never changes a chat you still have. By hand, copy\n"
                    "the .json files into the AI Assist chats folder (on Windows %LOCALAPPDATA%\\AI Assist\\chats,\n"
                    "elsewhere ~/.aiassist/chats) while AI Assist is closed.\n\n"
                    "readable/ holds the same conversations as Markdown.\n"))
                for name, path in extras.items():
                    if Path(path).is_file():
                        archive.write(path, name)
                for chat in chats:
                    if chat["id"] in unsaved:
                        archive.writestr(f"chats/{chat['id']}.json", json.dumps(chat, ensure_ascii=False, indent=2))
                    else:
                        archive.write(self._path(chat["id"]), f"chats/{chat['id']}.json")
                    base = re.sub(r"[^\w\- ]+", "", chat["title"]).strip()[:60] or "chat"
                    name, counter = base, 2
                    while name.casefold() in used_names:
                        name, counter = f"{base} ({counter})", counter + 1
                    used_names.add(name.casefold())
                    lines = [f"# {chat['title']}", "",
                             f"Model: {chat['model'] or 'none'} ({chat.get('backend', 'Ollama')})  |  Updated: {chat['updated_at']}", ""]
                    for message in chat["messages"]:
                        speaker = {"user": "You", "assistant": "AI Assist"}.get(message["role"], message["role"].title())
                        lines += [f"## {speaker}", "", message["content"], ""]
                    archive.writestr(f"readable/{name}.md", "\n".join(lines))
            os.replace(temporary, destination)
        except (OSError, zipfile.BadZipFile) as error:
            temporary.unlink(missing_ok=True)
            raise ChatStoreError(f"Could not write the backup: {error}") from error
        return len(chats)

    @staticmethod
    def _stamp(path):
        status = path.stat()
        return status.st_mtime_ns, status.st_size, status.st_ino

    def _remembered(self, chat_id):
        """A chat from memory while its file is unchanged; otherwise read (and remember) it.
        The stamp is taken before reading, so a file replaced meanwhile is read again next time."""
        path = self._path(chat_id)
        try:
            stamp = self._stamp(path)
        except FileNotFoundError as error:
            with self._cache_lock:
                self._cache.pop(chat_id, None)
            raise FileNotFoundError(f"Saved chat {chat_id} does not exist.") from error
        except OSError:
            stamp = None
        with self._cache_lock:
            cached = self._cache.get(chat_id)
        if stamp is not None and cached is not None and cached[0] == stamp:
            return cached[1]
        record = self.load(chat_id)
        if stamp is not None:
            with self._cache_lock:
                self._cache[chat_id] = (stamp, record)
        return record

    def load(self, chat_id):
        """Read one chat, raising a diagnostic error for damaged files."""
        path = self._path(chat_id)
        try:
            with path.open("rb") as source:
                data = source.read(_MAX_CHAT_BYTES + 1)
            if len(data) > _MAX_CHAT_BYTES:
                raise ChatStoreError("The saved chat exceeds the 64 MiB file limit.")
            chat = json.loads(data.decode("utf-8"))
            return self._validate(chat, expected_id=chat_id)
        except FileNotFoundError as error:
            raise FileNotFoundError(f"Saved chat {chat_id} does not exist.") from error
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            message = f"Could not load saved chat {chat_id}: {error}"
            self._note_error(message)
            raise ChatStoreError(message) from error

    def save(self, chat):
        """Atomically persist one record and return its updated copy.

        An existing record is read first to protect damaged files and retain its
        original creation timestamp. Independent chats never rewrite each other.
        """
        record = self._validate(chat)
        path = self._path(record["id"])
        if path.exists():
            existing = self._remembered(record["id"])
            record["created_at"] = existing["created_at"]
            # An older copy must not lose an explicit rename flag on save.
            if existing.get("_manual_title"):
                if record["updated_at"] != existing["updated_at"]:
                    record["title"] = existing["title"]
                record["_manual_title"] = True
            # Nor undo a pin made after that copy was read.
            if record["updated_at"] != existing["updated_at"]:
                if existing.get("pinned"):
                    record["pinned"] = True
                else:
                    record.pop("pinned", None)
        if record["title"] == NEW_CHAT_TITLE and not record.get("_manual_title", False):
            record["title"] = title_from(record["messages"]) or record["title"]
        record["updated_at"] = _utc_now()
        self._write(record, path)
        return record

    def validate(self, chat, expected_id=None):
        """The checked copy of a chat record, or ChatStoreError; nothing is written."""
        return self._validate(chat, expected_id=expected_id)

    def import_record(self, chat):
        """Write a chat from a backup exactly as it was saved (title and times kept).
        Refuses to replace a file that exists: a chat you still have is never changed."""
        record = self._validate(chat)
        path = self._path(record["id"])
        if path.exists():
            raise ChatStoreError(f"Saved chat {record['id']} already exists.")
        self._write(record, path)
        return record

    def set_aside_damaged(self, chat_id):
        """Move an unreadable chat file into ``damaged/`` (never deleted), so a backup can replace it."""
        path = self._path(chat_id)
        folder = self.directory / "damaged"
        try:
            ensure_private_directory(folder)
            target = folder / f"{chat_id}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
            os.replace(path, target)
        except OSError as error:
            raise ChatStoreError(f"Could not move the damaged chat {chat_id} aside: {error}") from error
        return target

    def _write(self, record, path):
        temporary_path = None
        with self._cache_lock:
            self._cache.pop(record["id"], None)
        try:
            payload = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
            if len(payload.encode("utf-8")) > _MAX_CHAT_BYTES:
                raise ChatStoreError("The saved chat exceeds the 64 MiB file limit.")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.directory, prefix=f".{record['id']}.",
                                             suffix=".tmp", delete=False) as target:
                temporary_path = Path(target.name)
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
            try:
                # Remember a private copy: callers keep and change the record they get back.
                remembered = (self._stamp(path), deepcopy(record))
                with self._cache_lock:
                    self._cache[record["id"]] = remembered
            except OSError:
                pass
        except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise ChatStoreError(f"Could not save chat {record['id']}: {error}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def rename(self, chat_id, title):
        """Persist a manual title, which subsequent message saves preserve."""
        if not isinstance(title, str) or not title.strip():
            raise ChatStoreError("A chat title cannot be empty.")
        chat = self.load(chat_id)
        chat["title"] = title.strip()
        chat["_manual_title"] = True
        return self.save(chat)

    def set_pinned(self, chat_id, pinned):
        """Pin a chat to the top of the list, or unpin it."""
        if type(pinned) is not bool:
            raise ChatStoreError("Chat pinned must be true or false.")
        chat = self.load(chat_id)
        if pinned:
            chat["pinned"] = True
        else:
            chat.pop("pinned", None)
        return self.save(chat)

    def delete(self, chat_id):
        """Delete only the selected UUID file; an already absent file is fine."""
        path = self._path(chat_id)
        with self._cache_lock:
            self._cache.pop(chat_id, None)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise ChatStoreError(f"Could not delete saved chat {chat_id}: {error}") from error
