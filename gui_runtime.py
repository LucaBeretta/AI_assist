"""Validate the GUI runtime before opening the application window."""

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


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


class GUIRuntimeError(RuntimeError):
    """The selected Python cannot provide a usable Tk application window."""


def _guidance():
    launch = "Start AI Assist.command" if sys.platform == "darwin" else "AI Assist"
    minimum = " On macOS, AI Assist requires Tk 8.6 or newer." if sys.platform == "darwin" else ""
    return ("Install a current Python from https://www.python.org/downloads/ with its Tcl/Tk support, "
            f"then run {launch} again.{minimum}")


def _version_pair(value):
    parts = str(value).split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def create_root():
    """Create Tk, rejecting the older Apple Tk runtime before displaying a GUI."""
    try:
        tkinter = importlib.import_module("tkinter")
    except ImportError as error:
        raise GUIRuntimeError(f"This Python has no usable Tkinter module. {_guidance()}") from error
    if sys.platform == "darwin":
        version = getattr(tkinter, "TkVersion", None)
        pair = _version_pair(version)
        if pair is None:
            raise GUIRuntimeError(f"Could not determine this Python's Tk version ({version!r}). {_guidance()}")
        if pair < (8, 6):
            raise GUIRuntimeError(f"This Python uses Tk {version}, which is too old for AI Assist on macOS. {_guidance()}")
    try:
        return tkinter.Tk()
    except tkinter.TclError as error:
        raise GUIRuntimeError(f"Could not initialize the GUI: {error}\n{_guidance()}") from error


def probe_root():
    """Exercise a real, hidden Tk root and print the interpreter it will use."""
    root = create_root()
    try:
        root.withdraw()
        diagnostics = (
            ("Python interpreter", sys.executable),
            ("Python version", sys.version.split()[0]),
            ("Tcl version", root.tk.call("info", "patchlevel")),
            ("Tk version", root.tk.call("package", "present", "Tk")),
            ("Windowing system", root.tk.call("tk", "windowingsystem")),
        )
        for label, value in diagnostics:
            print(f"{label}: {value}")
    finally:
        root.destroy()


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="check a hidden Tk window and print runtime details")
    parser.add_argument("--launch", metavar="SCRIPT", help="start SCRIPT detached with pythonw (Windows); exit 3 if unavailable")
    parser.add_argument("--web", action="store_true", help="pass --web to the detached app")
    args = parser.parse_args(argv)
    if args.launch:
        return 0 if launch_detached(args.launch, arguments=("--web",) if args.web else ()) else 3
    if not args.probe:
        parser.error("Use --probe to check the GUI runtime.")
    try:
        probe_root()
    except Exception as error:
        print(f"GUI runtime check failed: {error}", file=sys.stderr)
        if not isinstance(error, GUIRuntimeError):
            print(_guidance(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
