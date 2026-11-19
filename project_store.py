"""Local projects stored as individually validated, atomically replaced JSON."""

from copy import deepcopy
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import uuid

from chat_store import ChatStore, ChatStoreError, _CHAT_ID, _environment, _timestamp, _utc_now, default_environment as chat_environment
import developer_tools


class ProjectStoreError(ValueError):
    pass


class ProjectStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else ChatStore().directory / "projects"
        self.directory = self.directory.absolute()
        self.errors = []
        self._root()

    def _root(self, create=False):
        if self.directory.is_symlink() or getattr(self.directory, "is_junction", lambda: False)():
            raise ProjectStoreError("The projects folder must not be a link.")
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory.resolve()

    def _path(self, project_id):
        if not isinstance(project_id, str) or not _CHAT_ID.fullmatch(project_id):
            raise ProjectStoreError("Project IDs must be canonical lowercase UUIDs.")
        root = self._root()
        path = root / (project_id + ".json")
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or path.resolve().parent != root:
            raise ProjectStoreError("Project records must stay inside their storage folder.")
        return path

    def _validate(self, record, expected_id=None):
        if not isinstance(record, dict):
            raise ProjectStoreError("A project must be an object.")
        self._path(record.get("id"))
        if expected_id is not None and expected_id != record["id"]:
            raise ProjectStoreError("The project ID does not match its filename.")
        if not isinstance(record.get("name"), str) or not record["name"].strip() or len(record["name"]) > 160:
            raise ProjectStoreError("A project name needs 1 to 160 characters.")
        if not isinstance(record.get("instructions"), str) or len(record["instructions"]) > 1000000:
            raise ProjectStoreError("Project instructions must be text up to 1,000,000 characters.")
        folder = record.get("folder")
        if folder is not None and (not isinstance(folder, str) or not Path(folder).is_absolute()):
            raise ProjectStoreError("A project folder must be an absolute path or null.")
        pins = record.get("pinned_paths")
        if not isinstance(pins, list) or any(not isinstance(path, str) or not path for path in pins):
            raise ProjectStoreError("Project pinned paths must be a list of relative paths.")
        for path in pins:
            relative = Path(path.replace("\\", "/"))
            windows = PureWindowsPath(path)
            if relative.is_absolute() or windows.drive or windows.root or ".." in relative.parts or any(
                    ":" in part or part.endswith((".", " ")) for part in relative.parts):
                raise ProjectStoreError("Pinned paths must stay inside the project.")
            developer_tools._check_components(relative.parts)
        try:
            _environment(record.get("default_environment"))
            for key in ("created_at", "updated_at"):
                _timestamp(record.get(key), key)
        except ChatStoreError as error:
            raise ProjectStoreError(str(error)) from error
        return deepcopy(record)

    def create(self, name, instructions="", folder=None, pinned_paths=None, default_environment=None):
        if folder is not None:
            folder = str(developer_tools._project_root(folder))
        now = _utc_now()
        return self.save({"id": str(uuid.uuid4()), "name": name, "instructions": instructions, "folder": folder,
            "pinned_paths": [] if pinned_paths is None else pinned_paths,
            "default_environment": default_environment if default_environment is not None else chat_environment({"model": ""}),
            "created_at": now, "updated_at": now})

    def load(self, project_id):
        path = self._path(project_id)
        with path.open("rb") as source:
            data = source.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ProjectStoreError("The project record exceeds 4 MiB.")
        return self._validate(json.loads(data.decode("utf-8")), project_id)

    def list(self):
        self.errors.clear()
        result = []
        for path in sorted(self._root().glob("*.json")):
            try:
                result.append(self.load(path.stem))
            except (OSError, ValueError, RecursionError) as error:
                self.errors.append(f"Skipped {path.name}: {error}")
        return sorted(result, key=lambda record: (record["updated_at"], record["id"]), reverse=True)

    def save(self, record):
        value = self._validate(record)
        path = self._path(value["id"])
        if path.exists():
            value["created_at"] = self.load(value["id"])["created_at"]
        value["updated_at"] = _utc_now()
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        if len(data.encode("utf-8")) > 4 * 1024 * 1024:
            raise ProjectStoreError("The project record exceeds 4 MiB.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self._root(create=True),
                                             suffix=".tmp", delete=False) as target:
                temporary = Path(target.name)
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value

    def update(self, project_id, changes):
        if not isinstance(changes, dict) or not set(changes).issubset({"name", "instructions", "folder", "pinned_paths", "default_environment"}):
            raise ProjectStoreError("Only project name, instructions, folder, pinned paths and environment can be updated.")
        value = self.load(project_id)
        changes = deepcopy(changes)
        if changes.get("folder") is not None:
            changes["folder"] = str(developer_tools._project_root(changes["folder"]))
        value.update(changes)
        return self.save(value)

    def delete(self, project_id):
        self._path(project_id).unlink(missing_ok=True)


def inherited_environment(project):
    environment = deepcopy(project["default_environment"])
    if project["instructions"] and project["instructions"] != environment["instructions"]:
        environment["instructions"] = "\n\n".join(filter(None, (environment["instructions"], project["instructions"])))
    environment["pinned_paths"] = list(dict.fromkeys(environment["pinned_paths"] + project["pinned_paths"]))
    return environment
