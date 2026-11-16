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


REPOSITORY = "https://github.com/LucaBeretta/AI_assist"
API = "https://api.github.com/repos/LucaBeretta/AI_assist"


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
        # The application currently ships its runtime modules and launcher at root.
        if (item["type"] == "blob" and len(path.parts) == 1
                and path.suffix in (".py", ".cmd") and not path.name.startswith("test_")):
            files[path.name] = item["sha"]
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
    def __init__(self, app):
        self.app = app
        self.results = queue.Queue()
        self.busy = False
        self.result = None
        self.window = None
        self.message = "Updates have not been checked yet."
        self.startup_job = self.app.root.after(1500, self.check)

    def close(self):
        if self.startup_job is not None:
            self.app.root.after_cancel(self.startup_job)
            self.startup_job = None

    def check(self):
        self.close()
        if self.busy or self.app.closed or self.app.closing:
            return
        self.busy = True
        self.message = "Checking GitHub for the latest version..."
        self.refresh()

        def worker():
            try:
                self.results.put((check_update(Path(__file__).resolve().parent), None))
            except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError,
                    AttributeError, http.client.HTTPException) as error:
                self.results.put((None, str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.result = result
        if error:
            self.message = "Could not check for updates. Check your connection and retry.\n\n" + error
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
