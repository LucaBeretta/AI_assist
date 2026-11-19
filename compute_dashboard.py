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
from collections import deque

from compute_telemetry import TelemetrySampler
from dashboard_placement import DashboardPlacement, geometry_string


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


# A fixed, resize-driven layout: all five panels fit inside the visible canvas.
DASHBOARD_COLORS = {
    "background": "#09121F", "panel": "#111F30", "outline": "#23354A",
    "ink": "#EAF2FF", "muted": "#91A6BE", "faint": "#42566D",
    "teal": "#4FE0B4", "blue": "#7FB6FF", "amber": "#F3BD73",
    "chip": "#1B2E44", "danger": "#EF8888",
}


def dashboard_layout(width, height):
    """Pure geometry for 480x240 content and larger, never a scrollregion."""
    width, height = max(1, int(width)), max(1, int(height))
    margin = min(18, max(7, round(width * .018)))
    gap = min(12, max(6, round(width * .012)))
    usable_w = max(1, width - 2 * margin)
    usable_h = max(1, height - 2 * margin - gap)
    top_height = round(usable_h * .52)
    bottom_height = usable_h - top_height
    metric_w = (usable_w - 2 * gap) // 3
    metrics = {}
    for index, name in enumerate(("cpu", "gpu", "ram")):
        x = margin + index * (metric_w + gap)
        metrics[name] = (x, margin,
                         metric_w if index < 2 else width - margin - x,
                         top_height)
    lower_y = margin + top_height + gap
    models_w = round((usable_w - gap) * .57)
    metrics["models"] = (margin, lower_y, models_w, bottom_height)
    metrics["peers"] = (margin + models_w + gap, lower_y,
                        usable_w - models_w - gap, bottom_height)
    return metrics


def _value_ratio(numerator, denominator):
    if (type(numerator) not in (int, float) or type(denominator) not in (int, float)
            or denominator <= 0 or not 0 <= numerator <= denominator):
        return None
    return numerator / denominator


