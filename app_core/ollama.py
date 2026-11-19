"""Ollama's HTTP API, without any window code.

AIassist.py keeps its long-standing module functions (``request_ollama``,
``ollama_api``, ``running_details`` and so on) as thin compositions of these, so
both interfaces send the same requests and apply the same limits.
"""

import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from app_core.privacy import is_local_url
import network_gate
from response_metrics import response_metrics

DEFAULT_URL = "http://localhost:11434"
MAX_LINE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024


def base_url(environ=None):
    """Convert Ollama's listening address (``OLLAMA_HOST``) into a usable client URL."""
    environ = os.environ if environ is None else environ
    host = environ.get("OLLAMA_HOST", "").strip() or DEFAULT_URL
    if "://" not in host:
        host = f"http://{host}"
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname in ("0.0.0.0", "::"):
        port = parsed.port if parsed.port is not None else 11434
        parsed = parsed._replace(netloc=f"127.0.0.1:{port}")
    return parsed.geturl().rstrip("/")


def model_names(result):
    """Installed model names from an ``/api/tags`` answer."""
    models = result.get("models") if isinstance(result, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid model list.")
    return sorted({
        model["name"] for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str) and model["name"].strip()
    })


def list_models(base):
    """Read the models installed on the Ollama server at ``base``."""
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not load models from {base}. Start Ollama, then click Refresh.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid model list.") from error
    return model_names(result)


def api_json(base, path, payload=None, timeout=10):
    """One JSON request to Ollama; RuntimeError for any failure or an ``error`` answer."""
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(str(result))
        return result
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode("utf-8", errors="replace")) from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error


def running_names(result):
    """Names of the models loaded now, from an ``/api/ps`` answer."""
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    return {m["name"] for m in models if isinstance(m, dict) and "name" in m}


def running_details(result):
    """Per running model: its size, the part in GPU memory, and its context length
    when Ollama reports one (``/api/ps``)."""
    models = result.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid running-model list.")
    details = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue

        def number(key, model=model):
            value = model.get(key)
            return value if type(value) is int and value >= 0 else None

        details[model["name"]] = {"size": number("size") or 0, "size_vram": number("size_vram") or 0,
                                  "context_length": number("context_length")}
    return details


def placement(detail):
    """Where Ollama placed a running model: gpu, cpu, or the GPU share."""
    if not detail or not detail.get("size"):
        return ""
    share = detail.get("size_vram", 0) / detail["size"]
    if share >= 0.98:
        return "gpu"
    if share <= 0.02:
        return "cpu"
    return f"gpu {share:.0%} · cpu"


def lifecycle_payload(model, start=True, context_length=None):
    """The ``/api/generate`` body that loads (keeps resident) or unloads ``model``."""
    payload = {"model": model, "stream": False, "keep_alive": -1 if start else 0}
    if start and context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    return payload


def ensure_running(base, check, progress=lambda text: None, environ=None):
    """Start a local ``ollama serve`` when ``check()`` fails; a remote server is never started."""
    environ = os.environ if environ is None else environ
    try:
        check()
        return
    except RuntimeError:
        pass
    if not is_local_url(base):
        raise RuntimeError("The configured remote Ollama server is offline. Start it on that computer.")
    executable = shutil.which("ollama")
    if not executable:
        candidate = os.path.join(environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
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
            check()
            return
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError("Ollama did not become ready. Check Ollama and click Refresh models.")


def stream_chat(base, messages, model, context_length=None, on_chunk=None, on_metrics=None):
    """Stream one chat answer from ``/api/chat``; returns the complete text.

    Each text chunk goes to ``on_chunk`` as it arrives, and Ollama's timing
    metadata to ``on_metrics`` at the end. An interrupted, oversized or empty
    stream is an error, never a successful answer."""
    payload = {"model": model, "messages": messages, "stream": True, "keep_alive": -1}
    if context_length is not None:
        payload["options"] = {"num_ctx": context_length}
    request = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with network_gate.task(f"Send your message to {model}"), \
                urllib.request.urlopen(request, timeout=120) as response:
            parts = []
            received = 0
            while True:
                line = response.readline(MAX_LINE_BYTES + 1)
                if not line:
                    raise RuntimeError("The response connection closed before Ollama finished. Please retry.")
                received += len(line)
                if len(line) > MAX_LINE_BYTES or received > MAX_STREAM_BYTES:
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
        raise RuntimeError(f"Ollama returned HTTP {error.code}: {details or error.reason}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not connect to Ollama at {base}. Start Ollama or run 'ollama serve', then try again."
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
