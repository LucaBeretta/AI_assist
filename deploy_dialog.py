"""Vercel production deployment controls for the Tk workspace."""

from pathlib import Path
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
import webbrowser

import network_gate
from vercel_deploy import DeploymentCancelled, validate_project
from vercel_api import deploy_project_api as deploy_project


class DeployDialog:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.closed = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.result = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Deploy to Vercel (API)")
        self.window.geometry("760x650")
        self.window.minsize(600, 520)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.folder = tk.StringVar(self.window, value=app.project_path)
        default = re.sub(r"[^a-z0-9_-]", "-", Path(app.project_path).name.lower())[:100].strip("-")
        self.project = tk.StringVar(self.window, value=default)
        self.scope = tk.StringVar(self.window)
        self.domain = tk.StringVar(self.window)
        self.token = tk.StringVar(self.window, value=os.environ.get("VERCEL_TOKEN", ""))
        tk.Label(self.window, text="Deploy your web project", font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=20, pady=(18, 6))
        tk.Label(self.window, text="Publishes this folder to production in your Vercel account. An existing project's live site may be updated.",
                 wraplength=700, justify="left").pack(anchor="w", padx=20)
        form = tk.Frame(self.window)
        form.pack(fill="x", padx=20, pady=12)
        form.columnconfigure(1, weight=1)
        self.inputs = []
        for row, (label, variable) in enumerate((("Web project folder", self.folder), ("Vercel project name", self.project),
                ("Team slug or ID (optional)", self.scope), ("Domain (optional)", self.domain),
                ("Vercel access token", self.token))):
            tk.Label(form, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            entry = ttk.Entry(form, textvariable=variable, show="*" if variable is self.token else "")
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            self.inputs.append(entry)
        self.browse_button = tk.Button(form, text="Browse…", command=self.browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        tk.Button(form, text="Create token", command=lambda: webbrowser.open("https://vercel.com/account/tokens")).grid(row=4, column=2, padx=(8, 0))
        tk.Label(self.window, text="Paste a Vercel access token or set VERCEL_TOKEN. The token is kept in memory only.\n"
                 "Enter a domain you control, such as example.com. DNS and ownership verification may still be required.",
                 wraplength=700, justify="left").pack(anchor="w", padx=20)
        actions = tk.Frame(self.window)
        actions.pack(fill="x", padx=20, pady=12)
        self.deploy_button = tk.Button(actions, text="Deploy to production", command=self.start)
        self.deploy_button.pack(side="left")
        self.cancel_button = tk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = tk.Button(actions, text="Open deployment", command=self.open_site, state="disabled")
        self.open_button.pack(side="left")
        self.domain_button = tk.Button(actions, text="Open domain", command=self.open_domain, state="disabled")
        self.domain_button.pack(side="left", padx=8)
        tk.Button(actions, text="Vercel dashboard", command=lambda: webbrowser.open("https://vercel.com/dashboard")).pack(side="right")
        self.progress = ttk.Progressbar(self.window, mode="indeterminate")
        self.progress.pack(fill="x", padx=20)
        self.status = tk.Label(self.window, text="Ready. No files have been uploaded.", anchor="w", wraplength=700, justify="left")
        self.status.pack(fill="x", padx=20, pady=8)
        self.log = scrolledtext.ScrolledText(self.window, wrap="word", height=10, font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.poll_id = self.window.after(100, self.process_events)

    def browse(self):
        folder = filedialog.askdirectory(parent=self.window, title="Choose the web project to deploy")
        if folder:
            self.folder.set(folder)
            self.project.set(re.sub(r"[^a-z0-9_-]", "-", Path(folder).name.lower())[:100].strip("-"))

    def start(self):
        if self.running or self.app.closing:
            return
        if self.app.busy():
            self.status.config(text="Wait for the current workspace operation to finish.")
            return
        try:
            values = validate_project(self.folder.get(), self.project.get().strip(), self.scope.get().strip(), self.domain.get())
            token = self.token.get().strip()
            if not token or any(char.isspace() for char in token):
                raise ValueError("Enter a Vercel access token, or set VERCEL_TOKEN before starting AI Assist.")
        except (OSError, ValueError, RuntimeError) as error:
            self.status.config(text=str(error))
            return
        self.result = None
        self.open_button.config(state="disabled")
        self.domain_button.config(state="disabled")
        self.cancel.clear()
        self.running = True
        self.progress.start(12)
        self.controls()
        self.app.update_controls()
        self.status.config(text="Starting Vercel deployment…")
        self.append_log(f"Backend: Vercel REST API\nFolder: {values[0]}\nProject: {values[1]}\nTeam: {values[2] or 'auto-detect'}\nDomain: {values[3] or 'Vercel URL only'}")
        self.worker = threading.Thread(target=self.deploy_worker, args=(*values, token), daemon=True)
        self.worker.start()

    def deploy_worker(self, folder, project, scope, domain, token):
        try:
            with network_gate.task(f"Deploy {Path(folder).name} to Vercel as {project}",
                                   sends="This project's files (secrets and ignored files excluded) and your "
                                         "Vercel access token, to Vercel.", minutes=20):
                result = deploy_project(folder, project, scope, domain,
                    lambda text: self.events.put(("log", text)), self.cancel, token=token)
            self.events.put(("done", result))
        except DeploymentCancelled as error:
            self.events.put(("error", str(error)))
        except Exception as error:
            self.events.put(("error", f"Deployment failed: {error}".replace(token, "[redacted]")))

    def append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n\n")
        if int(self.log.index("end-1c").split(".")[0]) > 1500:
            self.log.delete("1.0", "500.0")
        self.log.config(state="disabled")
        self.log.see("end")

    def controls(self):
        for widget in self.inputs + [self.browse_button, self.deploy_button]:
            widget.config(state="disabled" if self.running else "normal")
        self.cancel_button.config(state="normal" if self.running else "disabled")

    def process_events(self):
        if self.closed:
            return
        for _ in range(50):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(value)
                self.status.config(text=value[:250])
                continue
            self.running = False
            self.progress.stop()
            self.controls()
            self.app.update_controls()
            if kind == "error":
                self.status.config(text=value)
                self.append_log(value)
            else:
                self.result = value
                self.open_button.config(state="normal")
                self.domain_button.config(state="normal" if value["domain_assigned"] else "disabled")
                message = f"Deployment created: {value['url']}"
                if value["domain_error"]:
                    message += "\nDomain setup needs attention; the deployment URL is still available."
                    self.append_log(value["domain_error"])
                elif value["domain_assigned"]:
                    message += f"\nDomain assigned: {value['domain']}. DNS and HTTPS may need time or verification."
                self.status.config(text=message)
                self.append_log(message)
        self.poll_id = self.window.after(100 if self.running else 250, self.process_events)

    def open_site(self):
        if self.result:
            webbrowser.open(self.result["url"])

    def stop(self):
        if self.running:
            self.cancel.set()
            self.status.config(text="Stopping locally… A submitted deployment may continue on Vercel.")

    def open_domain(self):
        if self.result and self.result["domain_assigned"]:
            webbrowser.open("https://" + self.result["domain"])

    def close(self, force=False):
        if self.closed:
            return
        if self.running and not force:
            self.stop()
            return
        self.cancel.set()
        self.closed = True
        self.token.set("")
        self.window.after_cancel(self.poll_id)
        self.window.destroy()
