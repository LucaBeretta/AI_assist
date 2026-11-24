"""Open OpenCode (opencode.ai, MIT) in a project folder, locked to this computer's local models.

OpenCode is a separate coding agent. AI Assist does not install it; when it is installed,
AI Assist starts it in its own terminal window with private settings that:

- offer only the Ollama models on this computer that can chat (no cloud models, not even
  Ollama's own cloud aliases), under AI Assist's own provider name;
- turn off update checks, the online model catalog, session sharing, free cloud models and
  language-server downloads;
- ask before every file edit and every shell command, and refuse web fetches and web search;
- ignore the project folder's own OpenCode settings, which could otherwise allow everything,
  point "ollama" at another server or add plugins.

OpenCode 2 normally joins one shared background service per user, which reads settings only
when it starts. AI Assist therefore starts OpenCode 2 with ``--standalone``: a private server
that gets these settings and ends with the window.

AI Assist's internet gate cannot see inside OpenCode (it is not a Python program), which is
why these settings are fixed here rather than left to OpenCode's defaults. The settings use
OpenCode's V1 keys, which V2 still reads (docs: migrate-v1); V2 also gets them through
OPENCODE_CONFIG_CONTENT, the layer that loads after every project file.
"""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import network_gate

CONFIG_NAME = "opencode.json"
PROVIDER = "aiassist"  # not "ollama": OpenCode 2 fills "ollama" with every model Ollama lists, cloud ones included
# OpenCode reads these at start; each one stops something it would otherwise do on its own.
QUIET_ENVIRONMENT = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_FREE_MODELS": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
}
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
_SCRIPT_MAX_AGE = 3600


class OpenCodeError(RuntimeError):
    """OpenCode could not be opened; the message says what to do."""


def _candidates(home, environ):
    """Where OpenCode's installers put it, for when AI Assist starts without the shell's PATH."""
    if os.name == "nt":
        appdata = environ.get("APPDATA")
        return [home / ".opencode" / "bin" / "opencode.exe", home / ".bun" / "bin" / "opencode.exe",
                *([Path(appdata) / "npm" / "opencode.cmd"] if appdata else [])]
    return [home / ".opencode" / "bin" / "opencode", Path("/opt/homebrew/bin/opencode"),
            Path("/usr/local/bin/opencode"), home / ".bun" / "bin" / "opencode",
            home / ".npm-global" / "bin" / "opencode", home / ".local" / "bin" / "opencode"]


def find_opencode(environ=None, home=None):
    """The OpenCode program, or None. Looks on PATH, then where its installers put it."""
    environ = os.environ if environ is None else environ
    home = Path(home) if home is not None else Path.home()
    found = shutil.which("opencode", path=environ.get("PATH"))
    if found:
        return found
    for candidate in _candidates(home, environ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def major_version(program, run=subprocess.run):
    """OpenCode's major version (1 or 2), or None when it cannot be read."""
    try:
        result = run([program, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                     timeout=20, **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}))
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\b(\d+)\.\d+\.\d+", result.stdout or "")
    return int(match.group(1)) if match else None


def ollama_v1(url):
    """Ollama's OpenAI-compatible address (``.../v1``), which OpenCode talks to."""
    base = (url or "http://127.0.0.1:11434").rstrip("/")
    # "localhost" can mean ::1 first on macOS, while Ollama listens on 127.0.0.1.
    base = base.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
    if base.endswith("://localhost"):
        base = base[:-len("localhost")] + "127.0.0.1"
    return base if base.endswith("/v1") else base + "/v1"


def _show(base, model):
    request = urllib.request.Request(base.rstrip("/") + "/api/show", data=json.dumps({"model": model}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read(4 * 1024 * 1024))
    return data if isinstance(data, dict) else {}


def chat_models(ollama_url, names, show=None):
    """The installed models OpenCode may use: plain names, able to chat, and running on this computer."""
    show = show or _show
    base = ollama_v1(ollama_url)[:-len("/v1")]
    usable = []
    for name in sorted(set(names)):
        if not isinstance(name, str) or not _MODEL.fullmatch(name) or name.lower().endswith(("-cloud", ":cloud")):
            continue
        try:
            info = show(base, name)
        except (OSError, ValueError, urllib.error.URLError):
            continue  # an unknown model is left out, never guessed
        capabilities = info.get("capabilities")
        if info.get("remote_host") or info.get("remote_model"):
            continue  # a local alias of a cloud model
        if isinstance(capabilities, list) and "completion" not in capabilities:
            continue  # embedding-only models cannot chat
        usable.append(name)
    return usable


def locked_config(ollama_url, models, default_model=None):
    """OpenCode settings that keep it on this computer's Ollama and make it ask before acting."""
    names = sorted({name for name in models if isinstance(name, str) and _MODEL.fullmatch(name)
                    and not name.lower().endswith(("-cloud", ":cloud"))})
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {PROVIDER: {"npm": "@ai-sdk/openai-compatible", "name": "Ollama on this computer (AI Assist)",
                                "options": {"baseURL": ollama_v1(ollama_url)},
                                "models": {name: {"name": name} for name in names}}},
        "enabled_providers": [PROVIDER],
        "autoupdate": False,
        "share": "disabled",
        "permission": {"edit": "ask", "bash": "ask", "webfetch": "deny", "websearch": "deny"},
    }
    if default_model in names:
        config["model"] = f"{PROVIDER}/{default_model}"
    elif names:
        config["model"] = f"{PROVIDER}/{names[0]}"
    return config


