"""Automatic local backups of chats and shared notes, and a restore that never destroys.

Backups are zip files in ``<data>/backups/`` named ``chats-YYYYMMDD-HHMMSS.zip``
(the same format as Chat options > Back up all chats, plus ``memory/shared.memory``).
One is written when the newest is older than a day; the newest ``KEEP`` are kept.
They stay on this computer: copy one to a USB stick to keep it elsewhere.

Restore rules:

- A chat you still have is **never changed**. Missing chats are added as they were.
- A chat whose file is damaged is replaced, and the damaged file is moved to
  ``damaged/``, not deleted.
- Shared notes are restored only when yours are empty.
- The archive is read entry by entry with exact names and size limits; nothing
  is extracted, so a crafted zip cannot write anywhere else.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import zipfile

from chat_store import ChatStoreError

FOLDER = "backups"
KEEP = 14
INTERVAL = timedelta(days=1)
MEMORY_NAME = "memory/shared.memory"
_NAME = re.compile(r"chats-(\d{8}-\d{6})\.zip")
_CHAT = re.compile(r"chats/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json")
_MAX_ENTRY = 64 * 1024 * 1024
_MAX_MEMORY = 32000


class BackupError(ValueError):
    """A backup could not be written or read."""


def folder_for(store):
    return Path(store.directory) / FOLDER


def _created(path):
    match = _NAME.fullmatch(Path(path).name)
    if not match:
        return None
    return datetime.strptime(match[1], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)


def list_backups(store):
    """Automatic backups, newest first: [{"name", "path", "created", "size"}]."""
    folder = folder_for(store)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        created = _created(path)
        if created is not None and path.is_file():
            found.append({"name": path.name, "path": path, "created": created, "size": path.stat().st_size})
    return sorted(found, key=lambda item: item["created"], reverse=True)


def due(store, now=None, interval=INTERVAL):
    """True when there is no backup from the last ``interval``."""
    now = now or datetime.now(timezone.utc)
    newest = next(iter(list_backups(store)), None)
    return newest is None or now - newest["created"] >= interval


def create(store, memory_path=None, now=None, keep=KEEP):
    """Write a backup now and keep the newest ``keep``. Returns (path, chat count)."""
    from app_core.paths import ensure_private_directory

    now = now or datetime.now(timezone.utc)
    folder = ensure_private_directory(folder_for(store))
    destination = folder / f"chats-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    extras = {MEMORY_NAME: memory_path} if memory_path else {}
    try:
        count = store.export_all(destination, extras=extras)
    except (ChatStoreError, OSError) as error:
        raise BackupError(f"Could not write the backup: {error}") from error
    for old in list_backups(store)[keep:]:
        try:
            old["path"].unlink()
        except OSError:
            pass
    return destination, count


def create_if_due(store, memory_path=None, now=None):
    """The daily backup: (path, count) when one was written, otherwise None."""
    return create(store, memory_path, now) if due(store, now) else None


def restore(store, archive_path, memory=None):
    """Bring back chats from a backup zip. Returns a report of counts:
    ``added``, ``replaced_damaged``, ``kept`` (you still have it), ``invalid``,
    and ``memory`` ("restored", "kept yours", "not in backup" or "unavailable")."""
    report = {"added": 0, "replaced_damaged": 0, "kept": 0, "invalid": 0, "memory": "not in backup"}
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise BackupError(f"This is not a readable AI Assist backup: {error}") from error
    with archive:
        entries = {info.filename: info for info in archive.infolist()}
        chats = [(match[1], info) for name, info in entries.items() if (match := _CHAT.fullmatch(name))]
        if not chats and MEMORY_NAME not in entries:
            raise BackupError("This zip holds no AI Assist chats.")
        for chat_id, info in chats:
            if info.file_size > _MAX_ENTRY:
                report["invalid"] += 1
                continue
            try:
                record = json.loads(archive.read(info).decode("utf-8"))
                if not isinstance(record, dict) or record.get("id") != chat_id:
                    raise ValueError("the chat's id does not match its name")
            except (ValueError, UnicodeError, zipfile.BadZipFile, OSError, RecursionError):
                report["invalid"] += 1
                continue
            try:
                store.load(chat_id)
                report["kept"] += 1
                continue
            except FileNotFoundError:
                damaged = False
            except ChatStoreError:
                damaged = True
            try:
                if damaged:
                    # Validate the backup copy before moving the damaged file aside.
                    store.validate(record, expected_id=chat_id)
                    store.set_aside_damaged(chat_id)
                store.import_record(record)
                report["replaced_damaged" if damaged else "added"] += 1
            except ChatStoreError:
                report["invalid"] += 1
        info = entries.get(MEMORY_NAME)
        if info is not None:
            report["memory"] = _restore_memory(archive, info, memory)
    store.errors.clear()
    return report


def _restore_memory(archive, info, memory):
    if memory is None:
        return "unavailable"
    if memory.notes.strip():
        return "kept yours"
    if info.file_size > _MAX_MEMORY:
        return "not in backup"
    try:
        data = json.loads(archive.read(info).decode("utf-8"))
        memory.save(data["notes"], data["enabled"])
    except (ValueError, KeyError, TypeError, UnicodeError, OSError):
        return "not in backup"
    return "restored"


def describe(report):
    """One readable sentence for a restore report."""
    parts = [f"{report['added']} chat{'s' if report['added'] != 1 else ''} added"]
    if report["replaced_damaged"]:
        parts.append(f"{report['replaced_damaged']} damaged chat{'s' if report['replaced_damaged'] != 1 else ''} replaced "
                     "(the damaged files are in the damaged folder)")
    if report["kept"]:
        parts.append(f"{report['kept']} you still have left unchanged")
    if report["invalid"]:
        parts.append(f"{report['invalid']} unreadable in the backup and skipped")
    memory = {"restored": "shared notes restored", "kept yours": "your shared notes kept"}.get(report["memory"])
    if memory:
        parts.append(memory)
    return "Restore finished: " + ", ".join(parts) + "."
