"""Compatibility names used by the desktop API, composed from the shared core."""
import os

from app_core import ollama
from app_core.providers import OllamaProvider
from app_core.streaming import StreamStopped, run_stoppable  # noqa: F401 -- re-exported for callers of this module


def ollama_url():
    return ollama.base_url()


def list_ollama_models():
    return OllamaProvider(ollama_url(), auto_start=False).list_models()


def ollama_api(path, payload=None, timeout=10, method=None):
    return ollama.api_json(ollama_url(), path, payload, timeout, method=method)


def running_models():
    return ollama.running_names(ollama_api("/api/ps"))


def running_details():
    return ollama.running_details(ollama_api("/api/ps"))


def ensure_ollama(progress):
    return ollama.ensure_running(ollama_url(), running_models, progress)


def set_model_running(model, start=True, context_length=None):
    return ollama_api("/api/generate", ollama.lifecycle_payload(model, start, context_length), timeout=300)


def request_ollama(messages, model=None, context_length=None, on_chunk=None, on_metrics=None, keep_alive=-1,
                   cancel_event=None):
    return ollama.stream_chat(ollama_url(), messages, model or os.environ.get("OLLAMA_MODEL", "qwen3:30b"),
                              context_length, on_chunk, on_metrics, keep_alive=keep_alive, cancel_event=cancel_event)
