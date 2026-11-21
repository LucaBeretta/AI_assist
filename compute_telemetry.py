"""Read-only compute telemetry for the AI Assist workstation.

Safe to import: no listeners, discovery, or background threads are started.
Snapshots never include prompts, paths, environment variables, credentials or
project contents. Failed probes are reported as unknown, never as 0% usage.
No third-party dependencies are needed.
"""

import csv
import ctypes
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from model_advisor import detect_hardware


GIB = 1024 ** 3
MAX_MODELS = 40
MAX_GPU = 8


def _finite_percent(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return round(number, 1) if 0 <= number <= 100 else None


def _cpu_times():
    """Return cumulative (total, idle) ticks for delta-based CPU percentage."""
    system = platform.system()
    if system == "Linux":
        with open("/proc/stat", encoding="ascii") as handle:
            row = handle.readline().split()
        if not row or row[0] != "cpu" or len(row) < 5:
            raise ValueError("Missing aggregate CPU counters.")
        values = [int(item) for item in row[1:]]
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)
    if system == "Windows":
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]
            def integer(self):
                return (self.high << 32) | self.low
        idle, kernel, user = FileTime(), FileTime(), FileTime()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            raise OSError("GetSystemTimes failed")
        return kernel.integer() + user.integer(), idle.integer()
    if system == "Darwin":
        # Mach HOST_CPU_LOAD_INFO: cumulative user, system, idle, nice ticks. Instant,
        # unlike `top -l 1`, which takes about five seconds of CPU on recent macOS.
        libc = ctypes.CDLL(None)
        libc.mach_host_self.restype = ctypes.c_uint32
        ticks = (ctypes.c_uint32 * 4)()
        count = ctypes.c_uint32(4)
        if libc.host_statistics(libc.mach_host_self(), 3, ticks, ctypes.byref(count)) != 0:
            raise OSError("host_statistics failed")
        return sum(ticks), ticks[2]
    raise OSError("Cumulative CPU counters unsupported")


def _memory():
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="ascii") as handle:
                details = {}
                for line in handle:
                    key, _, value = line.partition(":")
                    if key in ("MemTotal", "MemAvailable"):
                        details[key] = int(value.strip().split()[0]) * 1024
                return details.get("MemTotal"), details.get("MemAvailable")
        if system == "Windows":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.total_phys, status.avail_phys
    except (OSError, ValueError, TypeError):
        pass
    return None, None


