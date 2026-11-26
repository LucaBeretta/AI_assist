"""Local hourly report collection, independent of native widgets."""
import json
import logging
import os
import queue
import subprocess
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from app_core import hourly_summary, ollama, privacy
from app_core import settings
from model_advisor import detect_hardware
from quiet_audio import QUIET_THRESHOLD, WindowsQuietDetector, is_quiet
import network_gate

log = logging.getLogger(__name__)

HOUR_SECONDS = 3600
QUIET_SECONDS = 20
POLL_SECONDS = .5


class HourlyStatusReport:
    def __init__(self, store, context, emit, clock=time.monotonic, detector_factory=WindowsQuietDetector):
        self.app = SimpleNamespace(chat_store=store)
        self.context, self.emit, self.clock, self.detector_factory = context, emit, clock, detector_factory
        self.path = store.directory / "hourly-report.settings"
        preferences = settings.load_hourly_report(self.path)
        self.enabled, self.feedback = preferences["enabled"], preferences["feedback"]
        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.detector = self.thread = None
        self.collecting = False
        self.report = None
        self.message = ""
        self.quiet_since = None
        self.next_due = clock() + HOUR_SECONDS

    def start(self):
        with self.lock:
            if self.thread is not None or self.stop.is_set(): return
            if self.enabled: self._start_detector()
            self.thread = threading.Thread(target=self._run, daemon=True, name="webui-hourly")
            self.thread.start()

    def state(self):
        with self.lock:
            return {"ok": True, "enabled": self.enabled, "feedback": self.feedback, "collecting": self.collecting,
                    "report": deepcopy(self.report), "message": self.message}

    def _save(self):
        settings.save_hourly_report(self.path, self.enabled, self.feedback)

    def _start_detector(self):
        try:
            if os.name != "nt": raise RuntimeError("Quiet-time microphone detection is available only on Windows.")
            self.detector = self.detector_factory(self.events)
            self.detector.start()
        except Exception as error:
            self.enabled = False
            self.message = f"Microphone unavailable: {error}"
            self.detector = None
            self._save()

    def set_enabled(self, enabled):
        if type(enabled) is not bool: raise ValueError("Hourly check-in must be true or false.")
        with self.lock:
            if self.stop.is_set(): raise ValueError("Hourly check-in is closed.")
            if enabled == self.enabled: return self.state()
            self.enabled, self.quiet_since, self.message = enabled, None, ""
            self.next_due = self.clock() + HOUR_SECONDS
            if enabled: self._start_detector()
            elif self.detector is not None:
                self.detector.stop(); self.detector = None
            self._save()
            return self.state()

    def rate(self, value):
        if value not in {"useful", "not_useful"}: raise ValueError("Choose useful or not_useful.")
        with self.lock:
            if self.stop.is_set(): raise ValueError("Hourly check-in is closed.")
            self.feedback = value; self._save()
            return self.state()

    def update_updates(self, text):
        with self.lock:
            if not self.stop.is_set() and self.report is not None and self.report["updates"] != text:
                self.report["updates"] = text
                self.emit("hourly_update", report=deepcopy(self.report))

    def refresh(self, context=None):
        context = context if context is not None else self.context()
        with self.lock:
            if self.stop.is_set(): raise ValueError("Hourly check-in is closed.")
            if self.collecting: return self.state()
            self.collecting = True
            threading.Thread(target=self._collect, args=(datetime.now(timezone.utc), context["updates"],
                context["host"], context["model"], context["backend"], context["local"], context["connection"]),
                daemon=True, name="hourly-collection").start()
            return self.state()

    def _notify(self):
        if os.name != "nt": return
        try:
            command, environment = network_gate.notification_command("AI Assist · quiet-hour check-in",
                "Your concise local activity, device, and update summary is ready.")
            if command:
                subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass

    def tick(self):
        with self.lock:
            if self.stop.is_set(): return
            now = self.clock()
            while True:
                try: kind, value = self.events.get_nowait()
                except queue.Empty: break
                if kind == "report":
                    self.collecting = False
                    self.report = {**value, "generated_at": value["generated_at"].isoformat()}
                    self.emit("hourly_report", report=deepcopy(self.report))
                elif kind == "error":
                    self.message = str(value); self.enabled = False
                    if self.detector: self.detector.stop(); self.detector = None
                    self._save(); self.emit("hourly_state", **self.state())
                elif kind == "level" and self.enabled:
                    if is_quiet(value, QUIET_THRESHOLD):
                        if self.quiet_since is None: self.quiet_since = now
                    else: self.quiet_since = None
            due = self.enabled and not self.collecting and self.quiet_since is not None and now >= self.next_due and now - self.quiet_since >= QUIET_SECONDS
        if due:
            context = self.context()
            if context["busy"]: return
            with self.lock:
                if self.stop.is_set() or not self.enabled or self.collecting: return
                self.next_due, self.quiet_since = self.clock() + HOUR_SECONDS, None
            self._notify(); self.refresh(context)

    def _run(self):
        while not self.stop.is_set():
            self.tick(); self.stop.wait(POLL_SECONDS)

    def close(self):
        with self.lock:
            self.stop.set()
            detector, self.detector = self.detector, None
        if detector: detector.stop()
        if self.thread is not None and self.thread is not threading.current_thread(): self.thread.join(1)

    def _collect(self, now, update_line, host, model, backend, server_is_local, connection_status):
        chats = []
        try:
            if self.app.chat_store is not None:
                chats = hourly_summary.recent_chats(self.app.chat_store.list_chats(), now=now)
        except Exception:
            chats = []
        snapshot = hourly_summary.work_snapshot(chats)
        reflection = hourly_summary.fallback_feedback(snapshot)
        reflected_by_model = False
        local_model = model and not model.casefold().endswith(("-cloud", ":cloud"))
        if snapshot["prompts"] and local_model and backend == "Ollama" and privacy.is_local_url(host):
            prompt = "Recent chat titles and the user's own prompts from the last hour:\n" + json.dumps(
                {"topics": snapshot["topics"], "prompts": snapshot["prompts"]}, ensure_ascii=False)
            try:
                answer = ollama.stream_chat(host, [
                    {"role": "system", "content": "Give a concise, private work check-in in at most two short sentences. Identify progress and one useful next step. Do not invent completed work."},
                    {"role": "user", "content": prompt},
                ], model, context_length=2048)
                if isinstance(answer, str) and answer.strip():
                    reflection = " ".join(answer.split())[:320]
                    reflected_by_model = True
            except Exception as error:
                log.info("The hourly check-in used the plain summary: the local model did not answer (%s).", error)
        try:
            hardware = detect_hardware(host)
        except Exception:
            hardware = {}
        if backend == "Ollama" and server_is_local:
            service_status = "Local Ollama ready" if hardware.get("ollama_running") else "Local Ollama offline"
        else:
            service_status = f"{backend} · {connection_status or 'status unavailable'}"
        self.events.put(("report", {
            "threads": snapshot["threads"],
            "topics": snapshot["topics"],
            "system": hourly_summary.hardware_summary(hardware),
            "system_attention": hourly_summary.hardware_needs_attention(hardware),
            "ollama": service_status,
            "updates": update_line,
            "reflection": reflection,
            "reflected_by_model": reflected_by_model,
            "generated_at": now,
        }))
