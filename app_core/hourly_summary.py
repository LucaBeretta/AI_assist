"""What the hourly check-in says, without any window: recent chats, a short
work snapshot, a fallback suggestion, and a device-health line."""

from datetime import datetime, timedelta, timezone
import re


def recent_chats(chats, now=None, hours=1):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=hours)
    recent = []
    for chat in chats:
        value = chat.get("updated_at")
        if not isinstance(value, str):
            continue
        try:
            updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if cutoff <= updated <= now:
            recent.append(chat)
    return sorted(recent, key=lambda chat: chat.get("updated_at", ""), reverse=True)


def work_snapshot(chats):
    topics = []
    prompts = []
    for chat in chats:
        title = re.sub(r"\s+", " ", str(chat.get("title") or "Untitled conversation")).strip()
        if title and title not in topics:
            topics.append(title[:100])
        for message in reversed(chat.get("messages", [])):
            if message.get("role") != "user":
                continue
            text = re.sub(r"\s+", " ", str(message.get("content") or "")).strip()
            if text:
                prompts.append(text[:180])
            break
        if len(topics) >= 4 and len(prompts) >= 4:
            break
    return {"threads": len(chats), "topics": topics[:4], "prompts": prompts[:4]}


def fallback_feedback(snapshot):
    topics = snapshot.get("topics", [])
    if not topics:
        return "No chats were updated in the last hour. Take a moment to plan your next small step."
    if len(topics) == 1:
        return f"You spent this hour on {topics[0]}. Keep momentum by continuing with its next small step."
    names = ", ".join(topics[:3])
    return f"You moved across {len(topics)} threads, including {names}. Pick one to carry forward next."


def hardware_summary(hardware):
    parts = []
    ram_free = hardware.get("ram_available_bytes")
    if isinstance(ram_free, (int, float)) and ram_free >= 0:
        parts.append(f"RAM {ram_free / 1024 ** 3:.1f} GB free")
    gpus = hardware.get("gpus") or []
    if gpus:
        gpu = gpus[0]
        name = str(gpu.get("name") or "GPU")
        free = gpu.get("free_bytes")
        total = gpu.get("total_bytes")
        if isinstance(free, (int, float)) and isinstance(total, (int, float)) and total > 0:
            parts.append(f"{name} · {free / 1024 ** 3:.1f}/{total / 1024 ** 3:.1f} GB free")
        else:
            parts.append(name)
    else:
        parts.append("No GPU detected")
    disk = hardware.get("disk_free_bytes")
    if isinstance(disk, (int, float)) and disk >= 0:
        parts.append(f"Disk {disk / 1024 ** 3:.0f} GB free")
    return "  ·  ".join(parts) if parts else "Device health is unavailable right now."


def hardware_needs_attention(hardware):
    ram_free = hardware.get("ram_available_bytes")
    disk_free = hardware.get("disk_free_bytes")
    if isinstance(ram_free, (int, float)) and ram_free < 1024 ** 3:
        return True
    if isinstance(disk_free, (int, float)) and disk_free < 2 * 1024 ** 3:
        return True
    return any(isinstance(gpu.get("free_bytes"), (int, float)) and gpu["free_bytes"] < 512 * 1024 ** 2
               for gpu in hardware.get("gpus", []))
