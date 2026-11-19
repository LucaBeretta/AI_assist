# Platform support and packaging plan

## Support matrix (honest, as of 2026-11-19)

"Tested" means the full unit suite (and, for the web workspace, the browser end-to-end check) actually ran on that configuration in this milestone. Everything else is labelled by what is known.

| Configuration | Desktop app (Tk) | Web workspace (preview) | Firewall lock | Evidence |
| --- | --- | --- | --- | --- |
| Windows 11 x64, Python 3.14.10 and 3.9.25 | **Tested** | **Tested** (Edge 157 headless, real Ollama 0.40.16) | Read path tested; admin (UAC) path untested | 585 unit tests (7 skipped) on both Pythons, also with a short 8.3 temp folder; `tools/web_e2e.py` 43/43 |
| GitHub `windows-latest` x64, Python 3.13 and 3.14 | **Tested in CI** (full suite) | Unit and API tests only (no browser run) | Mocked only | `.github/workflows/tests.yml`, run 44620618926 (`6591f80`), all jobs green |
| Windows 11 ARM64 | Untested; launcher and private-runtime entry exist | Untested; no platform-specific code | Same code as x64 | `install_runtime.ps1`, `python-runtime.txt` |
| macOS Apple silicon / Intel | Partially tested earlier (Apple-silicon memory logic has unit tests with mocks); **not run**: the CI job exists (`tests-macos.yml`, started by hand) but on 2026-11-19 GitHub gave this project no Mac machine for 14+ hours | Untested; standard library only | Not available (Windows only) | `test_apple_silicon.py`, `Start AI Assist.command` |
| Linux x86_64: Ubuntu 26.04 (Python 3.13), Ubuntu 24.04 (Python 3.9) | **Tested in CI** (full suite, Tk under Xvfb) | Unit and API tests only | Not available | `.github/workflows/tests.yml`, run 44620618926; WSL Ubuntu (Python 3.14, no Tk): the 408 tests that load without Tk |
| Linux ARM64 | Untested; launcher exists | Untested | Not available | `Start AI Assist.sh`, `test_launchers.py` (static checks) |
| Python 3.10–3.12 | Expected to work (3.9, 3.13 and 3.14 are tested; minimum 3.9: `str.removeprefix`, `Path.is_relative_to`); not run | same | — | audit §1 |
| Android, iOS | **Unsupported** | Not offered (loopback only; LAN pairing not built) | — | section 4 |

CI: every upload and pull request runs `.github/workflows/tests.yml` (Windows and Linux, Python 3.9/3.13/3.14, plus the page's Node tests). A failed test is shown as a public note, because GitHub shows full logs only to signed-in users.

Known gaps: macOS is not run (see its row); the browser end-to-end test needs a real model, so it runs only by hand; ARM64 is not run anywhere.

## 1. Distribution today (unchanged)

Source ZIP or git clone + a launcher per OS. When no Python with Tk works, the launcher offers a **pinned, SHA-256-verified** python-build-standalone runtime (two confirmations, HTTPS only, stored in `runtime/`). The web workspace needs Python only (no Tk): `python web_workspace.py`.

## 2. Packaging roadmap

| Step | What | Why this order |
| --- | --- | --- |
| P1 | **CI matrix** (GitHub Actions). Done: Windows x64 and Ubuntu x64, Python 3.9, 3.13 and 3.14, Tk under Xvfb, `node --test`. Left: macOS (job ready, no Mac machine given yet) and ARM64 runners. | Turns "untested" rows into evidence before shipping installers. |
| P1 | **Release process:** semantic version tags, a changelog, release notes, and a **signed release manifest** (file list + SHA-256, signed with a minisign/Ed25519 key kept offline). The update checker compares against the latest *tagged release* manifest instead of `main`, and verifies the signature. | Today the checker reports any development copy as "different"; tags give stable/beta channels. |
| P2 | **Self-contained bundles** built in CI from the same pinned python-build-standalone runtime: Windows `.zip` + per-user installer (no admin), macOS `.app` (notarized, Developer ID signed), Linux AppImage (and Flatpak later). No pip packages, so bundling is copy + launcher. | Removes the "find a Python" step without new dependencies. |
| P2 | **Code signing:** Authenticode (Windows), Developer ID + notarization (macOS), signed AppImage/checksums (Linux). | Required for SmartScreen/Gatekeeper and for update verification. |
| P3 | **Optional Tauri 2 shell** for the web workspace: system WebView, Python core as a sidecar bound to loopback, launch token passed in-process (never on a command line), strict capability allowlist, no remote URLs. | Native window + OS integration with a small bundle; only after the web workspace reaches parity. |

Updates never install themselves: the person downloads, verifies (the app shows the verified signature) and starts the new copy. That keeps "nothing goes online without two confirmations" true for updates too.

## 3. User data across upgrades

- The data folder never moves silently (`%LOCALAPPDATA%\AI Assist\chats`, `~/.aiassist/chats`, `AI_ASSIST_DATA_DIR`). A future XDG location on Linux would be opt-in with a migration that copies, verifies, then switches.
- Before any format change: add a `schema` field read tolerantly; write a full backup zip (`ChatStore.export_all`) before rewriting files; keep older readers able to skip, not destroy, newer records (they already never overwrite damaged files).
- Settings files keep their names; unknown keys are preserved by `read_json` users.

## 4. Mobile feasibility plan (separate track)

What will **not** happen: a desktop Python sidecar on iOS/Android, shell execution, the developer agent's file access, or the firewall lock. Mobile OSes forbid or sandbox these, and they are exactly the dangerous tools.

**Track A: companion client (phone talks to the desktop).** Reuses the web API and page. Prerequisites in `docs/threat-model.md` §7 (pairing, TLS-PSK, scopes, explicit LAN listener). Scope: chat and read-only views. Delivery: the existing responsive page in the phone's browser first; a thin native shell later if needed.

**Track B: on-device inference (phone works alone, offline).**

| Runtime | Android | iOS | Notes |
| --- | --- | --- | --- |
| llama.cpp (GGUF) | yes | yes (Metal) | Broadest model support; same model files as Ollama |
| LiteRT-LM (Google) | yes, primary | yes (Swift API) | Production focus on Gemma-class models ([overview](https://ai.google.dev/edge/litert-lm/overview)) |
| MLC LLM | yes (Vulkan/OpenCL) | yes (Metal) | Compile step per model |
| Apple Foundation Models | — | iOS 18+ on recent devices | System model (~3B); no model choice |

Realistic expectations: 1–4B-parameter models at 4-bit quantization, short contexts, battery and thermal limits. Recommended spike: llama.cpp in a minimal native chat app reusing the same chat JSON format, so conversations can be exported between devices by file (no network).

## 5. Platform notes for contributors

- Keep new code standard-library only; keep `app_core/` and `webui/` UI-free.
- Avoid platform-specific fonts in new UI (the Tk app hard-codes "Segoe UI" 120 times; the web page uses a system font stack).
- Anything that listens must bind loopback; the gate now enforces it in the app process.