def _gpu_usage():
    executable = shutil.which("nvidia-smi")
    if not executable and platform.system() == "Windows":
        candidate = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                 "System32", "nvidia-smi.exe")
        executable = candidate if os.path.isfile(candidate) else None
    if not executable:
        return []
    try:
        process = subprocess.run(
            [executable, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if process.returncode:
            return []
        result = []
        for row in csv.reader(io.StringIO(process.stdout)):
            if len(row) != 4 or len(result) >= MAX_GPU:
                continue
            name, percent, used, total = (item.strip() for item in row)
            try:
                used_bytes = round(float(used) * 1024 ** 2)
                total_bytes = round(float(total) * 1024 ** 2)
            except ValueError:
                continue
            if not (0 <= used_bytes <= total_bytes and total_bytes > 0):
                continue
            result.append({"name": name[:96], "percent": _finite_percent(percent),
                           "used_bytes": used_bytes, "total_bytes": total_bytes})
        return result
    except (OSError, subprocess.TimeoutExpired):
        return []


def _apple_gpu_usage(run=None):
    """Apple silicon GPU load from the I/O registry, without administrator rights.

    ``ioreg`` publishes the AGX accelerator's PerformanceStatistics: "Device
    Utilization %" and "In use system memory" (bytes the GPU holds in unified
    memory). Returns {"percent", "used_bytes"} or None when unavailable.
    """
    def default_run():
        executable = shutil.which("ioreg") or shutil.which("ioreg", path="/usr/sbin:/sbin")
        if not executable:
            return ""
        process = subprocess.run([executable, "-r", "-d", "1", "-c", "IOAccelerator"],
                                 capture_output=True, text=True, timeout=3, check=False)
        return process.stdout if process.returncode == 0 else ""
    try:
        text = (run or default_run)()
    except (OSError, subprocess.TimeoutExpired):
        return None
    statistics = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', text or "")
    if not statistics:
        return None
    fields = dict(re.findall(r'"([^"]+)"=(\d+)', statistics.group(1)))
    percent = _finite_percent(fields.get("Device Utilization %"))
    used = fields.get("In use system memory")
    used_bytes = int(used) if used is not None and used.isdigit() else None
    if percent is None and used_bytes is None:
        return None
    return {"percent": percent, "used_bytes": used_bytes}


def _is_loopback_ollama(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "http" and parsed.hostname in
                ("127.0.0.1", "localhost", "::1") and
                (parsed.username is None and parsed.password is None))
    except (TypeError, ValueError):
        return False


def _local_json(url, path):
    req = urllib.request.Request(url.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=1.5) as response:
        data = response.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise ValueError("Local model response is oversized.")
    return json.loads(data)


def _installed_models(host):
    if not _is_loopback_ollama(host):
        return {"installed": [], "loaded": [], "available": False,
                "note": "Remote Ollama endpoints are not probed by local telemetry."}
    try:
        tags = _local_json(host, "/api/tags")
        running = _local_json(host, "/api/ps")
        installed = sorted({m["name"][:128] for m in tags.get("models", [])
                            if isinstance(m, dict) and isinstance(m.get("name"), str)})
        loaded = sorted({m["name"][:128] for m in running.get("models", [])
                         if isinstance(m, dict) and isinstance(m.get("name"), str)})
        return {"installed": installed[:MAX_MODELS], "loaded": loaded[:MAX_MODELS],
                "available": True, "note": ""}
    except (OSError, ValueError, KeyError, TypeError):
        return {"installed": [], "loaded": [], "available": False,
                "note": "Local Ollama not reachable."}


class TelemetrySampler:
    """Thread-safe polling; callers should sample on a worker, never Tk's thread."""

    def __init__(self, ollama_host="http://127.0.0.1:11434"):
        self.host = ollama_host
        self._lock = threading.Lock()
        self._previous_cpu = None
        self._hardware = {}
        self._last_hardware = 0.0

    def sample(self):
        with self._lock:
            now = time.monotonic()
            if not self._hardware or now - self._last_hardware >= 45:
                try:
                    # This discovery reads local hardware. It may query Ollama's local port.
                    self._hardware = detect_hardware("http://127.0.0.1:11434")
                    self._last_hardware = now
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    self._hardware = {}
                    self._last_hardware = now
            hardware = self._hardware
            cpu_percent = None
            try:
                current = _cpu_times()
                if self._previous_cpu is not None:
                    # Mach tick counters are 32-bit and may wrap; treat a wrap as no reading.
                    total = current[0] - self._previous_cpu[0]
                    idle = current[1] - self._previous_cpu[1]
                    if total > 0 and 0 <= idle <= total:
                        cpu_percent = _finite_percent(100 * (1 - idle / total))
                self._previous_cpu = current
            except (OSError, ValueError, AttributeError):
                pass

            ram_total, ram_free = _memory()
            ram_total = ram_total or hardware.get("ram_total_bytes")
            ram_free = ram_free if ram_free is not None else hardware.get("ram_available_bytes")
            if (not isinstance(ram_total, int) or ram_total <= 0 or
                    not isinstance(ram_free, int) or ram_free < 0):
                ram_total, ram_free = None, None

            gpus = _gpu_usage()
            if not gpus:
                for info in hardware.get("gpus", [])[:MAX_GPU]:
                    if not isinstance(info, dict):
                        continue
                    total = info.get("total_bytes")
                    free = info.get("free_bytes")
                    gpus.append({"name": str(info.get("name", "Unknown GPU"))[:96],
                                 "percent": None, "total_bytes": total if type(total) is int else None,
                                 "used_bytes": (total - free if type(total) is int
                                                and type(free) is int and 0 <= free <= total else None),
                                 "unified": bool(info.get("unified"))})
                if gpus and gpus[0].get("unified") and platform.system() == "Darwin":
                    apple = _apple_gpu_usage()
                    if apple:
                        gpus[0]["percent"] = apple["percent"]
                        total = gpus[0].get("total_bytes")
                        if apple["used_bytes"] is not None and (total is None or apple["used_bytes"] <= total):
                            gpus[0]["used_bytes"] = apple["used_bytes"]
            models = _installed_models(self.host)
            return {
                "schema": 1,
                "time": time.time(),
                "device": socket.gethostname()[:64],
                "platform": platform.system(),
                "cpu": {"name": str(hardware.get("cpu") or platform.processor() or "Unknown")[:120],
                        "percent": cpu_percent,
                        "logical_cores": hardware.get("logical_processors") or os.cpu_count()},
                "ram": {"total_bytes": ram_total, "available_bytes": ram_free},
                "gpus": gpus[:MAX_GPU],
                "models": models,
            }
