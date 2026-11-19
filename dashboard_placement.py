"""Persist the dashboard's last *visible* window and display placement.

No network access and no extra Python packages. Screen detection uses native
APIs where available; Tk's virtual root is a conservative fallback. Placement
can only be restored to a currently detected screen, otherwise it is centered
on the primary display. Fullscreen is a temporary presentation mode, not a
persisted setting.
"""

import ctypes
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile


MAX_SETTINGS_BYTES = 4096
WINDOW_MIN_WIDTH = 480
WINDOW_MIN_HEIGHT = 320


def settings_path():
    """A per-user path independent of the project's checkout."""
    if os.environ.get("AI_ASSIST_DATA_DIR"):
        base = Path(os.environ["AI_ASSIST_DATA_DIR"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AI Assist"
    else:
        base = Path.home() / ".aiassist"
    return base / "dashboard-placement.json"


def _screen(id_, x, y, width, height, primary=False):
    if (not isinstance(id_, str) or not id_ or
            not all(type(v) is int for v in (x, y, width, height)) or
            width < 100 or height < 100):
        return None
    return {"id": id_[:100], "x": x, "y": y,
            "width": width, "height": height, "primary": bool(primary)}


def _mac_screens():
    """Quartz display bounds in desktop coordinates, including negative origins."""
    from ctypes.util import find_library
    library = find_library("CoreGraphics")
    if not library:
        return []
    cg = ctypes.CDLL(library)

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    cg.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32)]
    cg.CGGetActiveDisplayList.restype = ctypes.c_int32
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayBounds.restype = CGRect
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    count = ctypes.c_uint32()
    displays = (ctypes.c_uint32 * 24)()
    if cg.CGGetActiveDisplayList(24, displays, ctypes.byref(count)) != 0:
        return []
    main = cg.CGMainDisplayID()
    result = []
    for display_id in displays[:count.value]:
        bounds = cg.CGDisplayBounds(display_id)
        info = _screen(f"mac-{display_id}", round(bounds.origin.x),
                       round(bounds.origin.y), round(bounds.size.width),
                       round(bounds.size.height), display_id == main)
        if info:
            result.append(info)
    return result


def _windows_screens():
    """EnumDisplayMonitors gives rectangles across the whole Windows desktop."""
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    result = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_long)

    def visit(_monitor, _hdc, bounds, _data):
        rect = bounds.contents
        x, y = int(rect.left), int(rect.top)
        info = _screen(f"win-{x}:{y}", x, y,
                       int(rect.right - x), int(rect.bottom - y),
                       x <= 0 < rect.right and y <= 0 < rect.bottom)
        if info:
            result.append(info)
        return 1

    callback = callback_type(visit)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    return result


