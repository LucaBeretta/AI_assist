"""Check source distributions against GitHub without requiring Git."""

import hashlib
import http.client
import json
from pathlib import Path, PurePosixPath
import queue
import threading
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request
import webbrowser

import network_gate


SHIPPED_SUBFOLDER_FILE = ("child_gate", "sitecustomize.py")
# Packages whose modules ship with the app, and the web workspace's page files.
SHIPPED_PACKAGES = ("app_core", "webui")
SHIPPED_STATIC = ("webui", "static")
STATIC_SUFFIXES = (".html", ".css", ".js", ".svg")
REPOSITORY = "https://github.com/LucaBeretta/AI_assist"
API = "https://api.github.com/repos/LucaBeretta/AI_assist"


def is_shipped(path):
    """Whether ``path`` (a PurePosixPath in the repository) is part of the app itself."""
    parts = path.parts
    if path.name.startswith("test_"):
        return False
    if len(parts) == 1:
        return path.suffix in (".py", ".cmd")
    if parts == SHIPPED_SUBFOLDER_FILE:
        return True
    if len(parts) == 2 and parts[0] in SHIPPED_PACKAGES:
        return path.suffix == ".py"
    return len(parts) == 3 and parts[:2] == SHIPPED_STATIC and path.suffix in STATIC_SUFFIXES


def github_json(path):
    request = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AI-Assist-Update-Checker"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("GitHub response is too large.")
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code in (403, 429):
            raise RuntimeError("GitHub's request limit was reached. Try again later.") from error
        raise RuntimeError(f"GitHub returned HTTP {code}. Try again later.") from error


def blob_hash(data):
    # Git checkouts on Windows may convert LF to CRLF.
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def check_update(directory):
    commits = github_json("/commits?per_page=1")
    commit = commits[0]
    revision = commit["sha"]
    tree = github_json("/git/trees/" + commit["commit"]["tree"]["sha"] + "?recursive=1")
    if tree.get("truncated") or not isinstance(tree.get("tree"), list):
        raise ValueError("GitHub returned an incomplete file list.")
    files = {}
    for item in tree["tree"]:
        path = PurePosixPath(item["path"])
        # The application ships its root modules and launcher, the internet gate's
        # loader for the Python programs it starts, its packages, and the web workspace.
        if item["type"] == "blob" and is_shipped(path):
            files[path.as_posix()] = item["sha"]
    if "AIassist.py" not in files:
        raise ValueError("The GitHub file list does not contain AI Assist.")
    differences = []
    for name, expected in files.items():
        try:
            data = (Path(directory) / name).read_bytes()
        except FileNotFoundError:
            differences.append(name)
            continue
        if blob_hash(data) != expected:
            differences.append(name)
    return {"revision": revision, "differences": differences,
            "download": REPOSITORY + "/archive/" + revision + ".zip"}


class UpdateChecker:
    """Checks GitHub only when asked: AI Assist never goes online by itself."""

    def __init__(self, app):
        self.app = app
        self.results = queue.Queue()
        self.busy = False
        self.result = None
        self.window = None
        self.message = "Updates have not been checked yet."

    def check(self):
        if self.busy or self.app.closed or self.app.closing:
            return
        self.busy = True
        self.message = "Checking GitHub for the latest version..."
        self.refresh()

        def worker():
            try:
                with network_gate.task("Check GitHub for a newer version of AI Assist",
                                       sends="Requests for the repository's latest commit and file list. "
                                             "No chats, files or settings are sent."):
                    result = check_update(Path(__file__).resolve().parent)
                self.results.put((result, None))
            except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError,
                    AttributeError, http.client.HTTPException) as error:
                self.results.put((None, error))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.result = result
        if isinstance(error, network_gate.InternetBlocked):
            self.message = "Updates were not checked: the connection to GitHub was blocked.\n\n" + str(error)
        elif error:
            self.message = "Could not check for updates. Check your connection and retry.\n\n" + str(error)
        elif result["differences"]:
            self.message = ("Your copy differs from the latest GitHub version. An update may be available; "
                            "local edits or a development copy can also cause this.\n\n"
                            f"Latest commit: {result['revision'][:12]}\n"
                            f"Different or missing app files: {len(result['differences'])}\n\n"
                            "Download and extract the complete app into a new folder, then start it there. "
                            "Saved chats stay in your configured data folder.")
        else:
            self.message = "You have the latest version on GitHub.\n\nCommit: " + result["revision"][:12]
        self.refresh()

    def refresh(self):
        if self.window is not None and self.window.winfo_exists():
            self.status.configure(text=self.message)
            self.retry.configure(state="disabled" if self.busy else "normal")
            self.download.configure(state="normal" if self.result and not self.busy else "disabled")

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        window = self.window = tk.Toplevel(self.app.root)
        window.title("AI Assist updates")
        window.geometry("510x350")
        window.transient(self.app.root)
        self.status = ttk.Label(window, text=self.message, wraplength=460, justify="left")
        self.status.pack(fill="both", expand=True, padx=24, pady=24)
        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=24, pady=(0, 20))
        self.retry = ttk.Button(actions, text="Check again", command=self.check)
        self.retry.pack(side="left")
        self.download = ttk.Button(actions, text="Download latest", command=self.download_latest)
        self.download.pack(side="left", padx=8)
        ttk.Button(actions, text="Close", command=window.destroy).pack(side="right")
        self.refresh()
        if self.result is None and not self.busy:
            self.check()

    def download_latest(self):
        if self.result:
            webbrowser.open(self.result["download"])
