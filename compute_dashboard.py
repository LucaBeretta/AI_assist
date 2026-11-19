"""Live compute dashboard for the existing Tkinter AI Assist application.

Only the local node is sampled. Paired-device entries are enrollment records,
not remote telemetry. No LAN listeners, discovery or remote calls are started.
Worker threads never access Tk widgets. The window can be closed safely while
a probe is in flight.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from compute_telemetry import TelemetrySampler


POLL_MS = 5000


def display_bytes(value):
    if type(value) not in (int, float) or value < 0:
        return "unavailable"
    return f"{value / 1024 ** 3:.1f} GiB"


def usage_percent(value):
    if type(value) not in (int, float) or value < 0 or value > 100:
        return "unavailable"
    return f"{value:.1f}%"


def normalize_peer_rows(records):
    """Render only non-sensitive pairing metadata. Never expose secret tokens."""
    if not isinstance(records, dict):
        return []
    rows = []
    for peer_id, item in sorted(records.items()):
        if not isinstance(peer_id, str) or not isinstance(item, dict):
            continue
        address = item.get("address")
        port = item.get("port")
        # Enrollment is not evidence the peer is online, authentic *now*,
        # or allowed to offer telemetry. Do not contact this address.
        rows.append({
            "id": peer_id[:32],
            "endpoint": f"{address}:{port}" if isinstance(address, str) and
                        type(port) is int and 1 <= port <= 65535 else "address unavailable",
            "state": "paired · live telemetry unavailable",
        })
    return rows[:32]


def format_snapshot(data):
    """Pure formatted fields; unknown readings are never portrayed as zero."""
    cpu = data.get("cpu") or {}
    ram = data.get("ram") or {}
    total = ram.get("total_bytes")
    free = ram.get("available_bytes")
    used = total - free if type(total) is int and type(free) is int and 0 <= free <= total else None
    model = data.get("models") or {}
    graphics = []
    for gpu in data.get("gpus") or []:
        if not isinstance(gpu, dict):
            continue
        graphics.append({
            "name": str(gpu.get("name", "GPU unavailable"))[:96],
            "percent": usage_percent(gpu.get("percent")),
            "memory": f"{display_bytes(gpu.get('used_bytes'))} / {display_bytes(gpu.get('total_bytes'))}",
        })
    return {
        "device": str(data.get("device") or "Local workstation"),
        "system": str(data.get("platform") or "unknown OS"),
        "cpu": str(cpu.get("name") or "Unknown CPU"),
        "cpu_usage": usage_percent(cpu.get("percent")),
        "cores": str(cpu.get("logical_cores") or "unavailable"),
        "ram": f"{display_bytes(used)} / {display_bytes(total)}",
        "ram_free": display_bytes(free),
        "gpus": graphics,
        "installed": list(model.get("installed") or []),
        "loaded": list(model.get("loaded") or []),
        "models_available": model.get("available") is True,
        "model_note": str(model.get("note") or ""),
    }


class ComputeDashboard:
    """A compact, scrollable metrics window. No requests to LAN peers."""

    def __init__(self, app, peer_records=None, sampler=None):
        self.app = app
        self.root = app.root
        self.peer_records = peer_records or (lambda: {})
        self.sampler = sampler or TelemetrySampler()
        self.events = queue.Queue()
        self.closed = False
        self.working = False
        self.last_at = None
        self._after_id = None

        self.window = tk.Toplevel(self.root)
        self.window.title("AI Assist | Network compute")
        self.window.geometry("860x680")
        self.window.minsize(570, 440)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        c = app.COLORS
        self.window.configure(bg=c["background"])

        top = tk.Frame(self.window, bg=c["background"])
        top.pack(fill="x", padx=20, pady=(16, 9))
        tk.Label(top, text="COMPUTE NETWORK", font=("Segoe UI", 15, "bold"),
                 bg=c["background"], fg=c["ink"]).pack(side="left")
        self.refresh_button = tk.Button(top, text="Refresh", command=self.refresh,
                                        relief="flat", cursor="hand2",
                                        bg=c["accent"], fg=c.get("on_accent", "white"),
                                        padx=12, pady=5)
        self.refresh_button.pack(side="right")
        self.status = tk.Label(self.window, text="Connecting to local telemetry...",
                               bg=c["background"], fg=c["muted"], anchor="w")
        self.status.pack(fill="x", padx=20, pady=(0, 8))

        wrapper = tk.Frame(self.window, bg=c["background"])
        wrapper.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.canvas = tk.Canvas(wrapper, bg=c["background"], highlightthickness=0)
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.content = tk.Frame(self.canvas, bg=c["background"])
        content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(content_id, width=e.width))

        self.local_section = self._section("THIS DEVICE")
        self.cpu_label = self._label(self.local_section, "CPU: detecting...")
        self.cpu_rate = self._label(self.local_section, "CPU load: unavailable")
        self.ram_label = self._label(self.local_section, "RAM: detecting...")
        self.gpu_section = self._section("GPU")
        self.gpu_label = self._label(self.gpu_section, "GPU metrics: checking...")
        self.model_section = self._section("OLLAMA MODELS")
        self.model_label = self._label(self.model_section, "Installed / loaded: checking...")
        self.peers_section = self._section("PAIRED DEVICES")
        self.peer_label = self._label(
            self.peers_section,
            "Paired device performance is unavailable until an authenticated telemetry API is implemented.")
        self.privacy = self._label(
            self._section("NETWORK AND PRIVACY"),
            "Read-only local hardware polling. No discovery or LAN telemetry requests. "
            "Sharing remains manually activated and OFF by default.")

        self.refresh()
        self._tick()

    def _section(self, title):
        c = self.app.COLORS
        panel = tk.Frame(self.content, bg=c["surface"],
                         highlightbackground=c["border"], highlightthickness=1)
        panel.pack(fill="x", padx=5, pady=(4, 10))
        tk.Label(panel, text=title, font=("Segoe UI", 9, "bold"), anchor="w",
                 bg=c["surface"], fg=c["accent"]).pack(fill="x", padx=14, pady=(12, 6))
        return panel

    def _label(self, panel, text):
        c = self.app.COLORS
        label = tk.Label(panel, text=text, bg=c["surface"], fg=c["ink"],
                         font=("Segoe UI", 10), anchor="w", justify="left",
                         wraplength=710)
        label.pack(fill="x", padx=14, pady=(0, 12))
        label.bind("<Configure>", lambda e: label.config(wraplength=max(180, e.width - 10)))
        return label

    def refresh(self):
        if self.closed or self.working:
            return
        self.working = True
        self.refresh_button.config(state="disabled")
        self.status.config(text="Reading local hardware and model status...")
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        try:
            self.events.put((self.sampler.sample(), None))
        except Exception as error:
            self.events.put((None, str(error)[:200]))

    def _tick(self):
        if self.closed:
            return
        try:
            while True:
                data, error = self.events.get_nowait()
                self.working = False
                self.refresh_button.config(state="normal")
                if error:
                    self.status.config(text="Metrics unavailable: " + error)
                else:
                    self._render(data)
        except queue.Empty:
            pass
        try:
            self._render_peers()
        except (OSError, ValueError, TypeError):
            self.peer_label.config(text="Paired-device records could not be read. No connections attempted.")
        if not self.working and (self.last_at is None or time.monotonic() - self.last_at > POLL_MS / 1000):
            self.refresh()
        self._after_id = self.root.after(300, self._tick)

    def _render(self, data):
        formatted = format_snapshot(data)
        self.last_at = time.monotonic()
        self.status.config(text=f"{formatted['device']} · {formatted['system']} · local metrics")
        self.cpu_label.config(text=f"CPU · {formatted['cpu']}   |   {formatted['cores']} logical cores")
        self.cpu_rate.config(text=f"CPU load · {formatted['cpu_usage']}")
        self.ram_label.config(text=f"RAM used · {formatted['ram']}   |   Available {formatted['ram_free']}")
        if formatted["gpus"]:
            self.gpu_label.config(text="\n".join(
                f"{g['name']}\n  Load {g['percent']} · Memory {g['memory']}"
                for g in formatted["gpus"]))
        else:
            self.gpu_label.config(text="GPU metrics unavailable on this system or driver.")
        if formatted["models_available"]:
            installed = ", ".join(formatted["installed"]) or "none"
            loaded = ", ".join(formatted["loaded"]) or "none"
            self.model_label.config(text=f"Installed ({len(formatted['installed'])} shown): {installed}\n"
                                         f"Loaded ({len(formatted['loaded'])} shown): {loaded}")
        else:
            self.model_label.config(text=formatted["model_note"] or "Local Ollama is unavailable.")

    def _render_peers(self):
        rows = normalize_peer_rows(self.peer_records())
        if not rows:
            self.peer_label.config(text="No paired devices loaded. Discovery is OFF. "
                                        "Pair devices explicitly to list them here.")
            return
        self.peer_label.config(text="\n".join(
            f"{row['id']}   ·   {row['endpoint']}\n   {row['state']}"
            for row in rows))

    def focus(self):
        if not self.closed:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.window.destroy()
