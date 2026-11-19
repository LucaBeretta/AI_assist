"""Pure, local-first compute routing policy for AI Assist.

This module deliberately performs NO peer discovery, authentication, networking,
model loading or remote tool execution. Peer identity/consent booleans must be
provided by a separate, verified trust and transport layer before use.
"""

from dataclasses import dataclass, field
from typing import FrozenSet, Optional, Sequence


class RoutingError(ValueError):
    """No authorized, compatible inference node satisfies the request."""


@dataclass(frozen=True)
class ComputeNode:
    node_id: str
    is_local: bool
    online: bool
    installed_models: FrozenSet[str]
    features: FrozenSet[str] = field(default_factory=frozenset)
    max_context_tokens: int = 0
    free_memory_bytes: int = 0
    model_backend_local: bool = True
    accepts_inference: bool = True
    # For peers only: these values MUST originate from authenticated pairing.
    paired: bool = False
    trusted_lan: bool = False
    peer_inference_allowed: bool = False
    # Benchmark data is optional. Missing measurements never imply high speed.
    tokens_per_second: Optional[float] = None
    queue_depth: int = 0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RoutingRequest:
    model: str
    mode: str = "local"  # local | auto | pinned
    pinned_node_id: Optional[str] = None
    allow_peer_inference: bool = False
    required_features: FrozenSet[str] = field(default_factory=frozenset)
    context_tokens: int = 0
    memory_required_bytes: int = 0


@dataclass(frozen=True)
class RouteDecision:
    node_id: str
    is_local: bool
    model: str
    reason: str


def _eligible(node: ComputeNode, request: RoutingRequest) -> bool:
    """Compatibility alone does not authorize a remote inference session."""
    if not node.node_id or not node.online or not node.accepts_inference:
        return False
    if not node.model_backend_local or request.model not in node.installed_models:
        return False
    if request.context_tokens > node.max_context_tokens:
        return False
    if request.memory_required_bytes > node.free_memory_bytes:
        return False
    if not request.required_features.issubset(node.features):
        return False
    if node.is_local:
        return True
    return bool(request.allow_peer_inference and node.paired and node.trusted_lan
                and node.peer_inference_allowed)


def _estimated_speed(node: ComputeNode) -> float:
    """Small heuristic for *measured* throughput, not a hardware benchmark."""
    if node.tokens_per_second is None or node.tokens_per_second <= 0:
        return 0.0
    return (node.tokens_per_second / (1.0 + 0.25 * max(0, node.queue_depth))
            * (1.0 - min(max(0.0, node.latency_ms), 500.0) / 2000.0))


def choose_route(request: RoutingRequest, nodes: Sequence[ComputeNode]) -> RouteDecision:
    """Select an authorized inference node without any network or side effects.

    Local is the default. Auto prefers local when remote measured speed improves
    by less than 20%; when local is unavailable, an eligible approved peer may
    be selected. Pinned never falls back silently.
    """
    if not request.model or request.mode not in {"local", "auto", "pinned"}:
        raise RoutingError("Specify a model and routing mode: local, auto or pinned.")
    if request.context_tokens < 0 or request.memory_required_bytes < 0:
        raise RoutingError("Context and memory requirements cannot be negative.")
    if request.mode == "pinned" and not request.pinned_node_id:
        raise RoutingError("Pinned routing requires a device ID.")
    if request.mode != "pinned" and request.pinned_node_id is not None:
        raise RoutingError("A pinned device ID is only valid in pinned mode.")

    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes) or "" in by_id:
        raise RoutingError("Device IDs must be unique and nonempty.")
    local_nodes = [node for node in nodes if node.is_local]
    if len(local_nodes) != 1:
        raise RoutingError("Exactly one local device must be provided.")
    local = local_nodes[0]

    if request.mode == "local":
        if not _eligible(local, request):
            raise RoutingError("This device cannot run the requested local model.")
        return RouteDecision(local.node_id, True, request.model, "Local-only policy")

    if request.mode == "pinned":
        node = by_id.get(request.pinned_node_id)
        if node is None or not _eligible(node, request):
            raise RoutingError("Chosen device is unavailable, unauthorized or incompatible.")
        return RouteDecision(node.node_id, node.is_local, request.model,
                             "User-selected device")

    eligible = [node for node in nodes if _eligible(node, request)]
    if not eligible:
        raise RoutingError("No authorized device can run the requested model.")
    if local in eligible:
        peers = [node for node in eligible if not node.is_local]
        if not peers:
            return RouteDecision(local.node_id, True, request.model, "Only eligible device")
        fastest = max(peers, key=lambda node: (_estimated_speed(node), node.node_id))
        local_speed, peer_speed = _estimated_speed(local), _estimated_speed(fastest)
        if local_speed == 0 or peer_speed <= local_speed * 1.2:
            return RouteDecision(local.node_id, True, request.model,
                                 "Prefer local when peer advantage is unverified or small")
        return RouteDecision(fastest.node_id, False, request.model,
                             "Paired peer has substantially higher measured throughput")

    fastest = max(eligible, key=lambda node: (_estimated_speed(node), node.node_id))
    return RouteDecision(fastest.node_id, fastest.is_local, request.model,
                         "Local device cannot run this model; eligible paired peer selected")
