"""Original file snapshots per Developer task, with confined explicit restore."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import uuid

from app_core.paths import replace_retrying
import developer_tools


_LOCK = threading.RLock()
_MAX_BYTES = 4000000


def _identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Checkpoint task IDs must be canonical UUIDs.")
    return value


def _atomic(target, data, mode=None):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CheckpointStore:
    def __init__(self, directory):
        # Do not resolve a link away before checking it.
        self.directory = Path(directory).absolute()

    def _folder(self, task_id):
        _identity(task_id)
        for parent in (self.directory, *self.directory.parents):
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise ValueError("Checkpoint storage must not use directory links.")
        path = self.directory / task_id
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or path.resolve().parent != self.directory.resolve():
            raise ValueError("Checkpoint folders must remain inside checkpoint storage.")
        return path

    def _load(self, task_id):
        folder = self._folder(task_id)
        source = folder / "manifest.json"
        if source.is_symlink():
            raise ValueError("Checkpoint manifests must not be links.")
        with source.open("rb") as manifest:
            data = manifest.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Checkpoint manifest is too large.")
        value = json.loads(data.decode("utf-8"))
        if (not isinstance(value, dict) or value.get("task_id") != task_id
                or not isinstance(value.get("project"), str) or not isinstance(value.get("created_at"), str)
                or not isinstance(value.get("files"), dict)):
            raise ValueError("Invalid checkpoint manifest.")
        return value

    def capture(self, task_id, project, target, original):
        project = developer_tools._project_root(project)
        target = Path(target).resolve()
        try:
            relative = target.relative_to(project).as_posix()
        except ValueError:
            # The existing approval UI can explicitly choose a destination
            # outside the task project. Such files never enter its checkpoint.
            return False
        developer_tools._check_components(Path(relative).parts)
        if original is not None:
            if len(original) > _MAX_BYTES:
                raise ValueError("The original exceeds the checkpoint file limit.")
            developer_tools._decode_text(original)
        with _LOCK:
            folder = self._folder(task_id)
            existing = folder.exists()
            if existing:
                manifest = self._load(task_id)
                if manifest["project"] != str(project):
                    raise ValueError("A task checkpoint belongs to one project only.")
            else:
                manifest = {"task_id": task_id, "project": str(project), "created_at":
                    datetime.now(timezone.utc).isoformat(), "files": {}}
            if relative in manifest["files"]:
                return False
            backup = str(uuid.uuid4()) + ".bak" if original is not None else None
            entry = {"backup": backup, "created": original is None, "size": len(original or b""),
                     "sha256": hashlib.sha256(original or b"").hexdigest(),
                     "mode": stat.S_IMODE(target.stat().st_mode) if original is not None else None}
            manifest["files"][relative] = entry
            if existing:
                self._save_capture(folder, manifest, backup, original)
            else:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=self.directory) as staging:
                    self._save_capture(Path(staging), manifest, backup, original)
                    replace_retrying(staging, folder)
        return True

    @staticmethod
    def _save_capture(folder, manifest, backup, original):
        if backup:
            _atomic(folder / backup, original, 0o600)
        _atomic(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"), 0o600)

    def list(self, project, *, warnings=None):
        project = developer_tools._project_root(project)
        with _LOCK:
            if not self.directory.exists():
                return []
            # Validate the storage root even if it contains no valid task IDs.
            self._folder(str(uuid.uuid4()))
            records = []
            for path in self.directory.iterdir():
                try:
                    _identity(path.name)
                except ValueError:
                    continue
                try:
                    manifest = self._load(path.name)
                except (OSError, ValueError, RecursionError):
                    if warnings is not None:
                        warnings.append(f"Skipped unreadable checkpoint {path.name}.")
                    continue
                if manifest["project"] == str(project):
                    records.append(manifest)
            return sorted(records, key=lambda item: (item["created_at"], item["task_id"]), reverse=True)

    def prune(self, project, current):
        """Apply retention after the task's writes have finished."""
        with _LOCK:
            if not self._folder(current).is_dir():
                return
            records = [item for item in self.list(project) if item["task_id"] != current]
            for manifest in records[9:]:
                target = self._folder(manifest["task_id"])
                # Every recursive delete resolves to one UUID child of storage.
                if target.resolve().parent != self.directory.resolve():
                    raise ValueError("Checkpoint retention target escaped storage.")
                shutil.rmtree(target)

    def restore(self, task_id):
        import coding_agent
        with _LOCK:
            manifest = self._load(task_id)
            project = developer_tools._project_root(manifest["project"])
            folder = self._folder(task_id)
            prepared = []
            # Preflight every target and backup before changing any project file.
            for relative, entry in manifest["files"].items():
                target, _ = coding_agent._safe_target(project, relative)
                if not isinstance(entry, dict) or type(entry.get("created")) is not bool:
                    raise ValueError("Invalid checkpoint file entry.")
                if target.exists() and not target.is_file():
                    raise ValueError("A checkpoint target is no longer a regular file.")
                if entry["created"]:
                    if entry.get("backup") is not None:
                        raise ValueError("Created checkpoint files cannot have original backups.")
                    prepared.append((target, None, None))
                    continue
                name = entry.get("backup")
                if not isinstance(name, str) or not name.endswith(".bak"):
                    raise ValueError("Invalid checkpoint backup name.")
                _identity(name[:-4])
                backup = folder / name
                if backup.is_symlink() or not backup.is_file():
                    raise ValueError("Checkpoint backups must be regular files.")
                with backup.open("rb") as source:
                    data = source.read(_MAX_BYTES + 1)
                if len(data) > _MAX_BYTES or len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                    raise ValueError("The checkpoint backup is damaged.")
                developer_tools._decode_text(data)
                mode = entry.get("mode")
                if type(mode) is not int or not 0 <= mode <= 0o7777:
                    raise ValueError("Invalid original file mode.")
                prepared.append((target, data, mode))
            for target, data, mode in prepared:
                checked, _ = coding_agent._safe_target(project, target.relative_to(project).as_posix())
                if checked != target:
                    raise ValueError("A checkpoint target changed during restore.")
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    checked, _ = coding_agent._safe_target(project, target.relative_to(project).as_posix())
                    if checked != target:
                        raise ValueError("A checkpoint target changed during restore.")
                    _atomic(target, data, mode)
            return {"task_id": task_id, "project": str(project), "restored": len(prepared)}
