"""Token-authenticated Vercel deployments using only the standard library."""

import fnmatch
import hashlib
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from vercel_deploy import DeploymentCancelled, check_cancelled, validate_project


class VercelAPIError(RuntimeError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class VercelAPI:
    def __init__(self, token, scope, cancel):
        self.token = token.strip()
        if not self.token or any(char.isspace() for char in self.token):
            raise ValueError("Enter a Vercel access token, or set VERCEL_TOKEN before starting AI Assist.")
        self.query = urlencode({"teamId" if scope.startswith("team_") else "slug": scope}) if scope else ""
        self.cancel = cancel
        self.opener = build_opener(_NoRedirect())

    def select_account(self, scope, on_output):
        if scope:
            on_output(f"Using Vercel team: {scope}")
            return
        on_output("Checking available Vercel teams...")
        teams = self.request("GET", "/v2/teams").get("teams", [])
        if len(teams) > 1:
            names = ", ".join(str(team.get("slug", team.get("id", ""))) for team in teams)
            raise ValueError(f"Choose a team in the Team field before deploying. Available teams: {names}")
        if teams:
            team = teams[0]
            self.query = urlencode({"teamId": team["id"]})
            on_output(f"Using Vercel team: {team.get('slug', team['id'])}")
        else:
            on_output("Using the token's personal account.")

    def request(self, method, path, payload=None, headers=None):
        check_cancelled(self.cancel)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        request_headers = {"Authorization": "Bearer " + self.token,
                           "Content-Type": "application/octet-stream" if isinstance(payload, bytes) else "application/json"}
        request_headers.update(headers or {})
        url = "https://api.vercel.com" + path + ("?" + self.query if self.query else "")
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024)
        except HTTPError as error:
            try:
                detail = json.loads(error.read(65536)).get("error", {})
                code, message = detail.get("code", ""), detail.get("message", "Request failed")
            except (ValueError, AttributeError):
                code, message = "", "Request failed"
            finally:
                error.close()
            message = str(message).replace(self.token, "[redacted]")[:2000]
            if error.code in (401, 403):
                message += ". Check the token's expiry, permissions, and selected team."
            raise VercelAPIError(error.code, code, f"Vercel API ({error.code}, {method} {path}, {code or 'unknown_error'}): {message}") from None
        except (URLError, OSError):
            raise RuntimeError("Could not reach Vercel API. Check your connection and the Vercel dashboard before retrying.") from None
        check_cancelled(self.cancel)
        try:
            result = json.loads(raw) if raw.strip() else {}
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except ValueError:
            raise RuntimeError("Vercel API returned an invalid response. Check the dashboard before retrying.") from None


def _glob(pattern):
    """Translate slash-separated ignore globs without matching * across folders."""
    parts = pattern.split("/")
    result = ""
    for index, part in enumerate(parts):
        if part == "**":
            result += ".*" if index == len(parts) - 1 else "(?:[^/]+/)*"
        else:
            result += fnmatch.translate(part)[4:-3]
            if index < len(parts) - 1:
                result += "/"
    return re.compile("^" + result + "$")


def _rules(folder, names):
    rules = []
    for name in names:
        path = folder / name
        if not path.is_file() or path.is_symlink():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            include = line.startswith("!")
            pattern = line[1:] if include else line
            directory = pattern.endswith("/")
            anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
            pattern = pattern.strip("/")
            if pattern:
                rules.append((folder, _glob(pattern), include, directory, anchored))
    return rules


def deployment_files(root, cancel):
    """Preflight before uploading; ignore dependencies, credentials and local data."""
    excluded = {".git", ".vercel", ".aiassist", "node_modules", ".next", ".nuxt", ".venv", "venv",
                "__pycache__", ".pytest_cache", ".idea", ".vscode"}
    root_rules = _rules(root, (".vercelignore",))
    result, total = [], 0

    def visit(folder, inherited):
        nonlocal total
        rules = inherited + _rules(folder, (".gitignore",))
        for path in sorted(folder.iterdir()):
            check_cancelled(cancel)
            name = path.name.lower()
            if (name in excluded or name == ".env" or name.startswith(".env.")
                    or name in {".npmrc", ".pypirc", "credentials.json"}
                    or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".pyc"}
                    or path.is_symlink() or getattr(path, "is_junction", lambda: False)()):
                continue
            if not path.resolve().is_relative_to(root):
                continue
            is_dir = path.is_dir()
            ignored = False
            # Vercel-specific rules override gitignore rules.
            for base, matcher, include, directory, anchored in rules + root_rules:
                relative = path.relative_to(base).as_posix()
                target = relative if anchored else path.name
                if (not directory or is_dir) and matcher.fullmatch(target):
                    ignored = not include
            if ignored:
                continue
            if is_dir:
                visit(path, rules)
            elif path.is_file():
                size = path.stat().st_size
                total += size
                if size > 50 * 1024 * 1024 or total > 100 * 1024 * 1024 or len(result) >= 10000:
                    raise ValueError("Project exceeds the uploader limit (50 MB per file, 100 MB total, 10,000 files). Exclude unnecessary files in .vercelignore.")
                result.append(path)

    visit(root, [])
    if not result:
        raise ValueError("No deployable files remain after applying ignore rules.")
    return result


