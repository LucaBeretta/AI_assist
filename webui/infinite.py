"""Generate → Infinite: a local model writes a patch, and the Infinite app renders it as a clip."""

import base64
import re
import shutil
import uuid

import developer_tools
import infinite_engine
from status_values import is_cloud_model
from webui.api_utils import json_method

_KEEP_CLIPS = 8  # the newest clip folders kept in the app data folder
_CLIP_ID = re.compile(r"[0-9a-f]{32}")
INSTALL_HINT = ("Infinite is not installed on this computer. Install it for free from "
                "github.com/n1m21n/Infinite, then try again.")


class InfiniteApi:
    """Mixed into the desktop API. Infinite runs on this computer only, without a window."""

    def _infinite_folder(self):
        return self._store.directory / "infinite"

    def _find_infinite(self):
        return infinite_engine.find_infinite()

    def _model_thinks(self, model):
        """Whether Ollama reports the model can reason before answering.

        A thinking model asked to write a patch can reason for many minutes first, so its
        reasoning is turned off here; other models are asked the ordinary way."""
        try:
            return "thinking" in developer_tools.fetch_model_capabilities(self._engine.ollama_url(), model)["capabilities"]
        except (RuntimeError, ValueError):
            return False

    def _infinite_clip_folder(self, clip):
        if not isinstance(clip, str) or not _CLIP_ID.fullmatch(clip):
            raise ValueError("That clip is no longer available.")
        folder = self._infinite_folder() / clip
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("That clip is no longer available.")
        return folder

    def _infinite_file(self, clip, name, limit):
        path = self._infinite_clip_folder(clip) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError("That clip is no longer available.")
        return path.read_bytes()

    def _prune_infinite_clips(self):
        """Keep the newest clips; older folders made here are removed."""
        root = self._infinite_folder()
        try:
            folders = [item for item in root.iterdir()
                       if _CLIP_ID.fullmatch(item.name) and item.is_dir() and not item.is_symlink()]
        except OSError:
            return
        folders.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for old in folders[_KEEP_CLIPS - 1:]:
            shutil.rmtree(old, ignore_errors=True)

    @json_method
    def infinite_state(self):
        return {"ok": True, "installed": bool(self._find_infinite()), "making": self._jobs.busy("infinite"),
                "lengths": list(infinite_engine.LENGTHS)}

    @json_method
    def make_infinite_clip(self, description, model, seconds=4):
        description = infinite_engine.clean_description(description)
        if seconds not in infinite_engine.LENGTHS:
            raise ValueError("Choose a length of 4, 8 or 12 seconds.")
        if not isinstance(model, str) or not model.strip() or len(model) > 300:
            raise ValueError("Choose a model that runs on this computer.")
        if is_cloud_model(model):
            raise ValueError("Choose a model that runs on this computer. Cloud models are not used for Infinite clips.")
        program = self._find_infinite()
        if not program:
            raise ValueError(INSTALL_HINT)
        clip = uuid.uuid4().hex
        folder = self._infinite_folder() / clip
        minutes = self._settings.get("idle_unload_minutes")
        keep_alive = {"keep_alive": f"{minutes}m"} if minutes else {}

        def work(progress, cancel):
            self._prune_infinite_clips()
            options = {**keep_alive, **({"think": False} if self._model_thinks(model) else {})}

            def ask(messages):
                return self._engine.request_ollama(messages, model=model, context_length=infinite_engine.CONTEXT_LENGTH,
                                                   cancel_event=cancel, **options)

            made = infinite_engine.make_clip(description, ask, program, folder, seconds=seconds,
                                             progress=lambda stage, **values: progress(stage=stage, **values),
                                             cancel=cancel)
            return {"clip": clip, "patch": made["patch"], "sound": made["sound"], "warnings": made["warnings"],
                    "replies": made["replies"], "credit": made["credit"], "seconds": seconds}

        return self._jobs.start("infinite", work, key="infinite")

    @json_method
    def infinite_clip(self, clip):
        data = self._infinite_file(clip, "clip.mp4", infinite_engine.MAX_VIDEO_BYTES)
        return {"ok": True, "video": base64.b64encode(data).decode("ascii")}

    @json_method
    def save_infinite_clip(self, clip):
        data = self._infinite_file(clip, "clip.mp4", infinite_engine.MAX_VIDEO_BYTES)
        return self._save_local_file("infinite-clip.mp4", data, suffix=".mp4")

    @json_method
    def save_infinite_patch(self, clip):
        data = self._infinite_file(clip, "patch.inf", infinite_engine.MAX_PATCH_CHARS * 4)
        return self._save_local_file("infinite-patch.inf", data, suffix=".inf")
