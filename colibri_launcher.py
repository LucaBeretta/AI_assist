"""colibri's own launcher, driven from AI Assist: find the colibri folder, read
its recommendations for this PC, install a model, change its context, and start
or stop its server.

AI Assist never imports colibri's code. It runs the user's own ``coli`` script
(``coli status|setup|start|stop --json``); answers come from colibri's HTTP
server through colibri_backend.ColibriClient.
"""

import glob
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.parse


GITHUB_URL = "https://github.com/JustVugg/colibri"
_MAX_JSON_OUTPUT = 8 * 1024 * 1024
# Families whose colibri gateway renders and parses tool calls
# (colibri docs/SETTINGS.md and family_registry capabilities.tools).
TOOL_FAMILIES = {"glm", "deepseek_v4", "kimi", "qwen36", "qwen38", "mimo"}


class ColibriError(RuntimeError):
    pass


class ColibriCancelled(ColibriError):
    pass


# ---------------------------------------------------------------- locating the install


def coli_path(directory):
    """The ``coli`` launcher inside a colibri checkout or release folder, or None."""
    if not directory:
        return None
    for candidate in (Path(directory) / "c" / "coli", Path(directory) / "coli"):
        if candidate.is_file():
            return candidate
    return None


def load_settings(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_directory(directory, settings_path=None):
    """Check a colibri folder the user chose and remember it in ``settings_path``."""
    directory = str(Path(directory).expanduser().resolve())
    if coli_path(directory) is None:
        raise ColibriError(f"{directory} does not look like a colibri folder: c/coli is missing.")
    if settings_path is not None:
        data = load_settings(settings_path)
        data["directory"] = directory
        path = Path(settings_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            raise ColibriError(f"Could not remember the colibri folder: {error}") from error
    return directory


def _candidate_directories(saved=None):
    home = Path.home()
    here = Path(__file__).resolve().parent
    yield os.environ.get("COLIBRI_DIR")
    yield saved
    for base in (here.parent, home, home / "Documents", home / "Desktop", home / "Downloads", home / "Projects"):
        # A clone is "colibri"; GitHub's Download ZIP extracts "colibri-main",
        # sometimes with a second "colibri-main" inside it.
        for match in sorted(glob.glob(str(base / "colibri*"))):
            yield match
            yield from sorted(glob.glob(str(Path(match) / "colibri*")))


def find_directory(saved=None):
    """The first colibri folder found: $COLIBRI_DIR, the saved choice, then usual places."""
    for directory in _candidate_directories(saved):
        if directory and coli_path(directory) is not None:
            return str(Path(directory).resolve())
    return None


# ---------------------------------------------------------------- the coli launcher


def coli_command(directory, *arguments):
    launcher = coli_path(directory)
    if launcher is None:
        raise ColibriError("colibri was not found. Install it, then choose its folder in AI Assist.")
    # coli is a Python script without an extension (coli.cmd on Windows only finds
    # a Python and runs it), so the app's own interpreter runs it everywhere.
    return [sys.executable, str(launcher), *arguments]


def _process_options():
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def _environment():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("NO_COLOR", "1")
    return env


def parse_json_output(text):
    """The JSON document in a coli --json run's stdout, skipping any banner lines."""
    if not isinstance(text, str) or len(text) > _MAX_JSON_OUTPUT:
        raise ColibriError("colibri returned unreadable output.")
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character in "{[" and (index == 0 or text[index - 1] == "\n"):
            try:
                value, _ = decoder.raw_decode(text, index)
                return value
            except ValueError:
                continue
    raise ColibriError("colibri did not return JSON. Update colibri (git pull) and try again.")


def run_coli(directory, arguments, timeout=60):
    """Run ``coli <arguments>`` to completion; return (exit code, stdout, stderr)."""
    command = coli_command(directory, *arguments)
    try:
        completed = subprocess.run(command, cwd=str(Path(command[1]).parent), stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=timeout, env=_environment(),
                                   encoding="utf-8", errors="replace", check=False, **_process_options())
    except subprocess.TimeoutExpired as error:
        raise ColibriError(f"colibri did not answer within {timeout} seconds.") from error
    except OSError as error:
        raise ColibriError(f"Could not run colibri: {error}") from error
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def run_coli_json(directory, arguments, timeout=60):
    code, stdout, stderr = run_coli(directory, arguments, timeout)
    try:
        return parse_json_output(stdout)
    except ColibriError:
        detail = (stderr.strip() or stdout.strip())[-600:]
        raise ColibriError(f"colibri {' '.join(arguments[:2])} failed (exit {code}): {detail or 'no output'}") from None


def status(directory):
    """``coli status --json``, plus the configured family from colibri's setup file."""
    report = run_coli_json(directory, ["status", "--json"], timeout=30)
    if not isinstance(report, dict):
        raise ColibriError("colibri returned an invalid status report.")
    config = {}
    config_path = report.get("config_path")
    if isinstance(config_path, str) and config_path:
        try:
            loaded = json.loads(Path(config_path).read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            config = {}
    family, ctx = config.get("family"), config.get("ctx")
    report["family"] = family if isinstance(family, str) else None
    # The context the model was set up with; None or 0 means colibri chooses it.
    report["ctx"] = ctx if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0 else None
    return report


def server_state(report):
    server = report.get("server") if isinstance(report, dict) else None
    return server.get("state", "stopped") if isinstance(server, dict) else "not configured"


def tokens_per_second(report):
    """colibri's measured speed on its last answer (prompt time included), or None."""
    server = report.get("server") if isinstance(report, dict) else None
    value = server.get("tokens_per_second") if isinstance(server, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def speed_text(report):
    speed = tokens_per_second(report)
    if speed is None:
        return ""
    return f"~{speed:.1f} tok/s on the last answer" if speed < 10 else f"~{speed:.0f} tok/s on the last answer"


def server_url(report):
    """colibri's OpenAI base URL from its status (``http://host:port/v1``), or None."""
    server = report.get("server") if isinstance(report, dict) else None
    urls = server.get("urls") if isinstance(server, dict) else None
    base = urls.get("openai_base_url") if isinstance(urls, dict) else None
    if not isinstance(base, str) or not base:
        return None
    parsed = urllib.parse.urlsplit(base if "://" in base else "http://" + base)
    if parsed.hostname in ("0.0.0.0", "::"):
        parsed = parsed._replace(netloc=f"127.0.0.1:{parsed.port or 8000}")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme or "http", parsed.netloc, path if path.endswith("/v1") else path + "/v1", "", ""))


def is_local_url(url):
    try:
        return urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def start_server(directory, progress=lambda text: None, timeout=1800, cancel_event=None):
    """Start the configured model in the background and wait until it answers."""
    report = status(directory)
    if not report.get("configured") or not (report.get("setup") or {}).get("ready"):
        raise ColibriError("colibri has no model set up yet. Open Find & download models > colibri engine.")
    if server_state(report) == "stopped":
        progress("Starting colibri...")
        code, stdout, stderr = run_coli(directory, ["start", "--json", "--no-browser"], timeout=120)
        if code != 0:
            raise ColibriError("colibri could not start: " + (stderr.strip() or stdout.strip())[-600:])
    started = time.monotonic()
    name = ((report.get("setup") or {}).get("model") or {}).get("name") or "the model"
    while time.monotonic() - started < timeout:
        if cancel_event is not None and cancel_event.is_set():
            raise ColibriCancelled("Stopped waiting for colibri.")
        report = status(directory)
        state = server_state(report)
        if state == "ready":
            return report
        if state == "stopped":
            raise ColibriError("colibri stopped while loading. `coli logs -n 50` in its folder shows why.")
        progress(f"colibri is loading {name} ({int(time.monotonic() - started)}s)...")
        time.sleep(2)
    raise ColibriError("colibri is still loading. Check `coli status` and click Refresh.")


def stop_server(directory):
    code, stdout, stderr = run_coli(directory, ["stop"], timeout=90)
    if code != 0:
        raise ColibriError("colibri could not stop: " + (stderr.strip() or stdout.strip())[-600:])


def recommendations(directory):
    """``coli setup --list --json``: every catalog model assessed against this machine."""
    rows = run_coli_json(directory, ["setup", "--list", "--json"], timeout=120)
    if not isinstance(rows, list):
        raise ColibriError("colibri returned an invalid model list.")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        number = lambda key: row.get(key) if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool) else None
        text = lambda key: row.get(key) if isinstance(row.get(key), str) else ""
        cleaned.append({
            "id": row["id"], "name": text("name") or row["id"], "family": text("family"),
            "download_gb": number("download_gb"), "ram_min_gb": number("ram_min_gb"),
            "ram_good_gb": number("ram_good_gb"), "dense_gb": number("dense_gb"),
            "kind": text("kind") or "text", "runs": text("runs"), "summary": text("summary"),
            "status": text("status"), "fits": row.get("fits") is True, "reason": text("reason"),
            "recommended": row.get("recommended") is True, "license_note": text("license_note"),
            "doc": text("doc"), "tools": text("family") in TOOL_FAMILIES,
        })
    return cleaned


def install_progress(report):
    """(phase, done bytes, total bytes, running) from a status report's install state."""
    install = report.get("install") if isinstance(report, dict) else None
    if not isinstance(install, dict):
        return None, None, None, False
    number = lambda key: install.get(key) if isinstance(install.get(key), (int, float)) else None
    return install.get("phase"), number("done"), number("total"), install.get("running") is True


def transfer_metrics(report):
    """Validated download bytes/second and estimated seconds remaining, if supplied."""
    install = report.get("install") if isinstance(report, dict) else None
    if not isinstance(install, dict):
        return None, None
    def number(key):
        value = install.get(key)
        return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0 else None)
    return number("rate"), number("eta_seconds")


