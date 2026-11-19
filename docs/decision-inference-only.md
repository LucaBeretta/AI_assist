# Decision: inference-only paired devices (2026-11-19)

**Decision:** Initial LAN compute sharing transports AI inference requests and streamed results **only**. Do **not** expose remote filesystem access, project sync, shell commands, terminal sessions, memory databases, or agent tool execution.

**Device modes:** Local (default), Auto (explicit consent and paired nodes only), and pinned device (explicit consent, no silent fallback).

**Security boundary:** A LAN address is not a trustworthy identity. Remote nodes must be authenticated and explicitly paired in a future transport layer; the routing policy's flags must never be populated from unauthenticated discovery messages. Default server listeners are disabled until the user opts in. Neither this decision nor the new pure routing module establishes cryptographic authentication or an encrypted transport.

**Data flow:** Only required inference prompts/context may be sent to a paired inference server. Chats, secrets and project files remain on the requesting device. Display which node receives prompts. Explicitly forbid remote command and file-tool APIs.

**Implementation status:**
- Complete: independent `compute_routing.py` selection policy and `test_compute_routing.py` unit tests; the module does not open sockets or change the application UI.
- Not implemented: secure pairing, encryption, discovery, capability measurements, streaming transport, integration into Ollama/Colibri, UI toggle, mobile companions.
- Test evidence: 10 isolated routing tests passed in a Python 3 runtime prior to committing. Full repository suite and cross-OS runtime checks have not been performed.

**Next implementation checkpoint:** Pair two trusted computers using an authenticated and encrypted inference-only API, initially with manual selection. Keep the existing internet gate and Windows firewall behavior unchanged. After validating transport and cancellation, integrate the router and build Auto selection.

## Decision: manual sharing only (2026-11-19)

LAN inference hosting remains **off at startup** and after restart. The user must click a future **Start sharing** UI control each session. No remembered setting, discovery request, login item, background daemon or scheduled task may activate a listener. **Stop sharing** and application shutdown turn it off. Discovery of other nodes, if separately user initiated, does not enable serving.

Implemented as an isolated lifecycle guard in `sharing_control.py` with `test_sharing_control.py`. Its constructor is inert: it does not construct `PairingHost`, whose constructor currently binds a socket. Only `enable(user_initiated=True)` constructs and starts the host, and `disable()`/`close()` stop it. The flag is an application-level safeguard, not proof of a genuine user gesture; GUI integration must make that guarantee. This controller is not yet integrated with the main GUI.

**Validation status:** Unit test source was added, but cross-platform and full-repository tests have not been executed for this change.

Before production: verify stop() reliably closes listeners and active sessions on failure/shutdown; audit constructor failures and races; add end-to-end explicit UI click tests, and ensure the discovery service cannot remain active after a failed transition.
