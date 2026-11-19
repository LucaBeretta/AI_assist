"""Legacy update window; kept separately until the native UI removal."""
import http.client
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from update_checker import check_update


class UpdateChecker:
    def __init__(self, app):
        self.app = app
        self.results = queue.Queue()
        self.busy = False
        self.result = None
        self.window = None
        self.message = "Updates have not been checked yet."
        self.startup_job = self.app.root.after(1500, self.check)

    def close(self):
        if self.startup_job is not None:
            self.app.root.after_cancel(self.startup_job)
            self.startup_job = None

    def check(self):
        self.close()
        if self.busy or self.app.closed or self.app.closing:
            return
        self.busy = True
        self.message = "Checking GitHub for the latest version..."
        self.refresh()

        def worker():
            try:
                self.results.put((check_update(Path(__file__).resolve().parent), None))
            except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError,
                    AttributeError, http.client.HTTPException) as error:
                self.results.put((None, str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.result = result
        if error:
            self.message = "Could not check for updates. Check your connection and retry.\n\n" + error
        elif result["differences"]:
            self.message = ("Your copy differs from the latest GitHub version. An update may be available; "
                            "local edits or a development copy can also cause this.\n\n"
                            f"Latest commit: {result['revision'][:12]}\n"
                            f"Different or missing app files: {len(result['differences'])}\n\n"
                            "Download and extract the complete app into a new folder, then start it there. "
                            "Saved chats stay in your configured data folder.")
        else:
            self.message = "You have the latest version on GitHub.\n\nCommit: " + result["revision"][:12]
        self.refresh()

    def refresh(self):
        if self.window is not None and self.window.winfo_exists():
            self.status.configure(text=self.message)
            self.retry.configure(state="disabled" if self.busy else "normal")
            self.download.configure(state="normal" if self.result and not self.busy else "disabled")

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        window = self.window = tk.Toplevel(self.app.root)
        window.title("AI Assist updates")
        window.geometry("510x350")
        window.transient(self.app.root)
        self.status = ttk.Label(window, text=self.message, wraplength=460, justify="left")
        self.status.pack(fill="both", expand=True, padx=24, pady=24)
        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=24, pady=(0, 20))
        self.retry = ttk.Button(actions, text="Check again", command=self.check)
        self.retry.pack(side="left")
        self.download = ttk.Button(actions, text="Download latest", command=self.download_latest)
        self.download.pack(side="left", padx=8)
        ttk.Button(actions, text="Close", command=window.destroy).pack(side="right")
        self.refresh()
        if self.result is None and not self.busy:
            self.check()

    def download_latest(self):
        if self.result:
            webbrowser.open(self.result["download"])