CONTEXT_CHOICES = (0, 4096, 8192, 16384, 32768, 65536, 131072)


def _check_ctx(ctx):
    if ctx is not None and (isinstance(ctx, bool) or not isinstance(ctx, int) or ctx < 0 or ctx > 1048576):
        raise ColibriError("Choose a context between 0 (automatic) and 1,048,576 tokens.")


def set_context(directory, ctx):
    """Change the configured model's context (0 = automatic) and restart it with it.

    colibri keeps a running server as it is, so it is stopped first; setup then
    saves the context and starts the server again in the background."""
    _check_ctx(ctx)
    if ctx is None:
        raise ColibriError("Choose a context.")
    stop_server(directory)
    code, stdout, stderr = run_coli(directory, ["setup", "--ctx", str(ctx), "--yes", "--json", "--no-browser"], timeout=600)
    if code != 0:
        raise ColibriError("colibri could not change the context: " + (stderr.strip() or stdout.strip())[-600:])
    return status(directory)


def install_model(directory, model_id, on_progress, cancel_event, poll_seconds=2.0, ctx=None):
    """``coli setup --model ID --yes --json --no-browser``: download (resumable),
    build or fetch the engine, configure, and start the server in the background.

    Cancelling stops the whole process tree; the next run resumes the download."""
    if not isinstance(model_id, str) or not model_id or model_id.startswith("-"):
        raise ColibriError("Choose a colibri model to install.")
    _check_ctx(ctx)
    extra = ["--ctx", str(ctx)] if ctx is not None else []
    command = coli_command(directory, "setup", "--model", model_id, "--yes", "--json", "--no-browser", *extra)
    try:
        process = subprocess.Popen(command, cwd=str(Path(command[1]).parent), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_environment(),
                                   encoding="utf-8", errors="replace", **_process_options())
    except OSError as error:
        raise ColibriError(f"Could not run colibri setup: {error}") from error
    output = {"stdout": "", "stderr": ""}

    def collect(stream, key):
        output[key] = stream.read(_MAX_JSON_OUTPUT)
        stream.read()

    readers = [threading.Thread(target=collect, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=collect, args=(process.stderr, "stderr"), daemon=True)]
    for reader in readers:
        reader.start()
    last_poll = 0.0
    try:
        while process.poll() is None:
            if cancel_event.is_set():
                terminate_tree(process)
                raise ColibriCancelled("colibri setup cancelled. Run it again to resume the download.")
            if time.monotonic() - last_poll >= poll_seconds:
                last_poll = time.monotonic()
                try:
                    report = status(directory)
                    phase, done, total, _ = install_progress(report)
                    on_progress(phase, done, total, *transfer_metrics(report))
                except ColibriError:
                    pass
            time.sleep(0.2)
    finally:
        for reader in readers:
            reader.join(timeout=5)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
    if process.returncode != 0:
        detail = (output["stderr"].strip() or output["stdout"].strip())[-800:]
        raise ColibriError(f"colibri setup stopped: {detail or 'exit code ' + str(process.returncode)}")
    try:
        return parse_json_output(output["stdout"])
    except ColibriError:
        return {}


def terminate_tree(process):
    """Stop a process and its children (download workers, compilers)."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
