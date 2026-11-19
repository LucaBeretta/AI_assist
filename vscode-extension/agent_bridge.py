import json
import os
from pathlib import Path
import secrets
import sys
import threading
import urllib.parse

ROOT = Path(os.environ.get("AI_ASSIST_REPOSITORY", Path(__file__).resolve().parent.parent)).resolve()
sys.path.insert(0, str(ROOT))

import network_gate
from app_core import ollama, privacy
from coding_agent import AgentCancelled, run_agent

MAX_LINE_BYTES = 16 * 1024 * 1024
OUTPUT_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
ACTIVE = None


def emit(message):
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    with OUTPUT_LOCK:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()


def local_host(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Choose a loopback Ollama address.")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("The VS Code agent only connects to Ollama on this computer.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Choose a valid Ollama port.") from error
    return value.rstrip("/")


def is_local_model(name):
    return isinstance(name, str) and bool(name.strip()) and not privacy.is_cloud_model(name)


def _models(request):
    try:
        host = local_host(request.get("host"))
        models = [name for name in ollama.list_models(host) if is_local_model(name)]
        try:
            running = ollama.running_details(ollama.api_json(host, "/api/ps"))
        except (RuntimeError, OSError):
            running = {}
        placements = {name: ollama.placement(running[name]) for name in models if name in running}
        emit({"type": "models", "models": models, "placements": placements})
    except (ValueError, RuntimeError, OSError) as error:
        emit({"type": "error", "request": "models", "message": str(error)})


def _approval(cancel, action):
    pending = {"id": secrets.token_urlsafe(18), "event": threading.Event(), "approved": False,
               "action": action}
    with STATE_LOCK:
        if ACTIVE is None or ACTIVE["cancel"] is not cancel:
            return False
        ACTIVE["approval"] = pending
    emit({"type": "approval", "id": pending["id"], "action": pending["action"]})
    while not pending["event"].wait(0.1):
        if cancel.is_set():
            return False
    with STATE_LOCK:
        if ACTIVE is not None and ACTIVE.get("approval") is pending:
            ACTIVE["approval"] = None
    return pending["approved"] is True and not cancel.is_set()


def _start(request):
    global ACTIVE
    with STATE_LOCK:
        if ACTIVE is not None:
            emit({"type": "error", "request": "run", "message": "An agent turn is already running."})
            return
        active = {"cancel": threading.Event(), "approval": None, "finished": threading.Event()}
        ACTIVE = active

    def event(item):
        emit({"type": "event", "event": item})

    def approve(action):
        return _approval(active["cancel"], action)

    def worker():
        completion = {"type": "done", "stopped": True}
        try:
            host = local_host(request.get("host"))
            model = request.get("model")
            if not is_local_model(model) or model not in ollama.list_models(host):
                raise ValueError("Choose an installed local Ollama model.")
            project = request.get("project")
            if not isinstance(project, str) or not Path(project).is_dir():
                raise ValueError("Open a project folder in VS Code before starting an agent turn.")
            messages = request.get("messages")
            if (not isinstance(messages, list) or len(messages) > 200
                    or any(not isinstance(message, dict) for message in messages)):
                raise ValueError("The conversation history is invalid or too long.")
            context = request.get("contextLength", 4096)
            if isinstance(context, bool) or not isinstance(context, int) or not 2048 <= context <= 1048576:
                raise ValueError("Choose a context length from 2,048 to 1,048,576 tokens.")
            result = run_agent(host, model, project, messages, event, approve,
                               cancel_event=active["cancel"], context_length=context)
            completion = {"type": "done", "result": result}
        except AgentCancelled:
            pass
        except (ValueError, RuntimeError, OSError) as error:
            completion = {"type": "done", "error": str(error)}
        except Exception as error:
            completion = {"type": "done", "error": f"Agent bridge failed: {error}"}
        finally:
            global ACTIVE
            with STATE_LOCK:
                if ACTIVE is active:
                    ACTIVE = None
                active["finished"].set()
        emit(completion)

    threading.Thread(target=worker, name="ai-assist-vscode-agent", daemon=True).start()


def _handle(request):
    kind = request.get("type")
    if kind == "models":
        _models(request)
    elif kind == "run":
        _start(request)
    elif kind == "approve":
        with STATE_LOCK:
            pending = ACTIVE.get("approval") if ACTIVE else None
            if pending is not None and request.get("id") == pending["id"]:
                pending["approved"] = request.get("approved") is True
                if pending["approved"] and pending["action"].get("kind") == "run_command":
                    action = pending["action"]
                    network_gate.note_outside("Developer agent command", action["command"],
                                               action.get("description", ""))
                pending["event"].set()
    elif kind == "cancel":
        with STATE_LOCK:
            active = ACTIVE
            if active:
                active["cancel"].set()
                pending = active.get("approval")
                if pending:
                    pending["approved"] = False
                    pending["event"].set()
    elif kind == "shutdown":
        with STATE_LOCK:
            active = ACTIVE
            if active:
                active["cancel"].set()
                pending = active.get("approval")
                if pending:
                    pending["event"].set()
        if active:
            active["finished"].wait(3)
        return False
    return True


def main():
    network_gate.install()
    for line in sys.stdin.buffer:
        if len(line) > MAX_LINE_BYTES:
            emit({"type": "error", "message": "The extension request is too large."})
            continue
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("Requests must be JSON objects.")
        except (UnicodeError, ValueError) as error:
            emit({"type": "error", "message": f"Invalid extension request: {error}"})
            continue
        if not _handle(request):
            break
    with STATE_LOCK:
        active = ACTIVE
        if active:
            active["cancel"].set()
    if active:
        active["finished"].wait(3)


if __name__ == "__main__":
    main()
