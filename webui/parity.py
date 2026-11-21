"""JSON adapters over existing updates, feedback, capability and launcher logic."""
from copy import deepcopy
import json
from pathlib import Path

import colibri_launcher as launcher
from developer_tools import fetch_model_capabilities
from feedback import feedback_prompt
from update_checker import check_update
import vercel_deploy
import network_gate
from webui.api_utils import json_method
from webui.local_files import write_utf8


HYBRID_DEFAULTS = {"enabled": False, "review_model": "", "manage_memory": True}


def hybrid_settings(directory):
    try:
        value = json.loads((Path(directory) / "hybrid.settings").read_text(encoding="utf-8"))
        return validate_hybrid(value)
    except (OSError, ValueError, TypeError):
        return dict(HYBRID_DEFAULTS)


def validate_hybrid(value):
    if not isinstance(value, dict) or set(value) != set(HYBRID_DEFAULTS):
        raise ValueError("Choose enabled, review_model and manage_memory for quality review.")
    if type(value["enabled"]) is not bool or type(value["manage_memory"]) is not bool:
        raise ValueError("Quality review options must be true or false.")
    model = value["review_model"]
    if not isinstance(model, str) or len(model) > 256 or any(c in model for c in "\r\n") or (value["enabled"] and not model.strip()):
        raise ValueError("Choose a Colibri reviewer model (up to 256 characters).")
    return {**value, "review_model": model.strip()}


class ParityApi:
    @json_method
    def check_updates(self):
        root = Path(__file__).resolve().parent.parent
        def work(progress, cancel):
            with network_gate.task("Check AI Assist source files for updates", "The GitHub repository and revision; no chats or project content."):
                try:
                    self._last_update = check_update(root)
                except Exception as error:
                    self._last_update = {"error": str(error)}
                    raise
                finally:
                    self._hourly.update_updates(self._hourly_update_line())
                return self._last_update
        with self._lock:
            return self._jobs.start("updates", work, key="updates")

    @json_method
    def prepare_feedback(self, chat_id, index, text, criteria=""):
        with self._lock:
            record = self._chats.load(chat_id)
            if type(index) is not int or not 0 <= index < len(record["messages"]) or record["messages"][index]["role"] != "assistant":
                raise ValueError("Choose an assistant reply for feedback.")
            return {"ok": True, "text": feedback_prompt(record["messages"][index]["content"], text, criteria,
                                                        developer=record.get("mode") == "dev")}

    @json_method
    def model_capabilities(self, model, backend="Ollama"):
        if backend not in {"Ollama", "Colibri"}:
            raise ValueError("Choose Ollama or Colibri.")
        metadata = (self._colibri.client().metadata(model) if backend == "Colibri"
                    else fetch_model_capabilities(self._engine.ollama_url(), model))
        return {"ok": True, "backend": backend, "metadata": metadata}

    def _require_colibri_idle(self):
        if self._closed or self._jobs.busy("colibri") or self._developer.uses_colibri():
            raise ValueError("Stop the active Colibri operation or quality review first.")
        if any(item["record"].get("backend", item["record"]["environment"]["backend"]) == "Colibri" for item in self._chats.active_streams()):
            raise ValueError("Stop Colibri chat streams before changing the server or model.")

    @json_method
    def set_colibri_folder(self, folder):
        with self._lock:
            self._require_colibri_idle()
            directory = launcher.save_directory(folder, self._store.directory / "colibri.settings")
            with self._colibri._lock:
                self._colibri._folder = directory
        return {"ok": True, "folder": directory}

    @json_method
    def colibri_catalog(self):
        with self._lock:
            service = self._colibri
            folder = service.folder()
            if not folder:
                raise ValueError("Choose an installed Colibri folder first.")
            return self._jobs.start("colibri_catalog", lambda progress, cancel: {
                "models": launcher.recommendations(folder), "status": service.availability()}, key="colibri-catalog")

    @json_method
    def start_colibri(self):
        return self._change_colibri("start")

    @json_method
    def stop_colibri(self):
        return self._change_colibri("stop")

    @json_method
    def install_colibri(self, model, context=None):
        if not isinstance(model, str) or not model or len(model) > 300 or model.startswith("-"):
            raise ValueError("Choose a valid Colibri model ID.")
        launcher._check_ctx(context)
        return self._change_colibri("install", model, context)

    @json_method
    def set_colibri_context(self, context):
        launcher._check_ctx(context)
        if context is None:
            raise ValueError("Choose a Colibri context (0 means automatic).")
        return self._change_colibri("context", context=context)

    def _change_colibri(self, operation, model=None, context=None):
        with self._lock:
            self._require_colibri_idle()
            service = self._colibri
            folder = service.folder()
            if not folder or not launcher.is_local_url(service.url):
                raise ValueError("Server management requires a known local Colibri installation.")
            def work(progress, cancel):
                if operation == "start":
                    service._adopt(launcher.start_server(folder, progress=lambda text: progress(text=text), cancel_event=cancel))
                elif operation == "stop":
                    launcher.stop_server(folder)
                elif operation == "context":
                    service._adopt(launcher.set_context(folder, context))
                else:
                    launcher.stop_server(folder)
                    launcher.install_model(folder, model, lambda phase, done, total, rate=None, eta=None:
                        progress(phase=phase, completed=done, total=total, rate=rate, eta=eta), cancel, ctx=context)
                return service.availability()
            return self._jobs.start("colibri_" + operation, work, key="colibri")

    @json_method
    def deploy_vercel(self, folder, project, scope="", domain=""):
        root, project, scope, domain = vercel_deploy.validate_project(folder, project, scope, domain)
        def work(progress, cancel):
            if not network_gate.allow_external("Vercel production deployment", project,
                    "The chosen web project folder is uploaded to Vercel by its CLI.", purpose="Publish the chosen web project to Vercel"):
                raise RuntimeError("The deployment was blocked before starting the Vercel CLI.")
            if cancel.is_set():
                return None
            network_gate.note_outside("Vercel CLI", project, "Upload the explicitly approved web project.")
            return vercel_deploy.deploy_project(root, project, scope, domain, lambda text: progress(text=text), cancel)
        with self._lock:
            if self._closed or self._developer.uses_project(root):
                raise ValueError("Stop the task changing this project before deploying it.")
            return self._jobs.start("deploy", work, key="deploy:" + str(root))

    @json_method
    def cancel_job(self, job_id):
        self._internet.block_pending()
        return self._jobs.cancel(job_id)

    @json_method
    def set_hybrid_settings(self, enabled, review_model, manage_memory=True):
        value = validate_hybrid(dict(enabled=enabled, review_model=review_model, manage_memory=manage_memory))
        with self._lock:
            if self._closed:
                raise RuntimeError("The web UI is closing.")
            write_utf8(self._store.directory / "hybrid.settings", json.dumps(value, ensure_ascii=False))
            self._hybrid = value
        return {"ok": True, "hybrid": deepcopy(value)}
