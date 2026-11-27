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


def show_for(table):
    """A stand-in for Ollama's /api/show: name -> metadata (missing names fail like an unknown model)."""
    def show(base, name):
        if name not in table:
            raise OSError("model not found")
        return table[name]
    return show


class LauncherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = self.root / "opencode.json"
        oc.write_config(self.root, oc.locked_config(None, ["qwen3:8b"]))

    def test_finds_opencode_on_path_or_where_its_installers_put_it(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        with patch("opencode_launcher._candidates", return_value=[]):
            self.assertIsNone(oc.find_opencode({"PATH": str(bin_dir)}, home=self.root))
        installed = self.root / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
        installed.parent.mkdir(parents=True)
        installed.write_text("#!/bin/sh\n")
        installed.chmod(0o755)
        self.assertEqual(oc.find_opencode({"PATH": str(bin_dir)}, home=self.root), str(installed))
        if os.name != "nt":  # Homebrew and npm places are checked when AI Assist starts without the shell's PATH
            places = [str(path) for path in oc._candidates(self.root, {})]
            self.assertIn("/opt/homebrew/bin/opencode", places)
            self.assertIn(str(self.root / ".bun" / "bin" / "opencode"), places)

    def test_only_local_chat_models_are_offered(self):
        table = {"qwen3:8b": {"capabilities": ["completion", "tools"]},
                 "nomic-embed-text:latest": {"capabilities": ["embedding"]},
                 "my-alias:latest": {"capabilities": ["completion"], "remote_host": "https://ollama.com:443"},
                 "older:7b": {}}
        names = ["qwen3:8b", "nomic-embed-text:latest", "my-alias:latest", "gpt-oss:120b-cloud", "older:7b",
                 "missing:1b", "{file:~/.ssh/id_rsa}", "a b"]
        self.assertEqual(oc.chat_models("http://localhost:11434", names, show=show_for(table)), ["older:7b", "qwen3:8b"])

    def test_settings_allow_only_ai_assists_own_provider_and_ask_before_acting(self):
        config = oc.locked_config("http://127.0.0.1:11434", ["qwen3:32b", "gpt-oss:120b-cloud", "gemma3:4b", "", "{env:HOME}"], "qwen3:32b")
        self.assertEqual(config["enabled_providers"], ["aiassist"])  # OpenCode 2's auto-filled "ollama" stays off
        self.assertEqual(list(config["provider"]), ["aiassist"])
        self.assertEqual(config["provider"]["aiassist"]["options"]["baseURL"], "http://127.0.0.1:11434/v1")
        self.assertEqual(sorted(config["provider"]["aiassist"]["models"]), ["gemma3:4b", "qwen3:32b"])
        self.assertEqual(config["model"], "aiassist/qwen3:32b")
        self.assertEqual(config["permission"], {"edit": "ask", "bash": "ask", "webfetch": "deny", "websearch": "deny"})
        self.assertIs(config["autoupdate"], False)
        self.assertEqual(config["share"], "disabled")
        self.assertEqual(oc.ollama_v1("http://127.0.0.1:11434/v1/"), "http://127.0.0.1:11434/v1")
        self.assertEqual(oc.ollama_v1("http://localhost:11434"), "http://127.0.0.1:11434/v1")
        self.assertEqual(oc.ollama_v1("http://localhost"), "http://127.0.0.1/v1")
        self.assertNotIn("model", oc.locked_config(None, []))
        self.assertEqual(oc.locked_config(None, ["b:1", "a:1"], "missing")["model"], "aiassist/a:1")

    def test_opencode_2_gets_a_private_server_and_opencode_1_starts_plainly(self):
        self.assertEqual(oc.program_arguments("opencode", 2), ["opencode", "--standalone"])
        self.assertEqual(oc.program_arguments("opencode", None), ["opencode", "--standalone"])
        self.assertEqual(oc.program_arguments("opencode", 1), ["opencode"])
        run = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "opencode 2.4.1\n", "")
        self.assertEqual(oc.major_version("opencode", run=run), 2)
        self.assertEqual(oc.major_version("opencode", run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "1.19.6", "")), 1)
        def missing(*args, **kwargs):
            raise OSError("not found")
        self.assertIsNone(oc.major_version("opencode", run=missing))

    def test_windows_opens_a_new_console_with_the_settings_and_its_screen_attached(self):
        command, options = oc.launch_command([r"C:\oc\opencode.cmd", "--standalone"], self.root, self.config, self.root, platform="win32")
        self.assertEqual(command, [r"C:\oc\opencode.cmd", "--standalone"])
        self.assertEqual(options["cwd"], str(self.root))
        for key in ("stdin", "stdout", "stderr"):
            self.assertNotIn(key, options)  # OpenCode draws its screen in that console
        env = options["env"]
        self.assertEqual(env["OPENCODE_CONFIG"], str(self.config))
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"])["enabled_providers"], ["aiassist"])
        self.assertEqual(env["NoDefaultCurrentDirectoryInExePath"], "1")  # cmd.exe skips programs in the project folder
        for key, value in oc.QUIET_ENVIRONMENT.items():
            self.assertEqual(env[key], value)
        self.assertEqual(env["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")

    def test_macos_and_linux_open_a_terminal_and_no_terminal_is_explained(self):
        command, _ = oc.launch_command(["/usr/local/bin/opencode"], self.root, self.config, self.root, platform="darwin")
        self.assertEqual(command[:3], ["open", "-a", "Terminal"])
        self.assertTrue(command[3].endswith(".command"))
        first, _ = oc.launch_command(["/usr/bin/opencode"], self.root, self.config, self.root, platform="linux",
                                     which=lambda name: name == "gnome-terminal")
        second, _ = oc.launch_command(["/usr/bin/opencode"], self.root, self.config, self.root, platform="linux",
                                      which=lambda name: name == "gnome-terminal")
        self.assertEqual(first[:2], ["gnome-terminal", "--"])
        self.assertNotEqual(first[-1], second[-1])  # each click gets its own start script
        with self.assertRaisesRegex(oc.OpenCodeError, "No terminal"):
            oc.launch_command(["/usr/bin/opencode"], self.root, self.config, self.root, platform="linux", which=lambda name: None)

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "The start script is for macOS and Linux")
    def test_start_script_quotes_awkward_folder_names_passes_the_settings_and_removes_itself(self):
        project = self.root / "my project $(touch hacked) 'quoted' & more"
        project.mkdir()
        fake = self.root / "fake opencode"
        fake.write_text('#!/bin/sh\npwd\necho "args: $*"\nenv | grep -E "^(OPENCODE_CONFIG|OPENCODE_DISABLE)" | sort\n')
        fake.chmod(0o755)
        config = self.root / "c 'x'.json"
        oc.write_config(self.root, oc.locked_config(None, []))
        shutil.copy(self.root / "opencode.json", config)
        command, _ = oc.launch_command([str(fake), "--standalone"], project, config, self.root, platform="linux",
                                       which=lambda name: name == "xterm")
        script = Path(command[-1])
        output = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=10, cwd=self.root).stdout
        lines = output.splitlines()
        self.assertEqual(lines[0], str(project))
        self.assertEqual(lines[1], "args: --standalone")
        self.assertIn(f"OPENCODE_CONFIG={config}", output)
        self.assertIn("OPENCODE_DISABLE_PROJECT_CONFIG=1", output)
        self.assertIn('OPENCODE_CONFIG_CONTENT={', output)
        self.assertFalse((self.root / "hacked").exists())
        self.assertFalse((project / "hacked").exists())
        self.assertFalse(script.exists())  # used once

    def test_open_project_writes_private_settings_and_starts_one_process(self):
        project = self.root / "site"
        project.mkdir()
        with self.assertRaisesRegex(oc.OpenCodeError, "not installed"):
            with patch("opencode_launcher.find_opencode", return_value=None):
                oc.open_project(project, self.root, "http://127.0.0.1:11434", [])
        with patch("opencode_launcher.subprocess.Popen") as popen, patch("opencode_launcher.sys.platform", "win32"):
            oc.open_project(project, self.root, "http://127.0.0.1:11434", ["qwen3:32b"], program="opencode", major=2)
        self.assertEqual(popen.call_args.args[0], ["opencode", "--standalone"])
        saved = json.loads((self.root / "opencode" / "opencode.json").read_text())
        self.assertEqual(saved["model"], "aiassist/qwen3:32b")
        with self.assertRaisesRegex(oc.OpenCodeError, "project folder"):
            oc.open_project(self.root / "missing", self.root, None, [], program="opencode", major=2)


