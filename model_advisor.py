"""Inspect this computer and estimate sensible Ollama model choices.

Memory figures below are conservative estimates for one model at 4096 tokens,
not benchmarks or promises of GPU placement. Live model sizes come from Ollama.
"""

import csv
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from model_library import fetch_model_variants


GIB = 1024 ** 3
MIB = 1024 ** 2
CONTEXT_LENGTH = 4096
GPU_SUPPORT_URL = "https://docs.ollama.com/gpu"
CONTEXT_GUIDE_URL = "https://docs.ollama.com/context-length"


def _run(arguments, timeout=15):
    result = subprocess.run(
        arguments, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("Hardware inspection command did not finish successfully.")
    return result.stdout.strip()


def _windows_hardware():
    script = r'''
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$system = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
$processor = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
$operatingSystem = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
$video = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$models = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
if (-not $models) { $models = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'Machine') }
[pscustomobject]@{
    cpu = $processor.Name
    logical_processors = $processor.NumberOfLogicalProcessors
    ram_total_bytes = $system.TotalPhysicalMemory
    ram_available_bytes = [long]$operatingSystem.FreePhysicalMemory * 1024
    gpu_names = $video
    model_directory = $models
} | ConvertTo-Json -Depth 3 -Compress
'''
    result = json.loads(_run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]))
    if not isinstance(result, dict):
        raise ValueError("Windows hardware inspection did not return an object.")
    return result


def _number(value):
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_nvidia(text):
    """nvidia-smi reports MiB; never use Win32_VideoController.AdapterRAM."""
    gpus = []
    for fields in csv.reader(io.StringIO(text)):
        if len(fields) < 4:
            continue
        name, total, free, driver = (field.strip() for field in fields[:4])
        total, free = _number(total), _number(free)
        if not name or total is None or total <= 0:
            continue
        compute = _number(fields[4].strip()) if len(fields) > 4 else None
        if compute is None and re.search(r"\bRTX\s*(?:20|30|40|50)\d\d\b", name, re.I):
            # These RTX families are listed in Ollama's hardware support table.
            compute = 7.5
        driver_major = _number(driver.split(".", 1)[0])
        supported = None
        if compute is not None and driver_major is not None:
            minimum_driver = 570 if 5 <= compute <= 6.2 else 550
            supported = compute >= 5 and driver_major >= minimum_driver
        gpus.append({
            "name": name, "total_bytes": int(total * MIB),
            "free_bytes": int(min(total, free) * MIB) if free is not None else None,
            "driver_version": driver, "source": "nvidia-smi", "supported": supported,
        })
    return gpus


def _nvidia_hardware():
    executable = shutil.which("nvidia-smi")
    if not executable and platform.system() == "Windows":
        candidate = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        return []
    arguments = [executable, "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
                 "--format=csv,noheader,nounits"]
    try:
        text = _run(arguments, timeout=8)
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        arguments[1] = "--query-gpu=name,memory.total,memory.free,driver_version"
        text = _run(arguments, timeout=8)
    return _parse_nvidia(text)


def _parse_vm_stat(text):
    """Reclaimable memory from macOS vm_stat: free, inactive, speculative and purgeable pages."""
    page = re.search(r"page size of (\d+) bytes", text)
    size = int(page.group(1)) if page else 16384
    pages = 0
    found = False
    for label in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        match = re.search(rf"^{label}:\s+(\d+)", text, re.M)
        if match:
            pages += int(match.group(1))
            found = True
    return pages * size if found else None


def apple_gpu_budget(ram_total):
    """Memory Metal lets a GPU use on Apple silicon: about 2/3 of RAM up to 36 GB, 3/4 above."""
    return int(ram_total * (0.75 if ram_total > 36 * GIB else 2 / 3))


def _apple_hardware():
    """Apple silicon: CPU and GPU share one pool of memory that Ollama uses through Metal."""
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5) or "Apple silicon"
    total = int(_run(["sysctl", "-n", "hw.memsize"], timeout=5))
    try:
        available = _parse_vm_stat(_run(["vm_stat"], timeout=5))
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        available = None
    budget = apple_gpu_budget(total)
    gpu = {"name": f"{chip} GPU (unified memory)", "total_bytes": budget,
           "free_bytes": min(budget, available) if available is not None else None,
           "source": "Apple unified memory (Metal)", "supported": True, "unified": True}
    return {"cpu": chip, "ram_total_bytes": total, "ram_available_bytes": available, "gpus": [gpu]}


