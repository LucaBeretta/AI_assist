# Target architecture and component boundaries

D8 uses one pywebview desktop interface, loaded from local files. Tk and the loopback browser-preview API have been retired. The core extraction described in `architecture-decision.md` remains; there is no new inference engine.

## Layers

```text
Presentation       webui/static/* (offline vanilla JS, system fonts, Night/Day)
                       | native js_api calls             ^ batched JSON events
Interface adapter  webui/app.py and mixins                webui/bridge.py
                       |                                 ^ workers only enqueue
Application core   app_core/: ollama, streaming, providers, privacy, settings, paths, instance_lock
Domain modules     coding_agent, developer_tools, chat_store, shared_memory, hybrid_engine
Integrations       colibri_backend, colibri_launcher, model_advisor, model_library, firewall_lock
Security           network_gate, child_gate/sitecustomize.py, firewall_lock
Optional pairing   sharing_control -> gated pairing_worker child -> peer_pairing
```

`app_core` has no UI imports. Importing `webui.app` also imports neither Tk nor pywebview; the native toolkit loads only in `main()`. `network_gate.py` stays standalone because Python children load it by path. Tests cover these boundaries (`test_app_core`, `test_webui_imports`).

## Components and contracts

| Boundary | Module | Contract |
| --- | --- | --- |
| Ollama protocol | `app_core/ollama.py` | Existing transport, parsers and stream bounds (1 MiB per line, 16 MiB per stream). HTTP method overrides, empty delete responses and request `keep_alive` preserve desktop model management. |
| Engine compatibility | `app_core/runtime.py` | Thin delegation to the core, preserving request/list/load/Stop names. No second transport engine. |
| Providers | `app_core/providers.py` | Existing Ollama/Colibri providers; the Colibri adapter reuses its client and saved settings. |
| Conversation services | `app_core/conversation.py`, `app_core/chat_service.py` | The chat service owns drafts, incognito records, active streams and reply recovery. The desktop API coordinates projects, engines and the JSON bridge. |
| Privacy | `app_core/privacy.py` | Authoritative status-bar text/role based on gate, model, endpoint and firewall. |
| Persistence | `app_core/settings.py`, `paths.py`, `chat_store.py` | Atomic private JSON writes, private new folders, tolerant existing formats. No user-data migration. |
| Single instance | `app_core/instance_lock.py`, `webui/instance.py` | One OS file lock per data folder; a file signal activates the existing window. No second lock or activation socket. |
| Streaming | `app_core/streaming.py`, `app_core/chat_service.py` | Stop interrupts Ollama and Colibri reads, waits for the reader to exit, and saves accepted text. |
| Native API | `webui/app.py` and mixins | Plain JSON; failures return `{"ok": false, "error": "..."}`. No HTTP routes, bearer tokens or launch links. |
| Worker events | `webui/bridge.py` | Thread-safe queue, one flusher, consecutive-token merging around 30 ms, JS-safe JSON; workers never retain windows. |
| Internet approvals | `webui/internet.py`, `privacy.py`, `static/security.js` | Serialized two-stage approvals, server-enforced three-second second step; Block on Enter/Escape/close; connection log and existing firewall controls. |
| Compute | `compute_telemetry.py`, `webui/compute.py` | Read-only fixed grid, sampled at most every five seconds while visible. Paired metadata never implies live telemetry. |
| Hourly check-in | `app_core/hourly_status.py`, `hourly_summary.py`, `quiet_audio.py` | Default off, opt-in Windows level detection, one hour plus twenty seconds quiet, defer during work; no automatic internet update check. |
| Manual sharing | `sharing_control.py`, `webui/sharing.py`, `pairing_worker.py` | Off every run; explicit enable creates a gated child PairingHost. Bounded pipe control, TLS code comparison, short-lived PIN. No chat or command listener. |

## Threading and trust

API calls validate inputs and return operation IDs for long work. Streaming, Developer, firewall, telemetry and pairing work run off the UI thread. Native evaluation belongs to the bridge flusher. A worker waiting for approval blocks on an event; the UI remains available. Close and cancellation unblock approvals and stop optional services.

Only the main local-file window receives `js_api`. HTML previews receive no application API. Markdown uses safe DOM construction; text/metrics use `textContent`. The existing CSP remains, including its documented pywebview injection exception. See `threat-model.md` for current boundaries and the retained upstream analysis.

## Persistence and upgrades

Data stays in `%LOCALAPPDATA%/AI Assist/chats`, `~/.aiassist/chats`, or `AI_ASSIST_DATA_DIR`. Environments, summaries, drafts, versions, projects and recovery data remain readable. Incognito stays in memory and out of exports. Backup uses `ChatStore.export_all`.

Pairing identity/trust records stay below the private data directory; sharing enabled state is never saved. Hourly preferences are separate and audio detection requires opt-in. Unknown readings are never shown as measured zeroes.

Future schema migrations should remain tolerant and back up before rewriting files. Command sandboxing and non-Windows native validation remain separate work; the desktop bridge is not an OS sandbox.
