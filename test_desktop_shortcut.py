"""Verify native shortcut content and launcher behavior without touching the desktop."""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import desktop_shortcut as shortcut
import gui_runtime


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="AI Assist's test ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.python = self.root / "system python/python.exe"

    def test_windows_private_and_system_pythonw(self):
        system = self.python.with_name("pythonw.exe")
        system.parent.mkdir()
        system.touch()
        spec = shortcut.windows_spec(self.root, self.python)
        self.assertEqual(spec["TargetPath"], str(system))
        self.assertEqual(spec["WorkingDirectory"], str(self.root))
        self.assertEqual(spec["Arguments"], f'"{self.root / "gui_runtime.py"}" --launch "{self.root / "AIassist.py"}"')
        self.assertEqual(spec["IconLocation"], "")
        icon = self.root / "assets/icon/ai-assist.ico"
        icon.parent.mkdir(parents=True)
        icon.touch()
        private = self.root / "runtime/python/pythonw.exe"
        private.parent.mkdir(parents=True)
        private.touch()
        updated = shortcut.windows_spec(self.root, self.python)
        self.assertEqual(updated["TargetPath"], str(private))
        self.assertEqual(updated["IconLocation"], str(icon))

    def test_plist_and_quoted_script(self):
        import shlex
        info, script = shortcut.mac_bundle(self.root, self.python, True)
        self.assertEqual(plistlib.loads(info)["CFBundleIconFile"], "ai-assist.icns")
        self.assertEqual(plistlib.loads(info)["CFBundleExecutable"], "AI Assist")
        self.assertEqual(shlex.split(script.splitlines()[1]), ["cd", str(self.root), "||", "exit", "1"])
        self.assertEqual(shlex.split(script.splitlines()[2]), ["exec", str(self.python), "gui_runtime.py", "--launch", "AIassist.py"])
        self.assertNotIn("CFBundleIconFile", plistlib.loads(shortcut.mac_bundle(self.root, self.python, False)[0]))

    def test_linux_entry_and_recreation(self):
        (self.home / "Desktop").mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=self.home), patch("sys.platform", "linux"):
            for _ in range(2):
                shortcut.create_shortcut(self.root, self.python)
        entry = self.home / ".local/share/applications/ai-assist.desktop"
        text = entry.read_text(encoding="utf-8")
        self.assertEqual((self.home / "Desktop/ai-assist.desktop").read_bytes(), entry.read_bytes())
        self.assertIn("Terminal=false", text)
        self.assertIn(' --launch "', text)
        self.assertIn("AIassist.py\"", text)
        self.assertNotIn("Icon=", text)
        self.assertIn("%%", shortcut.desktop_entry(Path("100% app"), self.python, None))

    def test_return_accepts_no_declines_and_both_are_remembered(self):
        for choice in ("", "N"):
            root = self.root / ("yes" if not choice else "no")
            root.mkdir()
            ask, create = Mock(return_value=choice), Mock()
            shortcut.ask_once(root, ask, create)
            shortcut.ask_once(root, ask, create)
            ask.assert_called_once()
            self.assertEqual(create.call_count, int(not choice))
            self.assertEqual((root / "runtime/.shortcut-asked").read_text().strip(), "yes" if not choice else "no")

    def test_noninteractive_input_does_not_record_an_answer(self):
        shortcut.ask_once(self.root, Mock(side_effect=EOFError), Mock())
        self.assertFalse((self.root / "runtime/.shortcut-asked").exists())

    def test_posix_bundle_launch_replaces_the_process(self):
        with patch("sys.platform", "darwin"), patch("os.execv", side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit):
                gui_runtime.main(["--launch", "AIassist.py"])
        self.assertEqual(execute.call_args.args[1][1:], ["AIassist.py"])

    @unittest.skipUnless(os.name == "nt", "Windows shell links")
    def test_real_windows_shell_links_in_redirected_test_folders(self):
        # Exercise WScript.Shell itself while routing both destinations into this fixture.
        system = Path(os.sys.executable).with_name("pythonw.exe")
        spec = shortcut.windows_spec(self.root, system)
        env = {**os.environ, **{"SHORTCUT_" + k: v for k, v in spec.items()},
               "TEST_DESKTOP": str(self.root / "Desktop"), "TEST_PROGRAMS": str(self.root / "Start Menu")}
        create = shortcut.WINDOWS_SCRIPT.replace("[Environment]::GetFolderPath('Desktop')", "$env:TEST_DESKTOP")
        create = create.replace("[Environment]::GetFolderPath('Programs')", "$env:TEST_PROGRAMS")
        check = """
foreach ($dir in @($env:TEST_DESKTOP, $env:TEST_PROGRAMS)) {
    $link = $shell.CreateShortcut((Join-Path $dir 'AI Assist.lnk'))
    if ($link.TargetPath -ne $env:SHORTCUT_TargetPath -or $link.Arguments -ne $env:SHORTCUT_Arguments -or
        $link.WorkingDirectory -ne $env:SHORTCUT_WorkingDirectory) { throw 'Shortcut readback failed.' }
}
"""
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", create + check],
                                   creationflags=subprocess.CREATE_NO_WINDOW, env=env, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((self.root / "Desktop/AI Assist.lnk").is_file())
