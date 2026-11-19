# AI Assist architecture audit

Date: 2026-11-19. Branch inspected: `linux-support` at `e02fd1b` ("Add Linux support and update runtime").
Machine: Windows 11 (x86_64), Python 3.14.10, Tk 9.0, Ollama 0.40.16, Node 24.21 (development only).

This audit records what the code does today, with evidence. Each finding says how it was established:

- **Source**: read in the code at the cited lines.
- **Test**: shown by a test or a measurement that actually ran.
- **Inferred**: a reasoned expectation that was not demonstrated. Treat these as hypotheses.

Severity is about impact on the project's goals: an offline-first, privacy-preserving local workstation that never goes online without two human confirmations.

## 1. Inventory

83 tracked files, about 19,500 lines. Every module lives at the repository root; there are no packages except `child_gate/`.

| Area | Modules | Lines | Tk-free? |
| --- | --- | ---: | --- |
| Entry point and Ollama client | `AIassist.py` | 738 | No (imports `tkinter` at line 9) |
| Workspace UI (chat, developer, history) | `workspace_ui.py`, `developer_panel.py`, `message_widgets.py`, `feedback_ui.py`, `ui_widgets.py`, `appearance.py`, `status_bar.py`, `capabilities_view.py` | about 3,450 | No |
| Backends | `backend_ui.py` (selection mixin), `colibri_backend.py`, `colibri_launcher.py`, `colibri_panel.py` | about 1,500 | `colibri_backend.py` and `colibri_launcher.py` only |
| Developer agent | `coding_agent.py`, `developer_tools.py`, `hybrid_engine.py`, `hybrid_ui.py`, `feedback.py` | about 1,700 | All except `hybrid_ui.py` |
| Persistence | `chat_store.py`, `background_saves.py`, `shared_memory.py` | about 530 | Yes |
| Security | `network_gate.py`, `child_gate/sitecustomize.py`, `gate_ui.py`, `firewall_lock.py` | about 1,750 | All except `gate_ui.py` |
| Models and hardware | `model_library.py`, `model_downloads.py`, `model_advisor.py`, `hardware_panel.py` | about 1,350 | `model_library.py` and `model_advisor.py` only |
| Web tooling | `html_preview.py`, `deploy_dialog.py`, `vercel_api.py`, `vercel_deploy.py` | about 750 | All except `deploy_dialog.py` |
| Updates and runtime | `update_checker.py`, `gui_runtime.py`, `python-runtime.txt`, `install_runtime.ps1`, three launchers | about 600 | `gui_runtime.py` imports Tk lazily |
| Tests | 37 `test_*.py` files | about 8,000 | n/a |

**Entry points.** `AIassist.py` (`__main__`, line 727) installs the internet gate, creates the Tk root through `gui_runtime.create_root`, and starts `AIAssistantGUI`. The launchers `Start AI Assist.cmd`, `.command` and `.sh` probe interpreters with `gui_runtime.py --probe`, then run `AIassist.py`. `profile_workflows.py` is a profiling script.

**Dependencies.** Standard library only. No `requirements.txt` or `pyproject.toml`. The effective minimum Python is **3.9** (`str.removeprefix` in `firewall_lock.py:284` and `model_library.py:171`, `Path.is_relative_to` in `vercel_api.py:141`), but no document states it. The child gate requires 3.8 (`child_gate/sitecustomize.py:10`).

## 2. Architecture as built

### 2.1 Composition

The application is one object. `AIAssistantGUI(BackendMixin, WorkspaceMixin, OllamaAssistantBase)` (`AIassist.py:723`), and `WorkspaceMixin(HybridMixin, FeedbackMixin, DeveloperPanelMixin)` (`workspace_ui.py:90`). Seven classes cooperate through `super()` and share one `self`. Across `AIassist.py`, `workspace_ui.py`, `backend_ui.py`, `hybrid_ui.py`, `feedback_ui.py` and `developer_panel.py` there are **183 distinct `self.<name> =` assignments** (Source, counted with grep). Business state (conversation, agent history, backend, model contexts, pending saves) sits next to widget handles on that object.

### 2.2 Threading and events

- The Tk thread polls every 100 ms while busy and every 250 ms when idle (`workspace_ui.py:31-38`, `process_responses` at 1536).
- Workers communicate through queues: `response_queue`, `model_queue`, `lifecycle_queue`, `workspace_events`, `hybrid_events`, `BackgroundSaves.results`, `StatusBar.results`, and the queues of `GateUI`, `ConnectionLog` and `firewall_lock.Lock`.
- Chat streaming: `fetch_response` (`AIassist.py:690`) runs `request_ollama` under `run_stoppable` (`AIassist.py:212`). Chunks are batched every 50 ms into `workspace_events` (`stream_callbacks`, line 663). Stop sets an `Event`; the stream callback raises `StreamStopped` at the next chunk, which closes the connection.
- Agent approvals: the worker queues a `decision` dict with a `threading.Event` and waits on it in 100 ms slices, checking cancel (`workspace_ui.py:1306-1316`, duplicated in `backend_ui.py:314-323`).
- Internet approvals: `network_gate._ask` calls the registered approver from whatever thread made the request. `GateUI.approve` queues it for the Tk thread, or runs a nested `wait_window` loop if the request came from the Tk thread itself (`gate_ui.py:163-181`).

