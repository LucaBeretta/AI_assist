# Threat model

Scope: the desktop app, the programs it starts, and the new web workspace (preview), as of 2026-11-19. Rule from the project: **protections are mandatory and fail closed.** Limits are stated, not hidden.

## 1. Assets

| Asset | Where |
| --- | --- |
| Conversations, drafts, shared notes | `<data>/<uuid>.json`, `<data>/shared.memory` |
| Project source and secrets inside project folders | user-chosen folders |
| What went online, when and why | `<data>/network.log` |
| Credentials held in memory | Vercel token, Colibri API key (session only) |
| The machine itself | anything an agent command can do with the user's rights |
| The user's trust in "local only" | status bar, web status chip |

## 2. Actors

1. **A web page in the user's browser.** Any site can make the browser send requests to `127.0.0.1`. An HTML *preview* page runs generated scripts on another loopback port: same site, different origin.
2. **A model** (prompt injection from project files, command output, or recalled chats) that persuades the agent or the user.
3. **Another device on the LAN.**
4. **Another local user** on a multi-user macOS or Linux machine.
5. **Malicious local code running as the same user.** This is **out of scope**: it can already read every file and debug the process. The defenses below only make accidental exposure hard.

## 3. Trust boundaries

```
 [ other websites / preview page ]──(HTTP to 127.0.0.1)──┐
                                                         ▼
 [ browser: web page ]──HTTP+bearer──▶ [ AI Assist process: API · core · gate ]──▶ [ Ollama / Colibri on 127.0.0.1 ]
                                          │   │                                       (native: firewall lock, Windows)
                 two confirmations ──────┘   └──▶ [ child Python: child gate ] ──▶ asks the app
                                          └──▶ [ agent command: any program, user's rights ]  ← outside the gate
```

## 4. Threats and mitigations

| # | Threat | Mitigation (status) | Evidence |
| --- | --- | --- | --- |
| T1 | A website reads or drives the API (CSRF, DNS rebinding) | Exact `Host` check; `Origin` must equal this server; `Sec-Fetch-Site` must be `same-origin`; bearer token required; no cookies; no CORS headers; JSON-only bodies (a cross-origin `Content-Type: application/json` needs a preflight, which is never granted). **Implemented.** | `test_web_server.SecurityTests` (rebinding, other origins, OPTIONS, missing session, body types) |
| T2 | A preview page on a sibling loopback port uses the user's session | Same-site cookies would be sent, so none are used. The token lives in the page's own origin storage; the preview's CSP (`default-src 'self'`) cannot reach another port; an `Origin` from the preview's port is refused. **Implemented.** | as T1, `test_other_sites_are_refused_and_never_get_cors` |
| T3 | The API is reachable from the LAN | The constructor refuses any host but `127.0.0.1` or `::1`. **And** the gate now refuses any non-loopback listening socket in the app process, as a second, independent layer. `SO_EXCLUSIVEADDRUSE` on Windows. **Implemented.** | `test_only_loopback_addresses_can_be_served`, `test_network_gate.ListeningTests`, `InstalledGateTests.test_the_app_process_cannot_listen_beyond_this_computer` |
| T4 | Launch link leaks (shell history, process list, screen) | Single use, five-minute expiry, removed from the address bar and history at once; a fragment is never sent to the server or logged; a used code gets a clear error, so an interception becomes visible. **Implemented.** | `test_launch_codes_work_once_and_expire`, e2e "launch code left the address bar" |
| T5 | Model output injects markup or script into the page (XSS) | The renderer builds DOM nodes only from text; no `innerHTML`, `eval` or inline handlers (a source scan enforces this); CSP `default-src 'none'; script-src 'self'`, no `unsafe-*`; links are shown as text, not followed. **Implemented.** | `webui/tests/markdown.test.mjs`, `test_web_frontend.PageRuleTests`, e2e "no raw HTML elements", "no CSP violations" |
| T6 | The web page triggers commands, file writes or internet access | There is no direct command or file-write endpoint. Edits and commands happen only inside an agent task (`POST /api/chats/<id>/agent`), each one paused until the person answers `POST /api/approvals/<id>`, a random 64-bit id valid only while that review is open. The rules are in `app_core/approvals.py`, not in the page: an edit is declined unless approved; a command is refused until its second check was requested and the full wait has passed. Stop, a closed page or a server error decline what is waiting. The agent runs only in a folder already saved on a developer chat; the web API cannot choose a new folder. The route list is pinned by a test. Web mode has no internet approver, so the gate blocks all internet access. **Implemented.** | `test_web_agent.py` (edit waits then undo, rejected edit, command needs both confirmations and the wait, rejected command never runs, Stop declines, refusals), `test_the_api_has_no_direct_command_write_or_internet_endpoints`, `test_status_says_what_this_interface_cannot_do`, e2e `--agent-model` |
| T7 | Path traversal or secrets read through the project view | Reads only inside folders saved on developer chats; the same confinement and exclusions as the agent (`developer_tools`): no `..`, absolute paths, symlink escapes, secret names, private keys, binaries or dependency folders. **Implemented.** | `test_web_server.ProjectFileTests` |
| T8 | Two writers overwrite chats (desktop window and web, or two windows) | Per-data-folder OS lock in both entry points. **Implemented.** | `test_app_core.InstanceLockTests` (incl. a second process) |
| T9 | Error messages leak internals | A 500 is generic; server logging is off (paths and queries may hold file names). **Implemented.** | `test_unexpected_errors_reveal_no_details` |
| T10 | Other local users read chats or the log (POSIX) | A data folder created by the app is 0700; the log 0600; settings via `app_core.settings` 0600. Existing folders are not changed. **Implemented, untested here** (the tests are POSIX-only and skipped on this Windows machine). | `test_app_core...is_private`, `test_network_gate...log_is_private` |
| T11 | Something goes online silently | The gate, unchanged: audit hook, two confirmations, notifications, log; child gate for Python children; firewall lock for Ollama (Windows). | existing `test_network_gate.py`, `test_firewall_lock.py` |
| T12 | The approval dialog misattributes the requester | "Asked by" now names `app_core/` and `webui/` modules; frames from the private `runtime/` Python are skipped. **Implemented.** | `test_requests_from_the_apps_packages_name_their_module` |
| T13 | Prompt injection leads to a harmful agent command | Two confirmations with a 3-second second step, command and folder shown, logged and announced. The rules are enforced in `app_core/approvals.py`, so an approval that skips the second check or the wait is refused whatever the interface does: the desktop dialog and the web page use the same core. The web card shows the full command and the warning; the page cannot shorten the wait (the server counts it). **No OS sandbox yet** (section 5). | `test_approvals.py`, `test_workspace.py` (incl. `test_an_early_command_approval_is_refused_by_the_core_and_the_dialog_stays`), `test_web_agent.CommandTests` |
| T14 | A second browser tab or a stale page answers a review | Answers need the session token; each review id is single-use and random; an answer to a closed or unknown review gets 404; the first answer wins. Only the page that started a task sees its review cards (they arrive on its stream). | `test_answers_must_be_known_choices_for_open_reviews`, `test_stop_declines_the_waiting_review` |

