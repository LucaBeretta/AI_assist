"""Your AI network: the computers on this local network that AI Assist can use, and which one answers.

This module combines what AI Assist already knows (this computer's models and
load, its paired devices, and the last network scan) into one map, then picks
a computer for a request with compute_routing's local-first policy. It opens no
listener and contacts only paired devices, through their pinned TLS connection.
Nothing here sends a chat: it only decides and explains where one would go.
"""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from dataclasses import dataclass, field, replace
from typing import Optional, Tuple

import compute_routing as routing
import peer_pairing

LOCAL_ID = "this-computer"
# A peer's usable context is not reported; this only keeps the policy's size check from excluding it.
UNKNOWN_CONTEXT = 1 << 20
SURVEY_TIMEOUT = 2.0


@dataclass(frozen=True)
class NetworkNode:
    """One computer in the AI network, as plain data the UI can show."""
    node_id: str
    name: str
    kind: str                      # "this computer" | "paired"
    address: Optional[str] = None
    online: bool = True
    models: Tuple[str, ...] = ()
    shares_ai: bool = False        # this computer, or a peer that let us list its models
    status: str = ""               # plain words for the map: "Ready", "Not sharing its AI", ...
    gpu: str = ""
    gpu_free_bytes: Optional[int] = None
    ram_free_bytes: Optional[int] = None
    cpu_percent: Optional[float] = None
    tokens_per_second: Optional[float] = None

    def to_compute_node(self):
        free = self.gpu_free_bytes if self.gpu_free_bytes is not None else (self.ram_free_bytes or 0)
        local = self.kind == "this computer"
        return routing.ComputeNode(
            node_id=self.node_id, is_local=local, online=self.online, installed_models=frozenset(self.models),
            max_context_tokens=UNKNOWN_CONTEXT, free_memory_bytes=max(0, int(free)),
            accepts_inference=local or self.shares_ai, paired=not local,
            trusted_lan=not local and bool(self.address) and peer_pairing.permitted_ipv4(self.address, loopback=True),
            peer_inference_allowed=not local and self.shares_ai, tokens_per_second=self.tokens_per_second)


@dataclass(frozen=True)
class NetworkMap:
    nodes: Tuple[NetworkNode, ...]
    # Devices the last scan found running AI Assist or Ollama that are not paired yet.
    can_join: Tuple[dict, ...] = field(default_factory=tuple)


def _usage_numbers(usage):
    gpus = usage.get("gpus") or []
    gpu = gpus[0] if gpus else {}
    total, used = gpu.get("total_bytes"), gpu.get("used_bytes")
    ram = usage.get("ram") or {}
    return {"gpu": str(gpu.get("name") or ""),
            "gpu_free_bytes": int(total - used) if isinstance(total, (int, float)) and isinstance(used, (int, float)) else None,
            "ram_free_bytes": int(ram["available_bytes"]) if isinstance(ram.get("available_bytes"), (int, float)) else None,
            "cpu_percent": (usage.get("cpu") or {}).get("percent")}


def peer_models(record, client_id, timeout=SURVEY_TIMEOUT):
    """(models, status) for one paired computer: what it shares with this one, in plain words."""
    try:
        response = peer_pairing.open_shared_ai(record, client_id, "GET", "/api/tags", timeout=timeout)
    except peer_pairing.AIUnavailable:
        return (), "Not sharing its AI"
    except peer_pairing.PeerOffline:
        return None, "Offline"
    except peer_pairing.PairingError as error:
        text = str(error)
        if "has not allowed" in text:
            return (), "Hasn't allowed this computer to use its AI"
        if "certificate changed" in text:
            return None, "Pair it again: its certificate changed"
        return (), text[:120]
    try:
        data = json.loads(response.read(1024 * 1024))
    except (OSError, http.client.HTTPException):
        # It answered, then stopped sending (timeout or hang-up) before the list was complete.
        return None, "Offline"
    except (ValueError, RecursionError):
        return (), "Sent an unreadable model list"
    finally:
        response.close()
    listed = data.get("models") if isinstance(data, dict) else None
    names = tuple(sorted({item["name"] for item in listed
                          if isinstance(item, dict) and isinstance(item.get("name"), str)})) if isinstance(listed, list) else ()
    return names, "Ready" if names else "Shares its AI but has no models"