### 2.3 Persistence

| Data | File (under the data directory) | Writer |
| --- | --- | --- |
| Chats | `<uuid>.json`, one per chat, atomic replace with fsync | `chat_store.py:307` |
| Shared memory | `shared.memory` | `shared_memory.py:55` |
| Connection log | `network.log` and `network.log.1` (1 MB rotation) | `network_gate.py:773` |
| Settings | `backend.settings`, `colibri.settings`, `agent.settings`, `appearance.json`, the hybrid config, `firewall-lift.json` | six separate ad-hoc writers |

The data directory is `%LOCALAPPDATA%\AI Assist\chats` on Windows, otherwise `~/.aiassist/chats`, and `AI_ASSIST_DATA_DIR` overrides it. Chat records have no schema version field. Validation is strict and rejects unknown activity keys (`chat_store.py:124`).

### 2.4 Backends

Ollama is reached through module functions in `AIassist.py` (lines 24-205) and through `coding_agent._chat`. Colibri is reached through `colibri_backend.ColibriClient`, an OpenAI-compatible client. **The choice between them is made inside the Tk mixin**: `BackendMixin` overrides `fetch_response`, `agent_worker` and `model_worker` and branches on `self.backend` (`backend_ui.py:229, 288, 306`). No interface separates "an inference provider" from "the window".

## 3. Baseline checks (Test)

| Check | Result |
| --- | --- |
| `python -m unittest discover -v` | **397 tests, OK, 4 skipped**, 87.8 s. The skips are symlink tests that need a Windows privilege (`WinError 1314`). |
| `python -m compileall -q .` | OK |
| `python profile_workflows.py --chats 200 --messages 200` | 9.55 s total; `ChatStore.list_chats` took **2.24 s** for 200 chats of 200 messages each; `save` costs about 12.5 ms per chat (reads and validates the old file, writes, fsyncs) |

There is no CI configuration, no coverage measurement, and no linting configuration in the repository (Source: no `.github/`, `pyproject.toml`, `setup.cfg` or `tox.ini`).

## 4. Findings

### Security and privacy

**S1. Agent shell commands run unsandboxed with the user's full permissions.** Severity: High (inherent; mitigated). Source: `coding_agent.py:330-409`. Each command runs through `powershell.exe -Command` or `/bin/sh -c`, with the project folder as its working directory, which is not a security boundary (the docstring says so at line 3). The mitigations are real: every command needs two human confirmations, the second unlocks after three seconds (`workspace_ui.py:1408-1431`); commands are logged and announced (`network_gate.note_outside`); Python started by a command loads the child gate. The remaining risk is prompt injection from project files or command output persuading a user to approve a harmful command. **Recommendation:** add OS-level sandboxing as a separate, fail-closed execution backend (section 5 of `docs/threat-model.md`).

**S2. The gate audits connect, sendto and DNS, but not bind or accept.** Severity: Medium (latent). Source: `network_gate.py:58-61`, the `_EVENTS` set has no `socket.bind`. Any code in the app process can open a listener on all interfaces without the gate noticing. Today only loopback listeners exist (`html_preview.py:80`, `network_gate.py:204`). A future local API, or LAN compute sharing, could expose data or tools to the network by a one-character mistake. **Recommendation:** refuse non-loopback listeners in the app process, fail closed. *(Done in this branch, see `docs/target-architecture.md`.)*

**S3. Native code and some Python start modes bypass the gate.** Severity: Medium (documented limitation). Source: README "Limits", `network_gate.py:70`, `child_gate/sitecustomize.py`. Native extensions, Python started with `-I`, `-E` or `-S`, and every non-Python program are outside it. The firewall lock covers Ollama's and colibri's native programs on Windows only (`firewall_lock.py:138-141`). macOS and Linux have no equivalent of the firewall lock.

