"""Plain compute dashboard formatting and geometry."""

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
