"""The web launchers share setup and forward mode without opening a window."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import gui_runtime


class WebLauncherTests(unittest.TestCase):
    def test_detached_launch_forwards_web_mode_as_a_separate_argument(self):
        process = Mock()
        with patch("gui_runtime.windowless_python", return_value="pythonw.exe"):
            self.assertTrue(gui_runtime.launch_detached("AIassist.py", popen=process, arguments=("--web",)))
        self.assertEqual(process.call_args.args[0], ["pythonw.exe", "AIassist.py", "--web"])
        with patch("gui_runtime.launch_detached", return_value=True) as launch:
            self.assertEqual(gui_runtime.main(["--launch", "AIassist.py", "--web"]), 0)
        launch.assert_called_once_with("AIassist.py", arguments=("--web",))

    def test_single_launchers_use_private_setup_and_the_default_web_entry(self):
        root = Path(__file__).parent
        for extension in ("cmd", "command"):
            self.assertFalse((root / f"Start AI Assist (new UI).{extension}").exists())
        windows = (root / "Start AI Assist.cmd").read_text(encoding="utf-8")
        mac = (root / "Start AI Assist.command").read_text(encoding="utf-8")
        self.assertIn('--launch "%APP_DIR%AIassist.py"', windows)
        self.assertIn('"$1" AIassist.py', mac)
        for text in (windows, mac):
            self.assertIn('gui_runtime.py', text)
            self.assertIn('--probe', text)
            self.assertNotIn('AI_ASSIST_WEB_UI', text)


if __name__ == "__main__":
    unittest.main()
