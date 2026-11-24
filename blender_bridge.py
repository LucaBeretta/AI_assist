"""Talk to an open Blender through the AI Assist Bridge add-on (blender_addon/ai_assist_bridge.py).

The add-on, once someone clicks Connect in Blender, listens on 127.0.0.1 and writes
its port and a secret to blender-bridge.json in AI Assist's private data folder.
Nothing here starts Blender or listens: it only connects to this computer.
"""

import json
from pathlib import Path
import socket

from app_core.paths import data_directory

INFO_NAME = "blender-bridge.json"
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_CODE_CHARS = 100_000


class BlenderNotConnected(RuntimeError):
    """No Blender is connected, or it stopped answering."""


def _info(directory=None):
    path = Path(directory or data_directory()) / INFO_NAME
    try:
        if path.is_symlink():
            return None
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port, token = (info.get("port"), info.get("token")) if isinstance(info, dict) else (None, None)
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or len(token) < 20:
        return None
    return {"port": port, "token": token}


def request(command, timeout=10, directory=None, **fields):
    """Send one command to the connected Blender and return its reply (a dict with ``ok``)."""
    info = _info(directory)
    if info is None:
        raise BlenderNotConnected("Blender is not connected. In Blender, open the AI Assist tab and click Connect.")
    payload = json.dumps({**fields, "command": command, "token": info["token"]}).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=timeout) as connection:
            connection.sendall(payload)
            reader = connection.makefile("rb")
            line = reader.readline(MAX_REPLY_BYTES + 1)
    except OSError as error:
        raise BlenderNotConnected("Blender did not answer. Check that it is still open and connected.") from error
    if len(line) > MAX_REPLY_BYTES:
        raise RuntimeError("Blender's answer was too large.")
    try:
        reply = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise BlenderNotConnected("Blender sent an unreadable answer.") from error
    if not isinstance(reply, dict):
        raise BlenderNotConnected("Blender sent an unreadable answer.")
    if reply.get("error") == "Not authorized.":
        raise BlenderNotConnected("Blender refused the connection. Click Disconnect and Connect again in Blender.")
    return reply


def connected(directory=None):
    """Blender's version when a connected Blender answers within a moment, else None."""
    try:
        reply = request("ping", timeout=0.5, directory=directory)
    except (BlenderNotConnected, RuntimeError):
        return None
    return str(reply.get("blender") or "Blender") if reply.get("ok") else None


def scene(directory=None):
    reply = request("scene", timeout=15, directory=directory)
    if not reply.get("ok") or not isinstance(reply.get("scene"), dict):
        raise RuntimeError(str(reply.get("error") or "Blender could not describe the scene."))
    return reply["scene"]


def run(code, timeout=95, directory=None):
    """Run approved Python in Blender; returns {"ok", "output", "error"}."""
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE_CHARS:
        raise ValueError(f"Blender code must be between 1 and {MAX_CODE_CHARS:,} characters.")
    reply = request("run", timeout=timeout, directory=directory, code=code)
    return {"ok": reply.get("ok") is True, "output": str(reply.get("output") or ""),
            "error": reply.get("error") if reply.get("ok") is not True else None}
