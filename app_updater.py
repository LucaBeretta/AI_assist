"""Verify a source distribution and transact only the application's shipped files."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import tempfile
import urllib.request
import uuid
import zipfile

from update_checker import blob_hash, is_shipped, SHIPPED_PACKAGES

LIMIT = 50 * 1024 * 1024
EXPANDED_LIMIT = 4 * LIMIT
CHECKOUT = "This folder is a Git checkout: use git pull."
BACKUP_NAME = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{8}")


def is_git_checkout(app_dir):
    return os.path.lexists(Path(app_dir) / ".git")


def _mutable(root):
    if is_git_checkout(root):
        raise ValueError(CHECKOUT)


def _parts(name):
    parts = name.split("/")
    if any(not p or p in {".", ".."} or p.endswith((".", " ")) or
           re.search(r'[<>:"\\|?*\x00-\x1f]', p) or PureWindowsPath(p).is_reserved()
           for p in parts):
        raise ValueError(f"Unsafe update path: {name}")
    return parts


def _path(root, name):
    path = Path(root)
    for part in _parts(name):
        path = path / part
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or (
                os.name == "nt" and os.path.lexists(path) and
                path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f"Linked update path: {path}")
    return path


def _shipped(name):
    if not is_shipped(PurePosixPath(name)):
        raise ValueError(f"Unexpected application file: {name}")


def _hash(path):
    if path.stat().st_size > EXPANDED_LIMIT:
        raise ValueError(f"Application file is too large: {path.name}")
    return blob_hash(path.read_bytes(), binary=path.suffix.lower() in {".ico", ".icns", ".png"})


def _expected(files):
    if "AIassist.py" not in files or len({name.casefold() for name in files}) != len(files):
        raise ValueError("Missing entry point or ambiguous file list.")
    for name, digest in files.items():
        _parts(name)
        _shipped(name)
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise ValueError("Invalid file hash.")


def plan_update(app_dir, expected_files):
    _expected(expected_files)
    root = Path(app_dir)
    write, remove = [], []
    expected_names = {os.path.normcase(name) for name in expected_files}
    for name, digest in expected_files.items():
        path = _path(root, name)
        if not path.exists() or _hash(path) != digest:
            write.append(name)
    # Only enumerate directories covered by the shipping rules, never user/runtime trees.
    for folder in ("", *SHIPPED_PACKAGES, "child_gate", "webui/static", "assets/icon"):
        base = _path(root, folder) if folder else root
        if base.is_dir():
            for path in base.iterdir():
                name = path.relative_to(root).as_posix()
                if is_shipped(PurePosixPath(name)) and os.path.normcase(name) not in expected_names and _path(root, name).is_file():
                    remove.append(name)
    return {"write": sorted(write), "delete": sorted(remove), "expected_files": expected_files}


def extract_archive(zip_path, staging, expected_files):
    _expected(expected_files)
    staging = Path(staging)
    if staging.is_symlink() or staging.exists():
        raise ValueError("Staging must be a fresh directory.")
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            seen, roots, total = set(), set(), 0
            for member in archive.infolist():
                parts = _parts(member.orig_filename.removesuffix("/"))
                roots.add(parts[0])
                mode = member.external_attr >> 16
                if len(roots) != 1 or stat.S_IFMT(mode) not in {0, stat.S_IFDIR, stat.S_IFREG}:
                    raise ValueError("Unexpected archive root or special file.")
                if member.is_dir():
                    continue
                if len(parts) == 1:
                    raise ValueError("Archive entries need a top-level folder.")
                name = "/".join(parts[1:])
                if not is_shipped(PurePosixPath(name)):
                    continue
                if name not in expected_files or name in seen:
                    raise ValueError(f"Unexpected archive member: {name}")
                total += member.file_size
                if total > EXPANDED_LIMIT:
                    raise ValueError("Expanded update is too large.")
                target = _path(staging, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if _hash(target) != expected_files[name]:
                    raise ValueError(f"Update hash mismatch: {name}")
                if target.suffix in {".cmd", ".ps1"}:
                    # GitHub ZIPs contain Git's LF blobs; cmd.exe needs native line endings.
                    data = target.read_bytes().replace(b"\r\n", b"\n")
                    target.write_bytes(data.replace(b"\n", b"\r\n"))
                if mode & 0o111 or name in {"Start AI Assist.sh", "Start AI Assist.command"}:
                    target.chmod(0o755)
                seen.add(name)
            if seen != set(expected_files):
                raise ValueError("Update archive is incomplete.")
    except BaseException:
        shutil.rmtree(staging)
        raise


def _atomic(path, write):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    temp = Path(name)
    try:
        write(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _json(path, value):
    _atomic(path, lambda temp: temp.write_text(json.dumps(value), encoding="utf-8"))


def _copy(source, target):
    _atomic(target, lambda temp: shutil.copy2(source, temp))


def _state(root):
    path = _path(root, ".update/state.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _previous(root, state):
    name = state.get("previous") or ""
    if not BACKUP_NAME.fullmatch(name):
        return None
    path = _path(root, ".update/backup/" + name)
    return path if _path(path, "manifest.json").is_file() else None


def can_rollback(root):
    return not is_git_checkout(root) and bool(_previous(root, _state(root)))


def _snapshot(root, backup, names, state):
    backup.mkdir(parents=True, exist_ok=False)
    existing = {}
    for name in names:
        _shipped(name)
        source = _path(root, name)
        if source.exists():
            target = _path(backup, "files/" + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            existing[name] = _hash(target)
        else:
            existing[name] = None
    _json(backup / "manifest.json", {"files": existing, "state": state})


def _restore(root, backup, changed=None, restored=None):
    manifest = json.loads(_path(backup, "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    if changed is not None:
        files = {name: files[name] for name in changed}
    for name, digest in files.items():
        _shipped(name)
        _path(root, name)
        if digest is not None and _hash(_path(backup, "files/" + name)) != digest:
            raise ValueError(f"Damaged backup: {name}")
    for name, digest in files.items():
        target = _path(root, name)
        if digest is None:
            target.unlink(missing_ok=True)
        else:
            _copy(_path(backup, "files/" + name), target)
        if restored is not None:
            restored.append(name)
    state = dict(manifest["state"])
    if not _previous(root, state):
        state.pop("previous", None)
    state_path = _path(root, ".update/state.json")
    if state:
        _json(state_path, state)
    else:
        state_path.unlink(missing_ok=True)


def apply_update(app_dir, staging, plan, after_files=None):
    root, staging = Path(app_dir), Path(staging)
    _mutable(root)
    expected = plan["expected_files"]
    # Re-plan against the live copy and verify sources again before the first write.
    plan = plan_update(root, expected)
    for name in plan["write"]:
        if _hash(_path(staging, name)) != expected[name]:
            raise ValueError(f"Update hash mismatch: {name}")
    stamp = datetime.now(timezone.utc)
    identity = stamp.strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
    backup = _path(root, ".update/backup/" + identity)
    state = _state(root)
    try:
        _snapshot(root, backup, plan["write"] + plan["delete"], state)
    except BaseException:
        if backup.is_dir():
            shutil.rmtree(backup)
        raise
    result = {"revision": staging.name, "previous": identity, "installed_at": stamp.isoformat()}
    changed = []
    try:
        for name in plan["write"]:
            _copy(_path(staging, name), _path(root, name))
            changed.append(name)
        for name in plan["delete"]:
            _path(root, name).unlink()
            changed.append(name)
        if after_files:
            after_files(plan)
        _json(_path(root, ".update/state.json"), result)
    except BaseException:
        _restore(root, backup, changed)
        shutil.rmtree(backup)
        raise
    older = sorted(p for p in backup.parent.iterdir() if BACKUP_NAME.fullmatch(p.name) and p != backup)
    for path in older[:-1]:
        try:
            shutil.rmtree(_path(backup.parent, path.name))
        except OSError:
            logging.getLogger(__name__).warning("Could not prune backup %s", path, exc_info=True)
    return result


def rollback(app_dir):
    root = Path(app_dir)
    _mutable(root)
    state = _state(root)
    backup = _previous(root, state)
    if not backup:
        raise ValueError("No previous version is available.")
    manifest = json.loads(_path(backup, "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir=backup.parent) as temporary:
        recovery = Path(temporary) / "recovery"
        _snapshot(root, recovery, manifest["files"], state)
        restored = []
        try:
            _restore(root, backup, restored=restored)
        except BaseException:
            _restore(root, recovery, changed=restored)
            raise
    shutil.rmtree(backup)
    return _state(root)


def download(revision, target, progress, cancel):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid update revision.")
    url = "https://codeload.github.com/LucaBeretta/AI_assist/zip/" + revision
    with urllib.request.urlopen(url, timeout=20) as response, target.open("xb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        if total > LIMIT:
            raise ValueError("Update download is too large.")
        count = 0
        while not cancel.is_set():
            chunk = response.read(128 * 1024)
            if not chunk:
                if total and count != total:
                    raise ValueError("Incomplete update download.")
                return
            count += len(chunk)
            if count > LIMIT:
                raise ValueError("Update download is too large.")
            output.write(chunk)
            progress(completed=count, total=total)
        raise InterruptedError("Download cancelled.")
