"""The Blender add-on and AI Assist's connector, end to end, with a stand-in for Blender's bpy."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app_core import paths
import blender_bridge
import coding_agent

ADDON_PATH = Path(__file__).resolve().parent / "blender_addon" / "ai_assist_bridge.py"


def load_addon():
    spec = importlib.util.spec_from_file_location("ai_assist_bridge_under_test", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AddonTests(unittest.TestCase):
    def test_the_addon_finds_the_same_data_folder_as_the_app(self):
        addon = load_addon()
        for environ in ({"AI_ASSIST_DATA_DIR": "/tmp/custom"}, {"LOCALAPPDATA": "C:/Users/x/AppData/Local"}, {}):
            with self.subTest(environ=environ):
                self.assertEqual(addon.data_directory(environ), paths.data_directory(environ))

    def test_code_runs_as_one_undo_step_and_reports_output_and_errors(self):
        addon = load_addon()
        steps = []
        addon.bpy = SimpleNamespace(ops=SimpleNamespace(ed=SimpleNamespace(undo_push=lambda message: steps.append(message))))
        reply = addon.run_code("print('cubes:', 2 + 1)")
        self.assertEqual(reply, {"ok": True, "output": "cubes: 3\n", "error": None})
        self.assertEqual(steps, ["Before AI Assist", "AI Assist"])
        failed = addon.run_code("print('started')\nraise ValueError('no such object')")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["output"], "started\n")
        self.assertIn("ValueError: no such object", failed["error"])
        for code in ("", "   ", None, "x" * (addon.MAX_CODE_CHARS + 1)):
            self.assertFalse(addon.run_code(code)["ok"])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name).resolve()
        environment = patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": str(self.directory)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addon = load_addon()
        self.requests = []

        def handle(request):
            self.requests.append(request)
            if request["command"] == "ping":
                return {"ok": True, "blender": "4.2.3", "file": None}
            if request["command"] == "scene":
                return {"ok": True, "scene": {"scene": "Scene", "objects": [{"name": "Cube", "type": "MESH"}]}}
            return {"ok": True, "output": "ran\n", "error": None}

        self.addon.handle_request = handle
        # Blender's timer, played by a thread: requests are handled only here.
        self.stopped = threading.Event()

        def timer():
            while not self.stopped.wait(0.01):
                self.addon.pump()

        self.addon.start()
        self.addCleanup(self.addon.stop)
        self.pump = threading.Thread(target=timer, daemon=True)
        self.pump.start()
        self.addCleanup(self.pump.join, 2)
        self.addCleanup(self.stopped.set)

    def test_connect_scene_and_run_through_the_private_connection_file(self):
        info = json.loads((self.directory / "blender-bridge.json").read_text(encoding="utf-8"))
        self.assertEqual(info["port"], self.addon._state["port"])
        if os.name != "nt":
            mode = stat.S_IMODE((self.directory / "blender-bridge.json").stat().st_mode)
            self.assertEqual(mode, 0o600)
        self.assertEqual(blender_bridge.connected(), "4.2.3")
        self.assertEqual(blender_bridge.scene()["objects"], [{"name": "Cube", "type": "MESH"}])
        self.assertEqual(blender_bridge.run("print('ran')"), {"ok": True, "output": "ran\n", "error": None})
        self.assertEqual([request["command"] for request in self.requests], ["ping", "scene", "run"])
        self.assertEqual(self.requests[-1]["code"], "print('ran')")

    def test_a_request_without_the_secret_is_refused_before_blender_sees_it(self):
        with socket.create_connection(("127.0.0.1", self.addon._state["port"]), timeout=5) as connection:
            connection.sendall(json.dumps({"command": "run", "code": "print(1)", "token": "guess"}).encode() + b"\n")
            reply = json.loads(connection.makefile("rb").readline())
        self.assertEqual(reply, {"ok": False, "error": "Not authorized."})
        self.assertEqual(self.requests, [])

    def test_the_addon_listens_only_on_this_computer(self):
        self.assertEqual(self.addon._state["server"].server_address[0], "127.0.0.1")

    def test_disconnect_removes_the_connection_file(self):
        self.addon.stop()
        self.assertFalse((self.directory / "blender-bridge.json").exists())
        self.assertIsNone(blender_bridge.connected())
        with self.assertRaisesRegex(blender_bridge.BlenderNotConnected, "click Connect"):
            blender_bridge.scene()


class ConnectionFileTests(unittest.TestCase):
    def test_unusable_connection_files_mean_not_connected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "blender-bridge.json"
            for content in ("not json", "[]", '{"port": 0, "token": "x' + "y" * 30 + '"}',
                            '{"port": 5000, "token": "short"}', '{"port": "5000", "token": "' + "t" * 30 + '"}'):
                with self.subTest(content=content):
                    target.write_text(content, encoding="utf-8")
                    self.assertIsNone(blender_bridge._info(folder))
            self.assertIsNone(blender_bridge.connected(folder))
            target.write_text(json.dumps({"port": 5000, "token": "t" * 30}), encoding="utf-8")
            self.assertEqual(blender_bridge._info(folder), {"port": 5000, "token": "t" * 30})


class DeveloperBlenderTests(unittest.TestCase):
    def test_tools_and_instructions_appear_only_while_blender_is_connected(self):
        names = lambda tools: [tool["function"]["name"] for tool in tools]
        self.assertNotIn("blender_run", names(coding_agent._tools(False, None)))
        self.assertEqual(names(coding_agent._tools(False, "4.2.3"))[-2:], ["blender_scene", "blender_run"])
        self.assertEqual(names(coding_agent._tools(True, "4.2.3"))[-1], "blender_scene")
        self.assertNotIn("blender_run", names(coding_agent._tools(True, "4.2.3")))
        with patch("coding_agent.projects.find_blender", return_value=None):
            self.assertIn("Blender 4.2.3 is open and connected", coding_agent._system_prompt("4.2.3"))
            self.assertNotIn("connected", coding_agent._system_prompt(None))

    def test_blender_code_runs_only_after_approval(self):
        reviewed = []
        with patch("blender_bridge.run", return_value={"ok": True, "output": "3 cubes\n", "error": None}) as run:
            with self.assertRaisesRegex(ValueError, "declined"):
                coding_agent._execute_tool(".", "blender_run", {"code": "print(3)"},
                                           lambda action: reviewed.append(action) or False, None, None)
            run.assert_not_called()
            result = coding_agent._execute_tool(".", "blender_run", {"code": "print(3)", "description": "Count cubes"},
                                                lambda action: reviewed.append(action) or True, None, None)
        run.assert_called_once_with("print(3)")
        self.assertIn("3 cubes", result)
        self.assertEqual(reviewed[-1], {"kind": "blender_run", "code": "print(3)", "description": "Count cubes"})

    def test_reading_the_scene_needs_no_approval_and_errors_reach_the_model(self):
        with patch("blender_bridge.scene", return_value={"objects": []}):
            self.assertEqual(json.loads(coding_agent._execute_tool(".", "blender_scene", {}, None, None, None)), {"objects": []})
        with patch("blender_bridge.scene", side_effect=blender_bridge.BlenderNotConnected("Blender is not connected.")):
            with self.assertRaisesRegex(ValueError, "not connected"):
                coding_agent._execute_tool(".", "blender_scene", {}, None, None, None)
        with patch("blender_bridge.run", return_value={"ok": False, "output": "", "error": "NameError: cube"}):
            self.assertIn("NameError: cube", coding_agent._execute_tool(".", "blender_run", {"code": "cube"},
                                                                       lambda action: True, None, None))
        with self.assertRaisesRegex(ValueError, "characters"):
            coding_agent._execute_tool(".", "blender_run", {"code": " "}, lambda action: True, None, None)


if __name__ == "__main__":
    unittest.main()
