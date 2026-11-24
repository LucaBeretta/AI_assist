"""The Developer tab's "Open in OpenCode" button (see opencode_launcher.py)."""

import developer_tools
import opencode_launcher
from webui.api_utils import json_method


class OpenCodeApi:
    """Mixed into the desktop API. Nothing runs until the person clicks the button."""

    def _find_opencode(self):
        return opencode_launcher.find_opencode()

    def _opencode_models(self):
        """This computer's Ollama models that can chat and run here (no cloud models or aliases)."""
        try:
            names = list(self._engine.list_ollama_models())
        except Exception:
            return []  # OpenCode still opens; its model list is empty until Ollama runs
        return opencode_launcher.chat_models(self._engine.ollama_url(), names)

    @json_method
    def opencode_state(self):
        return {"ok": True, "installed": bool(self._find_opencode())}

    @json_method
    def open_opencode(self, folder, model=None):
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("Choose a project folder first.")
        if model is not None and not isinstance(model, str):
            raise ValueError("Choose an installed model.")
        root = developer_tools._project_root(folder)
        program = self._find_opencode()
        if not program:
            raise ValueError("OpenCode is not installed on this computer. Install it from opencode.ai, then try again.")
        models = self._opencode_models()
        opencode_launcher.open_project(root, self._store.directory, self._engine.ollama_url(), models,
                                       default_model=model, program=program)
        return {"ok": True, "folder": str(root), "models": len(models)}
