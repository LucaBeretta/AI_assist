# Target architecture and component boundaries

This describes the layers AI Assist is moving to, what exists after the first milestone, and the rules each layer follows. The reasoning is in `docs/architecture-decision.md`.

## Layers

```
 Presentation     Tk desktop window (AIassist.py + *_ui.py mixins)      Web page (webui/static/*.js)
                        │  calls core functions directly                      │  HTTP + NDJSON, bearer token
                        │                                                     ▼
 Interface adapter      │                                      webui/server.py  ·  webui/security.py
                        ▼                                                     │
 Application core  app_core/: chat_service · conversation · providers · privacy · settings · paths · instance_lock · streaming
                        │
 Domain modules    coding_agent · developer_tools · hybrid_engine · chat_store · shared_memory · feedback · model_advisor · vercel_api
                        │
 Integrations      app_core/ollama · colibri_backend · colibri_launcher · model_library · html_preview · firewall_lock
                        │
 Security          network_gate (audit hook, approvals, log)  ·  child_gate/sitecustomize.py  ·  firewall_lock
```

Dependencies point down only. Three rules are enforced by tests:

- `app_core/` and `webui/` never import a UI toolkit (`test_app_core.BoundaryTests`).
- `network_gate.py` stays standalone, because child processes load it by file path.
- The API's route list is pinned (`test_web_server.SecurityTests.test_the_api_has_no_command_write_or_approval_endpoints`).

## Components and their contracts

