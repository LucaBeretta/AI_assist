"""Inference providers: one interface over Ollama and Colibri.

A provider answers four questions for any interface: which engine and server
is this, which models does it serve, what does a model support, and stream an
answer. The desktop window still drives engine lifecycles (start/stop, model
loading) through its own mixins; these classes are what headless clients such
as the web workspace use, and what the window can adopt piece by piece.
"""

import dataclasses
from typing import Callable, Optional, Protocol

from app_core import ollama
from app_core.privacy import is_local_url

Chunk = Callable[[str], None]
Metrics = Callable[[dict], None]


@dataclasses.dataclass(frozen=True)
class ProviderInfo:
    name: str
    endpoint: str
    local: bool
    manages_lifecycle: bool


class ChatProvider(Protocol):
    name: str

    def info(self) -> ProviderInfo: ...

    def list_models(self) -> list: ...

    def running(self) -> dict: ...

    def model_details(self, model: str) -> dict: ...

    def stream_chat(self, messages: list, model: str, *, context_length: Optional[int] = None,
                    on_chunk: Optional[Chunk] = None, on_metrics: Optional[Metrics] = None,
                    cancel_event=None) -> str: ...


class OllamaProvider:
    name = "Ollama"

    def __init__(self, base_url=None, auto_start=True):
        self.base_url = base_url or ollama.base_url()
        self.auto_start = auto_start

    def info(self):
        return ProviderInfo(self.name, self.base_url, is_local_url(self.base_url), manages_lifecycle=False)

    def _ready(self):
        if self.auto_start:
            ollama.ensure_running(self.base_url, lambda: ollama.running_names(ollama.api_json(self.base_url, "/api/ps")))

    def list_models(self):
        self._ready()
        return ollama.list_models(self.base_url)

    def running(self):
        """Per loaded model: size, GPU share and context (``/api/ps``)."""
        return ollama.running_details(ollama.api_json(self.base_url, "/api/ps"))

    def model_details(self, model):
        from developer_tools import fetch_model_capabilities
        return fetch_model_capabilities(self.base_url, model)

    def stream_chat(self, messages, model, *, context_length=None, on_chunk=None, on_metrics=None, cancel_event=None):
        self._ready()
        return ollama.stream_chat(self.base_url, messages, model, context_length, on_chunk, on_metrics)

    def agent_backend(self, cancel_event=None):
        """(host, chat_fn) for coding_agent.run_agent: Ollama's own tool-calling API."""
        self._ready()
        return self.base_url, None


class ColibriProvider:
    """Colibri's OpenAI-compatible server. With a known local folder, the
    server is started through colibri's own launcher before a request."""

    name = "Colibri"

    def __init__(self, url, key="", directory=None):
        from colibri_backend import normalize_url
        self.url = normalize_url(url)
        self.key = key
        self.directory = directory

    def client(self):
        from colibri_backend import ColibriClient
        return ColibriClient(self.url, self.key)

    def manageable(self):
        return bool(self.directory) and is_local_url(self.url)

    def info(self):
        return ProviderInfo(self.name, self.url, is_local_url(self.url), manages_lifecycle=self.manageable())

    def list_models(self):
        return self.client().models()

    def running(self):
        return {name: {} for name in self.list_models()}

    def model_details(self, model):
        return self.client().metadata(model)

    def stream_chat(self, messages, model, *, context_length=None, on_chunk=None, on_metrics=None, cancel_event=None):
        # Colibri's context is set when its model is installed, not per request.
        if self.manageable():
            import colibri_launcher
            report = colibri_launcher.start_server(self.directory, cancel_event=cancel_event)
            self.url = colibri_launcher.server_url(report) or self.url
        return self.client().chat(messages, model, on_chunk=on_chunk, on_metrics=on_metrics)

    def agent_backend(self, cancel_event=None):
        """(host, chat_fn) for coding_agent.run_agent, through Colibri's OpenAI tool-calling API."""
        if self.manageable():
            import colibri_launcher
            report = colibri_launcher.start_server(self.directory, cancel_event=cancel_event)
            self.url = colibri_launcher.server_url(report) or self.url
        client = self.client()
        return client.url, client.agent_chat


def configured_provider(data_directory, environ=None):
    """The engine the user last chose (backend.settings), as a provider."""
    import os
    from colibri_backend import normalize_url
    import colibri_launcher
    from app_core import settings

    environ = os.environ if environ is None else environ
    backend, url = settings.load_backend(data_directory / "backend.settings", normalize_url, environ)
    if backend == "Colibri":
        saved = colibri_launcher.load_settings(data_directory / "colibri.settings").get("directory")
        directory = colibri_launcher.find_directory(saved if isinstance(saved, str) else None)
        return ColibriProvider(url, environ.get("COLI_API_KEY", ""), directory)
    return OllamaProvider(ollama.base_url(environ))