def _x11_screens():
    try:
        process = subprocess.run(["xrandr", "--listmonitors"], text=True,
                                 capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    monitors = []
    pattern = re.compile(r"^\s*\d+:\s+(\S+)\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)\s+(\S+)")
    for line in process.stdout.splitlines()[1:]:
        match = pattern.match(line)
        if not match:
            continue
        flags, w, h, x, y, name = match.groups()
        entry = _screen("x11-" + name, int(x), int(y), int(w), int(h),
                        "*" in flags)
        if entry:
            monitors.append(entry)
    return monitors


def connected_screens(root):
    """Best-effort physical display list; no display probing is mandatory."""
    try:
        system = platform.system()
        screens = (_mac_screens() if system == "Darwin" else
                   _windows_screens() if system == "Windows" else
                   _x11_screens() if system == "Linux" else [])
    except (OSError, ValueError, AttributeError):
        screens = []
    if screens:
        return screens
    # Tk's virtual root can be larger than one display, depending on backend.
    try:
        x, y = root.winfo_vrootx(), root.winfo_vrooty()
        width, height = root.winfo_vrootwidth(), root.winfo_vrootheight()
        entry = _screen("tk-desktop", x, y, width, height, True)
        if entry:
            return [entry]
    except (AttributeError, ValueError):
        pass
    try:
        entry = _screen("tk-primary", 0, 0, int(root.winfo_screenwidth()),
                        int(root.winfo_screenheight()), True)
        return [entry] if entry else []
    except (AttributeError, ValueError):
        return []


def _intersection(a, b):
    left = max(a["x"], b["x"])
    top = max(a["y"], b["y"])
    right = min(a["x"] + a["width"], b["x"] + b["width"])
    bottom = min(a["y"] + a["height"], b["y"] + b["height"])
    return max(0, right - left) * max(0, bottom - top)


def _valid_rect(rect):
    return (isinstance(rect, dict)
            and all(type(rect.get(k)) is int for k in ("x", "y", "width", "height"))
            and -100000 <= rect["x"] <= 100000 and -100000 <= rect["y"] <= 100000
            and 100 <= rect["width"] <= 16000
            and 100 <= rect["height"] <= 16000)


def _primary(screens):
    return next((screen for screen in screens if screen.get("primary")), screens[0])


def select_saved_screen(saved, screens):
    """Use same monitor ID, or match stable geometry; never invent one."""
    if not isinstance(saved, dict) or not screens:
        return None
    if not _valid_rect(saved):
        return None
    old_id = saved.get("id")
    if isinstance(old_id, str):
        found = next((screen for screen in screens if screen["id"] == old_id), None)
        if found:
            return found
    # Monitor identifiers sometimes change after reconnecting.
    exact = [s for s in screens if
             all(s[k] == saved.get(k) for k in ("x", "y", "width", "height"))]
    if len(exact) == 1:
        return exact[0]
    # A moved external monitor can be recognized by unique resolution.
    matches = [s for s in screens if
               s["width"] == saved.get("width") and
               s["height"] == saved.get("height") and
               s.get("primary") == saved.get("primary")]
    return matches[0] if len(matches) == 1 else None


def choose_restored_geometry(saved, screens):
    """Return a safe rect on a connected display; fallback to primary center."""
    if not screens:
        return None
    primary = _primary(screens)
    record = saved.get("window") if isinstance(saved, dict) else None
    old_screen = saved.get("screen") if isinstance(saved, dict) else None
    target = select_saved_screen(old_screen, screens)
    width = record["width"] if _valid_rect(record) else 800
    height = record["height"] if _valid_rect(record) else 480
    matched = target is not None
    if target is None:
        target = primary
    width = min(width, max(1, target["width"] - 24))
    height = min(height, max(1, target["height"] - 54))
    # Restore relative position only when the matching monitor is connected
    # and the old screen rectangle passes strict type validation.
    if matched and _valid_rect(record) and _valid_rect(old_screen):
        rel_x = record["x"] - old_screen["x"]
        rel_y = record["y"] - old_screen["y"]
        x = max(target["x"], min(target["x"] + target["width"] - width,
                                 target["x"] + rel_x))
        y = max(target["y"], min(target["y"] + target["height"] - height,
                                 target["y"] + rel_y))
        return {"x": x, "y": y, "width": width, "height": height}
    return {"x": target["x"] + (target["width"] - width) // 2,
            "y": target["y"] + (target["height"] - height) // 2,
            "width": width, "height": height}


def geometry_string(rect):
    # Standard Tk window-manager syntax; negative offsets may be platform-
    # relative, so test secondary monitors to the left of the main display.
    return f'{rect["width"]}x{rect["height"]}{rect["x"]:+d}{rect["y"]:+d}'


class DashboardPlacement:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else settings_path()

    def load(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_SETTINGS_BYTES + 1)
            if len(raw) > MAX_SETTINGS_BYTES:
                return None
            data = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(data, dict) or data.get("version") != 1:
            return None
        return data if _valid_rect(data.get("window")) else None

    def restore(self, root):
        return choose_restored_geometry(self.load(), connected_screens(root))

    def save(self, window, root):
        """Save only a normal, visible window substantially on a live screen."""
        try:
            if window.state() != "normal":
                return False
            rect = {"x": int(window.winfo_x()), "y": int(window.winfo_y()),
                    "width": int(window.winfo_width()), "height": int(window.winfo_height())}
            if not _valid_rect(rect):
                return False
            screens = connected_screens(root)
            if not screens:
                return False
            monitor = max(screens, key=lambda s: _intersection(s, rect))
            if _intersection(monitor, rect) < rect["width"] * rect["height"] * .25:
                return False
            data = {"version": 1, "window": rect, "screen": monitor}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                self.path.parent.chmod(0o700)
            fd, temp_name = tempfile.mkstemp(prefix=".dashboard-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, separators=(",", ":"))
                if os.name != "nt":
                    os.chmod(temp_name, 0o600)
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return True
        except (OSError, ValueError, AttributeError):
            return False
