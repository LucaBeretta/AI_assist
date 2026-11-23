"""Remember local clips without contacting the generation backend for playback."""

import base64
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat
import threading
from uuid import UUID, uuid4

from app_core import photo_video
from webui.local_files import write_bytes, write_utf8


MAX_RECENT = 12
MAX_MANIFEST_BYTES = 128 * 1024
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}
PUBLIC_FIELDS = ("id", "name", "kind", "prompt", "portrait", "created_at")


def video_header_matches(header, size, mime_type):
    if mime_type == "video/mp4":
        return header[4:8] == b"ftyp" and 16 <= int.from_bytes(header[:4], "big") <= size
    if mime_type == "video/webm":
        return header.startswith(b"\x1a\x45\xdf\xa3") and b"\x42\x82\x84webm" in header[:64]
    return False


def _local_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(("//", "\\\\")) or ".." in path.parts:
        raise ValueError("Choose a clip stored on this computer.")
    return path


def _file_info(path):
    """Reject linked and special files before opening anything from the history."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(path, "is_junction", lambda: False)()
            or path.resolve(strict=True) != path):
        raise ValueError("Choose a regular clip file on this computer.")
    return info


class RecentClips:
    def __init__(self, directory):
        directory = Path(directory).absolute()
        self._manifest = directory / "recent-clips.json"
        self._generated = directory / "generated-clips"
        self._lock = threading.Lock()

    def _read(self):
        manifest = self._manifest
        try:
            if not stat.S_ISREG(manifest.lstat().st_mode):
                raise ValueError("The recent clips list must be a regular file.")
            with manifest.open("rb") as stream:
                payload = stream.read(MAX_MANIFEST_BYTES + 1)
        except FileNotFoundError:
            return []
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError("The recent clips list is too large.")
        records = json.loads(payload)
        fields = ("id", "name", "kind", "prompt", "created_at", "path")
        if (not isinstance(records, list) or len(records) > MAX_RECENT
                or any(not isinstance(item, dict) or item.get("kind") not in ("quick", "wan", "camera")
                       or type(item.get("portrait")) is not bool
                       or any(not isinstance(item.get(key), str) for key in fields)
                       for item in records)):
            raise ValueError("Could not read the recent clips list.")
        return records

    def _remember(self, records, path, identity, *, name, kind, prompt="", portrait=False):
        item = {"id": identity, "name": name, "kind": kind, "prompt": prompt, "portrait": portrait,
                "created_at": datetime.now(UTC).isoformat(), "path": str(path)}
        rest = [previous for previous in records if previous["id"] != identity]
        payload = json.dumps([item, *rest[:MAX_RECENT - 1]], ensure_ascii=False, indent=2)
        if len(payload.encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise ValueError("The recent clips list is too large.")
        write_utf8(self._manifest, payload)
        for dropped in rest[MAX_RECENT - 1:]:
            self._delete_copy(dropped)

    def _delete_copy(self, record):
        """Delete this list's own copy of an AI video when it drops off; saved files stay."""
        if record["kind"] != "wan":
            return
        try:
            copy = self._generated.resolve(strict=True) / f"{UUID(record['id'])}.mp4"
            if record["path"] == str(copy) and stat.S_ISREG(copy.lstat().st_mode):
                copy.unlink()
        except (OSError, ValueError):
            pass  # ComfyUI keeps the original; a copy left behind only uses disk space.

    def remember_recording(self, saved_path, *, kind):
        path = _local_path(saved_path)
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Choose a regular clip file on this computer.")
        path = _local_path(path.resolve(strict=True))
        _file_info(path)
        with self._lock:
            records = self._read()
            normalized_path = os.path.normcase(str(path))
            previous = next((item for item in records
                             if os.path.normcase(item["path"]) == normalized_path), None)
            identity = previous["id"] if previous is not None else str(uuid4())
            self._remember(records, path, identity, name=path.name, kind=kind)

    def remember_wan(self, job, result, prompt, portrait):
        identity = str(UUID(job))
        with self._lock:
            records = self._read()
            if any(item["id"] == identity for item in records):
                return
            if self._generated.is_symlink() or getattr(self._generated, "is_junction", lambda: False)():
                raise ValueError("The generated clips folder must not be a link.")
            self._generated.mkdir(mode=0o700, exist_ok=True)
            path = _local_path(self._generated.resolve(strict=True) / f"{identity}.mp4")
            data = base64.b64decode(result["video"], validate=True)
            if len(data) > photo_video.MAX_VIDEO_BYTES or not video_header_matches(data, len(data), "video/mp4"):
                raise ValueError("Could not save this generated clip.")
            write_bytes(path, data)
            try:
                self._remember(records, path, identity, name=result["name"], kind="wan", prompt=prompt,
                               portrait=portrait)
            except Exception:
                path.unlink(missing_ok=True)
                raise

    def list(self):
        clips = []
        with self._lock:
            records = self._read()
        for record in records:
            try:
                path = _local_path(record["path"])
                info = _file_info(path)
                available = path.suffix.lower() in VIDEO_TYPES and 0 < info.st_size <= photo_video.MAX_VIDEO_BYTES
            except (OSError, ValueError):
                available = False
            public = {key: record[key] for key in PUBLIC_FIELDS}
            clips.append({**public, "available": available})
        return clips

    def _find(self, identity):
        with self._lock:
            record = next((item for item in self._read() if item["id"] == identity), None)
        if record is None:
            raise ValueError("Choose a clip from Recent clips.")
        path = _local_path(record["path"])
        if path.suffix.lower() not in VIDEO_TYPES:
            raise ValueError("This clip must be an MP4 or WebM.")
        try:
            info = _file_info(path)
        except FileNotFoundError:
            raise ValueError("The clip is no longer at its saved location.") from None
        if not 0 < info.st_size <= photo_video.MAX_VIDEO_BYTES:
            raise ValueError("The clip is empty or too large to preview.")
        return path, VIDEO_TYPES[path.suffix.lower()]

    def _read_clip(self, identity, *, preview):
        path, mime_type = self._find(identity)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        try:
            with os.fdopen(os.open(path, flags), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= photo_video.MAX_VIDEO_BYTES:
                    raise ValueError("Choose a regular clip file under 24 MiB.")
                data = stream.read(photo_video.MAX_VIDEO_BYTES + 1 if preview else 64)
        except FileNotFoundError:
            raise ValueError("The clip is no longer at its saved location.") from None
        if (preview and len(data) > photo_video.MAX_VIDEO_BYTES
                or not video_header_matches(data, len(data) if preview else info.st_size, mime_type)):
            raise ValueError("Could not read the saved video.")
        return path, mime_type, data

    def load(self, identity):
        _, mime_type, data = self._read_clip(identity, preview=True)
        return {"video": base64.b64encode(data).decode("ascii"), "mime_type": mime_type}

    def open(self, identity, open_file):
        path, _, _ = self._read_clip(identity, preview=False)
        open_file(path)
