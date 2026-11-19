"""Open the web workspace in its own window, like an app: no tabs, no address bar.

Edge and Chrome (and other Chromium browsers) open a page as an app window
with ``--app=<url>``, using the person's normal browser profile. Without one
of them, the page opens in a normal tab.

Only this computer's own workspace address is ever opened here, and it goes
through the internet gate's link check exactly as ``webbrowser.open`` does.
"""

import glob as _glob
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

_WINDOWS_BROWSERS = {
    "edge": ("Microsoft", "Edge", "Application", "msedge.exe"),
    "chrome": ("Google", "Chrome", "Application", "chrome.exe"),
    "brave": ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
}
_MAC_BROWSERS = {
    "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "chromium": "/Applications/Chromium.app/Contents/MacOS/Chromium",
}
_LINUX_BROWSERS = {
    "edge": ("microsoft-edge", "microsoft-edge-stable"),
    "chrome": ("google-chrome", "google-chrome-stable"),
    "brave": ("brave-browser",),
    "chromium": ("chromium", "chromium-browser"),
}


_CHROMIUM_EXES = {"msedge.exe": "edge", "chrome.exe": "chrome", "brave.exe": "brave", "chromium.exe": "chromium",
                  "vivaldi.exe": "vivaldi"}


def _registry_value(root_name, key, name=""):
    try:
        import winreg
        with winreg.OpenKey(getattr(winreg, root_name), key) as handle:
            return str(winreg.QueryValueEx(handle, name)[0])
    except (OSError, ImportError, AttributeError):
        return None


def _default_prog_id():
    return _registry_value("HKEY_CURRENT_USER",
                           r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice", "ProgId")


def default_browser_family():
    """'edge', 'chrome', 'brave' or None: the person's default browser on Windows, when it is one of these."""
    if os.name != "nt":
        return None
    prog_id = (_default_prog_id() or "").lower()
    for family, marker in (("edge", "msedge"), ("chrome", "chrome"), ("brave", "brave")):
        if marker in prog_id:
            return family
    return None


def default_browser_path():
    """The default browser's program on Windows, when it is a Chromium browser that has app windows."""
    if os.name != "nt":
        return None
    prog_id = _default_prog_id()
    if not prog_id:
        return None
    command = _registry_value("HKEY_CLASSES_ROOT", prog_id + r"\shell\open\command") or ""
    match = re.match(r'\s*"([^"]+)"|\s*(\S+)', command)
    path = (match.group(1) or match.group(2)) if match else ""
    return path if path and os.path.basename(path).lower() in _CHROMIUM_EXES and os.path.isfile(path) else None


def _app_path(exe):
    """Where Windows' "App Paths" says ``exe`` is installed."""
    for root in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        value = _registry_value(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe)
        if value:
            return value.strip('"')
    return None


def _version(path):
    folder = os.path.basename(os.path.dirname(path))
    return tuple(int(part) if part.isdigit() else 0 for part in folder.split("."))


def find_app_browser(platform=None, environ=None, which=shutil.which, isfile=os.path.isfile, preferred=None,
                     glob=_glob.glob, app_path=_app_path):
    """The path of a browser that can open an app window, preferring the default browser, or None.
    Edge's WebView runtime is never used: it is a component for other apps, not a browser."""
    platform = platform or ("windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux")
    environ = os.environ if environ is None else environ
    if platform == "windows":
        roots = [environ.get(name) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        found = {}
        for family, parts in _WINDOWS_BROWSERS.items():
            paths = [os.path.join(root, *parts) for root in roots if root]
            if family == "edge":
                # Newer Edge installs keep the browser in EdgeCore\<version>\msedge.exe.
                for root in roots:
                    if root:
                        paths += sorted(glob(os.path.join(root, "Microsoft", "EdgeCore", "*", "msedge.exe")), key=_version, reverse=True)
            paths.append(app_path(parts[-1]))
            found[family] = [path for path in paths if path and "edgewebview" not in path.lower()]
    elif platform == "mac":
        found = {family: [path] for family, path in _MAC_BROWSERS.items()}
    else:
        found = {family: [which(name) for name in names] for family, names in _LINUX_BROWSERS.items()}
    order = list(found)
    if preferred in found:
        order.remove(preferred)
        order.insert(0, preferred)
    for family in order:
        for path in found[family]:
            if path and isfile(path):
                return path
    return None


def own_address(url):
    """Whether ``url`` is this computer's workspace: http on 127.0.0.1 or ::1."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "::1") and bool(parts.port)
    except ValueError:
        return False


def open_app_window(url, browser=None, popen=subprocess.Popen):
    """Open ``url`` in an app window. Returns False when no suitable browser was found
    (the caller then opens a normal tab). Raises ValueError for any other address."""
    if not own_address(url):
        raise ValueError("Only this computer's AI Assist workspace opens in an app window.")
    browser = browser or default_browser_path() or find_app_browser(preferred=default_browser_family())
    if not browser:
        return False
    # The same check the internet gate applies to webbrowser.open.
    sys.audit("webbrowser.open", url)
    if os.name == "nt":
        options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        options = {"start_new_session": True}
    popen([browser, f"--app={url}", "--new-window"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, close_fds=True, **options)
    return True
