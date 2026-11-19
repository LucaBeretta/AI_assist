"""Checks shared by Vercel deployments: the project folder, names and cancellation.

Deployments themselves go through the Vercel REST API (vercel_api.py); no CLI,
Node.js or shell is involved.
"""

from pathlib import Path
import re


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


def check_cancelled(cancel):
    if cancel.is_set():
        raise DeploymentCancelled("Deployment stopped locally. A deployment already submitted to Vercel may continue; check the Vercel dashboard.")
