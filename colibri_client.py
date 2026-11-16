"""colibri engine support: locate the install, drive its ``coli`` launcher, and
talk to its OpenAI-compatible server.

colibri (https://github.com/JustVugg/colibri) serves one model at a time from
``coli serve``/``coli start``. AI Assist never imports colibri's code: it runs
the user's own ``coli`` launcher for setup, status, start and stop, and speaks
HTTP to ``/v1/models`` and ``/v1/chat/completions`` for answers.

Chats and agent history stay in Ollama's native message format. This module
translates at the boundary: Ollama tool calls carry a ``tool_name`` and object
arguments, OpenAI tool calls carry an ``id`` and JSON-string arguments, and a
tool result must name the ``tool_call_id`` it answers.
"""

import glob
import http.client
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_HOST = "http://127.0.0.1:8000"
GITHUB_URL = "https://github.com/JustVugg/colibri"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_JSON_OUTPUT = 8 * 1024 * 1024
# Families whose colibri gateway renders and parses tool calls
# (colibri docs/SETTINGS.md and family_registry capabilities.tools).
TOOL_FAMILIES = {"glm", "deepseek_v4", "kimi", "qwen36", "qwen38", "mimo"}


class ColibriError(RuntimeError):
    pass


class ColibriCancelled(ColibriError):
    pass


# ---------------------------------------------------------------- locating the install


def settings_path():
    """Where AI Assist remembers the colibri folder, beside the saved chats."""
    override = os.environ.get("AI_ASSIST_DATA_DIR")
    local_data = os.environ.get("LOCALAPPDATA")
    if override:
        # Inside the chats folder, but as a dot-folder: ChatStore lists *.json files only.
        return Path(override).expanduser() / ".settings" / "colibri.json"
    if local_data:
        return Path(local_data) / "AI Assist" / "settings" / "colibri.json"
    return Path.home() / ".aiassist" / "settings" / "colibri.json"


def coli_path(directory):
    """The ``coli`` launcher inside a colibri checkout or release folder, or None."""
    if not directory:
        return None
    for candidate in (Path(directory) / "c" / "coli", Path(directory) / "coli"):
        if candidate.is_file():
            return candidate
    return None


def _load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_setting(key):
    value = _load_settings().get(key)
    return value if isinstance(value, str) and value else None


def save_setting(key, value):
    """Atomically update one remembered setting (the colibri folder, the last engine)."""
    data = _load_settings()
    data[key] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_saved_directory():
    return load_setting("directory")


def save_directory(directory):
    """Remember a colibri folder the user chose. Raises ColibriError if it isn't one."""
    directory = str(Path(directory).expanduser().resolve())
    if coli_path(directory) is None:
        raise ColibriError(f"{directory} does not look like a colibri folder: c/coli is missing.")
    try:
        save_setting("directory", directory)
    except OSError as error:
        raise ColibriError(f"Could not remember the colibri folder: {error}") from error
    return directory


def _candidate_directories():
    home = Path.home()
    here = Path(__file__).resolve().parent
    yield os.environ.get("COLIBRI_DIR")
    yield load_saved_directory()
    for base in (here.parent, home, home / "Documents", home / "Desktop", home / "Downloads", home / "Projects"):
        # A clone is "colibri"; GitHub's Download ZIP extracts "colibri-main",
        # sometimes with a second "colibri-main" inside it.
        for match in sorted(glob.glob(str(base / "colibri*"))):
            yield match
            yield from sorted(glob.glob(str(Path(match) / "colibri*")))


def find_directory():
    """The first colibri folder found: $COLIBRI_DIR, the saved choice, then usual places."""
    for directory in _candidate_directories():
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
    family = None
    config_path = report.get("config_path")
    if isinstance(config_path, str) and config_path:
        try:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            family = config.get("family") if isinstance(config, dict) else None
        except (OSError, ValueError):
            family = None
    report["family"] = family if isinstance(family, str) else None
    return report


