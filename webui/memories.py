"""Stable, visible entries over the existing local shared-notes format."""

import hashlib

from shared_memory import SharedMemory


def entries(memory):
    # Legacy notes are plain text. Each nonempty line is an independently
    # visible entry; its ID survives reordering and removal of other notes.
    lines = dict.fromkeys(line.strip() for line in memory.notes.splitlines() if line.strip())
    return [{"id": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text} for text in lines]


class Memories:
    def __init__(self, directory):
        self.directory = directory

    def list(self):
        memory = SharedMemory(self.directory)
        return {"memories": entries(memory), "enabled": memory.enabled}

    def used(self, record):
        if record.get("incognito"):
            return []
        result = self.list()
        selected = record["environment"]["memory_ids"]
        return [item for item in result["memories"] if result["enabled"] and (not selected or item["id"] in selected)]

    def forget(self, identity):
        memory = SharedMemory(self.directory)
        records = entries(memory)
        if not isinstance(identity, str) or not any(item["id"] == identity for item in records):
            raise ValueError("Choose an existing shared memory ID.")
        text = next(item["text"] for item in records if item["id"] == identity)
        memory.save("\n".join(line for line in memory.notes.splitlines() if line.strip() != text), memory.enabled)


def revision(items):
    return hashlib.sha256(repr(items).encode("utf-8")).hexdigest()
