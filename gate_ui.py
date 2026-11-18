"""The internet gate's windows: the two-step approval and the connection log."""

import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

import network_gate
from status_bar import mono_family

CONFIRM_SECONDS = 3


class ApprovalDialog:
    """Two confirmations before anything goes online. Block is the default:
    closing the window, Escape and Enter all block, and Allow unlocks only
    after a pause on the second step."""

    def __init__(self, app, request, on_done, confirm_seconds=CONFIRM_SECONDS):
        self.app = app
        self.request = request
        self.on_done = on_done
        self.confirm_seconds = confirm_seconds
        self.remaining = confirm_seconds
        self.step = 1
        self.unlocked = False
        self.finished = False
        self.timer = None
        self.focus_tries = 0
        c = app.COLORS
        try:
            self.previous_grab = app.root.grab_current()
        except (tk.TclError, KeyError):
            self.previous_grab = None
        window = self.window = tk.Toplevel(app.root)
        window.title("AI Assist: internet request")
        window.configure(bg=c["background"])
        window.resizable(False, False)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        window.protocol("WM_DELETE_WINDOW", self.block)
        window.bind("<Escape>", lambda _event: self.block())
        window.bind("<Return>", lambda _event: self.block())
        self.heading = tk.Label(window, bg=c["background"], fg=c["ink"], font=("Segoe UI", 14, "bold"),
                                anchor="w", justify="left", wraplength=540)
        self.heading.pack(fill="x", padx=22, pady=(20, 4))
        self.note = tk.Label(window, bg=c["background"], fg=c["muted"], font=("Segoe UI", 10),
                             anchor="w", justify="left", wraplength=540)
        self.note.pack(fill="x", padx=22, pady=(0, 12))
        details = tk.Frame(window, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"])
        details.pack(fill="x", padx=22)
        rows = self.rows()
        for index, (label, value, warn) in enumerate(rows):
            pady = (12 if index == 0 else 3, 12 if index == len(rows) - 1 else 3)
            tk.Label(details, text=label, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9, "bold"),
                     anchor="nw").grid(row=index, column=0, sticky="nw", padx=(14, 12), pady=pady)
            tk.Label(details, text=value, bg=c["surface"], fg=c["amber"] if warn else c["ink"],
                     font=("Segoe UI", 10), anchor="nw", justify="left", wraplength=380).grid(
                row=index, column=1, sticky="nw", padx=(0, 14), pady=pady)
        buttons = tk.Frame(window, bg=c["background"])
        buttons.pack(fill="x", padx=22, pady=18)
        self.block_button = app.button(buttons, "Block", self.block, c["accent"], c["on_accent"], c["accent_hover"])
        self.block_button.pack(side="right")
        self.allow_button = app.button(buttons, "Continue to allow...", self.continue_or_allow,
                                       c["user"], c["ink"], c["border"])
        self.allow_button.pack(side="right", padx=(0, 10))
        self.heading.configure(text="AI Assist wants to use the internet")
        self.note.configure(text="Nothing has been sent yet. Check where it would go and why before you continue.")
        window.after(50, self.take_focus)

    def rows(self):
        request = self.request
        return (
            ("What", request.kind, False),
            ("Where", request.target, False),
            ("Why", request.purpose or "Not declared. Block this unless you expected it.", not request.purpose),
            ("Leaves this computer", request.sends or "Not declared.", not request.sends),
            ("Asked by", request.source, False),
            ("If allowed", f"Only this request, {request.scope}.", False),
        )

    def take_focus(self):
        if self.finished:
            return
        try:
            self.window.lift()
            self.window.focus_force()
            self.block_button.focus_set()
            self.window.grab_set()
        except tk.TclError:
            # The window may not be viewable yet.
            self.focus_tries += 1
            if self.focus_tries < 20:
                self.window.after(100, self.take_focus)

    def continue_or_allow(self):
        if self.finished:
            return
        if self.step == 1:
            self.step = 2
            self.heading.configure(text=f"Second check: allow {self.request.target}?")
            self.note.configure(text="Confirm again to allow it. Allowed requests are written to the connection log "
                                     "and announced with a notification.")
            self.block_button.focus_set()
            self.tick()
        elif self.unlocked:
            self.finish(True)

    def tick(self):
        if self.finished:
            return
        if self.remaining > 0:
            self.allow_button.configure(text=f"Allow ({self.remaining})", state="disabled")
            self.remaining -= 1
            self.timer = self.window.after(1000, self.tick)
        else:
            self.timer = None
            self.unlocked = True
            self.allow_button.configure(text="Allow", state="normal")

    def block(self):
        self.finish(False)

    def finish(self, approved):
        if self.finished:
            return
        self.finished = True
        try:
            if self.timer is not None:
                self.window.after_cancel(self.timer)
            self.window.grab_release()
            self.window.destroy()
        except tk.TclError:
            pass
        previous = self.previous_grab
        if previous is not None:
            try:
                if previous.winfo_exists():
                    previous.grab_set()
            except tk.TclError:
                pass
        self.on_done(approved)