def server_state(report):
    server = report.get("server") if isinstance(report, dict) else None
    return server.get("state", "stopped") if isinstance(server, dict) else "not configured"


def server_host(report):
    """The server's base URL (no /v1): COLIBRI_HOST wins, then colibri's own status."""
    configured = os.environ.get("COLIBRI_HOST", "").strip()
    if configured:
        return normalize_host(configured)
    server = report.get("server") if isinstance(report, dict) else None
    urls = server.get("urls") if isinstance(server, dict) else None
    base = urls.get("openai_base_url") if isinstance(urls, dict) else None
    if isinstance(base, str) and base:
        return normalize_host(base)
    return DEFAULT_HOST


def normalize_host(host):
    host = host.strip()
    if "://" not in host:
        host = "http://" + host
    parsed = urllib.parse.urlsplit(host)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ColibriError("COLIBRI_HOST must be an HTTP address such as http://127.0.0.1:8000.")
    if parsed.hostname in ("0.0.0.0", "::"):
        parsed = parsed._replace(netloc=f"127.0.0.1:{parsed.port or 8000}")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


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


def install_model(directory, model_id, on_progress, cancel_event, poll_seconds=2.0):
    """``coli setup --model ID --yes --json --no-browser``: download (resumable),
    build or fetch the engine, configure, and start the server in the background.

    Cancelling stops the whole process tree; the next run resumes the download."""
    if not isinstance(model_id, str) or not model_id or model_id.startswith("-"):
        raise ColibriError("Choose a colibri model to install.")
    command = coli_command(directory, "setup", "--model", model_id, "--yes", "--json", "--no-browser")
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
                    phase, done, total, _ = install_progress(status(directory))
                    on_progress(phase, done, total)
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


# ---------------------------------------------------------------- HTTP


def _request(host, path, payload=None, timeout=30):
    url = normalize_host(host) + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    api_key = os.environ.get("COLI_API_KEY")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        try:
            detail = error.read(4000).decode("utf-8", errors="replace")
        finally:
            error.close()
        try:
            parsed = json.loads(detail)
            message = parsed.get("error", {}).get("message") if isinstance(parsed.get("error"), dict) else parsed.get("error")
            detail = message or detail
        except (ValueError, AttributeError):
            pass
        raise ColibriError(f"colibri returned HTTP {error.code}: {str(detail)[:600]}") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise ColibriError(f"Could not reach colibri at {normalize_host(host)}. Start it, then click Refresh.") from error
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ColibriError("colibri returned too much output.")
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ColibriError("colibri returned an invalid response.") from error
    if not isinstance(result, dict):
        raise ColibriError("colibri returned an invalid response.")
    return result


def list_models(host):
    """Model ids the running server answers to (colibri serves one at a time)."""
    result = _request(host, "/v1/models", timeout=10)
    data = result.get("data")
    if not isinstance(data, list):
        raise ColibriError("colibri returned an invalid model list.")
    return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]


def model_card(host, model):
    result = _request(host, "/v1/models", timeout=10)
    for item in result.get("data") or []:
        if isinstance(item, dict) and item.get("id") == model:
            return item
    return {}


def capabilities(host, model, family=None):
    """Capabilities in the shape developer_tools.fetch_model_capabilities returns."""
    card = model_card(host, model)
    advertised = [item for item in card.get("capabilities", []) if isinstance(item, str)]
    capabilities_list = []
    if "chat" in advertised or not advertised:
        capabilities_list.append("completion")
    if family in TOOL_FAMILIES:
        capabilities_list.append("tools")
    if "image" in (card.get("input_modalities") or []):
        capabilities_list.append("vision")
    if "image_generation" in advertised:
        capabilities_list.append("image_generation")
    context = card.get("context_length")
    return {
        "name": model,
        "capabilities": capabilities_list,
        "context_length": context if isinstance(context, int) and not isinstance(context, bool) and context > 0 else None,
        "family": family,
        "parameter_size": None,
        "quantization": None,
        "engine": "colibri",
    }