def _survey_peer(peer_id, record, client_id, fetch_usage, fetch_models, scanned_name=None):
    models, status = fetch_models(record, client_id)
    numbers = {}
    if models is not None:
        try:
            numbers = _usage_numbers(fetch_usage(record, client_id))
        except peer_pairing.PairingError:
            numbers = {}  # usage sharing is a separate choice; the AI may still be shared
    # Pairing records hold no name; the network scan usually knows one for the same address.
    name = str(record.get("name") or scanned_name or peer_id)[:64]
    return NetworkNode(node_id=f"peer-{peer_id}", name=name, kind="paired", address=record.get("address"),
                       online=models is not None, models=models or (), shares_ai=bool(models),
                       status=status, **numbers)


def survey(local_models, local_usage, paired_records, client_id, scanned=(), tokens_per_second=None,
           fetch_usage=peer_pairing.fetch_usage, fetch_models=peer_models):
    """Build the AI network map. Paired computers are asked in parallel, each with a short timeout."""
    local = NetworkNode(node_id=LOCAL_ID, name="This computer", kind="this computer",
                        models=tuple(sorted(set(local_models or ()))), shares_ai=True,
                        status="Ready" if local_models else "No models installed",
                        tokens_per_second=tokens_per_second, **_usage_numbers(local_usage or {}))
    records = {peer_id: record for peer_id, record in (paired_records or {}).items()
               if isinstance(peer_id, str) and isinstance(record, dict)}
    names = {device.get("address"): device.get("name") for device in scanned or ()
             if isinstance(device, dict) and device.get("name") and device.get("name") != device.get("address")}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(records)))) as pool:
        peers = list(pool.map(lambda item: _survey_peer(item[0], item[1], client_id, fetch_usage, fetch_models,
                                                        names.get(item[1].get("address"))),
                              sorted(records.items())))
    paired_addresses = {record.get("address") for record in records.values()}
    can_join = tuple({"address": device.get("address"), "name": device.get("name") or device.get("address"),
                      "ai_assist": bool(device.get("ai_assist")), "ollama": device.get("ollama")}
                     for device in scanned or ()
                     if isinstance(device, dict) and (device.get("ai_assist") or device.get("ollama"))
                     and not device.get("this") and device.get("address") not in paired_addresses)
    return NetworkMap(nodes=(local, *peers), can_join=can_join)


@dataclass(frozen=True)
class Choice:
    node: NetworkNode
    model: str
    reason: str
    explanation: str               # what the person reads before anything is sent


def choose(network, model, mode="local", pinned_node_id=None):
    """Pick the computer that should answer, or raise routing.RoutingError with the reason.

    "local" (the default) never leaves this computer; "auto" may pick a paired computer
    that shares its AI and has the model; "pinned" uses exactly the chosen computer.
    Nothing falls back silently: a failure is an error the person sees.
    """
    request = routing.RoutingRequest(model=model, mode=mode, pinned_node_id=pinned_node_id,
                                     allow_peer_inference=mode != "local")
    decision = routing.choose_route(request, [node.to_compute_node() for node in network.nodes])
    node = next(item for item in network.nodes if item.node_id == decision.node_id)
    if node.kind == "this computer":
        explanation = f"{model} runs on this computer. Your message stays here."
    else:
        explanation = (f"{model} runs on {node.name} on your home network. Your message is sent there, "
                       "encrypted, and only to that paired computer.")
    return Choice(node=node, model=model, reason=decision.reason, explanation=explanation)


def with_speed(network, node_id, tokens_per_second):
    """Record a measured speed (tokens per second) for one computer, for later auto choices."""
    return replace(network, nodes=tuple(replace(node, tokens_per_second=tokens_per_second)
                                        if node.node_id == node_id else node for node in network.nodes))
