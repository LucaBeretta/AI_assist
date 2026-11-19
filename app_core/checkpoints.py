"""Undo for the developer agent: the original of every file it changes, per task.

Before the agent's file tools replace or create a file, the file's original
content is saved here, outside the project, in ``<data>/checkpoints/<id>/``.
``undo`` puts the originals back and removes files the task created.

Safety rules:

- **Nothing is changed without its undo copy.** If the original cannot be
  saved, the agent's write is refused (coding_agent raises before replacing).
- **Undo never destroys your later work.** A file is restored only while it
  still holds exactly what the agent wrote; otherwise it is left alone and
  reported, so edits made after the agent are kept.
- Only the agent's file tools are covered. A command the agent ran can change
  files in ways this cannot see; the undo report says so.

No git is needed, and a project's own git history is never touched.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import uuid

from app_core.paths import ensure_private_directory
from app_core.settings import read_json, write_json

KEEP = 50
_ID = re.compile(r"[0-9a-f]{32}")
_BACKUP = re.compile(r"\d{1,6}\.orig")


class CheckpointError(ValueError):
    """A checkpoint cannot be saved, read or undone."""


def _digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _key(path):
    return os.path.normcase(os.path.abspath(path))


class Checkpoint:
    """One agent task's originals. Created empty; its folder appears with the first write."""

    def __init__(self, directory, chat_id="", project=""):
        self.directory = Path(directory)
        self.id = self.directory.name
        self.lock = threading.Lock()
        self.manifest = {"id": self.id, "chat_id": chat_id, "project": str(project), "created": _now(),
                         "files": [], "undone": None}

    def _entry(self, target):
        key = _key(target)
        return next((entry for entry in self.manifest["files"] if _key(entry["path"]) == key), None)

    def _save(self):
        write_json(self.directory / "manifest.json", self.manifest)

    def before_write(self, target, old_bytes):
        """Keep ``target``'s original before its first change in this task."""
        with self.lock:
            if self._entry(target) is not None:
                return  # The earliest original is the one to restore.
            try:
                ensure_private_directory(self.directory)
                entry = {"path": str(Path(target).resolve()), "existed": old_bytes is not None, "backup": None,
                         "written": None}
                if old_bytes is not None:
                    name = f"{len(self.manifest['files'])}.orig"
                    with tempfile.NamedTemporaryFile("wb", dir=self.directory, delete=False) as handle:
                        handle.write(old_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(handle.name, self.directory / name)
                    entry["backup"] = name
                self.manifest["files"].append(entry)
                self._save()
            except OSError as error:
                raise CheckpointError(f"Could not save an undo copy of {Path(target).name}: {error}") from error

    def after_write(self, target, new_bytes):
        """Remember what the agent wrote, so undo can tell whether you changed it since."""
        with self.lock:
            entry = self._entry(target)
            if entry is not None:
                entry["written"] = _digest(new_bytes)
                try:
                    self._save()
                except OSError:
                    pass  # The file was written; undo will then see it as changed and keep it.


class CheckpointStore:
    def __init__(self, root, keep=KEEP):
        self.root = Path(root)
        self.keep = keep

    def begin(self, chat_id="", project=""):
        self.prune()
        return Checkpoint(self.root / uuid.uuid4().hex, chat_id, project)

    def _manifest(self, checkpoint_id):
        if not isinstance(checkpoint_id, str) or not _ID.fullmatch(checkpoint_id):
            raise CheckpointError("Unknown undo point.")
        data = read_json(self.root / checkpoint_id / "manifest.json")
        if data.get("id") != checkpoint_id or not isinstance(data.get("files"), list):
            raise CheckpointError("This undo point is missing or damaged.")
        return data

    def list(self, chat_id=None):
        """Undo points with at least one changed file, newest first."""
        if not self.root.is_dir():
            return []
        found = []
        for folder in self.root.iterdir():
            if not _ID.fullmatch(folder.name):
                continue
            try:
                data = self._manifest(folder.name)
            except CheckpointError:
                continue
            if data["files"] and (chat_id is None or data.get("chat_id") == chat_id):
                found.append(data)
        return sorted(found, key=lambda data: str(data.get("created", "")), reverse=True)

    def latest_undoable(self, chat_id):
        return next((data for data in self.list(chat_id) if not data.get("undone")), None)

    def undo(self, checkpoint_id):
        """Restore a task's originals. Returns {"restored", "removed", "kept"} as lists of paths
        (``kept`` holds (path, reason) pairs)."""
        data = self._manifest(checkpoint_id)
        if data.get("undone"):
            raise CheckpointError("These changes were already undone.")
        folder = self.root / checkpoint_id
        report = {"restored": [], "removed": [], "kept": []}
        for entry in reversed(data["files"]):
            path = Path(str(entry.get("path", "")))
            if not path.is_absolute():
                report["kept"].append((str(path), "the undo record is damaged"))
                continue
            try:
                current = path.read_bytes() if path.is_file() else None
            except OSError as error:
                report["kept"].append((str(path), f"could not be read ({error.strerror or error})"))
                continue
            if entry.get("written") is None or _digest(current) != entry["written"]:
                report["kept"].append((str(path), "changed after the AI wrote it"))
                continue
            temporary = None
            try:
                if entry.get("existed"):
                    backup = str(entry.get("backup") or "")
                    if not _BACKUP.fullmatch(backup):
                        raise OSError("the saved original is missing")
                    original = (folder / backup).read_bytes()
                    mode = stat.S_IMODE(path.stat().st_mode)
                    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".aiassist-undo-", delete=False) as handle:
                        temporary = handle.name
                        handle.write(original)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, mode)
                    os.replace(temporary, path)
                    temporary = None
                    report["restored"].append(str(path))
                else:
                    path.unlink()
                    report["removed"].append(str(path))
            except OSError as error:
                report["kept"].append((str(path), f"could not be restored ({error.strerror or error})"))
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        data["undone"] = _now()
        write_json(folder / "manifest.json", data)
        self.prune()
        return report

    def prune(self):
        """Keep the newest ``keep`` undo points."""
        if not self.root.is_dir():
            return
        folders = [folder for folder in self.root.iterdir() if folder.is_dir() and _ID.fullmatch(folder.name)]
        folders.sort(key=lambda folder: folder.stat().st_mtime, reverse=True)
        for folder in folders[self.keep:]:
            shutil.rmtree(folder, ignore_errors=True)