| Boundary | Module | Contract |
| --- | --- | --- |
| Inference providers | `app_core/providers.py` | `ChatProvider`: `info()` returns `ProviderInfo(name, endpoint, local, manages_lifecycle)`; `list_models()`, `running()`, `model_details(model)`, and `stream_chat(messages, model, *, context_length, on_chunk, on_metrics, cancel_event)`, which returns the full text or raises `RuntimeError`. Implementations: `OllamaProvider`, `ColibriProvider`. `configured_provider(data_dir)` reads `backend.settings`. |
| Ollama wire protocol | `app_core/ollama.py` | Transport (`api_json`, `stream_chat`, `list_models`), parsers (`model_names`, `running_names`, `running_details`, `placement`) and builders (`lifecycle_payload`). The limits are unchanged: 1 MiB per line, 16 MiB per stream. |
| Conversation rules | `app_core/conversation.py` | `title_from`, `chat_messages` (shared memory first), `agent_messages` (resumes native tool history), `developer_system_prompt`. Pure functions, used by both interfaces. |
| Chat service | `app_core/chat_service.py` | `prepare(chat, text, model, backend)` validates and reserves the chat, or raises `ServiceError(status)`. `send(...)` emits `user`, then `chunk` events, then `metrics`, then exactly one `done` (with `stopped`) or `error`. `release` undoes `prepare`. It also does list, get, create, rename and delete. The user's message is saved before the request; the answer is saved only when complete, or as the partial text after Stop. |
| Agent approvals | `app_core/approvals.py` | `approver(notify, cancel, closed)` is the agent's `approve_action`. A decision (`{"action", "approved", "event"}`) is shown through `notify` and answered with `resolve(decision, approved, destination=None)`, once only. For commands: `begin_second_check(decision)`, then approval no sooner than `COMMAND_CONFIRM_SECONDS` later; an earlier approval is refused. Approved commands are logged through `network_gate.note_outside`. Stop, closing, or an interface error decline. |
| Agent tasks | `app_core/agent_service.py`, `app_core/activity.py` | `AgentService(chats)`: `prepare(chat, text, model, backend)` (developer chat with an existing folder, a model, the current engine), `run(...)` emits `user`, `agent{event}` per step, `approval{id, action}` for each edit or command (a `review_view`: path, diff capped at 200k characters, command, folder; never the file's full content), `approval_closed{id, approved}`, then `done{stopped, changed}` or `error`. `answer(id, "reject" / "second_check" / "approve")` goes through `app_core.approvals`. `undo(chat)` restores the latest task's checkpoint; `create_task(chat)` starts a new chat in the same folder. Activity records are capped (100 entries, 40k characters per diff). |
| Privacy status | `app_core/privacy.py` | `is_local_url` (follows `network_gate.classify`) and `privacy_status(gate, model, url, firewall)`, which returns `(text, role)`. |
| Settings and paths | `app_core/settings.py`, `app_core/paths.py` | Atomic, private (0600) JSON writes; `load_backend` / `save_backend`; `load_file_limit` / `save_file_limit`; `data_directory()` and `ensure_private_directory()` (0700 when created). |
| One writer per data folder | `app_core/instance_lock.py` | An OS file lock (`msvcrt` or `flock`) held by every entry point. It ends with its process. `AlreadyRunning` names the holder. |
| Streaming and Stop | `app_core/streaming.py` | `run_stoppable(request, parts, cancel)` returns `(answer, error, stopped)`; `StreamStopped`. |
| Security policy | `network_gate.py` | The gate, as before, plus a new rule: the app process may listen on loopback only. |
| Web API | `webui/server.py`, `webui/security.py` | See below. |

### Web API (v1, preview)

All endpoints are under `/api`, take and return JSON, and require `Authorization: Bearer <token>`, except `POST /api/session`.

| Method and path | Purpose |
| --- | --- |
| `POST /api/session {launch}` | Exchange a single-use launch code for a token. |
| `GET /api/status?model=` | Engine, endpoint, privacy text, gate state, what this interface can do. |
| `GET /api/models`, `GET /api/models/details?name=` | Installed models, placement, capabilities. |
| `GET /api/hardware` | CPU, RAM, GPUs, disk (cached for 120 s). |
| `GET/POST /api/chats`, `GET/PATCH/DELETE /api/chats/<id>` | Saved chats. |
| `POST /api/chats/<id>/messages {content, model, context_length?}` | Streams `application/x-ndjson`: `start{request}`, `user{message}`, `chunk{text}`, `metrics{metrics,summary}`, `waiting`, then `done{stopped, chat, message, local}` or `error{error}`. |
| `POST /api/chats/<id>/agent {content, model, context_length?}` | A developer-agent task, streamed like a message: `start`, `user`, `agent{event}`, `approval{id, action}`, `approval_closed{id, approved}`, `waiting`, then `done{stopped, chat, message, changed}` or `error{error, help}`. |
| `POST /api/approvals/<id> {choice}` | Answer an open review: `reject`, `second_check` (commands; returns `seconds_left`) or `approve`. An early or skipped command approval gets 409; an unknown or closed review 404. |
| `POST /api/chats/<id>/undo`, `POST /api/projects/<chat>/chats` | Undo the latest task's file changes; start a new task in the same folder. |
| `POST /api/requests/<id>/cancel` | Stop. A closed page also stops the answer or task, which is noticed within one heartbeat; a waiting review is declined. |
| `GET/PUT /api/memory`, `POST /api/memory/notes {note}` | Shared memory. |
| `GET /api/projects`, `GET /api/projects/<chat>/files`, `GET /api/projects/<chat>/file?path=` | Read-only, and only inside folders chosen for developer chats; the agent's exclusions apply. |
| `GET /api/log` | The last 200 connection-log entries. |
| `PUT /api/settings/engine {backend}` | Ollama or Colibri. |

Errors are `{"error": "..."}` with 400, 401, 403, 404, 405, 409, 411, 413, 415, 421 or 503. A 500 never includes internal details.

## Threading

- **Tk:** unchanged. Workers post to queues, and the Tk thread polls them.
- **Web:** `ThreadingHTTPServer` with one thread per request. For a streamed answer, the handler thread is the only writer to the socket. `ChatService.send` runs on a worker that posts events to a queue, and its `_Emitter` drops anything after the terminal event. A provider's reader thread may outlive Stop until its next chunk, as in Tk.
- `ChatService` caches the chat list. That is safe because the instance lock guarantees no other process writes the folder.

## Persistence and upgrades

Data stays where it was (`%LOCALAPPDATA%\AI Assist\chats`, `~/.aiassist/chats`, or `AI_ASSIST_DATA_DIR`), in the same formats. Nothing was migrated in this milestone.

Planned (backlog P1-3): a `schema` field on chat records, read tolerantly, with a backup zip (`ChatStore.export_all`) written before any migration that rewrites files.

## What still lives in Tk, and the order to move it

1. **Engine lifecycle** (`BackendMixin.model_worker`, `colibri_lifecycle`) goes to a `ModelLifecycle` service built on the providers.
2. **Agent runs**: the web workspace runs them through `app_core/agent_service.py`, with the approval rules in `app_core/approvals.py`. The desktop window still uses its own `agent_worker` with the same rules and checkpoints; moving it onto `AgentService` is the remaining step. Choosing a new project folder stays in the desktop app.
3. **History list and search** go to a metadata index (audit item P1), shared by both interfaces.
4. **Status bar** values: `StatusBar.snapshot` already uses `app_core.privacy`; the rest becomes a `RuntimeStatus` service.

The Tk window can be retired only when the web workspace covers all of these and an approved migration milestone says so.
