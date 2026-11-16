"""Explicit Vercel CLI deployments, without shell command interpolation."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from coding_agent import _terminate_process_tree


class DeploymentCancelled(RuntimeError):
    pass


def domain_name(value):
    value = value.strip().lower().rstrip(".")
    if not value:
        return ""
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Enter a domain such as example.com, without https:// or a path.") from error
    labels = value.split(".")
    if (len(value) > 253 or len(labels) < 2 or labels[-1].isdigit()
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        raise ValueError("Enter a domain such as example.com, without https:// or a path.")
    return value


def validate_project(folder, project, scope="", domain=""):
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose the web project's folder.")
    if not any((root / name).is_file() for name in ("index.html", "package.json", "vercel.json", "requirements.txt", "pyproject.toml")) and not (root / "api").is_dir():
        raise ValueError("Choose a web project with index.html, package.json, vercel.json, Python web configuration, or an api folder. The Tkinter desktop app itself cannot run on a domain.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", project) or "---" in project:
        raise ValueError("Use a Vercel project name of up to 100 lowercase letters, numbers, dots, underscores, or hyphens (no triple hyphens).")
    if scope and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", scope):
        raise ValueError("Enter your Vercel team slug, or leave Team empty.")
    return root, project, scope, domain_name(domain)


def vercel_command():
    executable = shutil.which("vercel")
    if not executable:
        raise RuntimeError("Vercel CLI was not found. Install Node.js, run npm install -g vercel, then vercel login. Restart AI Assist if PATH changed.")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # npm's Windows launcher is a shell script. Invoke its JS entry point
        # directly so folder names and CLI arguments never pass through cmd.
        packages = (path.parent / "node_modules/vercel", path.parent.parent / "vercel")
        candidates = []
        for package in packages:
            try:
                metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
                bins = metadata.get("bin", {})
                entry = bins if isinstance(bins, str) else bins.get("vercel")
                if isinstance(entry, str):
                    candidates.append(package / entry)
            except (OSError, ValueError, AttributeError):
                pass
            candidates.extend((package / "dist/vc.js", package / "dist/index.js"))
        script = next((candidate for candidate in candidates if candidate.is_file()), None)
        node = shutil.which("node")
        if script is None or node is None:
            raise RuntimeError("Could not locate the Node.js Vercel entry point. Reinstall the global CLI with npm install -g vercel.")
        return [node, str(script)]
    return [executable]


def _cancelled(cancel):
    if cancel.is_set():
        raise DeploymentCancelled("Deployment stopped locally. A deployment already submitted to Vercel may continue; check the Vercel dashboard.")


def run_cli(arguments, root, on_output, cancel, timeout=900):
    _cancelled(cancel)
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    environment = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0")
    # These override local linking in Vercel CLI. The dialog's chosen project
    # and team must be authoritative rather than inherited CI settings.
    environment.pop("VERCEL_PROJECT_ID", None)
    environment.pop("VERCEL_ORG_ID", None)
    process = subprocess.Popen(arguments, cwd=str(root), env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    captured = [[], []]
    sizes = [0, 0]
    errors = []

    def collect(stream, index):
        try:
            while chunk := stream.read(4096):
                remaining = max(0, 65536 - sizes[index])
                if remaining:
                    captured[index].append(chunk[:remaining])
                    sizes[index] += min(remaining, len(chunk))
        except OSError as error:
            errors.append(str(error))
        finally:
            stream.close()

    readers = [threading.Thread(target=collect, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    try:
        while process.poll() is None:
            _cancelled(cancel)
            if time.monotonic() - started > timeout:
                raise RuntimeError("Vercel CLI timed out. Check its dashboard before retrying.")
            cancel.wait(.1)
    except BaseException:
        _terminate_process_tree(process)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=2)
    _cancelled(cancel)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("Vercel CLI output did not close. Check the dashboard before retrying.")
    stdout, stderr = (b"".join(parts).decode("utf-8", errors="replace") for parts in captured)
    # The deployment URL is stdout; diagnostic URLs from stderr are never
    # mistaken for a successful deployment URL.
    if stdout.strip():
        on_output(stdout.strip())
    if stderr.strip():
        on_output(stderr.strip())
    if process.returncode or errors:
        raise RuntimeError((stderr.strip() or stdout.strip() or "Vercel CLI failed.")[-4000:])
    return stdout


def deploy_project(folder, project, scope, domain, on_output, cancel):
    root, project, scope, domain = validate_project(folder, project, scope, domain)
    command = vercel_command()
    scoped = ["--scope", scope] if scope else []

    def run(step, arguments, timeout=900):
        _cancelled(cancel)
        on_output(step)
        return run_cli(command + arguments + scoped, root, on_output, cancel, timeout)

    try:
        run("Checking Vercel authentication…", ["whoami"], timeout=30)
    except DeploymentCancelled:
        raise
    except RuntimeError as error:
        raise RuntimeError(f"Vercel authentication failed. Run vercel login in a terminal, then retry.\n{error}") from error
    run(f"Linking this folder to Vercel project {project}…", ["link", "--yes", "--project", project])
    output = run("Uploading and building the production deployment…", ["deploy", "--prod", "--yes"])
    urls = re.findall(r"(?m)^\s*(https://[A-Za-z0-9.-]+\.vercel\.app)/?\s*$", output)
    if not urls:
        raise RuntimeError("The CLI exited successfully but did not return a deployment URL. Check the Vercel dashboard before retrying.")
    result = dict(url=urls[-1], domain=domain, domain_assigned=False, domain_error="")
    if domain:
        try:
            run(f"Adding {domain} to {project}…", ["domains", "add", domain, project], timeout=60)
            run(f"Assigning the deployment to {domain}…", ["alias", "set", result["url"], domain], timeout=60)
            result["domain_assigned"] = True
            run("Inspecting domain configuration and DNS…", ["domains", "inspect", domain], timeout=60)
        except DeploymentCancelled:
            on_output(f"Deployment already created: {result['url']}")
            raise
        except RuntimeError as error:
            result["domain_error"] = str(error)
    return result
