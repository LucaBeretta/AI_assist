"""Chat with a paired computer's shared AI (see peer_pairing.PairingHost and docs/compute-routing.md).

A chat whose backend is ``peer:<id>`` runs on that paired computer. Only plain-text
messages travel, over the TLS connection pinned at pairing; the other computer
writes Ollama's request itself and refuses cloud models, files and tools.
Nothing here starts a listener or contacts a computer that was not paired.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import peer_inference_contract as contract
from peer_pairing import AIUnavailable, PairingError, PeerOffline, TrustStore, open_shared_ai

PREFIX = "peer:"
LIST_TIMEOUT = 4


def backend_for(peer_id):
    return PREFIX + peer_id


def peer_of(backend):
    """The paired computer's ID for a ``peer:<id>`` backend, else None."""
    return backend[len(PREFIX):] if isinstance(backend, str) and backend.startswith(PREFIX) else None


def plain_messages(messages):
    """Text-only messages within the shared-AI limits: system notes first, then the latest turns."""
    if any(message.get("images") for message in messages):
        raise ValueError("Pictures stay on this computer. Choose a model on this computer to send images.")
    system = "\n\n".join(message["content"] for message in messages if message.get("role") == "system" and message.get("content"))
    turns = [{"role": message["role"], "content": str(message.get("content") or "")[-contract.MAX_CONTENT_CHARS:]}
             for message in messages if message.get("role") in ("user", "assistant")]
    head = [{"role": "system", "content": system[-contract.MAX_CONTENT_CHARS:]}] if system else []
    turns = turns[-(contract.MAX_MESSAGES - len(head)):]
    while len(turns) > 1 and len(json.dumps(head + turns, ensure_ascii=False).encode("utf-8")) > contract.MAX_REQUEST_BYTES - 4096:
        turns.pop(0)  # the oldest turns go first; the newest question always stays
    return head + turns


class SharedAI:
    def __init__(self, directory):
        self.store = TrustStore(directory)
        self.directory = directory

    def _client_id(self):
        try:
            value = json.loads((self.directory / "client.json").read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) else None

    def paired(self, peer_id):
        return isinstance(peer_id, str) and isinstance(self.store.client_record(peer_id), dict)

    def _models(self, peer_id, record, client_id):
        row = {"id": peer_id, "backend": backend_for(peer_id), "address": record.get("address"),
               "name": record.get("name") or "", "models": []}
        try:
            response = open_shared_ai(record, client_id, "GET", "/api/tags", timeout=LIST_TIMEOUT)
            try:
                data = json.loads(response.read(1024 * 1024))
            finally:
                response.close()
            names = [item.get("name") for item in data.get("models", []) if isinstance(item, dict)]
            row["models"] = sorted(name for name in names if isinstance(name, str) and not name.endswith(("-cloud", ":cloud")))
            row["state"] = "ready"
        except AIUnavailable:
            row["state"] = "not sharing"
        except PeerOffline:
            row["state"] = "offline"
        except (PairingError, OSError, ValueError, AttributeError) as error:
            row["state"], row["error"] = "refused", str(error)[:160]
        return row

    def computers(self):
        """Every paired computer, with the models it shares with this one (if it does)."""
        records = {key: value for key, value in self.store._read("paired_servers.json").items() if isinstance(value, dict)}
        client_id = self._client_id()
        if not records or client_id is None:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(records))) as pool:
            return list(pool.map(lambda item: self._models(item[0], item[1], client_id), sorted(records.items())))

    def chat(self, peer_id, messages, model, *, on_chunk=None, on_metrics=None, cancel_event=None):
        """Stream one answer from the paired computer. Returns the whole answer."""
        record, client_id = self.store.client_record(peer_id), self._client_id()
        if not isinstance(record, dict) or client_id is None:
            raise ValueError("This computer is no longer paired. Pair it again in Settings.")
        body = {"request_id": uuid.uuid4().hex, "model": model, "messages": plain_messages(messages)}
        response = open_shared_ai(record, client_id, "POST", "/api/chat", body)
        stop = threading.Event()

        def hang_up():
            # Stop pressed: closing the connection ends the answer on the other computer too.
            while not stop.wait(0.2):
                if cancel_event is not None and cancel_event.is_set():
                    response.close()
                    return
        threading.Thread(target=hang_up, daemon=True, name="shared-ai-cancel").start()
        parts = []
        try:
            while True:
                line = response.readline(1024 * 1024)
                if not line:
                    break
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(str(data["error"])[:300])
                text = (data.get("message") or {}).get("content") or ""
                if text:
                    parts.append(text)
                    if on_chunk:
                        on_chunk(text)
                if data.get("done"):
                    if on_metrics:
                        on_metrics({key: value for key, value in data.items() if key.endswith(("_count", "_duration"))})
                    break
        except (OSError, ValueError) as error:
            if cancel_event is not None and cancel_event.is_set():
                return "".join(parts)
            raise RuntimeError(f"The connection to the other computer was lost: {error}") from error
        finally:
            stop.set()
            response.close()
        return "".join(parts)
