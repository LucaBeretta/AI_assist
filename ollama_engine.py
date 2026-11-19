"""Ollama HTTP requests, model lifecycle and stoppable streaming; no UI objects."""
import json
import http.client
import os
import queue
import threading
import subprocess
import shutil
import time
import urllib.error
import urllib.request
import urllib.parse

from response_metrics import response_metrics


def ollama_url():
    """Convert Ollama's listening address into a usable client URL."""
    host = os.environ.get("OLLAMA_HOST", "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = f"http://{host}"
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname in ("0.0.0.0", "::"):
        port = parsed.port if parsed.port is not None else 11434
        parsed = parsed._replace(netloc=f"127.0.0.1:{port}")
    return parsed.geturl().rstrip("/")


def list_ollama_models():
    """Read the models installed on the configured Ollama server."""
    host = ollama_url()
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            f"Could not load models from {host}. Start Ollama, then click Refresh."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid model list.") from error

    models = result.get("models") if isinstance(result, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid model list.")
    return sorted({
        model["name"] for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
        and model["name"].strip()
    })


def ollama_api(path, payload=None, timeout=10, method=None):
    request = urllib.request.Request(
        ollama_url() + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            result = {} if not data and method == "DELETE" else json.loads(data)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(str(result))
        return result
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode("utf-8", errors="replace")) from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error


def running_models():
    result = ollama_api("/api/ps")
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    return {m["name"] for m in models if isinstance(m, dict) and "name" in m}


def running_details():
    """Per running model: its size, the part in GPU memory, and its context length
    when Ollama reports one (``/api/ps``). Used by the status bar."""
    result = ollama_api("/api/ps")
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    details = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue
        number = lambda key: model.get(key) if type(model.get(key)) is int and model.get(key) >= 0 else None
        details[model["name"]] = {"size": number("size") or 0, "size_vram": number("size_vram") or 0,
                                  "context_length": number("context_length")}
    return details


def ensure_ollama(progress):
    try:
        running_models()
        return
    except RuntimeError:
        pass
    host = urllib.parse.urlsplit(ollama_url()).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("The configured remote Ollama server is offline. Start it on that computer.")
    executable = shutil.which("ollama")
    if not executable:
        candidate = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(candidate):
            executable = candidate
    if not executable:
        raise RuntimeError("Ollama is not installed. Install Ollama, then click Refresh models.")
    progress("Starting Ollama...")
    subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(40):
        try:
            running_models()
            return
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError("Ollama did not become ready. Check Ollama and click Refresh models.")


def set_model_running(model, start=True, context_length=None):
    payload = {"model": model, "stream": False, "keep_alive": -1 if start else 0}
    if start and context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    ollama_api("/api/generate", payload, timeout=300)


def request_ollama(messages, model=None, context_length=None, on_chunk=None, on_metrics=None, keep_alive=-1):
    """Send the conversation to the local Ollama chat API."""
    host = ollama_url()
    model = model or os.environ.get("OLLAMA_MODEL", "qwen3:30b")
    payload = {"model": model, "messages": messages, "stream": True, "keep_alive": keep_alive}
    if context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            parts = []
            received = 0
            while True:
                line = response.readline(1024 * 1024 + 1)
                if not line:
                    raise RuntimeError("The response connection closed before Ollama finished. Please retry.")
                received += len(line)
                if len(line) > 1024 * 1024 or received > 16 * 1024 * 1024:
                    raise RuntimeError("Ollama returned too much chat output.")
                if not line.strip():
                    continue
                result = json.loads(line)
                if not isinstance(result, dict):
                    raise RuntimeError("Ollama returned an invalid response.")
                if result.get("error"):
                    raise RuntimeError(f"Ollama could not complete the response: {result['error']}")
                message = result.get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else None
                if not isinstance(content, str):
                    raise RuntimeError("Ollama returned an invalid message.")
                if content:
                    parts.append(content)
                    if on_chunk:
                        on_chunk(content)
                if result.get("done") is True:
                    if on_metrics:
                        on_metrics(response_metrics(result))
                    break
    except urllib.error.HTTPError as error:
        try:
            details = error.read(2000).decode("utf-8", errors="replace").strip()
        finally:
            error.close()
        raise RuntimeError(
            f"Ollama returned HTTP {error.code}: {details or error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not connect to Ollama at {host}. Start Ollama or run 'ollama serve', then try again."
        ) from error
    except TimeoutError as error:
        raise RuntimeError("The Ollama request timed out.") from error
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Ollama returned an invalid response.") from error
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"The Ollama connection failed: {error}") from error

    content = "".join(parts)
    if not content.strip():
        raise RuntimeError("Ollama's response did not contain a message.")
    return content.strip()

class StreamStopped(Exception):
    """Raised inside a stream callback to stop reading; the connection closes,
    which tells Ollama or Colibri to stop generating."""


def run_stoppable(request, parts, cancel_event):
    """Run ``request()`` on its own thread. Returns (answer, error, stopped).

    On Stop the caller gets the text received so far at once; the reader
    thread ends at its next chunk, when the stream callback raises."""
    if cancel_event is None:
        try:
            return request(), None, False
        except (RuntimeError, ValueError) as error:
            return None, str(error), False
    finished = queue.Queue(maxsize=1)

    def worker():
        try:
            finished.put((request(), None))
        except StreamStopped:
            finished.put((None, "stopped"))
        except (RuntimeError, ValueError, OSError) as error:
            finished.put((None, str(error)))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        if cancel_event.is_set():
            return "".join(parts), None, True
        try:
            answer, error = finished.get(timeout=.1)
        except queue.Empty:
            continue
        if error == "stopped" or cancel_event.is_set():
            return "".join(parts), None, True
        return answer, error, False
