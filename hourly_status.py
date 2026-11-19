"""The optional hourly check-in: after an hour of work and a quiet room, a short
local summary of recent chats, this computer's health, and update status.

Off by default. When on, the microphone gives sound levels only (quiet_audio);
no audio is kept or sent. The summary uses a local Ollama model when one is
selected, otherwise a fixed suggestion."""

from datetime import datetime, timezone
import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

from app_core import hourly_summary, ollama, privacy, settings as app_settings
from model_advisor import detect_hardware
import network_gate
from quiet_audio import QUIET_THRESHOLD, WindowsQuietDetector, is_quiet


HOUR_SECONDS = 60 * 60
QUIET_SECONDS = 20
POLL_MS = 500


class HourlyStatusReport:
    def __init__(self, app):
        self.app = app
        self.settings_path = app.chat_store.directory / "hourly-report.settings" if app.chat_store else None
        self.preferences = app_settings.load_hourly_report(self.settings_path)
        self.enabled = self.preferences["enabled"]
        self.feedback = self.preferences["feedback"]
        self.events = queue.Queue()
        self.detector = None
        self.window = None
        self.cards = None
        self.state_label = None
        self.feedback_label = None
        self.enabled_var = None
        self.report = None
        self.collecting = False
        self.next_due = time.monotonic() + HOUR_SECONDS
        self.quiet_since = None
        self.closed = False
        self.status_message = ""
        self._state_label_text = None
        self.last_update_line = ""
        if self.enabled and not self._start_detector():
            self.enabled = False
            self._save_preferences()
        self.poll_id = self.app.root.after(POLL_MS, self.poll)

    def open(self, preview=False):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
        else:
            self._build_window()
        if preview:
            self.refresh()
        return self.window

    def _build_window(self):
        c = self.app.COLORS
        window = self.window = tk.Toplevel(self.app.root)
        window.title("AI Assist · Hourly check-in")
        window.geometry("470x610")
        window.minsize(430, 560)
        window.configure(bg=c["background"])
        window.protocol("WM_DELETE_WINDOW", self._close_window)
        header = tk.Frame(window, bg=c["sidebar"], padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="AI ASSIST   /   LOCAL CHECK-IN", bg=c["sidebar"], fg=c["sidebar_muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(header, text="Your quiet-hour snapshot", bg=c["sidebar"], fg=c["on_accent"],
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(6, 2))
        tk.Label(header, text="A quick read on your work, device, and updates.", bg=c["sidebar"],
                 fg=c["sidebar_muted"], font=("Segoe UI", 9)).pack(anchor="w")

        controls = tk.Frame(window, bg=c["background"], padx=20, pady=12)
        controls.pack(fill="x")
        self.enabled_var = tk.BooleanVar(window, value=self.enabled)
        toggle = ttk.Checkbutton(controls, text="Enable hourly quiet-time check-ins",
                                 variable=self.enabled_var, command=self._toggle_from_ui)
        toggle.pack(anchor="w")
        self.state_label = tk.Label(controls, text="", bg=c["background"], fg=c["muted"],
                                    font=("Segoe UI", 8), justify="left", wraplength=420)
        self._state_label_text = None
        self.state_label.pack(anchor="w", pady=(4, 0))

        action_row = tk.Frame(controls, bg=c["background"])
        action_row.pack(fill="x", pady=(9, 0))
        ttk.Button(action_row, text="Preview now", command=self.refresh).pack(side="left")
        ttk.Button(action_row, text="Close", command=self._close_window).pack(side="right")

        self.cards = tk.Frame(window, bg=c["background"], padx=20)
        self.cards.pack(fill="both", expand=True, pady=(0, 8))
        self.feedback_label = tk.Label(window, text="", bg=c["background"], fg=c["muted"],
                                       font=("Segoe UI", 8))
        self.feedback_label.pack(anchor="w", padx=22, pady=(0, 4))
        feedback_row = tk.Frame(window, bg=c["background"], padx=20, pady=14)
        feedback_row.pack(fill="x")
        tk.Label(feedback_row, text="Was this useful?", bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        ttk.Button(feedback_row, text="Yes", command=lambda: self.rate("useful")).pack(side="right", padx=(6, 0))
        ttk.Button(feedback_row, text="Not quite", command=lambda: self.rate("not_useful")).pack(side="right")
        self._update_controls()
        self._render_report()

    def _close_window(self):
        if self.window is not None:
            self.window.destroy()
        self.window = None
        self.cards = None
        self.state_label = None
        self.feedback_label = None
        self.enabled_var = None

    def _toggle_from_ui(self):
        if self.enabled_var is not None:
            self.set_enabled(self.enabled_var.get())

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and os.name != "nt":
            self.status_message = "Quiet-time microphone detection is available only on Windows."
            self.enabled = False
            if self.enabled_var is not None:
                self.enabled_var.set(False)
            self._update_controls()
            return False
        if enabled == self.enabled:
            return True
        self.enabled = enabled
        self.quiet_since = None
        self.next_due = time.monotonic() + HOUR_SECONDS
        self.status_message = ""
        if enabled and not self._start_detector():
            self.enabled = False
            if self.enabled_var is not None:
                self.enabled_var.set(False)
        elif not enabled:
            self._stop_detector()
        self._save_preferences()
        self._update_controls()
        return self.enabled

    def _start_detector(self):
        try:
            detector = WindowsQuietDetector(self.events)
            detector.start()
            self.detector = detector
            self.status_message = ""
            return True
        except Exception as error:
            self.status_message = f"Microphone unavailable: {error}"
            self.detector = None
            return False

    def _stop_detector(self):
        detector = self.detector
        self.detector = None
        if detector is not None:
            detector.stop()

    def _save_preferences(self):
        self.preferences = {"enabled": self.enabled, "feedback": self.feedback}
        if self.settings_path is not None:
            try:
                app_settings.save_hourly_report(self.settings_path, self.enabled, self.feedback)
            except OSError as error:
                self.status_message = f"Could not save hourly report preferences: {error}"

    def _update_controls(self):
        if self.enabled_var is not None:
            self.enabled_var.set(self.enabled)
        if self.state_label is None or not self.state_label.winfo_exists():
            return
        if self.status_message:
            text = self.status_message
        elif self.enabled:
            text = "Listening for quiet locally · sound level only · no audio is saved or sent."
        else:
            text = "Off by default. When on, only sound levels are measured; no audio is saved or sent."
        if text != self._state_label_text:
            self.state_label.configure(text=text)
            self._state_label_text = text
        if self.feedback_label is not None:
            text = {"useful": "Thanks — feedback is saved on this device.",
                    "not_useful": "Thanks — feedback is saved on this device."}.get(self.feedback, "")
            self.feedback_label.configure(text=text)

    def _host_for_local_work(self):
        if getattr(self.app, "backend", "Ollama") == "Ollama":
            try:
                host = self.app.ollama_url()
                if privacy.is_local_url(host):
                    return host
            except (RuntimeError, ValueError, AttributeError):
                pass
        return "http://localhost:11434"

    def _update_line(self):
        checker = getattr(self.app, "update_checker", None)
        if checker is None:
            return "Update status is unavailable."
        if checker.busy:
            return "Checking GitHub for updates…"
        result = checker.result
        if result is not None:
            if result.get("differences"):
                return "Your app files differ from the latest GitHub copy."
            revision = str(result.get("revision", ""))[:8]
            return f"Up to date · {revision}" if revision else "Up to date · checked on GitHub"
        message = str(getattr(checker, "message", ""))
        if message and message != "Updates have not been checked yet.":
            if "blocked" in message.lower():
                return "Last check was blocked by your privacy settings."
            return "Last check could not be completed."
        return "Not checked yet · check only when you choose."

    def refresh(self):
        self.open()
        self._render_report(loading=True)
        if self.collecting:
            return
        self.collecting = True
        work_started = datetime.now(timezone.utc)
        update_line = self._update_line()
        host = self._host_for_local_work()
        model = self.app.model_name.get() if hasattr(self.app, "model_name") else ""
        backend = getattr(self.app, "backend", "Ollama")
        try:
            selected_host = self.app.ollama_url() if backend == "Ollama" else ""
            server_is_local = privacy.is_local_url(selected_host)
        except (RuntimeError, ValueError, AttributeError):
            server_is_local = False
        try:
            connection_status = self.app.connection_label.cget("text")
        except (AttributeError, tk.TclError):
            connection_status = ""
        threading.Thread(target=self._collect,
                         args=(work_started, update_line, host, model, backend, server_is_local, connection_status),
                         daemon=True, name="ai-assist-hourly-report").start()

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
        local_model = bool(model) and not privacy.is_cloud_model(model)
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
            except Exception:
                pass
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

    def _render_report(self, loading=False):
        if self.cards is None or not self.cards.winfo_exists():
            return
        c = self.app.COLORS
        for child in self.cards.winfo_children():
            child.destroy()
        if loading:
            tk.Label(self.cards, text="Gathering your local check-in…", bg=c["background"], fg=c["muted"],
                     font=("Segoe UI", 10)).pack(anchor="w", pady=16)
            return
        if self.report is None:
            tk.Label(self.cards, text="Your short work, device, and update summary will appear here.",
                     bg=c["background"], fg=c["muted"], font=("Segoe UI", 9), wraplength=410,
                     justify="left").pack(anchor="w", pady=16)
            return
        topics = self.report["topics"]
        work = (f"{self.report['threads']} chat{'s' if self.report['threads'] != 1 else ''} updated"
                if self.report["threads"] else "No chats updated in the last hour")
        if topics:
            work += " · " + " / ".join(topics[:2])
        self._card("01  WORK", work, c["accent"])
        device_accent = c.get("amber", c["accent"]) if self.report["system_attention"] else c["success"]
        self._card("02  DEVICE", f"{self.report['system']}\n{self.report['ollama']}", device_accent)
        self._update_card()
        label = "AI REFLECTION" if self.report["reflected_by_model"] else "NEXT STEP"
        self._card("04  " + label, self.report["reflection"], c.get("amber", c["accent"]))

    def _card(self, heading, body, accent):
        c = self.app.COLORS
        card = tk.Frame(self.cards, bg=c["surface"], highlightthickness=1,
                        highlightbackground=c["border"], padx=12, pady=8)
        card.pack(fill="x", pady=(0, 8))
        stripe = tk.Frame(card, bg=accent, width=3)
        stripe.pack(side="left", fill="y", padx=(0, 10))
        content = tk.Frame(card, bg=c["surface"])
        content.pack(side="left", fill="x", expand=True)
        tk.Label(content, text=heading, bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(content, text=body, bg=c["surface"], fg=c["ink"], font=("Segoe UI", 9),
                 wraplength=385, justify="left").pack(anchor="w", pady=(3, 0))

    def _update_card(self):
        c = self.app.COLORS
        card = tk.Frame(self.cards, bg=c["surface"], highlightthickness=1,
                        highlightbackground=c["border"], padx=12, pady=8)
        card.pack(fill="x", pady=(0, 8))
        tk.Frame(card, bg=c["amber"], width=3).pack(side="left", fill="y", padx=(0, 10))
        content = tk.Frame(card, bg=c["surface"])
        content.pack(side="left", fill="x", expand=True)
        tk.Label(content, text="03  APP UPDATES", bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(content, text=self.report["updates"], bg=c["surface"], fg=c["ink"],
                 font=("Segoe UI", 9), wraplength=320, justify="left").pack(anchor="w", pady=(3, 0))
        ttk.Button(content, text="Check now", command=self._check_updates).pack(anchor="w", pady=(5, 0))

    def _check_updates(self):
        self.app.update_checker.open()
        if self.report is not None:
            self.report["updates"] = self._update_line()
        self._render_report()

    def rate(self, value):
        if value not in {"useful", "not_useful"}:
            return
        self.feedback = value
        self._save_preferences()
        self._update_controls()

    def _show_system_notification(self):
        if os.name != "nt":
            return
        try:
            command, environment = network_gate.notification_command(
                "AI Assist · quiet-hour check-in",
                "Your concise local activity, device, and update summary is ready."
            )
            if command is None:
                return
            options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        except OSError:
            pass

    def _trigger_report(self):
        self.next_due = time.monotonic() + HOUR_SECONDS
        self.quiet_since = None
        self._show_system_notification()
        try:
            self.app.root.deiconify()
            self.app.root.lift()
        except tk.TclError:
            pass
        self.open(preview=True)

    def poll(self):
        if self.closed:
            return
        now = time.monotonic()
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "report":
                self.collecting = False
                self.report = value
                self.last_update_line = value["updates"]
                self._render_report()
            elif kind == "error":
                self.status_message = value
                self._stop_detector()
                self.enabled = False
                self._save_preferences()
            elif kind == "level" and self.enabled:
                if is_quiet(value, QUIET_THRESHOLD):
                    if self.quiet_since is None:
                        self.quiet_since = now
                else:
                    self.quiet_since = None
        if self.enabled and self.quiet_since is not None and now >= self.next_due:
            if now - self.quiet_since >= QUIET_SECONDS and not self.app.is_waiting and not self.app.agent_active:
                self._trigger_report()
        if self.report is not None:
            update_line = self._update_line()
            if update_line != self.last_update_line:
                self.report["updates"] = update_line
                self.last_update_line = update_line
                self._render_report()
        self._update_controls()
        try:
            self.poll_id = self.app.root.after(POLL_MS, self.poll)
        except tk.TclError:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._stop_detector()
        try:
            self.app.root.after_cancel(self.poll_id)
        except (tk.TclError, AttributeError):
            pass
        self._close_window()