class OpenCodeApiTests(unittest.TestCase):
    def setUp(self):
        test_webui_app.ApiTests.setUp(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.project = Path(folder.name)

    def test_state_and_opening_a_folder_with_the_chosen_model(self):
        self.api._find_opencode = lambda: None
        self.assertEqual(self.api.opencode_state(), {"ok": True, "installed": False})
        self.assertIn("opencode.ai", self.api.open_opencode(str(self.project))["error"])
        self.api._find_opencode = lambda: "/usr/bin/opencode"
        self.assertTrue(self.api.opencode_state()["installed"])
        self.assertFalse(self.api.open_opencode("")["ok"])
        self.assertFalse(self.api.open_opencode(str(self.project), 5)["ok"])
        with patch("opencode_launcher.chat_models", return_value=["fake:7b"]) as usable, \
                patch("opencode_launcher.open_project") as launch:
            result = self.api.open_opencode(str(self.project), "fake:7b")
        self.assertTrue(result["ok"])
        self.assertEqual(usable.call_args.args[1], ["fake:7b"])  # this computer's Ollama models, then filtered
        args, kwargs = launch.call_args
        self.assertEqual(args[3], ["fake:7b"])
        self.assertEqual(kwargs["default_model"], "fake:7b")
        self.assertEqual(kwargs["program"], "/usr/bin/opencode")


if __name__ == "__main__":
    unittest.main()