class GateUI:
    """Connects the gate to the window. Requests from worker threads wait here,
    one dialog at a time, until a person answers; closing the app blocks them."""

    POLL_MS = 150

    def __init__(self, app):
        self.app = app
        self.waiting = queue.Queue()
        self.dialog = None
        self.closed = False
        network_gate.set_approver(self.approve)
        self.poll_id = app.root.after(self.POLL_MS, self.poll)

    def approve(self, request):
        if self.closed:
            return False
        if threading.current_thread() is threading.main_thread():
            if self.dialog is not None:
                return False
            answer = []
            self.show(request, answer.append)
            try:
                self.app.root.wait_window(self.dialog.window)
            except (tk.TclError, AttributeError):
                pass
            return bool(answer and answer[0])
        done, answer = threading.Event(), []
        self.waiting.put((request, done, answer))
        while not done.wait(0.25):
            if self.closed:
                return False
        return bool(answer and answer[0])

    def show(self, request, callback):
        def finished(approved):
            self.dialog = None
            callback(approved)
        self.dialog = ApprovalDialog(self.app, request, finished)

    def poll(self):
        if self.closed:
            return
        if self.dialog is None:
            try:
                request, done, answer = self.waiting.get_nowait()
            except queue.Empty:
                pass
            else:
                def answered(approved, done=done, answer=answer):
                    answer.append(approved)
                    done.set()
                try:
                    self.show(request, answered)
                except tk.TclError:
                    answered(False)
        try:
            self.poll_id = self.app.root.after(self.POLL_MS, self.poll)
        except tk.TclError:
            pass

    def close(self):
        self.closed = True
        network_gate.set_approver(None)
        try:
            self.app.root.after_cancel(self.poll_id)
        except tk.TclError:
            pass
        if self.dialog is not None:
            self.dialog.finish(False)
        while True:
            try:
                _request, done, answer = self.waiting.get_nowait()
            except queue.Empty:
                break
            answer.append(False)
            done.set()


class ConnectionLog:
    """Every internet request AI Assist made or tried to make, live."""

    def __init__(self, app):
        self.app = app
        self.window = None
        self.text = None
        self.entries = queue.Queue()

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        c = self.app.COLORS
        window = self.window = tk.Toplevel(self.app.root)
        window.title("Connection log")
        window.geometry("780x540")
        window.minsize(560, 360)
        window.configure(bg=c["background"])
        tk.Label(window, text="Connection log", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 15, "bold"), anchor="w").pack(fill="x", padx=22, pady=(18, 4))
        if network_gate.installed():
            intro = ("Every internet request AI Assist made or tried to make, newest last. Nothing goes online "
                     "without two confirmations. Commands the developer agent runs are separate programs: "
                     "the gate cannot see them, so they are not listed here.")
        else:
            intro = "The internet gate is not active in this session, so requests are not checked."
        tk.Label(window, text=intro, bg=c["background"], fg=c["muted"], font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=720).pack(fill="x", padx=22, pady=(0, 10))
        footer = tk.Frame(window, bg=c["background"])
        footer.pack(side="bottom", fill="x", padx=22, pady=14)
        tk.Label(footer, text=f"Saved in {network_gate.log_path()}", bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)
        self.app.button(footer, "Close", window.destroy, c["user"], c["ink"], c["border"]).pack(side="right")
        text = self.text = scrolledtext.ScrolledText(window, wrap="word", font=(mono_family(self.app.root), 10),
                                                     bg=c["surface"], fg=c["ink"], borderwidth=0, height=16)
        text.tag_configure("allowed", foreground=c["amber"])
        text.tag_configure("blocked", foreground=c["error"])
        text.tag_configure("detail", foreground=c["muted"])
        text.pack(fill="both", expand=True, padx=22)
        entries = network_gate.recent()
        if not entries:
            text.insert("end", "No internet requests yet.\n", "detail")
        for entry in entries:
            self.add(entry)
        text.configure(state="disabled")
        network_gate.add_listener(self.entries.put)
        window.bind("<Destroy>", self.on_destroy, add="+")
        self.poll()

    def add(self, entry):
        text = self.text
        text.configure(state="normal")
        tag = "allowed" if entry["decision"] == "allowed" else "blocked"
        text.insert("end", f"{entry['time']}  {entry['decision'].upper()}  ", tag)
        text.insert("end", f"{entry['kind']}: {entry['target']}\n")
        text.insert("end", f"    why: {entry['purpose'] or 'not declared'} · asked by {entry['source']}\n", "detail")
        text.configure(state="disabled")
        text.see("end")

    def poll(self):
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            while True:
                self.add(self.entries.get_nowait())
        except queue.Empty:
            pass
        self.window.after(400, self.poll)

    def on_destroy(self, event):
        if event.widget is self.window:
            network_gate.remove_listener(self.entries.put)
            self.window = None
            self.entries = queue.Queue()

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
