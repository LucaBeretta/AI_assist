"""AI Assist Bridge: lets AI Assist on this computer read the open scene and run code you approve.

Install in Blender: Edit > Preferences > Add-ons > Install... and choose this file.
Then open the 3D Viewport sidebar (N), tab "AI Assist", and click Connect.

- Off until you click Connect, and off again when you click Disconnect or quit Blender.
- Listens on 127.0.0.1 only, on a random port, and answers only requests that carry
  the secret written for AI Assist in its private data folder (blender-bridge.json).
- AI Assist asks you to approve every piece of code before it is sent here. Each run
  is one undo step: Ctrl+Z in Blender takes it back.
- Blender's data may only change on its main thread, so requests wait in a queue
  that a Blender timer works through.
"""

bl_info = {
    "name": "AI Assist Bridge",
    "author": "AI Assist",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D Viewport > Sidebar > AI Assist",
    "description": "Let AI Assist on this computer read the scene and run code you approve",
    "category": "Interface",
}

import contextlib
import io
import json
import os
from pathlib import Path
import queue
import secrets
import socketserver
import threading
import traceback

try:
    import bpy
except ImportError:  # imported by AI Assist's tests, outside Blender
    bpy = None

INFO_NAME = "blender-bridge.json"
MAX_REQUEST_BYTES = 256 * 1024
MAX_CODE_CHARS = 100_000
MAX_OUTPUT_CHARS = 20_000
MAX_OBJECTS = 200
REQUEST_SECONDS = 90

_jobs = queue.Queue()
_state = {"server": None, "token": None, "port": None}


def data_directory(environ=None):
    """The same folder AI Assist uses (app_core/paths.py): AI_ASSIST_DATA_DIR, else
    %LOCALAPPDATA%\\AI Assist\\chats on Windows, else ~/.aiassist/chats."""
    environ = os.environ if environ is None else environ
    override, local_data = environ.get("AI_ASSIST_DATA_DIR"), environ.get("LOCALAPPDATA")
    if override:
        return Path(override).expanduser()
    if local_data:
        return Path(local_data) / "AI Assist" / "chats"
    return Path.home() / ".aiassist" / "chats"


def _write_info(port, token):
    folder = data_directory()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / INFO_NAME
    temporary = folder / f".{INFO_NAME}.{os.getpid()}.tmp"
    # Readable only by this user: the token lets a program act on the open scene.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "token": token, "pid": os.getpid(),
                   "blender": bpy.app.version_string if bpy else ""}, handle)
    os.replace(temporary, target)


def _remove_info(token):
    target = data_directory() / INFO_NAME
    try:
        if json.loads(target.read_text(encoding="utf-8")).get("token") == token:
            target.unlink()
    except (OSError, ValueError, AttributeError):
        pass


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(REQUEST_SECONDS)
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        reply = self._answer(line)
        self.wfile.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")

    def _answer(self, line):
        if len(line) > MAX_REQUEST_BYTES:
            return {"ok": False, "error": "The request is too large."}
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"ok": False, "error": "The request is not valid JSON."}
        token = _state["token"]
        if (not isinstance(request, dict) or not isinstance(request.get("token"), str) or token is None
                or not secrets.compare_digest(request["token"], token)):
            return {"ok": False, "error": "Not authorized."}
        box, done = {}, threading.Event()
        _jobs.put((request, box, done))
        if not done.wait(REQUEST_SECONDS):
            return {"ok": False, "error": "Blender did not finish in time. It may be busy rendering."}
        return box["reply"]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def pump():
    """Work through queued requests on Blender's main thread; a Blender timer calls this."""
    while True:
        try:
            request, box, done = _jobs.get_nowait()
        except queue.Empty:
            break
        try:
            box["reply"] = handle_request(request)
        except Exception:
            box["reply"] = {"ok": False, "error": traceback.format_exc(limit=5)}
        done.set()
    return 0.1 if _state["server"] is not None else None


def handle_request(request):
    command = request.get("command")
    if command == "ping":
        return {"ok": True, "blender": bpy.app.version_string, "file": bpy.data.filepath or None}
    if command == "scene":
        return {"ok": True, "scene": scene_summary()}
    if command == "run":
        return run_code(request.get("code"))
    return {"ok": False, "error": "Unknown command."}


def _rounded(values):
    return [round(float(value), 4) for value in values]


