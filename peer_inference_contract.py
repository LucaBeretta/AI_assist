"""Data-only validation for a future opt-in LAN inference service.

Does not open sockets or authorize peers. The caller must authenticate the
certificate-pinned pairing and check consent before accepting any request.
"""
import json
import re

MAX_REQUEST_BYTES = 1024 * 1024
MAX_MESSAGES = 64
MAX_CONTENT_CHARS = 65536
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_ALLOWED_ROLES = {"system", "user", "assistant"}


class InvalidInferenceRequest(ValueError):
    """Malformed, oversized or out-of-scope remote inference request."""


def _valid_id(value):
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def validate_request(payload):
    """Bound the data crossing the future paired-inference boundary.

    No tool definitions, URLs, file paths, commands, model downloads or
    arbitrary fields are admitted. This never grants network permission.
    """
    if not isinstance(payload, dict) or set(payload) != {"request_id", "model", "messages"}:
        raise InvalidInferenceRequest("Only request_id, model and messages are accepted.")
    identifier, model, messages = payload["request_id"], payload["model"], payload["messages"]
    if not _valid_id(identifier):
        raise InvalidInferenceRequest("Invalid inference request ID.")
    if not isinstance(model, str) or _MODEL_RE.fullmatch(model) is None or "://" in model:
        raise InvalidInferenceRequest("Invalid installed model identifier.")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise InvalidInferenceRequest("Inference messages are missing or exceed the limit.")
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise InvalidInferenceRequest("Only role and plain text content are supported.")
        if not isinstance(message["role"], str) or message["role"] not in _ALLOWED_ROLES:
            raise InvalidInferenceRequest("Remote tool or custom roles are not permitted.")
        content = message["content"]
        if not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS:
            raise InvalidInferenceRequest("Message content must be bounded text.")
    try:
        length = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise InvalidInferenceRequest("Request must be JSON text data.") from error
    if length > MAX_REQUEST_BYTES:
        raise InvalidInferenceRequest("Inference request exceeds the byte limit.")
    return {"request_id": identifier, "model": model, "messages":
            [{"role": msg["role"], "content": msg["content"]} for msg in messages]}


def validate_cancel(payload):
    """Cancellation is scoped to an exact request, not a peer command."""
    if not isinstance(payload, dict) or set(payload) != {"request_id"} or not _valid_id(payload["request_id"]):
        raise InvalidInferenceRequest("Invalid cancellation request ID.")
    return payload["request_id"]
