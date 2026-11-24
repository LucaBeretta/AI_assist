"""The Developer tab's "Open in OpenCode" button (see opencode_launcher.py)."""

import developer_tools
import opencode_launcher
from webui.api_utils import json_method


class OpenCodeApi:
    """Mixed into the desktop API. Nothing runs until the person clicks the button."""

    def _find_opencode(self):
        return opencode_launcher.find_opencode()

    @json_method
    def opencode_state(self):
        return {"ok": True, "installed": bool(self._find_opencode())}

    @json_method
    def open_opencode(self, folder):
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("Choose a project folder first.")
        root = developer_tools._project_root(folder)
        program = self._find_opencode()
        if not program:
            raise ValueError("OpenCode is not installed on this computer. Install it from opencode.ai, then try again.")
        try:
            models = list(self._engine.list_ollama_models())
        except Exception:
            models = []  # OpenCode still opens; it lists Ollama's models once Ollama runs
        opencode_launcher.open_project(root, self._store.directory, self._engine.ollama_url(), models, program=program)
        return {"ok": True, "folder": str(root), "models": len(models)}