def scene_summary():
    scene = bpy.context.scene
    layer = bpy.context.view_layer
    objects = []
    for obj in list(scene.objects)[:MAX_OBJECTS]:
        item = {"name": obj.name, "type": obj.type, "location": _rounded(obj.location),
                "rotation_euler": _rounded(obj.rotation_euler), "scale": _rounded(obj.scale),
                "dimensions": _rounded(obj.dimensions), "parent": obj.parent.name if obj.parent else None,
                "materials": [slot.material.name for slot in obj.material_slots if slot.material][:8]}
        try:
            item["hidden"], item["selected"] = obj.hide_get(view_layer=layer), obj.select_get(view_layer=layer)
        except (RuntimeError, TypeError):
            pass
        objects.append(item)
    active = layer.objects.active if layer else None
    render = scene.render
    return {"file": bpy.data.filepath or None, "scene": scene.name, "frame": scene.frame_current,
            "frame_range": [scene.frame_start, scene.frame_end], "fps": render.fps,
            "render": {"engine": render.engine, "resolution": [render.resolution_x, render.resolution_y],
                       "percentage": render.resolution_percentage, "output": render.filepath},
            "unit_system": scene.unit_settings.system, "active_object": active.name if active else None,
            "object_count": len(scene.objects), "objects": objects,
            "collections": [collection.name for collection in bpy.data.collections][:50],
            "materials": [material.name for material in bpy.data.materials][:100]}


def _undo_push(message):
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass  # Some Blender states refuse an undo step; the code still runs.


def run_code(code):
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE_CHARS:
        return {"ok": False, "error": f"Send between 1 and {MAX_CODE_CHARS:,} characters of Python."}
    output = io.StringIO()
    _undo_push("Before AI Assist")
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compile(code, "<AI Assist>", "exec"), {"bpy": bpy, "__name__": "__ai_assist__"})
        error = None
    except Exception:
        error = traceback.format_exc(limit=8)
    _undo_push("AI Assist")
    text = output.getvalue()
    if len(text) > MAX_OUTPUT_CHARS:
        text = "[earlier output omitted]\n" + text[-MAX_OUTPUT_CHARS:]
    return {"ok": error is None, "output": text, "error": error}


def start():
    if _state["server"] is not None:
        return _state["port"]
    token = secrets.token_urlsafe(32)
    server = _Server(("127.0.0.1", 0), _Handler)
    _state.update(server=server, token=token, port=server.server_address[1])
    threading.Thread(target=server.serve_forever, name="ai-assist-bridge", daemon=True).start()
    try:
        _write_info(_state["port"], token)
    except OSError:
        stop()
        raise
    if bpy is not None and not bpy.app.timers.is_registered(pump):
        bpy.app.timers.register(pump, first_interval=0.1, persistent=True)
    return _state["port"]


def stop():
    server, token = _state["server"], _state["token"]
    _state.update(server=None, token=None, port=None)
    if server is not None:
        server.shutdown()
        server.server_close()
    if token is not None:
        _remove_info(token)
    # Anything still waiting is answered, so AI Assist does not wait for nothing.
    while True:
        try:
            _, box, done = _jobs.get_nowait()
        except queue.Empty:
            break
        box["reply"] = {"ok": False, "error": "Blender disconnected from AI Assist."}
        done.set()


if bpy is not None:
    class AIASSIST_OT_connect(bpy.types.Operator):
        bl_idname = "ai_assist.connect"
        bl_label = "Connect"
        bl_description = "Let AI Assist on this computer read this scene and run code you approve there"

        def execute(self, context):
            try:
                start()
            except OSError as error:
                self.report({"ERROR"}, f"Could not connect: {error}")
                return {"CANCELLED"}
            self.report({"INFO"}, "AI Assist can now connect to this Blender.")
            return {"FINISHED"}

    class AIASSIST_OT_disconnect(bpy.types.Operator):
        bl_idname = "ai_assist.disconnect"
        bl_label = "Disconnect"
        bl_description = "Stop answering AI Assist"

        def execute(self, context):
            stop()
            self.report({"INFO"}, "Disconnected from AI Assist.")
            return {"FINISHED"}

    class AIASSIST_PT_panel(bpy.types.Panel):
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "AI Assist"
        bl_label = "AI Assist"

        def draw(self, context):
            layout = self.layout
            if _state["server"] is None:
                layout.label(text="Off. Only this computer can connect.")
                layout.operator("ai_assist.connect", icon="LINKED")
            else:
                layout.label(text="Connected to AI Assist", icon="CHECKMARK")
                layout.label(text="You approve every change there.")
                layout.label(text="Ctrl+Z undoes each change.")
                layout.operator("ai_assist.disconnect", icon="UNLINKED")

    _CLASSES = (AIASSIST_OT_connect, AIASSIST_OT_disconnect, AIASSIST_PT_panel)

    def register():
        for cls in _CLASSES:
            bpy.utils.register_class(cls)

    def unregister():
        stop()
        if bpy.app.timers.is_registered(pump):
            bpy.app.timers.unregister(pump)
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)
