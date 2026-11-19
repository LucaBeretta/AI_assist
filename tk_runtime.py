"""Legacy root creation; kept separately until the native UI removal."""
import importlib
import sys


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

