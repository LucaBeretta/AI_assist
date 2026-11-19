"""The GUI runner must refuse to run on the user's ordinary desktop."""

import os
import json
from pathlib import Path
import subprocess
import sys
import unittest


class IsolatedRunnerTests(unittest.TestCase):
    def test_skipped_tests_produce_a_serializable_report(self):
        from run_windows_tests import report_result
        result = unittest.TestResult()
        case = unittest.FunctionTestCase(lambda: None)
        result.addSkip(case, "Missing platform privilege")
        report = json.loads(json.dumps(report_result(result, "isolated")))
        self.assertEqual(report["skipped"], [{"test": case.id(), "reason": "Missing platform privilege"}])
        self.assertTrue(report["successful"])

    @unittest.skipUnless(os.name == "nt", "This runner is Windows-specific")
    def test_worker_refuses_a_different_desktop_before_loading_tests(self):
        result = subprocess.run([sys.executable, "-E", str(Path(__file__).with_name("run_windows_tests.py")),
            "--worker", "ai_assist_tests_not_the_actual_desktop", "--pattern", "test_webui_static.py"],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing GUI tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
