"""Bounded chat attachments; no rendering, execution or external resources."""

import base64
import binascii
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import uuid

from coding_agent import FILE_LIMIT_CHOICES
import developer_tools


def file_limit(directory):
    try:
        value = json.loads((Path(directory) / "agent.settings").read_text(encoding="utf-8")).get("file_limit")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if type(value) is int and value in FILE_LIMIT_CHOICES else FILE_LIMIT_CHOICES[0]


def validate_name(name):
    if (not isinstance(name, str) or not name or len(name) > 200 or name in {".", ".."}
            or any(c in name for c in '/\\:<>"|?*') or re.search(r"[\x00-\x1f\x7f]", name)
            or name.endswith((".", " ")) or PureWindowsPath(name).is_reserved()):
        raise ValueError("Use a plain attachment filename without paths or reserved characters.")
    developer_tools._check_components((name,))


def decode(name, encoded, limit):
    validate_name(name)
    if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError(f"The attachment exceeds the {limit:,}-byte agent file limit.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Attachment data must be valid base64.") from error
    if not data or len(data) > limit:
        raise ValueError(f"Attach a nonempty file up to the {limit:,}-byte agent file limit.")
    suffix = Path(name).suffix.lower()
    result = {"id": str(uuid.uuid4()), "name": name, "size": len(data), "kind": "text"}
    signatures = {".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                  ".jpg": data.startswith(b"\xff\xd8\xff"), ".jpeg": data.startswith(b"\xff\xd8\xff"),
                  ".gif": data.startswith((b"GIF87a", b"GIF89a")),
                  ".webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
    if suffix in signatures:
        if not signatures[suffix]:
            raise ValueError("The image content does not match its filename format.")
        result.update(kind="image", data_base64=base64.b64encode(data).decode("ascii"))
    elif suffix == ".pdf":
        from pdf_text import extract_pdf
        result["text"] = developer_tools._decode_text(extract_pdf(data, limit).encode("utf-8"))
    elif suffix in developer_tools._BINARY_SUFFIXES:
        raise ValueError("This binary attachment format is not supported.")
    else:
        result["text"] = developer_tools._decode_text(data)
    return result, data


def validate(items):
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("A message can have at most 20 attachments.")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid attachment metadata.")
        identity = item.get("id")
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
            raise ValueError("Attachment IDs must be canonical UUIDs.")
        validate_name(item.get("name"))
        if type(item.get("size")) is not int or not 0 < item["size"] <= 4000000:
            raise ValueError("Invalid attachment size.")
        if item.get("kind") == "text":
            if not isinstance(item.get("text"), str):
                raise ValueError("Text attachments require text content.")
        elif item.get("kind") == "image":
            if not isinstance(item.get("data_base64"), str):
                raise ValueError("Image attachments require base64 content.")
        else:
            raise ValueError("Unknown attachment kind.")


def folder(directory, chat_id):
    root = Path(directory).resolve()
    current = root
    for component in (chat_id, "attachments"):
        current = current / component
        if (current.is_symlink() or getattr(current, "is_junction", lambda: False)()
                or not current.resolve().is_relative_to(root)):
            raise ValueError("Attachments must stay inside the chat folder; links are not allowed.")
    return current


def write(directory, chat_id, item, data):
    target_folder = folder(directory, chat_id)
    target_folder.mkdir(parents=True, exist_ok=True)
    target = target_folder / item["id"]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target_folder, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Recheck parents after creating directories, before committing the file.
        folder(directory, chat_id)
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
