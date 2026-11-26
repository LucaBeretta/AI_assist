"""Format compute dashboard data for the desktop web UI."""

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
        # Enrollment alone is not evidence the peer is online or sharing. Usage
        # appears only after a pinned-TLS, token-authenticated request succeeded
        # (webui.sharing._peer_status); it is re-validated here before display.
        state = item.get("state") if item.get("state") in ("live", "not sharing", "offline", "rejected") else "paired"
        usage = None
        if state == "live":
            try:
                from peer_pairing import sanitize_usage
                usage = sanitize_usage(item.get("usage"))
            except Exception:
                state, usage = "rejected", None
        rows.append({
            "id": peer_id[:32],
            "endpoint": f"{address}:{port}" if isinstance(address, str) and
                        type(port) is int and 1 <= port <= 65535 else "address unavailable",
            "state": {"paired": "paired · live telemetry unavailable", "live": "live", "not sharing": "not sharing usage",
                      "offline": "offline", "rejected": "rejected"}[state],
            "usage": usage,
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
