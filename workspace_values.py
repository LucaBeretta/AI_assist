"""Plain conversation and agent settings helpers retained independently of widgets."""
import json
from pathlib import Path

from coding_agent import FILE_LIMIT_CHOICES, estimate_tokens


POLL_ACTIVE_INTERVAL_MS = 100
POLL_IDLE_INTERVAL_MS = 250


def _response_poll_interval(is_waiting, is_loading_models, lifecycle_busy, project_loading):
    if is_waiting or is_loading_models or lifecycle_busy or project_loading:
        return POLL_ACTIVE_INTERVAL_MS
    return POLL_IDLE_INTERVAL_MS


def _history_matches(record, query):
    """Search individual fields without copying the entire conversation."""
    if not query:
        return True
    fields = (record["title"], record["model"], record.get("project_path", ""), record.get("draft", ""))
    return (any(query in field.casefold() for field in fields)
            or any(query in message["content"].casefold() for message in record["messages"]))


AGENT_DEFAULT_CONTEXT = 16384


def agent_context_length(chosen, model, running_details=None):
    """Context for a Developer task: the user's choice, else the context the model is
    already loaded with in Ollama (/api/ps), else 16k. 4k was too small for the agent:
    it cut tool calls (1k output) and shortened the task itself."""
    if type(chosen) is int and chosen > 0:
        return chosen
    try:
        loaded = (running_details() or {}).get(model, {}).get("context_length") if running_details else None
    except Exception:
        loaded = None
    if type(loaded) is int and loaded >= 8192:
        return loaded
    return AGENT_DEFAULT_CONTEXT


def file_limit_label(value):
    return f"{value // 1000:,} KB" if value < 1000000 else f"{value / 1000000:g} MB"


def file_limit_value(label):
    return next((value for value in FILE_LIMIT_CHOICES if file_limit_label(value) == label), FILE_LIMIT_CHOICES[0])


def load_file_limit(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("file_limit")
    except (OSError, ValueError, TypeError, AttributeError):
        return FILE_LIMIT_CHOICES[0]
    return value if value in FILE_LIMIT_CHOICES else FILE_LIMIT_CHOICES[0]


def file_limit_warning(limit, context, ram_free=None):
    """What a limit means for this model and computer, or "" when it is comfortable.

    Rough guides only: about four characters per token, and about 0.15 MB of
    memory per token of context for an 8-14B model."""
    tokens = estimate_tokens(limit)
    context = context or 4096
    if tokens <= context // 2:
        return ""
    lines = [f"A {file_limit_label(limit)} file is about {tokens:,} tokens; the current context is {context:,} tokens. "
             "Larger files will be read and edited in parts, which takes longer."]
    needed_gb = tokens * 0.15 / 1024
    if ram_free is not None:
        free_gb = ram_free / 1024 ** 3
        if needed_gb > free_gb * 0.8:
            lines.append(f"Holding a whole file of this size would need a context of about {tokens:,} tokens, roughly "
                         f"{needed_gb:.1f} GB of extra memory for an 8-14B model; this computer has about {free_gb:.1f} GB free. "
                         "Keep the context smaller and let the agent read in parts.")
        else:
            lines.append(f"Raising the context to about {tokens:,} tokens would need roughly {needed_gb:.1f} GB of extra memory "
                         f"for an 8-14B model; this computer has about {free_gb:.1f} GB free.")
    return "\n\n".join(lines)
