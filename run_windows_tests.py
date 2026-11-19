"""Run Tk tests on a separate Windows desktop without activating it.

Usage: python run_windows_tests.py [--pattern test_*.py] [--timeout 300]
The worker verifies its desktop before importing any application or Tk code.
Logs and the machine-readable result stay in the ignored __pycache__ folder.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                *[(name, wintypes.DWORD) for name in (
                    "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars", "dwYCountChars",
                    "dwFillAttribute", "dwFlags")], ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def windows_api():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user.CreateDesktopW.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return user, kernel


def desktop_name(user, handle):
    buffer = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if not user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def report_result(result, desktop):
    return {"desktop": desktop, "tests": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": [{"test": test.id(), "reason": reason}
                for test, reason in result.skipped], "successful": result.wasSuccessful()}


def worker(options, log_path, report_path):
    # Check before importing unittest, which discovers modules importing Tk.
    user, kernel = windows_api()
    actual = desktop_name(user, user.GetThreadDesktop(kernel.GetCurrentThreadId()))
    if not options.worker.startswith("ai_assist_tests_") or actual != options.worker:
        raise RuntimeError("Refusing GUI tests: the worker is not on the isolated desktop.")
    import unittest
    suite = (unittest.defaultTestLoader.loadTestsFromNames(options.test) if options.test else
             unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent), pattern=options.pattern))
    with log_path.open("w", encoding="utf-8", buffering=1) as output, redirect_stdout(output), redirect_stderr(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        report = report_result(result, actual)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--test", action="append", default=[], help="Run a specific unittest ID; repeat to select multiple tests.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if os.name != "nt":
        parser.error("This runner requires Windows. Use xvfb-run on Linux.")
    if options.timeout < 1:
        parser.error("Timeout must be positive.")
    root = Path(__file__).resolve().parent
    cache = root / "__pycache__"
    cache.mkdir(exist_ok=True)
    log_path, report_path = cache / "isolated-windows-tests.log", cache / "isolated-windows-tests.json"
    if options.worker:
        return worker(options, log_path, report_path)
    report_path.unlink(missing_ok=True)
    user, kernel = windows_api()
    identity = "ai_assist_tests_" + uuid.uuid4().hex
    # DESKTOP_CREATEWINDOW | READOBJECTS | WRITEOBJECTS | ENUMERATE.
    desktop = user.CreateDesktopW(identity, None, None, 0, 0x00C3, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    process = ProcessInfo()
    try:
        startup = StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        startup.lpDesktop = identity
        startup.dwFlags = 0x80  # STARTF_FORCEOFFFEEDBACK; no change to the user's cursor.
        args = [sys.executable, "-E", str(Path(__file__).resolve()), "--worker", identity,
                "--pattern", options.pattern]
        for name in options.test:
            args.extend(["--test", name])
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        if not kernel.CreateProcessW(sys.executable, command, None, None, False,
                                     0x08000000, None, str(root), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + options.timeout
        while True:
            waited = kernel.WaitForSingleObject(process.hProcess, 100)
            if waited == 0:
                break
            if waited != 258:
                raise ctypes.WinError(ctypes.get_last_error())
            if time.monotonic() >= deadline:
                subprocess.run(["taskkill", "/PID", str(process.dwProcessId), "/T", "/F"],
                    capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                raise TimeoutError("Isolated GUI tests timed out; their process tree was stopped.")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not report_path.exists():
            raise RuntimeError(f"The isolated worker exited without a report (0x{code.value:08X}).")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps({key: value for key, value in report.items() if key != "skipped"}, ensure_ascii=False))
        print(f"Skipped: {len(report['skipped'])}. Log: {log_path}")
        if code.value != 0 or not report["successful"]:
            print(f"Worker exit: 0x{code.value:08X}")
            print("\n".join(log_path.read_text(encoding="utf-8").splitlines()[-90:]))
        return 0 if code.value == 0 and report["successful"] else 1
    finally:
        if process.hProcess and kernel.WaitForSingleObject(process.hProcess, 0) == 258:
            subprocess.run(["taskkill", "/PID", str(process.dwProcessId), "/T", "/F"],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if process.hThread:
            kernel.CloseHandle(process.hThread)
        if process.hProcess:
            kernel.CloseHandle(process.hProcess)
        user.CloseDesktop(desktop)


if __name__ == "__main__":
    raise SystemExit(main())
