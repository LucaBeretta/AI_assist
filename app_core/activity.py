"""The developer agent's activity, recorded the same way by every interface.

An activity entry is what a saved developer chat keeps of one agent event:
its type and text, plus the file path, command, applied diff and destination
when present. Diffs are capped so a chat stays a reasonable size; only the
newest ``KEEP`` entries are kept.
"""

KEEP = 100
MAX_DIFF_CHARS = 40000


def activity_record(event):
    """The saved form of an agent event (plain text fields only)."""
    record = {"type": str(event.get("type", "status")), "text": str(event.get("text", ""))}
    if isinstance(event.get("path"), str):
        record["path"] = event["path"]
    if isinstance(event.get("command"), str):
        record["command"] = event["command"]
    if record["type"] == "changed" and isinstance(event.get("destination"), str):
        record["destination"] = event["destination"]
    if record["type"] == "changed" and isinstance(event.get("diff"), str):
        record["diff"] = event["diff"][:MAX_DIFF_CHARS]
        if len(event["diff"]) > MAX_DIFF_CHARS:
            record["diff"] += "\n[Diff truncated for saved history.]"
    return record


def append(activity, event):
    """``activity`` with ``event`` recorded, keeping the newest entries."""
    return (list(activity) + [activity_record(event)])[-KEEP:]