## 5. Residual risks (stated plainly)

- **Agent commands run with the user's full rights, outside the gate.** Only Python they start is checked. The confirmations depend on the person reading the command.
- **Native code bypasses the audit hook.** Python extensions with their own networking (others than `hf_xet`, `hf_transfer`, `pycurl`), and Python started with `-I`, `-E` or `-S`. Ollama is covered by the firewall lock on Windows only; **macOS and Linux have no network lock for the engines.**
- **The browser is outside the gate.** The page cannot connect elsewhere (CSP), but browser extensions can read any page; that is out of scope, as with any local web app. The HTML *preview* feature still lets a generated page navigate the browser elsewhere (documented in the README).
- **The bearer token is in `sessionStorage`.** A successful XSS could use it. The renderer and CSP are the defense; there is no second factor.
- **Same-user malware** can do anything the user can (actor 5).

## 6. Sandboxing agent commands (investigation and recommendation)

What exists elsewhere ([Codex CLI's sandbox](https://developers.openai.com/codex/concepts/sandboxing), [the Windows design write-up](https://openai.com/index/building-codex-windows-sandbox/)):

| OS | Mechanism | Network block | File confinement | Notes |
| --- | --- | --- | --- | --- |
| macOS | Seatbelt (`sandbox-exec` with a profile) | yes | yes (write to project only) | `sandbox-exec` is deprecated but still shipped and used by major tools |
| Linux | bubblewrap (namespaces) and/or Landlock (LSM, 5.13+), seccomp | yes (network namespace) | yes | bubblewrap is usually installed by Flatpak; Landlock needs no helper |
| Windows | restricted tokens, a dedicated low-rights user, ACLs, firewall rules per SID; AppContainer | elevated mode only | yes | no built-in "sandbox this command"; Windows Sandbox needs Pro/Enterprise |

**Recommendation (backlog P0-2):** an `Executor` interface with backends `unsandboxed` (today), `seatbelt`, `bubblewrap`/`landlock`, `windows-restricted`. Fail closed: when the chosen sandbox cannot start, the command does not run. Default, once available: **no network**, write access to the project folder only, read-only elsewhere. Network needs a separate, explicit approval through the gate. Until then the two-step confirmation stays the control.

## 7. Before any LAN or mobile access (not enabled)

Required design, all mandatory:

1. **Pairing:** a short code shown on both screens and compared by the person, with a per-device key. TLS with a pre-shared key (or pinned self-signed certificate) bound to that pairing.
2. **A separate listener** created only after the user turns LAN access on, through an explicit, logged gate exception (the new listening rule refuses it otherwise).
3. **Per-device scopes:** `chat` only by default; never `agent`, `files` or `approvals` until those have their own reviewed design.
4. **Raw Ollama never exposed;** only AI Assist's API, which applies the rules above.
5. **Revocation and audit:** a list of paired devices, last seen, revoke button; every remote session in the connection log.