def write_config(directory, config):
    """Save the settings in AI Assist's private data folder and return the file."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / CONFIG_NAME
    if path.is_symlink():
        raise OpenCodeError("OpenCode's settings file must not be a link.")
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _settings(config_path):
    values = dict(QUIET_ENVIRONMENT)
    values["OPENCODE_CONFIG"] = str(config_path)
    # Loaded after every project file (OpenCode 2), so a project cannot loosen these settings.
    values["OPENCODE_CONFIG_CONTENT"] = Path(config_path).read_text(encoding="utf-8")
    return values


def environment(config_path, base=None):
    env = dict(os.environ if base is None else base)
    env.update(_settings(config_path))
    # Windows: cmd.exe (which runs npm's opencode.cmd) would otherwise look for programs in the project folder first.
    env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return env


def program_arguments(program, major):
    """OpenCode 2 gets a private server (--standalone) so the settings apply; OpenCode 1 has no shared one."""
    return [program] if major == 1 else [program, "--standalone"]


def _unix_script(arguments, project, config_path):
    """A small script a terminal runs: settings, folder, then OpenCode. Every value is quoted."""
    lines = ["#!/bin/sh", 'rm -f -- "$0"']  # used once
    lines += [f"export {key}={shlex.quote(value)}" for key, value in _settings(config_path).items()]
    lines += [f"cd {shlex.quote(str(project))} || exit 1",
              "exec " + " ".join(shlex.quote(part) for part in arguments)]
    return "\n".join(lines) + "\n"


LINUX_TERMINALS = (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                   ("xfce4-terminal", ["-x"]), ("kitty", []), ("alacritty", ["-e"]), ("xterm", ["-e"]))


def _new_script(folder, suffix):
    """A fresh start script per click, so a terminal that opens late still runs its own folder."""
    for old in folder.glob("open-opencode-*"):
        try:
            if time.time() - old.stat().st_mtime > _SCRIPT_MAX_AGE and not old.is_symlink():
                old.unlink()
        except OSError:
            pass
    return folder / f"open-opencode-{uuid.uuid4().hex}{suffix}"


def launch_command(arguments, project, config_path, data_directory, platform=None, which=shutil.which):
    """How to open OpenCode in a new terminal window on this system: (command, Popen options)."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        # A new console window. OpenCode draws its screen there, so its input and output stay attached.
        return list(arguments), {"cwd": str(project), "env": environment(config_path, os.environ),
                                 "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)}
    folder = Path(data_directory)
    script = _new_script(folder, ".command" if platform == "darwin" else ".sh")
    script.write_text(_unix_script(arguments, project, config_path), encoding="utf-8")
    script.chmod(0o700)
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if platform == "darwin":
        return ["open", "-a", "Terminal", str(script)], quiet
    for terminal, flag in LINUX_TERMINALS:
        if which(terminal):
            return [terminal, *flag, str(script)], quiet
    script.unlink(missing_ok=True)
    raise OpenCodeError("No terminal program was found. Run OpenCode from a terminal in your project folder instead.")


def open_project(project, data_directory, ollama_url, models, default_model=None, program=None, major=None):
    """Start OpenCode in ``project`` with the locked settings, in its own window. Returns the program used."""
    program = program or find_opencode()
    if not program:
        raise OpenCodeError("OpenCode is not installed on this computer. Install it from opencode.ai, then try again.")
    project = Path(project)
    if not project.is_dir():
        raise OpenCodeError("Choose a project folder first.")
    folder = Path(data_directory) / "opencode"
    config_path = write_config(folder, locked_config(ollama_url, models, default_model))
    arguments = program_arguments(program, major if major is not None else major_version(program))
    command, options = launch_command(arguments, project, config_path, folder)
    network_gate.note_outside("OpenCode", "Open your project in OpenCode",
                              "OpenCode runs outside AI Assist's internet gate, with local models only.")
    try:
        subprocess.Popen(command, **options)
    except OSError as error:
        raise OpenCodeError(f"OpenCode could not be started: {error}") from error
    return program