def _short(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


class ComputeDashboard:
    """Fixed-grid compute instrument panel with zero vertical/horizontal scrolling.

    Samples only local hardware and the loopback model API. A saved pairing
    record does not establish a live connection or authorize remote telemetry.
    No discovery, listener, LAN probe, or remote execution is started here.
    """

    def __init__(self, app, peer_records=None, sampler=None):
        self.app = app
        self.root = app.root
        self.peer_records = peer_records or (lambda: {})
        self.sampler = sampler or TelemetrySampler()
        self.events = queue.Queue()
        self.closed = False
        self.working = False
        self.last_at = None
        self.last_attempt = None
        self.latest = None
        self.error = ""
        self.peer_rows = []
        self.history = {key: deque(maxlen=30) for key in ("cpu", "gpu", "ram")}
        self._after_id = None
        self.fullscreen = False
        self.placement = DashboardPlacement()
        self._placement_after = None
        self._restoring = True
        p = DASHBOARD_COLORS

        self.window = tk.Toplevel(self.root)
        self.window.title("AI Assist | Compute monitor")
        self.window.geometry("800x480")
        self.window.minsize(480, 320)
        # Best-effort restore; never use an off-screen coordinate.
        saved_geometry = self.placement.restore(self.root)
        if saved_geometry:
            self.window.geometry(geometry_string(saved_geometry))
        self.window.configure(bg=p["background"])
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<F11>", self.toggle_fullscreen)
        self.window.bind("<Escape>", self.exit_fullscreen)
        self.window.bind("<Control-w>", lambda _event: self.close())
        self.window.bind("<Control-r>", lambda _event: self.refresh())
        self.window.bind("<Command-r>", lambda _event: self.refresh())
        self.window.bind("<Configure>", self._on_window_move)

        header = tk.Frame(self.window, bg=p["background"], height=48)
        header.pack(side="top", fill="x", padx=12, pady=(6, 0))
        tk.Label(header, text="AI ASSIST  /  COMPUTE", font=("Segoe UI", 13, "bold"),
                 bg=p["background"], fg=p["ink"], anchor="w").pack(side="left", fill="x", expand=True)
        self.fullscreen_button = tk.Button(
            header, text="FULL SCREEN  F11", font=("Segoe UI", 9),
            command=self.toggle_fullscreen, bg=p["chip"], fg=p["ink"],
            activebackground=p["outline"], activeforeground=p["ink"],
            padx=8, pady=4, bd=0, relief="flat", cursor="hand2",
            takefocus=True)
        self.fullscreen_button.pack(side="right", padx=(6, 0))
        self.refresh_button = tk.Button(
            header, text="REFRESH", font=("Segoe UI", 9, "bold"),
            command=self.refresh, bg=p["teal"], fg=p["background"],
            activebackground=p["blue"], activeforeground=p["background"],
            padx=10, pady=4, bd=0, relief="flat", cursor="hand2",
            takefocus=True)
        self.refresh_button.pack(side="right")

        footer = tk.Frame(self.window, bg=p["background"], height=25)
        footer.pack(side="bottom", fill="x", padx=14, pady=(0, 5))
        self.privacy_label = tk.Label(
            footer, text="● LOCAL METRICS   ·   PEERS NOT POLLED   ·   SHARING MANUAL",
            font=("Segoe UI", 8), fg=p["teal"], bg=p["background"], anchor="w")
        self.privacy_label.pack(side="left", fill="x", expand=True)
        self.status = tk.Label(footer, text="WAITING", font=("Segoe UI", 8),
                               fg=p["muted"], bg=p["background"], anchor="e")
        self.status.pack(side="right")

        self.canvas = tk.Canvas(self.window, bg=p["background"], highlightthickness=0,
                                borderwidth=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

        # All Tk work happens on the UI thread. The worker communicates via Queue.
        self.window.after_idle(self._finish_restore)
        self.refresh()
        self._tick()

    def _finish_restore(self):
        # Ignore the initial geometry events while the window manager places us.
        if not self.closed:
            self._restoring = False

    def _on_window_move(self, event):
        if self.closed or self.fullscreen or self._restoring or event.widget is not self.window:
            return
        if self._placement_after is not None:
            self.window.after_cancel(self._placement_after)
        self._placement_after = self.window.after(650, self._save_placement)

    def _save_placement(self):
        self._placement_after = None
        if not self.closed and not self.fullscreen:
            self.placement.save(self.window, self.root)

    def _txt(self, x, y, text, color=None, size=10, bold=False, anchor="nw"):
        self.canvas.create_text(x, y, text=text, fill=color or DASHBOARD_COLORS["ink"],
                                font=("Segoe UI", size, "bold" if bold else "normal"),
                                anchor=anchor)

    def _panel(self, rect):
        x, y, width, height = rect
        self.canvas.create_rectangle(x + 1, y + 2, x + width + 1, y + height + 2,
                                     fill="#07101B", outline="")
        self.canvas.create_rectangle(x, y, x + width, y + height,
                                     fill=DASHBOARD_COLORS["panel"],
                                     outline=DASHBOARD_COLORS["outline"])

    def _ring(self, x, y, radius, ratio, color, *, line=5):
        box = (x - radius, y - radius, x + radius, y + radius)
        self.canvas.create_oval(*box, outline=DASHBOARD_COLORS["outline"], width=line)
        if ratio is not None and ratio > 0:
            self.canvas.create_arc(*box, start=90, extent=-359.9 * ratio,
                                   style="arc", outline=color, width=line)

    def _spark(self, rect, key, color, compact=False):
        x, y, width, height = rect
        left, right = x + 12, x + width - 12
        bottom = y + height - 10
        top = bottom - (14 if compact else 24)
        if right <= left:
            return
        self.canvas.create_line(left, bottom, right, bottom,
                                fill=DASHBOARD_COLORS["outline"], width=1)
        values = list(self.history[key])
        if len(values) < 2:
            return
        step = (right - left) / (len(values) - 1)
        segment = []
        for i, value in enumerate(values):
            if value is None:
                if len(segment) > 1:
                    self.canvas.create_line(*segment, fill=color, width=2, smooth=True)
                segment = []
            else:
                segment.extend((left + i * step, bottom - value * (bottom - top)))
        if len(segment) > 1:
            self.canvas.create_line(*segment, fill=color, width=2, smooth=True)

    def _metric(self, rect, name, percent, ratio, detail, color, key, compact):
        self._panel(rect)
        x, y, width, height = rect
        pad = 10 if compact else 15
        label_size, value_size, detail_size = ((8, 20, 8) if compact else (10, 28, 9))
        self._txt(x + pad, y + 10, name, DASHBOARD_COLORS["muted"], label_size, True)
        self._txt(x + pad, y + (29 if compact else 36), percent, color, value_size, True)
        radius = min(27 if compact else 35, max(10, width // 5), max(10, height // 4))
        self._ring(x + width - pad - radius, y + (51 if compact else 70),
                   radius, ratio, color, line=4 if compact else 6)
        max_chars = max(9, int((width - pad * 2) / (5.3 if compact else 6.2)))
        detail_y = y + height - (35 if compact else 46)
        self._txt(x + pad, detail_y, _short(detail, max_chars),
                  DASHBOARD_COLORS["muted"], detail_size)
        self._spark(rect, key, color, compact=compact)

    def _models(self, rect, snapshot, compact):
        self._panel(rect)
        x, y, width, height = rect
        model = snapshot.get("models") or {}
        loaded = list(model.get("loaded") or [])
        installed = list(model.get("installed") or [])
        self._txt(x + 12, y + 10, "MODELS", DASHBOARD_COLORS["muted"],
                  8 if compact else 10, True)
        if model.get("available") is not True:
            self._txt(x + 12, y + 36, "OLLAMA UNAVAILABLE",
                      DASHBOARD_COLORS["amber"], 10 if compact else 13, True)
            self._txt(x + 12, y + height - 19, "Local server only · no remote probe",
                      DASHBOARD_COLORS["muted"], 8)
            return
        self._txt(x + width - 12, y + 10,
                  f"{len(loaded)} LOADED  /  {len(installed)} INSTALLED",
                  DASHBOARD_COLORS["teal"], 8 if compact else 9, anchor="ne")
        if not loaded:
            self._txt(x + 12, y + 37, "No models loaded", DASHBOARD_COLORS["ink"],
                      11 if compact else 15, True)
            if installed:
                self._txt(x + 12, y + 59, _short("Installed: " + ", ".join(installed),
                                                max(12, int(width / 6))),
                          DASHBOARD_COLORS["muted"], 8 if compact else 9)
            return
        row_h = 19 if compact else 24
        visible = max(1, min(len(loaded), int((height - 40) / row_h)))
        for i, label in enumerate(loaded[:visible]):
            cy = y + 37 + i * row_h
            self.canvas.create_oval(x + 12, cy + 3, x + 18, cy + 9,
                                    fill=DASHBOARD_COLORS["teal"], outline="")
            self._txt(x + 26, cy, _short(label, max(10, int((width - 42) / 6))),
                      DASHBOARD_COLORS["ink"], 9 if compact else 10)
        if len(loaded) > visible:
            self._txt(x + width - 10, y + height - 15, f"+{len(loaded) - visible} more",
                      DASHBOARD_COLORS["muted"], 8, anchor="ne")

    def _peers(self, rect, compact):
        self._panel(rect)
        x, y, width, height = rect
        self._txt(x + 12, y + 10, "PAIRED NODES", DASHBOARD_COLORS["muted"],
                  8 if compact else 10, True)
        self._txt(x + width - 12, y + 10, f"{len(self.peer_rows)} SAVED",
                  DASHBOARD_COLORS["blue"], 8 if compact else 9, anchor="ne")
        if not self.peer_rows:
            self._txt(x + 12, y + 36, "No paired devices", DASHBOARD_COLORS["ink"],
                      10 if compact else 13, True)
            self._txt(x + 12, y + height - 19, "Discovery disabled · manual pairing",
                      DASHBOARD_COLORS["muted"], 8)
            return
        row_h = 30 if compact else 39
        visible = max(1, min(len(self.peer_rows), int((height - 32) / row_h)))
        for i, peer in enumerate(self.peer_rows[:visible]):
            top = y + 33 + i * row_h
            self.canvas.create_oval(x + 12, top + 3, x + 18, top + 9,
                                    fill=DASHBOARD_COLORS["faint"], outline="")
            self._txt(x + 25, top, _short(peer["id"], max(7, int((width - 39) / 6))),
                      DASHBOARD_COLORS["ink"], 9 if compact else 10, True)
            if not compact and row_h >= 39:
                self._txt(x + 25, top + 17, "Paired · telemetry unavailable",
                          DASHBOARD_COLORS["amber"], 8)
        if len(self.peer_rows) > visible:
            self._txt(x + width - 10, y + height - 13,
                      f"+{len(self.peer_rows) - visible} more",
                      DASHBOARD_COLORS["muted"], 8, anchor="ne")

    def redraw(self):
        if self.closed:
            return
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 190 or height < 125:
            self._txt(12, 12, "Resize window for compute metrics", DASHBOARD_COLORS["muted"])
            return
        rects = dashboard_layout(width, height)
        compact = width < 700 or height < 315
        data = self.latest or {}
        cpu = data.get("cpu") or {}
        ram = data.get("ram") or {}
        gpu = next((item for item in (data.get("gpus") or []) if isinstance(item, dict)), {})
        cpu_percent = cpu.get("percent")
        gpu_percent = gpu.get("percent")
        ram_total, ram_free = ram.get("total_bytes"), ram.get("available_bytes")
        ram_used = (ram_total - ram_free if type(ram_total) is int and
                    type(ram_free) is int and 0 <= ram_free <= ram_total else None)
        ram_ratio = _value_ratio(ram_used, ram_total)
        cpu_ratio = _value_ratio(cpu_percent, 100)
        gpu_ratio = _value_ratio(gpu_percent, 100)
        self._metric(rects["cpu"], "CPU", usage_percent(cpu_percent) if cpu_ratio is not None else "—",
                     cpu_ratio, f'{cpu.get("logical_cores") or "?"} cores · {cpu.get("name") or "unknown"}',
                     DASHBOARD_COLORS["teal"], "cpu", compact)
        gpu_detail = str(gpu.get("name") or "GPU telemetry unavailable")
        if type(gpu.get("used_bytes")) is int and type(gpu.get("total_bytes")) is int:
            gpu_detail = f'{gpu_detail} · {display_bytes(gpu["used_bytes"])} / {display_bytes(gpu["total_bytes"])}'
        self._metric(rects["gpu"], "GPU", usage_percent(gpu_percent) if gpu_ratio is not None else "—",
                     gpu_ratio, gpu_detail, DASHBOARD_COLORS["blue"], "gpu", compact)
        self._metric(rects["ram"], "RAM", f"{ram_ratio * 100:.0f}%" if ram_ratio is not None else "—",
                     ram_ratio, f'{display_bytes(ram_used)} / {display_bytes(ram_total)} used',
                     DASHBOARD_COLORS["amber"], "ram", compact)
        self._models(rects["models"], data, compact)
        self._peers(rects["peers"], compact)

    def _record_history(self, data):
        cpu = data.get("cpu") or {}
        ram = data.get("ram") or {}
        gpu = next((item for item in (data.get("gpus") or []) if isinstance(item, dict)), {})
        ram_total, ram_free = ram.get("total_bytes"), ram.get("available_bytes")
        used = (ram_total - ram_free if type(ram_total) is int and
                type(ram_free) is int and 0 <= ram_free <= ram_total else None)
        values = {"cpu": _value_ratio(cpu.get("percent"), 100),
                  "gpu": _value_ratio(gpu.get("percent"), 100),
                  "ram": _value_ratio(used, ram_total)}
        for name, ratio in values.items():
            self.history[name].append(ratio)

    def _read_peers(self):
        try:
            self.peer_rows = normalize_peer_rows(self.peer_records())
        except (OSError, ValueError, TypeError):
            self.peer_rows = []

    def refresh(self):
        if self.closed or self.working:
            return
        self.working = True
        self.last_attempt = time.monotonic()
        self.refresh_button.configure(state="disabled")
        self.status.configure(text="SAMPLING...")
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        try:
            self.events.put((self.sampler.sample(), None))
        except Exception as error:
            self.events.put((None, str(error)[:120]))

    def _tick(self):
        if self.closed:
            return
        try:
            while True:
                data, error = self.events.get_nowait()
                self.working = False
                self.refresh_button.configure(state="normal")
                if error:
                    self.error = error
                    self.status.configure(text="METRICS ERROR")
                else:
                    self.error = ""
                    self.latest = data
                    self.last_at = time.monotonic()
                    self._record_history(data)
                    self._read_peers()
                    self.redraw()
        except queue.Empty:
            pass
        if (not self.working and self.last_attempt is not None and
                time.monotonic() - self.last_attempt >= POLL_MS / 1000):
            self.refresh()
        if self.last_at is not None and not self.working:
            age = int(time.monotonic() - self.last_at)
            self.status.configure(text=(f"STALE · {age}s" if age > 15 else f"UPDATED {age}s AGO"))
        self._after_id = self.root.after(250, self._tick)

    def toggle_fullscreen(self, _event=None):
        if self.closed:
            return
        try:
            if not self.fullscreen:
                # Never overwrite the remembered normal window with fullscreen.
                self.placement.save(self.window, self.root)
            self.fullscreen = not self.fullscreen
            self.window.attributes("-fullscreen", self.fullscreen)
            self.fullscreen_button.configure(
                text="WINDOW  ESC" if self.fullscreen else "FULL SCREEN  F11")
        except tk.TclError:
            self.fullscreen = False
        return "break"

    def exit_fullscreen(self, _event=None):
        if self.fullscreen:
            return self.toggle_fullscreen()
        return None

    def focus(self):
        if not self.closed:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()

    def close(self):
        if self.closed:
            return
        if self._placement_after is not None:
            try:
                self.window.after_cancel(self._placement_after)
            except tk.TclError:
                pass
            self._placement_after = None
        if not self.fullscreen:
            self.placement.save(self.window, self.root)
        self.closed = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.window.destroy()