**S4. Saved data may be readable by other local users on macOS and Linux.** Severity: Medium on multi-user POSIX systems; none on Windows, where `%LOCALAPPDATA%` is private to the profile. Source: `chat_store.py:84` creates the directory with the default mode (0777 masked by umask, typically 0755). `network_gate.py:779` opens `network.log` with `open(path, "a")`, typically 0644. The settings writers in `backend_ui.py:75`, `appearance.py:228` and `workspace_ui.py:1636` use the same defaults. Chat files themselves are safe: `tempfile.NamedTemporaryFile` creates them 0600. Inferred: under a 022 umask the directory listing and `network.log` (destinations and purposes of internet requests) are world-readable. **Recommendation:** create the default data directory 0700 and the log 0600. *(Directory and log done in this branch; settings files are in the backlog.)*

**S5. A second app instance can silently overwrite chats.** Severity: Medium (data loss). Source: there is no lock anywhere. `workspace_ui.persist_current` (line 650) saves its in-memory `current_chat` copy. Two windows on the same data directory (or a future second UI) editing one chat means last writer wins. **Recommendation:** a per-data-directory single-instance lock taken by every entry point. *(Done in this branch.)*

**S6. HTML previews run generated scripts with `unsafe-inline` and `unsafe-eval` on a loopback origin.** Severity: Low (intended). Source: `html_preview.py:17-19`. The CSP blocks loading from the internet and the server checks `Host` and `Origin` (`html_preview.py:41-44`), so this is defense in depth. Any new local web server must therefore assume an **untrusted, same-site page on 127.0.0.1**. SameSite cookies would be sent to it, because ports do not separate sites. **Recommendation:** never use cookie authentication for a loopback API; require a bearer token and an exact `Origin`.

**S7. The approval dialog's "Asked by" depends on modules sitting at the repository root.** Severity: Low today, high for refactoring. Source: `network_gate.py:646`, `os.path.dirname(path) == _APP_DIRECTORY`. Code moved into a package would be reported as "code outside AI Assist's own files", which hides the true source from the person approving. **Recommendation:** recognise the app's own packages explicitly, not by walking up into `runtime/` (the private Python lives inside the app folder). *(Done in this branch.)*

**S8. Secrets handling is sound.** Severity: none (positive). Source: `vercel_api.py:73, 222` redacts the token; `deploy_dialog.py:120` redacts in errors; `colibri_backend.py:45-52` never shows server bodies; Colibri and Vercel keys are session-only. Agent file tools exclude secret names and private-key contents (`developer_tools.py:73-81, 127`).

### Correctness, races and technical debt

**C1. Business logic lives in Tk event handlers.** Severity: High (maintainability; blocks any second UI). Source: `workspace_ui.send_prompt` (lines 1153-1250) does memory-command parsing, assembles the agent system prompt, injects shared memory and chooses the worker. `BackendMixin` routes inference (2.4). `StatusBar.snapshot` works out "connected" by reading a label's text (`status_bar.py:128`). None of this can be reused without a Tk root.

**C2. Duplicated logic with inconsistent rules.** Severity: Medium. Source:
- Data directory resolution appears twice: `chat_store.py:72-80` and `network_gate.py:299-309`. The gate copy is intentionally standalone, because the child gate loads it by path.
- Title derivation appears twice: `chat_store.py:323-328` and `workspace_ui.py:668-672`.
- "Is this host local?" exists in four places with different answers: `status_bar.is_local`, `colibri_launcher.is_local_url` and `AIassist.ensure_ollama` accept only `localhost`, `127.0.0.1` and `::1`, while the authoritative `network_gate.classify` treats all of 127.0.0.0/8 as local. So `127.0.0.2` is "remote server" in the status bar but local to the gate.
- Two process-tree killers: `coding_agent._terminate_process_tree` and `colibri_launcher.terminate_tree`.
- The agent approval bridge is written out twice (2.2).
- The agent step limit is duplicated: `status_bar.AGENT_MAX_STEPS = 12` and `coding_agent.run_agent(max_steps=12)`.

**C3. Dead production code.** Severity: Low. Source: `vercel_deploy.deploy_project`, `vercel_command` and `run_cli` (the Vercel CLI flow) are referenced only by `test_vercel_deploy.py`; the app deploys through `vercel_api.deploy_project_api` (`deploy_dialog.py:14`). `OllamaAssistantBase.resize_chat` and its `COLORS` palette are always overridden by `WorkspaceMixin`.

**C4. Worker threads write attributes that the Tk thread reads.** Severity: Low. Source: `backend_ui.py:296, 303, 328`, which set `self.colibri_report` and `self.health_poll_at` from worker threads; `workspace_ui.py:1345` does the same. CPython attribute assignment is atomic, so nothing is corrupted, but ordering is undefined. Inferred: the status bar can briefly show a stale Colibri state.

**C5. A stopped stream's reader thread can linger.** Severity: Low. Source: `run_stoppable` returns at once on Stop, but the worker stops only at the next chunk (`AIassist.py:212-242`). For Colibri the socket timeout is 1,800 s (`colibri_backend.py:196`). A stalled model can keep a daemon thread and its connection open for up to 30 minutes.

