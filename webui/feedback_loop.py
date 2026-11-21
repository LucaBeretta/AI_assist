"""Opt-in, local post-task feedback; no model call or code execution here."""
import json
from pathlib import Path
import re
import uuid

from app_core.settings import write_json
from webui.api_utils import json_method
from chat_store import _utc_now

_MAX_ENTRIES = 250
_MAX_FILE_BYTES = 512 * 1024
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,120}")


class FeedbackLog:
    """A bounded local journal outside chat transcripts and exports."""

    def __init__(self, directory):
        self.path = Path(directory) / "task-feedback.json"

    def _check_path(self):
        if self.path.is_symlink() or getattr(self.path, "is_junction", lambda: False)():
            raise ValueError("Feedback journal must not be a link.")

    def read(self):
        self._check_path()
        if not self.path.exists():
            return []
        if self.path.stat().st_size > _MAX_FILE_BYTES:
            raise ValueError("Feedback journal exceeds its size limit.")
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise ValueError("Feedback journal is unreadable; it was not overwritten.") from error
        if not isinstance(entries, list):
            raise ValueError("Feedback journal must be a list.")
        return entries

    def append(self, entry):
        entries = self.read()
        entries.append(entry)
        write_json(self.path, entries[-_MAX_ENTRIES:])
        return entry


class FeedbackApi:
    @json_method
    def record_task_feedback(self, chat_id, reply_index, rating, improvement="", remember=False, task_id=""):
        """Save explicit evaluation; remembering a preference requires its own checkbox.

        Developer feedback refers to its task ID and reply_index=-1; chat feedback
        refers to a specific saved assistant message. Neither form reruns work.
        """
        if rating not in ("useful", "not_useful"):
            raise ValueError("Choose useful or not useful.")
        if type(improvement) is not str or len(improvement) > 1200:
            raise ValueError("Feedback must be at most 1,200 characters.")
        if type(remember) is not bool:
            raise ValueError("Remember must be true or false.")
        if type(reply_index) is not int or type(task_id) is not str:
            raise ValueError("Choose a valid completed task.")
        if task_id:
            if not _TASK_ID.fullmatch(task_id) or reply_index != -1:
                raise ValueError("Choose a valid Developer task.")
            kind = "developer"
        else:
            kind = "chat"
        improvement = improvement.strip()
        if remember and not improvement:
            raise ValueError("Describe a preference before asking AI Assist to remember it.")
        with self._lock:
            if self._closed:
                raise RuntimeError("The app is closing.")
            record = self._load_chat(chat_id)
            if record.get("incognito"):
                raise ValueError("Incognito work cannot be saved as feedback.")
            if kind == "chat" and (not 0 <= reply_index < len(record["messages"])
                    or record["messages"][reply_index]["role"] != "assistant"):
                raise ValueError("Choose a saved assistant reply.")
            journal = FeedbackLog(self._store.directory)
            journal.read()  # Fail before updating shared memory if the journal is corrupted.
            if remember:
                self._memories.put("User preference from feedback: " + improvement)
                self._bridge.emit("memories_changed")
            entry = {"id": str(uuid.uuid4()), "chat_id": chat_id, "kind": kind,
                     "reply_index": reply_index if kind == "chat" else None,
                     "task_id": task_id if kind == "developer" else None,
                     "rating": rating, "improvement": improvement, "remembered": remember,
                     "created_at": _utc_now()}
            journal.append(entry)
        return {"ok": True, "id": entry["id"], "remembered": remember}
