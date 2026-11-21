"""Check source distributions against GitHub without requiring Git."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.error
import urllib.request


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
        return (path.suffix in (".py", ".cmd") or path.name in {
            "requirements.txt", "Start AI Assist.command", "Start AI Assist.sh",
            "install_runtime.ps1", "README.md", "LEGGIMI-colibri.md"})
    if len(parts) == 3 and parts[:2] == ("assets", "icon"):
        return path.suffix.lower() in {".ico", ".icns", ".png", ".svg"}
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


def blob_hash(data, *, binary=False):
    # Git checkouts on Windows may convert LF to CRLF.
    if not binary:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def check_update(directory):
    commits = github_json("/commits?sha=main&per_page=1")
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
    from app_updater import plan_update
    plan = plan_update(Path(directory), files)
    differences = plan["write"] + plan["delete"]
    return {"revision": revision, "differences": differences,
            "expected_files": files, "download": REPOSITORY + "/archive/" + revision + ".zip"}
