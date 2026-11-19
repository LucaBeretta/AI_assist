"""Real temporary-project actions with simulated Ollama HTTP responses.

No model is loaded and no network request is made. Commands execute only the
current Python interpreter, through the same shell used by the application.
"""

import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from coding_agent import run_agent
from test_agent_support import isolate_agent_data


def _tool_response(name, arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}},
    ]}}


def _python_command(code):
    if os.name == "nt":
        executable = sys.executable.replace("'", "''")
        quoted_code = code.replace("'", "''")
        return f"& '{executable}' -I -S -c '{quoted_code}'"
    return " ".join(shlex.quote(argument) for argument in [sys.executable, "-I", "-S", "-c", code])


def _process_is_running(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: the process no longer exists.
                return False
            raise OSError(error, "Cannot inspect the integration test's child process")
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A terminated process can briefly remain a zombie until its parent reaps it.
    process_status = Path(f"/proc/{pid}/stat")
    try:
        if process_status.exists() and process_status.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    return True


def _stop_test_child(pid):
    """A failure cleanup targets only the PID written by this test's own child."""
    if not _process_is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        os.kill(pid, signal.SIGKILL)


class CodingAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        isolate_agent_data(self)
        self.temporary = tempfile.TemporaryDirectory(prefix="aiassist-agent-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.events = []

    def run_tool(self, name, arguments, approve_action):
        responses = iter([
            _tool_response(name, arguments),
            {"message": {"role": "assistant", "content": "Integration action finished."}},
        ])
        requests = []

        def simulated_http(request, **kwargs):
            requests.append((request.full_url, json.loads(request.data)))
            try:
                response = next(responses)
            except StopIteration as error:
                raise RuntimeError("Unexpected extra Ollama request in integration test") from error
            return io.BytesIO(json.dumps(response).encode("utf-8"))

        with patch("coding_agent.urllib.request.urlopen", side_effect=simulated_http):
            result = run_agent(
                "http://localhost:11434", "integration-test-model", self.project,
                [{"role": "user", "content": "Perform this integration test action."}],
                self.events.append, approve_action,
            )
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(url == "http://localhost:11434/api/chat" for url, payload in requests))
        native_results = [message for message in requests[1][1]["messages"] if message["role"] == "tool"]
        self.assertEqual(len(native_results), 1)
        self.assertEqual(native_results[0]["tool_name"], name)
        return result, native_results[0]["content"]

    def test_real_file_is_created_only_after_explicit_approval(self):
        target = self.project / "src" / "integration.py"
        content = "print('temporary project only')\n"
        approvals = []

        def deny(action):
            self.assertFalse(target.exists())
            approvals.append(action)
            return False

        _, denied = self.run_tool("write_file", {"path": "src/integration.py", "content": content}, deny)
        self.assertIn("declined", denied)
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())

        def approve(action):
            self.assertFalse(target.exists())
            approvals.append(action)
            self.assertEqual(action["kind"], "write_file")
            self.assertEqual(action["content"], content)
            self.assertFalse(action["exists"])
            self.assertIn("+print('temporary project only')", action["diff"])
            return True

        _, completed = self.run_tool("write_file", {"path": "src/integration.py", "content": content}, approve)
        self.assertIn("Successfully created", completed)
        self.assertEqual(target.read_bytes(), content.encode("utf-8"))
        self.assertEqual(len(approvals), 2)
        self.assertEqual([path.name for path in target.parent.iterdir()], ["integration.py"])

    def test_reviewed_command_runs_real_python_and_returns_its_output(self):
        command = _python_command("print('AIASSIST_REAL_COMMAND_OK', flush=True)")
        approvals = []

        def approve(action):
            approvals.append(action)
            self.assertEqual(action["kind"], "run_command")
            self.assertEqual(action["command"], command)
            self.assertEqual(Path(action["cwd"]), self.project.resolve())
            return True

        _, output = self.run_tool("run_command", {
            "command": command, "description": "Print an integration test marker.", "timeout_seconds": 15,
        }, approve)
        self.assertEqual(len(approvals), 1)
        self.assertIn("Command exited with code 0.", output)
        self.assertIn("AIASSIST_REAL_COMMAND_OK", output)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_short_timeout_stops_real_shell_and_owned_python_child(self):
        pid_file = self.project / "child.pid"
        code = ("import os,time; "
                "f=open('child.pid','w'); f.write(str(os.getpid())); f.close(); "
                "print('AIASSIST_CHILD_STARTED', flush=True); time.sleep(30)")
        command = _python_command(code)
        started = time.monotonic()
        _, output = self.run_tool("run_command", {
            "command": command, "description": "Verify timeout stops the owned test process.",
            "timeout_seconds": 2,
        }, lambda action: True)
        elapsed = time.monotonic() - started
        self.assertTrue(pid_file.is_file(), "The Python child did not start before the short timeout.")
        pid = int(pid_file.read_text(encoding="utf-8"))
        self.addCleanup(_stop_test_child, pid)
        self.assertIn("Command timed out after 2 seconds", output)
        self.assertIn("AIASSIST_CHILD_STARTED", output)
        self.assertLess(elapsed, 12)
        self.assertFalse(_process_is_running(pid), "The owned Python child survived command timeout.")


if __name__ == "__main__":
    unittest.main()
