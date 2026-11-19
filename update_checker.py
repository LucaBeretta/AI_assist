"""Check source distributions against GitHub without requiring Git."""

import hashlib
import http.client
import json
from pathlib import Path, PurePosixPath
import urllib.error
import urllib.request


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
