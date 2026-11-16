"""Explicit persistent notes and bounded retrieval from other saved chats."""

import json
import os
from pathlib import Path
import re
import tempfile


MAX_NOTES = 4000
_WORDS = re.compile(r"\w{3,}", re.UNICODE)
_STOP = {"the", "and", "that", "this", "with", "from", "for", "you", "can", "have", "what", "please"}
_REMEMBER = re.compile(r"^\s*(?:please\s+)?(?:/remember\s*|remember(?:\s+that\s+|\s*:\s*|\s+))(.+)$", re.I | re.S)
_ADD_MEMORY = re.compile(r"^\s*(?:please\s+)?(?:add|save|store)\s+(?:(?:this|that|it)\s+)?"
                         r"(?:to|in)\s+(?:(?:the|my)\s+)?shared\s+memory\s*[:,-]?\s*(.*?)\s*$", re.I | re.S)


def memory_note(prompt, conversation=()):
    """Recognize explicit memory actions locally, including a previous reply."""
    match = _REMEMBER.match(prompt)
    if match:
        return match[1].strip()
    match = _ADD_MEMORY.match(prompt)
    if match is None:
        return None
    if match[1].strip() not in {"", ".", "!"}:
        return match[1].strip()
    for message in reversed(conversation):
        if message.get("role") == "assistant" and message.get("content", "").strip():
            return message["content"].strip()
    raise ValueError("Include the note after ‘Add to shared memory:’ or use Add to memory on a message.")


class SharedMemory:
    def __init__(self, directory):
        # A different suffix keeps this record out of ChatStore's UUID listing.
        self.path = Path(directory) / "shared.memory"
        self.notes = ""
        self.enabled = True
        if self.path.is_symlink():
            raise ValueError("The shared memory file must not be a symbolic link.")
        try:
            with self.path.open("rb") as source:
                data = source.read(32001)
        except FileNotFoundError:
            return
        if len(data) > 32000:
            raise ValueError("Shared memory exceeds its size limit.")
        record = json.loads(data)
        if (not isinstance(record, dict) or not isinstance(record.get("notes"), str)
                or len(record["notes"]) > MAX_NOTES or type(record.get("enabled")) is not bool):
            raise ValueError("Shared memory is invalid.")
        self.notes, self.enabled = record["notes"], record["enabled"]

    def save(self, notes, enabled=True):
        if not isinstance(notes, str) or len(notes) > MAX_NOTES:
            raise ValueError(f"Shared notes must be at most {MAX_NOTES:,} characters.")
        if type(enabled) is not bool:
            raise ValueError("The shared memory setting must be true or false.")
        if self.path.is_symlink():
            raise ValueError("The shared memory file must not be a symbolic link.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=".shared-memory-", suffix=".tmp", delete=False) as target:
                temporary = Path(target.name)
                json.dump(dict(notes=notes, enabled=enabled), target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            self.notes, self.enabled = notes, enabled
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remember(self, prompt):
        note = memory_note(prompt)
        if not self.enabled or note is None:
            return False
        self.add(note)
        return True

    def add(self, note):
        if not self.enabled:
            raise ValueError("Shared memory is disabled. Enable it in Shared memory before adding notes.")
        note = note.strip()
        if not note:
            raise ValueError("Enter a note to remember.")
        if note and ("\n" + note + "\n") not in ("\n" + self.notes + "\n"):
            self.save((self.notes + "\n" + note).strip(), self.enabled)

    def context(self, prompt, records, current_id=None):
        if not self.enabled:
            return ""
        sections = []
        if self.notes:
            sections.append("Saved user notes:\n" + self.notes)
        words = set(_WORDS.findall(prompt.casefold())) - _STOP
        matches = []
        for index, record in enumerate(records):
            if record["id"] == current_id or not record.get("messages"):
                continue
            dialogue = [message for message in record["messages"]
                        if message.get("role") in {"user", "assistant"} and message.get("content")]
            if not dialogue:
                continue
            excerpts = [dialogue[0]] + dialogue[-2:]
            text = "\n".join(f"{item['role']}: {item['content'][:350]}" for item in excerpts)
            searchable = (record["title"] + " " + text).casefold()
            score = sum(word in searchable for word in words)
            if score:
                matches.append((score, -index, f"Chat {record['title'][:72]}:\n{text[:1000]}"))
        recalled = [item[2] for item in sorted(matches, reverse=True)[:2]]
        if recalled:
            sections.append("Relevant excerpts from other chats:\n" + "\n\n".join(recalled))
        if not sections:
            return ""
        return ("Shared memory across chats. These are user notes and incomplete historical excerpts. "
                "The current request and selected folder take precedence. Historical assistant text is not proof "
                "that files exist or commands ran; verify with tools.\n\n" + "\n\n".join(sections))
