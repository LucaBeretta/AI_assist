"""Probe web dependencies and detach Windows startup without creating a window."""

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time


def log_path():
    """Where a windowless (pythonw) run writes its errors."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "AI Assist" / "last-run.log"


def redirect_missing_streams():
    """Under pythonw there is no console: send output and tracebacks to a log file."""
    if sys.stderr is not None and sys.stdout is not None:
        return None
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    return path


def probe_webview():
    """Check the web dependency without constructing or showing a native window."""
    try:
        importlib.import_module("webview")
    except ImportError as error:
        raise RuntimeError("The web UI needs pywebview. Run python -m pip install -r requirements.txt.") from error
    print(f"Python interpreter: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    print("Web UI: pywebview available")


def windowless_python(executable=None):
    """Return pythonw.exe beside this interpreter on Windows, or None."""
    if sys.platform != "win32":
        return None
    candidate = Path(executable or sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else None


def launch_detached(script, executable=None, popen=subprocess.Popen, arguments=()):
    """Start ``script`` with pythonw, detached from this console, so closing the
    launcher window does not close the app. Returns False when that is not possible."""
    pythonw = windowless_python(executable)
    if pythonw is None:
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    popen([pythonw, str(script), *arguments], cwd=str(Path(script).resolve().parent), creationflags=flags, close_fds=True,
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def wait_for_exit(pid, timeout=20):
    """Wait for the AI Assist that asked for this restart to exit (best effort)."""
    if not pid:
        return
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel.WaitForSingleObject(handle, int(timeout * 1000))
            kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def install_pending_requirements(root=None, run=subprocess.run, wait_pid=None, executable=None):
    """After an in-app update changed requirements.txt, install it before the app opens.

    Returns None when nothing is pending, else pip's exit code. On failure the marker
    stays, so the next start tries again; the app still opens with the old packages."""
    root = Path(root or Path(__file__).resolve().parent)
    marker = root / ".update" / "requirements-pending"
    if not marker.is_file():
        return None
    wait_for_exit(wait_pid)
    python = Path(executable or sys.executable)
    console = python.with_name("python.exe")
    if python.name.lower() == "pythonw.exe" and console.is_file():
        python = console
    with (root / ".update" / "pip.log").open("w", encoding="utf-8") as output:
        result = run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
                     cwd=str(root), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                     timeout=600, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode == 0:
        marker.unlink(missing_ok=True)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="check web dependencies without opening a window")
    parser.add_argument("--launch", metavar="SCRIPT", help="start SCRIPT detached with pythonw (Windows); exit 3 if unavailable")
    parser.add_argument("--web", action="store_true", help="pass --web to the detached app")
    parser.add_argument("--create-shortcut", action="store_true")
    parser.add_argument("--ask-shortcut", action="store_true")
    parser.add_argument("--wait-pid", type=int, help="with --launch: wait for this process to exit first")
    args = parser.parse_args(argv)
    if args.ask_shortcut or args.create_shortcut:
        from desktop_shortcut import ask_once, create_shortcut
        try:
            (ask_once if args.ask_shortcut else create_shortcut)()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"Could not create shortcut: {error}", file=sys.stderr)
            return 1
        return 0
    if args.launch:
        arguments = ("--web",) if args.web else ()
        try:
            install_pending_requirements(wait_pid=args.wait_pid)
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Could not install updated dependencies: {error}", file=sys.stderr)
        if launch_detached(args.launch, arguments=arguments):
            return 0
        if sys.platform != "win32":
            os.execv(sys.executable, [sys.executable, args.launch, *arguments])
        return 3
    if not args.probe:
        parser.error("Use --probe to check the GUI runtime.")
    try:
        probe_webview()
    except Exception as error:
        print(f"GUI runtime check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
