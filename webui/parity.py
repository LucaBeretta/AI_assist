"""JSON adapters over existing updates, feedback, capability and launcher logic."""
from copy import deepcopy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import app_updater
import desktop_shortcut
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


APP_ROOT = Path(__file__).resolve().parent.parent


def relaunch():
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    subprocess.Popen([sys.executable, str(APP_ROOT / "gui_runtime.py"), "--wait-pid", str(os.getpid()),
                      "--launch", str(APP_ROOT / "AIassist.py")],
                     cwd=APP_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **options)


def update_dependencies(root):
    python = Path(sys.executable)
    private_dirs = [root / "runtime/python", root / "runtime/linux/python"]
    if not any(python.is_relative_to(directory) for directory in private_dirs):
        command = [str(python.with_name("python.exe") if python.name.lower() == "pythonw.exe" else python),
                   "-m", "pip", "install", "-r", str(root / "requirements.txt")]
        return ("& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in command)
                if os.name == "nt" else shlex.join(command))
    with network_gate.task("Install updated AI Assist dependencies from PyPI",
                           sends="Package names and versions required by AI Assist."):
        if not network_gate.allow_external("pip", "PyPI", "Download AI Assist dependencies."):
            raise RuntimeError("Dependency installation was blocked.")
    # Installed packages are in use (and locked on Windows) while the app runs, so pip
    # runs on the next start, before the window opens (gui_runtime.install_pending_requirements).
    marker = root / ".update" / "requirements-pending"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("python -m pip install -r requirements.txt\n", encoding="utf-8")


class ParityApi:
    @json_method
    def check_updates(self):
        root = APP_ROOT
        def work(progress, cancel):
            with network_gate.task("Check AI Assist source files for updates", "The GitHub repository and revision; no chats or project content."):
                try:
                    checked = check_update(root)
                    checkout = app_updater.is_git_checkout(root)
                    self._last_update = {**checked, "can_install": bool(checked["differences"]) and not checkout,
                                         "reason": app_updater.CHECKOUT if checkout else ""}
                except Exception as error:
                    self._last_update = {"error": str(error)}
                    raise
                finally:
                    self._hourly.update_updates(self._hourly_update_line())
                return {k: v for k, v in self._last_update.items() if k != "expected_files"}
        with self._lock:
            return self._jobs.start("updates", work, key="updates")

    @json_method
    def update_status(self):
        return {"ok": True, "can_rollback": app_updater.can_rollback(APP_ROOT),
                "restart_ready": self._restart_ready}

    @json_method
    def install_update(self):
        with self._lock:
            if self._jobs.busy("updates"):
                raise ValueError("An update operation is already running.")
            if app_updater.is_git_checkout(APP_ROOT):
                raise ValueError(app_updater.CHECKOUT)
            checked = self._last_update or {}
            if not checked.get("can_install"):
                raise ValueError("Check for updates first.")
            revision, expected = checked["revision"], checked["expected_files"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Invalid update revision.")

            def work(progress, cancel):
                directory = app_updater._path(APP_ROOT, ".update/staging")
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=directory) as temporary:
                    archive = Path(temporary) / "source.zip"
                    with network_gate.task("Download the AI Assist update",
                            sends="A request for the AI Assist source archive from GitHub. No chats or files are sent."):
                        app_updater.download(revision, archive, progress, cancel)
                    staging = app_updater._path(directory, revision)
                    if staging.exists():
                        # An interrupted verification may have left this revision staged.
                        shutil.rmtree(staging)
                    app_updater.extract_archive(archive, staging, expected)
                    command = None
                    def dependencies(plan):
                        nonlocal command
                        if "requirements.txt" in plan["write"]:
                            command = update_dependencies(APP_ROOT)
                    try:
                        plan = app_updater.plan_update(APP_ROOT, expected)
                        progress(cancellable=False, phase="applying")
                        app_updater.apply_update(APP_ROOT, staging, plan, after_files=dependencies)
                    finally:
                        shutil.rmtree(staging)
                self._restart_ready = True
                self._last_update = None
                return {"command": command, "can_rollback": True}
            return self._jobs.start("install_update", work, key="updates", finish_on_close=True)

    @json_method
    def rollback_update(self):
        def work(progress, cancel):
            progress(cancellable=False, phase="restoring")
            app_updater.rollback(APP_ROOT)
            self._restart_ready = True
            self._last_update = None
            return {"can_rollback": app_updater.can_rollback(APP_ROOT)}
        with self._lock:
            return self._jobs.start("rollback_update", work, key="updates", finish_on_close=True)

    @json_method
    def restart_app(self):
        with self._lock:
            if not self._restart_ready or self._jobs.busy("updates") or self._closed:
                raise ValueError("Finish the update before restarting.")
            self._restart_requested = True
            self._desktop.request_quit()
            return {"ok": True}

    @json_method
    def create_shortcut(self):
        desktop_shortcut.create_shortcut(APP_ROOT)
        return {"ok": True}

    @json_method
    def prepare_feedback(self, chat_id, index, text, criteria=""):
        with self._lock:
            record = self._load_chat(chat_id)
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
        if any(item["record"].get("backend", item["record"]["environment"]["backend"]) == "Colibri" for item in self._streams.values()):
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
