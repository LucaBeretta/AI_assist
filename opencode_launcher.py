"""Open OpenCode (opencode.ai, MIT) in a project folder, locked to this computer's local models.

OpenCode is a separate coding agent. AI Assist does not install it; when it is installed,
AI Assist starts it in its own terminal window with a private configuration that:

- offers only Ollama on this computer (other providers, including OpenCode's free cloud
  models, are not enabled);
- turns off update checks, the online model catalog, session sharing and language-server
  downloads;
- asks before every file edit and every shell command, and refuses web fetches.

AI Assist's internet gate cannot see inside OpenCode (it is not a Python program), which
is why these settings are fixed here rather than left to OpenCode's defaults. The keys are
OpenCode's V1 configuration, which V2 still reads (docs: migrate-v1, config).
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import network_gate

CONFIG_NAME = "opencode.json"
# OpenCode reads these at start; each one stops a request it would otherwise make on its own.
QUIET_ENVIRONMENT = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_FREE_MODELS": "1",
}


class OpenCodeError(RuntimeError):
    """OpenCode could not be opened; the message says what to do."""


def find_opencode(environ=None, home=None):
    """The OpenCode program, or None. Looks on PATH, then in the official installer's folder."""
    environ = os.environ if environ is None else environ
    home = Path(home) if home is not None else Path.home()
    found = shutil.which("opencode", path=environ.get("PATH"))
    if found:
        return found
    name = "opencode.exe" if os.name == "nt" else "opencode"
    for candidate in (home / ".opencode" / "bin" / name,):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def ollama_v1(url):
    """Ollama's OpenAI-compatible address (``.../v1``), which OpenCode talks to."""
    base = (url or "http://127.0.0.1:11434").rstrip("/")
    # "localhost" can mean ::1 first on macOS, while Ollama listens on 127.0.0.1.
    base = base.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
    if base.endswith("://localhost"):
        base = base[:-len("localhost")] + "127.0.0.1"
    return base if base.endswith("/v1") else base + "/v1"


def locked_config(ollama_url, models, default_model=None):
    """OpenCode settings that keep it on this computer's Ollama and make it ask before acting."""
    names = sorted({name for name in models if isinstance(name, str) and name
                    and not name.endswith(("-cloud", ":cloud"))})
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama on this computer",
                                "options": {"baseURL": ollama_v1(ollama_url)},
                                "models": {name: {"name": name} for name in names}}},
        "enabled_providers": ["ollama"],
        "autoupdate": False,
        "share": "disabled",
        "permission": {"edit": "ask", "bash": "ask", "webfetch": "deny"},
    }
    if default_model in names:
        config["model"] = "ollama/" + default_model
    elif names:
        config["model"] = "ollama/" + names[0]
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


def environment(config_path, base=None):
    env = dict(os.environ if base is None else base)
    env.update(QUIET_ENVIRONMENT)
    env["OPENCODE_CONFIG"] = str(config_path)
    return env


def _unix_script(program, project, config_path):
    """A small script a terminal runs: settings, folder, then OpenCode. Every value is quoted."""
    lines = ["#!/bin/sh"]
    lines += [f"export {key}={shlex.quote(value)}" for key, value in QUIET_ENVIRONMENT.items()]
    lines += [f"export OPENCODE_CONFIG={shlex.quote(str(config_path))}",
              f"cd {shlex.quote(str(project))} || exit 1",
              f"exec {shlex.quote(program)}"]
    return "\n".join(lines) + "\n"


LINUX_TERMINALS = (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                   ("xfce4-terminal", ["-x"]), ("kitty", []), ("alacritty", ["-e"]), ("xterm", ["-e"]))


def launch_command(program, project, config_path, data_directory, platform=None, which=shutil.which):
    """How to open OpenCode in a new terminal window on this system: (command, options)."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        # A new console window; the folder and settings go straight to the process, nothing to quote.
        return [program], {"cwd": str(project), "env": environment(config_path),
                           "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)}
    script = Path(data_directory) / ("open-opencode.command" if platform == "darwin" else "open-opencode.sh")
    if script.is_symlink():
        raise OpenCodeError("OpenCode's start script must not be a link.")
    script.write_text(_unix_script(program, project, config_path), encoding="utf-8")
    script.chmod(0o700)
    if platform == "darwin":
        return ["open", "-a", "Terminal", str(script)], {}
    for terminal, flag in LINUX_TERMINALS:
        if which(terminal):
            return [terminal, *flag, str(script)], {}
    raise OpenCodeError("No terminal program was found. Run OpenCode from a terminal in your project folder instead.")


def open_project(project, data_directory, ollama_url, models, default_model=None, program=None):
    """Start OpenCode in ``project`` with the locked settings, in its own window. Returns the program used."""
    program = program or find_opencode()
    if not program:
        raise OpenCodeError("OpenCode is not installed on this computer. Install it from opencode.ai, then try again.")
    project = Path(project)
    if not project.is_dir():
        raise OpenCodeError("Choose a project folder first.")
    folder = Path(data_directory) / "opencode"
    config_path = write_config(folder, locked_config(ollama_url, models, default_model))
    command, options = launch_command(program, project, config_path, folder)
    network_gate.note_outside("OpenCode", "Open your project in OpenCode",
                              "OpenCode runs outside AI Assist's internet gate, with local models only.")
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    except OSError as error:
        raise OpenCodeError(f"OpenCode could not be started: {error}") from error
    return program
