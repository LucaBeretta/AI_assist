"""Check the AI Assist Bridge add-on inside a real Blender, driven through AI Assist's connector.

From the AI Assist folder:

    blender --background --factory-startup --python tools/blender_bridge_smoke.py

It registers the add-on, connects, reads the default scene, adds a monkey,
runs code that fails, disconnects and unregisters. Data goes to a temporary
folder, and nothing is saved. The last line starts with RESULT.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import traceback

import bpy

REPO = os.environ.get("AI_ASSIST_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["AI_ASSIST_DATA_DIR"] = tempfile.mkdtemp(prefix="ai-assist-blender-")

spec = importlib.util.spec_from_file_location("ai_assist_bridge", os.path.join(REPO, "blender_addon", "ai_assist_bridge.py"))
addon = importlib.util.module_from_spec(spec)
sys.modules["ai_assist_bridge"] = addon
spec.loader.exec_module(addon)
addon.register()
bpy.ops.ai_assist.connect()
print("connected on port", addon._state["port"])

import blender_bridge  # noqa: E402  (AI Assist's side, as the app would use it)

results = {}


def client():
    try:
        results["ping"] = blender_bridge.connected()
        results["scene_before"] = blender_bridge.scene()
        results["run"] = blender_bridge.run(
            "bpy.ops.mesh.primitive_monkey_add(location=(2, 0, 1))\n"
            "bpy.context.active_object.name = 'AI Monkey'\n"
            "print('objects:', len(bpy.context.scene.objects))")
        results["error_run"] = blender_bridge.run("bpy.data.objects['Missing'].location.x = 1")
        results["scene_after"] = blender_bridge.scene()
    except Exception:
        results["exception"] = traceback.format_exc()


thread = threading.Thread(target=client)
thread.start()
# Background Blender has no event loop for timers, so play the timer here on the main thread.
while thread.is_alive():
    addon.pump()
    thread.join(0.02)

bpy.ops.ai_assist.disconnect()
results["file_removed"] = not os.path.exists(os.path.join(os.environ["AI_ASSIST_DATA_DIR"], "blender-bridge.json"))
addon.unregister()
summary = {
    "ping": results.get("ping"),
    "before": [(o["name"], o["type"], o.get("selected")) for o in results.get("scene_before", {}).get("objects", [])],
    "run": results.get("run"),
    "error_run_ok": results.get("error_run", {}).get("ok"),
    "error_run_tail": (results.get("error_run", {}).get("error") or "").strip().splitlines()[-1:],
    "after": [(o["name"], o["type"], o["location"]) for o in results.get("scene_after", {}).get("objects", [])],
    "render": results.get("scene_after", {}).get("render"),
    "file_removed": results.get("file_removed"),
    "exception": results.get("exception"),
}
print("RESULT " + json.dumps(summary))
ok = (summary["ping"] and summary["run"] and summary["run"]["ok"] and summary["error_run_ok"] is False
      and any(name == "AI Monkey" for name, _, _ in summary["after"]) and summary["file_removed"] and not summary["exception"])
print("SMOKE TEST " + ("PASSED" if ok else "FAILED"))
sys.exit(0 if ok else 1)
