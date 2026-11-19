# AI Assist: local-first and LAN compute routing

Status: **Proposed architecture / accepted product direction**. This document is a specification, not an implemented feature.  
Branch: `architecture-modernization`.  
Goal: Every device can operate independently where its hardware and OS permit, while trusted devices on the same local network can share AI inference capacity.

## User experience

Add a clear **Compute location** selector in the model controls:

- **This device (default)**: Run models locally; do not discover, connect to, or transfer content to peer devices. Offline operation must remain usable without LAN or internet.
- **Auto — best trusted device**: Choose an explicitly paired and online LAN peer (or this device) that can execute the *requested model/task* most effectively. Display the selected machine before sending content and while generating.
- **Choose device**: Pin inference to a specific paired peer or this device. If unavailable, show an explicit error and offer a user-controlled fallback.

A top-level Auto toggle can switch between **This device** and **Auto**; a detail menu should offer **Choose device**, pairing, and selection rules.

The interface must distinguish *where the user interface runs* from *where inference runs*: remote inference should not require moving memory, stored conversations or command execution onto the peer.

Show which computer is active, the model used, estimated capability, whether traffic remains on LAN, and whether a request sends private text. Never silently reinterpret LAN as “this computer.”

## Independent device behavior and limitations

- Windows/macOS/Linux: Prefer the existing Python + Ollama/Colibri capabilities initially; add a separate inference-service interface without immediately replacing Tkinter.
- Android/iOS: Provide a companion client for approved LAN inference first. True standalone on-device inference requires native runtimes, constrained model sizes and per-platform investigation; do not claim that the desktop Python/Ollama processes can run unchanged on mobile.
- Low-power or offline devices: Continue to support whatever locally available tasks/models are genuinely feasible, even when no other computer is reachable.
- No central cloud, account, public internet, router port-forward, or mandatory always-on server.

## Proposed boundaries

```
UI / session
    -> ComputeRouter (policy, mode, consent, device health)
        -> LocalInferenceAdapter (existing Ollama/Colibri integration)
        -> PairedPeerInferenceAdapter (authenticated LAN transport)
             -> AI Assist Node on another device
                  -> LocalInferenceAdapter
```

Keep developer-agent tools, filesystem edits and shell execution on the **requesting device** unless a separately reviewed remote-execution permission system is designed. A peer initially offers only inference/model metadata and health, *not* unrestricted shell, file, or memory access.

Introduce testable interfaces for:
- `ComputeMode`: `local`, `auto`, `pinned`.
- `NodeCapabilities`: device ID, CPU architecture, GPU and available VRAM/unified memory, RAM, installed models, runtime, capabilities, queue depth, version and consent status.
- `RoutingRequest`: selected model, required features (tool calling, streaming, context size), estimated memory, privacy requirement, and fallback policy.
- `RouteDecision`: chosen node, score inputs, reason, model and whether fallback was performed.
- `InferenceSession`: streaming tokens, cancel, error, usage metrics and node ID.

These are proposed concepts, not descriptions of currently existing classes.

## How Auto chooses the best machine

**Most powerful** must mean best *eligible for this specific request*, not simply the computer with the largest RAM or a hard-coded device priority.

1. Consider only paired, online, permitted nodes on trusted LAN connections, plus the local node.
2. Filter by model availability/compatibility, required features, available memory, runtime version, user privacy settings and node consent.
3. Rank eligible candidates based on measured model-specific throughput and time-to-first-token, available GPU/CPU memory, queue load and network latency; use hardware estimates only when no benchmark exists.
4. Prefer local processing when remote gains are negligible, to reduce unnecessary data transfer. Allow a manual preferred-host override.
5. Show the selected node and reason. Do not launch downloads or move models onto a peer automatically.
6. On loss of connection: stop the incomplete stream; never silently replay a private request to another node. Offer retry locally or on another approved node. Keep chat data intact and mark incomplete outputs.

Benchmarks should be opt-in or lightweight, cached for a limited duration, and never waste significant compute without user consent.

## Discovery, trust, privacy and network design

AI Assist currently distinguishes loopback, private LAN and internet requests with `network_gate.py`; LAN classification is **not peer authentication**. The existing privacy rules must not be weakened.

- Peer discovery: disabled by default; opt-in local discovery (e.g. mDNS) or manual IP entry; no public lookup. Do not treat every device on a private subnet as trusted.
- Pairing: verify an out-of-band code or QR with mutual identity keys; persist revocable paired-device identities, not shared blanket secrets.
- Transport: mutually authenticated encrypted sessions (TLS or equivalent), versioned, with explicit session permissions and strict endpoint allowlists.
- Listener: bind only to selected LAN interfaces when sharing is enabled; loopback by default; no UPnP, internet exposure, port forwarding or unsafe wildcard-open defaults.
- Consent: enabling Auto is an explicit choice to send model prompts/context to a paired node. Explain that chat context travels over the LAN. Allow per-peer permissions and a simple unpair/revoke action.
- Privacy: keep saved chat history and secrets local by default. Send only the necessary inference context, not entire workspaces, credential files or logs. Redact credentials; enforce payload/time/size limits.
- Controls: a peer should be able to refuse work; no arbitrary file operations, shell execution, model downloads or remote developer tooling through the inference endpoint.
- Network gate: design the peer transport to comply with the existing internet gating, including DNS resolution policies. Never route to public addresses or cloud hosts under LAN Auto.
- Logging: record timestamp, source/destination peer ID, requested model, routing decision, status and approximate transferred byte counts, but **not** prompt text by default.

Threat-model untrusted Wi-Fi participants, spoofed discovery, replay, stolen pairing tokens, malicious peers, cross-origin local browser attacks and compromised desktop processes. Explicitly document security limits rather than claiming audit hooks firewall all processes.

## Delivery plan

**Milestone A — boundaries and tests (no UI replacement)**  
Extract adapter protocols and the pure local-only routing policy. Record capabilities and write deterministic unit tests. Keep existing tests and desktop behavior passing.

**Milestone B — manual paired LAN inference**  
Prototype an opt-in node server with authenticated, encrypted streaming, cancellation and health reporting. Pair through a code; select a node manually. No remote developer tools.

**Milestone C — automatic selection**  
Add node benchmarking, capacity/load reporting, routing decisions and offline-first fallback behavior. Add the Compute location toggle and active-device status.

**Milestone D — cross-platform companion interfaces**  
Build responsive clients and verify on supported desktop/mobile configurations. Distinguish companion mode from true independent on-device inference, which requires its own feasibility milestones.

### Acceptance tests

1. With no network, a compatible local model still responds under **This device**.
2. Auto never sends data to an unpaired node, an internet host or a denied peer.
3. Two paired desktop nodes can authenticate, stream tokens and cancel requests.
4. Auto chooses an eligible faster/less-loaded node for a specified task, not necessarily the one with the highest raw hardware score.
5. Node loss leaves the local chat intact without secret replay or duplicated tool calls.
6. Developer-agent commands and file editing remain local; peer requests cannot invoke them.
7. A user can revoke peer trust, disable discovery and inspect routing events.
8. LAN/remote indicators correctly identify when content leaves the originating device.

## Open product decision

Should paired devices also be allowed to expose *shared storage/project files*, or should the first release support **inference-only** routing? The safer initial default is inference-only, with future storage sharing evaluated separately.
