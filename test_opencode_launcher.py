"""Opening OpenCode: found where it is installed, locked to local models, started safely in its own window."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import opencode_launcher as oc
import test_webui_app


class LauncherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_finds_opencode_on_path_or_in_the_installers_folder(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.assertIsNone(oc.find_opencode({"PATH": str(bin_dir)}, home=self.root))
        installed = self.root / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
        installed.parent.mkdir(parents=True)
        installed.write_text("#!/bin/sh\n")
        installed.chmod(0o755)
        self.assertEqual(oc.find_opencode({"PATH": str(bin_dir)}, home=self.root), str(installed))

    def test_settings_allow_only_local_ollama_and_ask_before_acting(self):
        config = oc.locked_config("http://127.0.0.1:11434", ["qwen3:32b", "gpt-oss:120b-cloud", "gemma3:4b", ""], "qwen3:32b")
        self.assertEqual(config["enabled_providers"], ["ollama"])
        self.assertEqual(list(config["provider"]), ["ollama"])
        self.assertEqual(config["provider"]["ollama"]["options"]["baseURL"], "http://127.0.0.1:11434/v1")
        self.assertEqual(sorted(config["provider"]["ollama"]["models"]), ["gemma3:4b", "qwen3:32b"])  # no cloud model
        self.assertEqual(config["model"], "ollama/qwen3:32b")
        self.assertEqual(config["permission"], {"edit": "ask", "bash": "ask", "webfetch": "deny"})
        self.assertIs(config["autoupdate"], False)
        self.assertEqual(config["share"], "disabled")
        self.assertEqual(oc.ollama_v1("http://127.0.0.1:11434/v1/"), "http://127.0.0.1:11434/v1")
        self.assertEqual(oc.ollama_v1("http://localhost:11434"), "http://127.0.0.1:11434/v1")
        self.assertEqual(oc.ollama_v1("http://localhost"), "http://127.0.0.1/v1")
        self.assertNotIn("model", oc.locked_config(None, []))
        self.assertEqual(oc.locked_config(None, ["b:1", "a:1"], "missing")["model"], "ollama/a:1")

    def test_windows_opens_a_new_console_with_the_settings_in_its_environment(self):
        command, options = oc.launch_command(r"C:\\oc\\opencode.exe", self.root, self.root / "opencode.json", self.root, platform="win32")
        self.assertEqual(command, [r"C:\\oc\\opencode.exe"])
        self.assertEqual(options["cwd"], str(self.root))
        self.assertEqual(options["env"]["OPENCODE_CONFIG"], str(self.root / "opencode.json"))
        for key, value in oc.QUIET_ENVIRONMENT.items():
            self.assertEqual(options["env"][key], value)

    def test_macos_and_linux_open_a_terminal_and_no_terminal_is_explained(self):
        command, _ = oc.launch_command("/usr/local/bin/opencode", self.root, self.root / "c.json", self.root, platform="darwin")
        self.assertEqual(command[:3], ["open", "-a", "Terminal"])
        self.assertTrue(command[3].endswith(".command"))
        command, _ = oc.launch_command("/usr/bin/opencode", self.root, self.root / "c.json", self.root, platform="linux",
                                       which=lambda name: name == "gnome-terminal")
        self.assertEqual(command[:2], ["gnome-terminal", "--"])
        with self.assertRaisesRegex(oc.OpenCodeError, "No terminal"):
            oc.launch_command("/usr/bin/opencode", self.root, self.root / "c.json", self.root, platform="linux", which=lambda name: None)

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "The start script is for macOS and Linux")
    def test_start_script_quotes_awkward_folder_names_and_passes_the_settings(self):
        project = self.root / "my project $(touch hacked) 'quoted' & more"
        project.mkdir()
        fake = self.root / "fake opencode"
        fake.write_text('#!/bin/sh\npwd\nenv | grep "^OPENCODE_" | sort\n')
        fake.chmod(0o755)
        command, _ = oc.launch_command(str(fake), project, self.root / "c 'x'.json", self.root, platform="linux",
                                       which=lambda name: name == "xterm")
        output = subprocess.run(["sh", command[-1]], capture_output=True, text=True, timeout=10, cwd=self.root).stdout
        self.assertEqual(output.splitlines()[0], str(project))
        self.assertIn(f"OPENCODE_CONFIG={self.root / chr(99)} 'x'.json", output)
        self.assertIn("OPENCODE_DISABLE_AUTOUPDATE=1", output)
        self.assertFalse((self.root / "hacked").exists())
        self.assertFalse((project / "hacked").exists())

    def test_open_project_writes_private_settings_and_starts_one_process(self):
        project = self.root / "site"
        project.mkdir()
        with self.assertRaisesRegex(oc.OpenCodeError, "not installed"):
            with patch("opencode_launcher.find_opencode", return_value=None):
                oc.open_project(project, self.root, "http://127.0.0.1:11434", [])
        with patch("opencode_launcher.subprocess.Popen") as popen, patch("opencode_launcher.sys.platform", "win32"):
            oc.open_project(project, self.root, "http://127.0.0.1:11434", ["qwen3:32b"], program="opencode")
        self.assertEqual(popen.call_args.args[0], ["opencode"])
        saved = json.loads((self.root / "opencode" / "opencode.json").read_text())
        self.assertEqual(saved["model"], "ollama/qwen3:32b")
        with self.assertRaisesRegex(oc.OpenCodeError, "project folder"):
            oc.open_project(self.root / "missing", self.root, None, [], program="opencode")


class OpenCodeApiTests(unittest.TestCase):
    def setUp(self):
        test_webui_app.ApiTests.setUp(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.project = Path(folder.name)

    def test_state_and_opening_a_folder(self):
        self.api._find_opencode = lambda: None
        self.assertEqual(self.api.opencode_state(), {"ok": True, "installed": False})
        self.assertIn("opencode.ai", self.api.open_opencode(str(self.project))["error"])
        self.api._find_opencode = lambda: "/usr/bin/opencode"
        self.assertTrue(self.api.opencode_state()["installed"])
        self.assertFalse(self.api.open_opencode("")["ok"])
        with patch("opencode_launcher.open_project") as launch:
            result = self.api.open_opencode(str(self.project))
        self.assertTrue(result["ok"])
        args, kwargs = launch.call_args
        self.assertEqual(args[3], ["fake:7b"])  # this computer's Ollama models
        self.assertEqual(kwargs["program"], "/usr/bin/opencode")


if __name__ == "__main__":
    unittest.main()
