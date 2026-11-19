# Modernization backlog

Priorities: **P0**, safety or data-loss risk, do next. **P1**, needed for web-workspace parity or reliable releases. **P2**, packaging and reach. **P3**, polish.

Sizes: S (≤1 day), M (2–4 days), L (1–2 weeks). Each item names its evidence (`audit §` = `docs/architecture-audit.md`).

## Done in milestone 1 (branch `modernization/web-workstation`)

- UI-free core `app_core/`: providers, conversation rules, chat service, privacy, settings, paths, streaming, and the single-instance lock. Tk compatibility facades keep every existing patch point (audit C1, C2, C6, S5).
- Gate hardening: no listening beyond loopback, package-aware "Asked by", and a private log file (audit S2, S4, S7).
- The update checker knows the new packages and page files (audit R2).
- Web workspace (preview): loopback API and a page with streaming chat, Stop, Markdown, chats, models, settings, memory, connection log, a developer project view, inspector, themes, and responsive layout.
- Tests: core, API contract and security, frontend unit tests (Node), page rules, and a browser end-to-end tool.
- **P0-1, approvals as a core service** (`app_core/approvals.py`). The rules are now kept in the core: actions are declined unless approved, each is answered once, and a command needs its second check plus the full wait, so an early approval is refused even if a button skips the dialog. Every approved command is logged. The desktop window, for both Ollama and Colibri, uses it.
- **P1-1, developer agent in the web workspace** (`app_core/agent_service.py`, routes `/api/chats/<id>/agent`, `/api/approvals/<id>`, `/undo`, `/api/projects/<chat>/chats`). Each edit is a card with its diff; each command has the same two confirmations and the 3-second wait, counted by the server; Stop or a closed page declines what is waiting; Undo and New task. Evidence: `test_web_agent.py`, and `tools/web_e2e.py --agent-model qwen3-coder:30b` (a real command taken to its second check and rejected, never run; a real edit reviewed, applied and undone). Not in the web version: **Save As** (choosing another file for an edit) and choosing a new project folder; both stay in the desktop app.

- **Undo for the agent's file changes** (`app_core/checkpoints.py`): originals saved per task outside the project; undo restores them and leaves files you edited afterwards alone.
- **Automatic daily backups with restore** (`app_core/backups.py`): 14 kept; restore adds missing chats, replaces damaged ones, never changes a chat you still have.

## P0

| # | Item | Size | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P0-2 | **Sandboxed agent commands** (`Executor` with Seatbelt, bubblewrap/Landlock, and Windows restricted-token backends). Fail closed: no network, writes to the project only. | L | — | Per-OS tests show network refused and writes outside the project refused. |
| P0-3 | **CI** on Windows, macOS and Linux (Xvfb), Python 3.9 and 3.13, plus `node --test`. | S | — | Matrix green on every push; the support-matrix rows show evidence. |
| P0-4 | **Private settings everywhere:** `appearance.settings`, `colibri.settings`, the hybrid config and `firewall-lift.json` go through `app_core.settings.write_json` (audit S4, C6). | S | — | A POSIX test shows 0600 for each file. |

## P1

| # | Item | Size | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P1-1b | **Rest of web agent parity:** Save As for an edit, choosing a new project folder (a server-side folder picker limited to the user's home, with its own security review), and moving the desktop window's `agent_worker` onto `AgentService`. | M | P1-1 (done) | Tk agent tests pass through `AgentService`; a web edit can be saved to another file. |
| P1-2 | **Chat metadata index** off the UI thread: title, mode, model, `updated_at`, size; full-text search in a worker (audit P1: 2.24 s per refresh at 200×200). | M | — | `profile_workflows.py` shows refresh under 50 ms at 200×200. |
| P1-3 | **Schema version for chats**, with a backup zip before any migration that rewrites files (audit C7). | S | — | Old and new readers round-trip; damaged and newer files are skipped, not overwritten. |
| P1-4 | **Model lifecycle service** (load, unload, start/stop Colibri) shared by both UIs; web: Run all / Stop all. | M | — | `BackendMixin.model_worker` delegates; web buttons work. |
| P1-5 | **Web internet approvals**: two-step, delayed, with notification; only for an authenticated page with focus; denial by default; logged. Until then web mode stays fail-closed. | M | P0-1 | Gate tests: approve and deny through the API with the same timings; no auto-approve path. |
| P1-6 | **Type-check the page** with `tsc --allowJs --checkJs --noEmit` in CI (dev dependency only). | S | P0-3 | Zero type errors in CI. |
| P1-7 | **Signed release manifest** and a tag-based update check (`docs/platform-support.md` §2). | M | P0-3 | The checker verifies a signature; development copies are labelled as such. |
| P1-8 | **Name the agent's shell** in its system prompt (PowerShell 5.1 on Windows), or prefer `pwsh` when present (audit C8). | S | — | Agent tests cover the prompt text; fewer first-attempt command failures. |
| P1-9 | **Web parity items**: export a chat, back up all chats, edit and resend, keyboard shortcuts help, feedback flow (`feedback.py`), firewall-lock status in privacy text. | M | — | e2e covers each. |

## P2

| # | Item | Size | Depends on |
| --- | --- | --- | --- |
| P2-1 | Tauri 2 shell around the web workspace (Python sidecar, in-process launch token, capability allowlist). | L | P1-1, P1-4 |
| P2-2 | Self-contained signed bundles (Windows per-user installer, notarized macOS `.app`, AppImage). | L | P0-3, P1-7 |
| P2-3 | LAN companion: pairing, TLS-PSK, scopes, revocation (`docs/threat-model.md` §7). | L | P1-5 |
| P2-4 | Network lock for engines on macOS (pf anchor or Network Extension) and Linux (nftables per cgroup or uid). | L | — |
| P2-5 | On-device mobile spike (llama.cpp minimal app, chat JSON import/export). | M | — |

## P3

- Retire `vercel_deploy.deploy_project` / `vercel_command` / `run_cli` (dead code, audit C3), keeping the validation helpers.
- One process-tree killer (audit C2): `coding_agent._terminate_process_tree` and `colibri_launcher.terminate_tree`.
- One agent-approval bridge for Ollama and Colibri (audit C2).
- Replace 120 literal "Segoe UI" fonts in Tk with a platform font helper (audit X1).
- Virtualize long message lists in Tk (audit P3), or stop investing in Tk once web parity lands.
- `AGENT_MAX_STEPS` from `coding_agent` (audit C2).