**C6. Settings writes are not uniformly atomic.** Severity: Low. Source: `backend_ui.py:74-76`, `workspace_ui.py:1635-1637` and `appearance.py:228` write a fixed `<name>.tmp` without fsync, then replace. Two writers collide on the same temporary name.

**C7. Chat records have no schema version.** Severity: Medium for future migrations. Source: `chat_store.py:104-178`. The validator rejects unknown activity keys, so a newer app's records can make an older copy skip the chat entirely ("Skipped <file>"). Upgrades need an explicit version and a tolerant read path.

**C8. Windows agent commands run in PowerShell 5.1.** Severity: Medium (usability). Source: `coding_agent.py:345`. Models commonly emit POSIX shell syntax such as `&&`, which Windows PowerShell 5.1 rejects. Inferred: a share of first-attempt command failures on Windows comes from this. The system prompt does not name the shell.

### Performance

**P1. Opening or creating a chat re-reads every saved chat on the UI thread.** Severity: Medium, growing with history. Test: `list_chats` took 2.24 s for 200 × 200-message chats. Source: `refresh_history(reload=True)` is the default and is called from `new_chat` (`workspace_ui.py:647`) and `open_chat` (line 866), both on the Tk thread. **Recommendation:** a metadata index (title, mode, model, `updated_at`, byte size), rebuilt off-thread; search over the full text in a worker.

**P2. Each save re-reads and re-validates the existing file.** Severity: Low. Test: about 12.5 ms per save at 200 messages (`chat_store.py:315-322`). This is acceptable now, but it grows linearly with chat size.

**P3. Messages are rendered as Tk widget trees with no virtualization.** Severity: Low or Medium. Inferred from `message_widgets.render_message` being called for every message in `open_chat` (`workspace_ui.py:831-832`). Long chats open slowly; this was not measured, because it needs an interactive display.

### Platform assumptions

**X1.** There are 120 literal `"Segoe UI"` font references (Windows-only); Tk substitutes other fonts on macOS and Linux. Source: grep.
**X2.** The firewall lock, toast notifications through PowerShell, `taskkill` and `CREATE_NO_WINDOW` are Windows paths with POSIX fallbacks, but **there is no network lock on macOS or Linux** (S3).
**X3.** The data directory is not XDG-compliant on Linux (`~/.aiassist`). It should be kept for compatibility; any move must migrate the user's data.
**X4.** The Tk integration tests need a display. A Linux CI would need Xvfb (Inferred; Tk raises `TclError` without `DISPLAY`).

### Release and update process

**R1. There is no release process.** No tags, no CI, no changelog, no signed artifacts. Users download a ZIP of the default branch. Source: `git tag` is empty; `update_checker.py:46-74` compares local files with the default branch's latest commit.
**R2. The update checker only knows root-level `.py` and `.cmd` files plus `child_gate/sitecustomize.py`** (`update_checker.py:56-61`). Any new package or static asset would be invisible to it. *(Extended in this branch.)*
**R3. The private Python bootstrap is good supply-chain hygiene.** Severity: none (positive). It is pinned, checks SHA-256 before use, needs two confirmations, and allows HTTPS only (`python-runtime.txt`, `install_runtime.ps1:58`, `Start AI Assist.sh:79`).

## 5. What is solid and should be preserved

- The internet gate: fail-closed defaults, per-task grants, a 30-second denial memory, child-process approvals over an authenticated loopback socket, and cloud-model detection through `/api/show` (`network_gate.py`).
- Bounded inputs everywhere: response sizes, line lengths, tool-call counts, file limits, scan budgets.
- Atomic chat writes with strict validation, and the ordered background writer.
- The agent core (`coding_agent.run_agent`) has **no UI dependency**. It takes callbacks for events and approvals, which is exactly the seam a second interface needs.
- The test suite is broad and realistic: real subprocesses, real loopback servers, timeout cleanup.

## 6. Recommendations (summary)

1. Extract a UI-free application core (providers, conversation service, settings, privacy status, single-instance lock), keeping every existing patch point and behavior. Do it incrementally, behind compatibility facades.
2. Add a loopback-only local API with explicit authentication, exact `Host` and `Origin` checks, no cookies, and no shell or file-write endpoints. Build the new UI on it, and keep Tk as the default until parity.
3. Harden the gate (S2, S7) and file permissions (S4), and add the instance lock (S5).
4. Add CI (Windows, macOS, Linux with Xvfb), a schema version for chats, and a metadata index.

The decision record is `docs/architecture-decision.md`, the boundaries are in `docs/target-architecture.md`, the threats in `docs/threat-model.md`, platform support in `docs/platform-support.md`, and priorities in `docs/modernization-backlog.md`.
