"""Reuse Tk's Colibri settings, launcher and streaming client without Tk objects."""

import os
from pathlib import Path
import threading

from colibri_backend import normalize_url
from app_core.settings import load_backend, read_json
from app_core.providers import ColibriProvider
import colibri_launcher as launcher


DEFAULT_URL = "http://127.0.0.1:8000/v1"


class ColibriService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.default_backend, self.url = load_backend(self.directory / "backend.settings", normalize_url)
        legacy = read_json(self.directory / "backend.settings")
        if "backend" not in legacy and "url" in legacy and not os.environ.get("COLIBRI_URL"):
            try:
                self.url = normalize_url(legacy["url"])
            except (ValueError, TypeError, AttributeError):
                pass
        self._explicit_url = bool(os.environ.get("COLIBRI_URL")) or self.url != DEFAULT_URL
        self._key = os.environ.get("COLI_API_KEY", "")
        self._folder = None
        self._lock = threading.Lock()

    def folder(self):
        with self._lock:
            if self._folder is None:
                saved = launcher.load_settings(self.directory / "colibri.settings").get("directory")
                self._folder = launcher.find_directory(saved if isinstance(saved, str) else None)
            return self._folder

    def _adopt(self, report):
        url = launcher.server_url(report)
        if url and not self._explicit_url:
            with self._lock:
                self.url = normalize_url(url)

    def client(self):
        with self._lock:
            return ColibriProvider(self.url, self._key).client()

    def availability(self):
        folder = self.folder()
        value = {"installed": folder is not None, "folder": folder, "running": False, "models": [], "host": self.url,
                 "state": "offline", "context_length": None, "tokens_per_second": None}
        try:
            report = launcher.status(folder) if folder and launcher.is_local_url(self.url) else {}
            self._adopt(report)
            value.update(host=self.url, state=launcher.server_state(report) if report else "offline",
                         context_length=report.get("ctx"), tokens_per_second=launcher.tokens_per_second(report))
            if not report or launcher.server_state(report) == "ready":
                value["models"] = self.client().models()
                value.update(running=True, state="ready")
        except Exception:
            value["error"] = "Colibri is unavailable. Check its server and model setup."
        return value

    def prepare(self, cancel, progress):
        folder = self.folder()
        if folder and launcher.is_local_url(self.url):
            self._adopt(launcher.start_server(folder, progress=progress, cancel_event=cancel))
        return self.client()