def request_chat(host, messages, model, max_tokens=None, timeout=1800):
    """A plain chat turn; returns the answer text."""
    payload = {"model": model, "messages": to_openai_messages(messages), "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    result = _request(host, "/v1/chat/completions", payload, timeout=timeout)
    message = from_openai_response(result)["message"]
    content = message.get("content", "").strip()
    if not content:
        raise ColibriError("colibri's response did not contain a message.")
    return content


def agent_chat(host, model, messages, tools, max_tokens, cancel_event=None, timeout=1800):
    """One agent step. Takes and returns Ollama-native messages ({"message": ...})."""
    payload = {"model": model, "messages": to_openai_messages(messages), "tools": tools, "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    completed = queue.Queue(maxsize=1)

    def worker():
        try:
            completed.put((_request(host, "/v1/chat/completions", payload, timeout=timeout), None))
        except ColibriError as error:
            completed.put((None, error))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise ColibriCancelled("The coding agent was stopped.")
        try:
            result, error = completed.get(timeout=0.1)
        except queue.Empty:
            continue
        if error is not None:
            raise error
        return from_openai_response(result)


# ---------------------------------------------------------------- format translation


def to_openai_messages(messages):
    """Ollama-native history -> OpenAI chat messages.

    Each assistant tool call gets an id; the tool results that follow it are
    matched to those ids by tool name, then in order. A tool result whose call
    was trimmed from the history is passed on as a user note instead, because
    an OpenAI tool message must answer a call it can name."""
    converted = []
    pending = []
    serial = 0
    for message in messages:
        role = message.get("role")
        if role == "tool":
            name = message.get("tool_name") or "tool"
            match = next((item for item in pending if item[1] == name), pending[0] if pending else None)
            if match is None:
                converted.append({"role": "user", "content": f"Tool result ({name}):\n{message.get('content', '')}"})
                continue
            pending.remove(match)
            converted.append({"role": "tool", "tool_call_id": match[0], "content": message.get("content", "")})
            continue
        pending = []
        item = {"role": role if role in ("system", "user", "assistant") else "user",
                "content": message.get("content", "") or ""}
        if role == "assistant":
            if isinstance(message.get("thinking"), str) and message["thinking"]:
                item["reasoning_content"] = message["thinking"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                    continue
                serial += 1
                call_id = call.get("id") if isinstance(call.get("id"), str) and call.get("id") else f"call_{serial}"
                arguments = function.get("arguments", {})
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
                calls.append({"id": call_id, "type": "function",
                              "function": {"name": function["name"], "arguments": arguments}})
                pending.append((call_id, function["name"]))
            if calls:
                item["tool_calls"] = calls
        converted.append(item)
    return converted


def from_openai_response(result):
    """OpenAI chat completion -> Ollama-native {"message": {...}}.

    Arguments stay as the JSON string colibri sent; coding_agent.run_agent
    parses strings and reports malformed JSON to the model as a tool error."""
    choices = result.get("choices") if isinstance(result, dict) else None
    if isinstance(result, dict) and result.get("error"):
        error = result["error"]
        raise ColibriError(str(error.get("message") if isinstance(error, dict) else error)[:600])
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ColibriError("colibri returned no answer.")
    source = choices[0].get("message")
    if not isinstance(source, dict):
        raise ColibriError("colibri returned an unreadable message.")
    content = source.get("content")
    message = {"role": "assistant", "content": content if isinstance(content, str) else ""}
    reasoning = source.get("reasoning_content") or source.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        message["thinking"] = reasoning
    calls = []
    for call in source.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ColibriError("colibri returned an invalid tool call.")
        arguments = function.get("arguments", "{}")
        calls.append({"function": {"name": function["name"],
                                   "arguments": arguments if isinstance(arguments, (str, dict)) else {}}})
    if calls:
        message["tool_calls"] = calls
    return {"message": message, "done_reason": choices[0].get("finish_reason")}