def _fallback_ram():
    if hasattr(os, "sysconf"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            return (os.sysconf("SC_PHYS_PAGES") * page, os.sysconf("SC_AVPHYS_PAGES") * page)
        except (ValueError, OSError):
            pass
    if platform.system() == "Darwin":
        try:
            total = int(_run(["sysctl", "-n", "hw.memsize"]))
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
            return None, None
        try:
            return total, _parse_vm_stat(_run(["vm_stat"]))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return total, None
    return None, None


def _existing_directory(directory):
    path = Path(os.path.expandvars(str(directory))).expanduser().resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _server_local(host):
    return urllib.parse.urlsplit(host).hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def detect_hardware(host):
    """Return independently detected hardware facts; failed probes stay unknown."""
    notes = []
    result = {
        "cpu": platform.processor() or "CPU not identified", "logical_processors": os.cpu_count(),
        "ram_total_bytes": None, "ram_available_bytes": None, "gpus": [],
        "disk_free_bytes": None, "model_directory": None, "ollama_version": None,
        "ollama_running": False, "server_local": _server_local(host), "notes": notes,
    }
    windows = {}
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    if apple:
        try:
            details = _apple_hardware()
            for field in ("cpu", "ram_total_bytes", "ram_available_bytes"):
                result[field] = details[field]
            result["gpus"] = details["gpus"]
            notes.append("Apple silicon: the GPU shares system memory. About 2/3 of RAM (3/4 above 36 GB) "
                         "is usable by Metal, and other apps use the same memory.")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            apple = False
            notes.append("Apple silicon memory inspection was unavailable; RAM-based estimates are used.")
    if platform.system() == "Windows":
        try:
            windows = _windows_hardware()
            for field in ("cpu", "logical_processors", "ram_total_bytes", "ram_available_bytes"):
                if windows.get(field) is not None:
                    result[field] = windows[field]
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            notes.append("Windows CPU/RAM inspection was unavailable; some details remain unknown.")
    if result["ram_total_bytes"] is None:
        result["ram_total_bytes"], result["ram_available_bytes"] = _fallback_ram()
    if not apple:
        try:
            result["gpus"] = _nvidia_hardware()
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            notes.append("NVIDIA dedicated video memory could not be read.")
    known_names = {gpu["name"].lower() for gpu in result["gpus"]}
    gpu_names = windows.get("gpu_names", [])
    if isinstance(gpu_names, str):
        gpu_names = [gpu_names]
    for name in gpu_names:
        if isinstance(name, str) and name.lower() not in known_names:
            result["gpus"].append({"name": name, "total_bytes": None, "free_bytes": None,
                                   "source": "Windows adapter name; VRAM unknown", "supported": None})
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if visible:
        selected = visible.split(",")
        numeric = all(value.strip().isdigit() for value in selected)
        indices = {int(value) for value in selected} if numeric else set()
        nvidia_index = 0
        for gpu in result["gpus"]:
            if gpu["source"] == "nvidia-smi":
                gpu["available_to_ollama"] = numeric and nvidia_index in indices
                nvidia_index += 1
        if visible == "-1":
            notes.append("CUDA_VISIBLE_DEVICES=-1 disables NVIDIA GPU use for this process.")
        elif not numeric:
            notes.append("CUDA_VISIBLE_DEVICES names devices that could not be matched; GPU fit is not assumed.")
        else:
            notes.append("GPU estimates respect CUDA_VISIBLE_DEVICES; a separately running Ollama server may use different settings.")
    if any(gpu.get("supported") is False for gpu in result["gpus"]):
        notes.append("A detected GPU or driver is outside the verified Ollama support range.")
    if not any(gpu.get("total_bytes") and gpu.get("supported") is True
               and gpu.get("available_to_ollama") is not False for gpu in result["gpus"]):
        notes.append("GPU acceleration and dedicated VRAM are not verified; smaller RAM-based choices are used.")

    if result["server_local"]:
        directory = (os.environ.get("OLLAMA_MODELS") or windows.get("model_directory")
                     or str(Path.home() / ".ollama" / "models"))
        result["model_directory"] = os.path.expandvars(os.path.expanduser(directory))
        try:
            result["disk_free_bytes"] = shutil.disk_usage(_existing_directory(directory)).free
        except (OSError, ValueError):
            notes.append("Free space in the Ollama model directory could not be verified.")
    else:
        notes.append("Ollama is on a remote server. This computer's RAM/GPU cannot establish which models fit there.")

    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/version", timeout=5) as response:
            status = json.load(response)
        if isinstance(status, dict) and isinstance(status.get("version"), str):
            result["ollama_version"] = status["version"]
            result["ollama_running"] = True
        else:
            notes.append("Ollama did not return a valid version response.")
    except (OSError, ValueError, urllib.error.URLError, TimeoutError):
        notes.append("Ollama is not reachable. Open the Ollama app before downloading or chatting.")
    return result


def estimate_memory(size_bytes):
    """Weights plus runtime/context headroom, for one 4096-token conversation."""
    return int(size_bytes * 1.15 + 2 * GIB)


def _parameters(variant):
    # Hugging Face tags name a quantization, not a size; the size comes from the repository name.
    if "parameters" in variant:
        value = variant["parameters"]
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    tag = variant["name"].split(":", 1)[-1]
    match = re.fullmatch(r"e?(\d+(?:\.\d+)?)([bm])", tag, re.I)
    if not match:
        return None
    return float(match.group(1)) / (1000 if match.group(2).lower() == "m" else 1)


# Active counts from the publishers' model cards; never infer them from file size.
# https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
# https://huggingface.co/openai/gpt-oss-20b
_ACTIVE_PARAMETERS = {"qwen3-coder:30b": 3.3, "gpt-oss:20b": 3.6}


def _best_gpu(hardware):
    candidates = [gpu for gpu in hardware.get("gpus", [])
                  if gpu.get("supported") is True and gpu.get("available_to_ollama") is not False
                  and isinstance(gpu.get("total_bytes"), (int, float))]
    return max(candidates, key=lambda gpu: gpu["total_bytes"], default=None)


def _fit(hardware, variant):
    size = variant["size_bytes"]
    needed = estimate_memory(size)
    disk = hardware.get("disk_free_bytes")
    if disk is not None and size + max(512 * MIB, int(size * .1)) > disk:
        return None
    gpu = _best_gpu(hardware)
    if gpu and needed <= gpu["total_bytes"] - GIB:
        free = gpu.get("free_bytes")
        if free is not None and needed <= max(0, free - GIB):
            return ("Fits available GPU memory", "Estimated to fit the currently free video memory at 4096 tokens.")
        return ("Run one model at a time", "Estimated to fit this GPU after freeing other models/apps, at 4096 tokens.")
    ram = hardware.get("ram_total_bytes")
    available = hardware.get("ram_available_bytes")
    budget = min(ram * .70, available * .85) if ram and available else (ram * .60 if ram else None)
    active = _ACTIVE_PARAMETERS.get(variant["name"])
    parameters = active if active is not None else _parameters(variant)
    # A large host RAM total does not make a giant CPU-only model responsive.
    if parameters is not None and parameters > 8:
        return None
    # Active parameters bound compute, not storage: budget every weight and
    # runtime overhead across one dedicated GPU and conservative host RAM.
    if active is not None and gpu and not gpu.get("unified") and budget is not None:
        if needed <= max(0, gpu["total_bytes"] - GIB) + budget:
            return ("GPU + CPU split", f"MoE with about {active:g}B active parameters; all weights still need memory. "
                    "Estimated to fit GPU plus RAM after freeing other models/apps, at 4096 tokens. "
                    "CPU offload may be slower; verify placement with ollama ps.")
    if budget is not None and needed <= budget:
        return ("CPU / RAM fallback", "Fits a conservative RAM budget; GPU placement is unverified and responses may be slower.")
    if budget is None and size <= 2 * GIB:
        return ("Hardware check needed", "RAM and usable GPU memory could not be verified. Check memory before running this small option.")
    return None


def _steps(hardware, variant, fit):
    name = variant["name"]
    steps = [
        "Open the Ollama app. If it is not running, start it or run: ollama serve",
        f"Click this model's Download button. To download from a terminal instead, run: ollama pull {name}",
        "Use one model at a time. In AI Assist, choose Stop all models before loading a larger model; close other GPU-heavy apps if needed.",
        f"Choose Use in chat for {name}. The recommended starting context is {CONTEXT_LENGTH} tokens; larger contexts require more memory.",
        "Run ollama ps to inspect actual placement. 100% GPU means the model is fully on the GPU; a CPU/GPU split means some work is offloaded.",
        "If loading fails or is slow, stop other models, lower the context, or choose a smaller download. These memory estimates are not performance guarantees.",
    ]
    if fit == "CPU / RAM fallback":
        steps[4] = "CPU operation can be substantially slower. Check Ollama's Hardware support page to see whether your GPU/driver supports acceleration."
    return steps


def recommend_models(hardware, catalog):
    """Choose up to six live variants, with estimates and model-specific guides.

    The curated purposes are engineering choices. Live family/tag availability
    and advertised download sizes are checked each time, rather than cached.
    """
    if not hardware.get("server_local", True):
        return []
    available = {model["name"] for model in catalog if isinstance(model, dict) and isinstance(model.get("name"), str)}
    specs = [
        ("qwen3.5", "Balanced everyday chat", [9, 4, 2, .8]),
        ("qwen3.5", "Larger general model", [27]),
        ("qwen3-coder", "Coding", [30]),
        ("deepseek-r1", "Reasoning alternative", [14, 8, 7, 1.5]),
        ("gemma4", "Alternative general chat", [12, 4, 2]),
        ("gpt-oss", "Reasoning and developer tasks", [20]),
        ("qwen2.5-coder", "Smaller coding model", [7, 3, 1.5, .5]),
        ("gemma3", "Alternative general chat", [12, 4, 1]),
    ]
    cache = {}
    cards = []
    used = set()
    for family, purpose, preferences in specs:
        if family not in available:
            continue
        if family not in cache:
            try:
                cache[family] = fetch_model_variants(family)
            except (RuntimeError, ValueError, OSError):
                cache[family] = []
        candidates = [(variant, _fit(hardware, variant)) for variant in cache[family]
                      if variant["name"] not in used and _parameters(variant) is not None]
        candidates = [(variant, fit) for variant, fit in candidates if fit is not None]
        if not candidates:
            continue
        preferred = {value: index for index, value in enumerate(preferences)}
        candidates.sort(key=lambda item: (
            preferred.get(_parameters(item[0]), len(preferences)),
            item[0]["size_bytes"], item[0]["name"],
        ))
        variant, (fit, reason) = candidates[0]
        if _parameters(variant) not in preferred:
            continue
        name = variant["name"]
        used.add(name)
        cards.append({
            **variant, "purpose": purpose, "fit": fit, "reason": reason,
            "estimated_memory_bytes": estimate_memory(variant["size_bytes"]),
            "context_length": CONTEXT_LENGTH, "setup_steps": _steps(hardware, variant, fit),
            "source_url": f"https://ollama.com/library/{urllib.parse.quote(name, safe=':')}",
        })
        if len(cards) >= 6:
            break
    return cards


# Quantizations in order of preference: Q4_K_M balances quality and size, then
# better quality, then smaller files for tighter memory.
_HF_QUANTS = ("Q4_K_M", "Q5_K_M", "Q4_K_S", "Q6_K", "Q8_0", "IQ4_XS", "Q3_K_M")


def recommend_hugging_face(hardware, models, limit=3, skip=()):
    """Up to ``limit`` popular Hugging Face models, each in the preferred quantization that fits.

    ``models`` comes from model_library.fetch_hugging_face_models. A model whose
    size cannot be judged safely is left out rather than guessed.
    """
    if not hardware.get("server_local", True):
        return []
    cards, skip = [], set(skip)
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("variants"), list):
            continue
        by_quant = {variant.get("quant"): variant for variant in model["variants"] if isinstance(variant, dict)}
        for quant in _HF_QUANTS:
            variant = by_quant.get(quant)
            if variant is None or variant["name"] in skip:
                continue
            sized = {**variant, "parameters": model.get("parameters")}
            fit = _fit(hardware, sized)
            if fit is None:
                continue
            fit, reason = fit
            downloads = model.get("downloads")
            cards.append({
                "name": variant["name"], "size_bytes": variant["size_bytes"],
                "purpose": "Popular on Hugging Face" + (f" · {downloads:,} downloads" if downloads else ""),
                "fit": fit, "reason": reason, "source": "Hugging Face",
                "estimated_memory_bytes": estimate_memory(variant["size_bytes"]),
                "context_length": CONTEXT_LENGTH, "setup_steps": _steps(hardware, variant, fit),
                "source_url": f"https://huggingface.co/{model['repo']}",
            })
            break
        if len(cards) >= limit:
            break
    return cards
