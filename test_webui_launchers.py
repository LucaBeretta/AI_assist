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

    def test_wrappers_reuse_private_setup_and_default_tk_path(self):
        root = Path(__file__).parent
        for extension in ("cmd", "command"):
            text = (root / f"Start AI Assist (new UI).{extension}").read_text(encoding="utf-8")
            self.assertIn("AI_ASSIST_WEB_UI", text)
            self.assertIn(f"Start AI Assist.{extension}", text)
        windows = (root / "Start AI Assist.cmd").read_text(encoding="utf-8")
        mac = (root / "Start AI Assist.command").read_text(encoding="utf-8")
        self.assertIn('if "%AI_ASSIST_WEB_UI%"=="1" set "APP_MODE=--web"', windows)
        self.assertIn('--launch "%APP_DIR%AIassist.py" %APP_MODE%', windows)
        self.assertIn('APP_ARGS=()', mac)
        self.assertIn('AIassist.py "${APP_ARGS[@]}"', mac)


if __name__ == "__main__":
    unittest.main()