def deploy_project_api(folder, project, scope, domain, on_output, cancel, *, token=None):
    root, project, scope, domain = validate_project(folder, project, scope, domain)
    api = VercelAPI(token if token is not None else os.environ.get("VERCEL_TOKEN", ""), scope, cancel)
    files = deployment_files(root, cancel)
    api.select_account(scope, on_output)
    on_output(f"Checking Vercel project {project}...")
    payload = {"name": project, "target": "production", "projectSettings": {}}
    try:
        existing = api.request("GET", "/v9/projects/" + quote(project, safe=""))
        payload["project"] = existing.get("id") or project
    except VercelAPIError as error:
        if error.status != 404:
            raise
        on_output(f"Preparing the first deployment of {project}...")
        if not (root / "package.json").is_file() and not (root / "api").is_dir() and (root / "index.html").is_file():
            payload["projectSettings"] = {"framework": None}
    on_output(f"Uploading {len(files)} project files to Vercel...")
    references = []
    uploaded = set()
    total = 0
    for path in files:
        check_cancelled(cancel)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Project files changed during upload. Retry with a stable project folder.")
        data = path.read_bytes()
        total += len(data)
        if len(data) > 50 * 1024 * 1024 or total > 100 * 1024 * 1024:
            raise ValueError("A project file grew beyond the upload limit. Retry after saving your files.")
        digest = hashlib.sha1(data).hexdigest()
        if digest not in uploaded:
            api.request("POST", "/v2/files", data, {"x-vercel-digest": digest})
            uploaded.add(digest)
        references.append({"file": path.relative_to(root).as_posix(), "sha": digest, "size": len(data)})
    on_output(f"Creating production deployment for {project}...")
    payload["files"] = references
    deployment = api.request("POST", "/v13/deployments", payload)
    identifier = deployment.get("id")
    hostname = deployment.get("url", "")
    if not identifier or not re.fullmatch(r"[A-Za-z0-9.-]+\.vercel\.app", hostname):
        raise RuntimeError("Vercel did not return a deployment ID and URL. Check the dashboard before retrying.")
    url = "https://" + hostname
    on_output(f"Deployment submitted: {url}")
    deadline = time.monotonic() + 900
    last_state = None
    while True:
        check_cancelled(cancel)
        state = deployment.get("readyState") or deployment.get("status")
        if state != last_state:
            on_output(f"Build status: {state or 'pending'}")
            last_state = state
        if state == "READY":
            break
        if state in {"ERROR", "CANCELED"}:
            detail = str(deployment.get("errorMessage") or deployment.get("errorCode") or "Check build logs in the Vercel dashboard.")
            detail = detail.replace(api.token, "[redacted]")[:2000]
            raise RuntimeError(f"Vercel build {state.lower()}: {detail} Deployment: {url}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for the build. It may continue on Vercel: {url}")
        if cancel.wait(2):
            check_cancelled(cancel)
        deployment = api.request("GET", "/v13/deployments/" + quote(identifier, safe=""))
    result = dict(url=url, domain=domain, domain_assigned=False, domain_error="")
    if domain:
        try:
            on_output(f"Connecting {domain}...")
            domain_path = "/v10/projects/" + quote(project, safe="") + "/domains"
            try:
                domain_info = api.request("GET", "/v9/projects/" + quote(project, safe="") + "/domains/" + quote(domain, safe=""))
            except VercelAPIError as error:
                if error.status != 404:
                    raise
                domain_info = api.request("POST", domain_path, {"name": domain})
            if domain_info.get("verified") is False:
                raise RuntimeError("Domain ownership verification is required in the Vercel dashboard: " + json.dumps(domain_info.get("verification", [])))
            api.request("POST", "/v2/deployments/" + quote(identifier, safe="") + "/aliases", {"alias": domain})
            result["domain_assigned"] = True
        except DeploymentCancelled:
            raise
        except RuntimeError as error:
            result["domain_error"] = str(error)
    return result
