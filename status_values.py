"""Plain status values shared by the local model APIs and web UI."""
from app_core import privacy as core_privacy
import network_gate

AGENT_MAX_STEPS = 12
is_local = core_privacy.is_local_url
is_cloud_model = core_privacy.is_cloud_model


def short_tokens(count):
    """6200 -> 6.2k, 16384 -> 16k, 950 -> 950."""
    if count < 1000:
        return str(count)
    value = count / 1000
    return f"{value:.1f}k" if value < 10 and round(value, 1) != int(value) else f"{round(value):.0f}k"


def placement(detail):
    """Where Ollama placed a running model: gpu, cpu, or the GPU share."""
    if not detail or not detail.get("size"):
        return ""
    share = detail.get("size_vram", 0) / detail["size"]
    if share >= 0.98:
        return "gpu"
    if share <= 0.02:
        return "cpu"
    return f"gpu {share:.0%} · cpu"






def generation_rate(metrics):
    if metrics.get("eval_duration", 0) > 0 and metrics.get("eval_count"):
        return metrics["eval_count"] / (metrics["eval_duration"] / 1e9)
    return None


def snapshot_values(*, engine="Ollama", connected=False, model="", details=None, metrics=None,
                    choice="", report=None, url="", step=0, agent_active=False,
                    max_steps=AGENT_MAX_STEPS, used=None):
    """Compute shared status values from plain data, without inspecting widgets."""
    details, metrics, report = details or {}, metrics or {}, report or {}
    choice = str(choice) if choice is not None else ""
    server = report.get("server") if isinstance(report, dict) else None
    stopped = engine == "Colibri" and isinstance(server, dict) and server.get("state") == "stopped"
    state = "stopped" if stopped else ("ready" if connected else "offline")
    detail = details.get(model) if engine == "Ollama" else None
    where = placement(detail) if engine == "Ollama" else ("ram + ssd" if model else "")
    model_text = (f"{model} · {where}" if where else model) if model else "no model selected"

    if used is None and isinstance(metrics.get("prompt_eval_count"), int):
        used = metrics["prompt_eval_count"] + int(metrics.get("eval_count") or 0)
    total = int(choice) if choice.isdigit() else (detail or {}).get("context_length")
    if used is not None and total:
        context_text, fraction = f"{short_tokens(used)} / {short_tokens(total)}", min(1.0, used / total)
    elif total:
        context_text, fraction = short_tokens(total), 0.0
    elif used is not None:
        context_text, fraction = short_tokens(used), 0.0
    else:
        context_text, fraction = "default", 0.0

    vram = sum(item.get("size_vram", 0) for item in details.values()) if engine == "Ollama" else 0
    vram_text = f"vram {vram / 1024 ** 3:.1f} GB" if vram else ""

    rate = generation_rate(metrics)
    if engine == "Colibri" and isinstance(server, dict):
        speed = server.get("tokens_per_second")
        rate = speed if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0 else rate
    speed_text = (f"{rate:.1f} tok/s" if rate < 10 else f"{rate:.0f} tok/s") if rate else ""

    agent_text = f"agent step {step} / {max_steps}" if agent_active and step else ""

    privacy, privacy_role = core_privacy.privacy_status(network_gate.status(), model, url)
    return {
        "engine": f"{engine.lower()} {state}", "engine_role": "success" if state == "ready" else "muted",
        "model": model_text, "context": context_text, "fraction": fraction,
        "vram": vram_text, "speed": speed_text, "agent": agent_text,
        "privacy": privacy, "privacy_role": privacy_role,
    }
