"""Named reusable prompts, stored separately from chat JSON."""

import json
from pathlib import Path

from webui.local_files import write_utf8


class PromptStore:
    def __init__(self, directory):
        self.path = Path(directory) / "saved.prompts"

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
            raise ValueError("A prompt name must contain 1 to 100 characters without control characters.")
        return value.strip()

    @staticmethod
    def text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 1000000:
            raise ValueError("A saved prompt must contain 1 to 1,000,000 characters.")
        return value

    def _load(self):
        if self.path.is_symlink() or getattr(self.path, "is_junction", lambda: False)():
            raise ValueError("Saved prompts must not be a link.")
        try:
            with self.path.open("rb") as source:
                data = source.read(4000001)
        except FileNotFoundError:
            return {}
        if len(data) > 4000000:
            raise ValueError("Saved prompts exceed the 4 MB limit.")
        value = json.loads(data)
        if not isinstance(value, dict) or len(value) > 1000:
            raise ValueError("Saved prompts must be an object with at most 1,000 entries.")
        for name, text in value.items():
            if self.name(name) != name:
                raise ValueError("Saved prompt names must not have surrounding whitespace.")
            self.text(text)
        return value

    def list(self):
        return [{"name": name, "text": text} for name, text in sorted(self._load().items(), key=lambda item: item[0].casefold())]

    def save(self, name, text):
        name, text = self.name(name), self.text(text)
        value = self._load()
        value[name] = text
        encoded = json.dumps(value, ensure_ascii=False)
        if len(value) > 1000 or len(encoded.encode("utf-8")) > 4000000:
            raise ValueError("Save at most 1,000 prompts within 4 MB.")
        write_utf8(self.path, encoded)
        return {"name": name, "text": text}

    def delete(self, name):
        name = self.name(name)
        value = self._load()
        if name not in value:
            raise ValueError("Choose an existing saved prompt.")
        del value[name]
        write_utf8(self.path, json.dumps(value, ensure_ascii=False))
