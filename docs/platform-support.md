# Platform support and packaging plan

## Support matrix (honest, as of 2026-11-19)

Current desktop evidence is separated from the former upstream Tk/browser milestone.

| Configuration | Desktop pywebview UI | Firewall lock | Evidence / limit |
| --- | --- | --- | --- |
| Windows 11 x64 | Unit suite and isolated native WebView2 smoke run during D8 | Read path covered; UAC/admin mutations not exercised | [D8 report](d8-integration.md): 573 tests, 11 skips, plus native smoke. Temporary chats/projects only. |
| Windows 11 ARM64 | Untested | Same Windows implementation | Private-runtime manifest and installer paths exist; no native run. |
| macOS Apple silicon / Intel | Untested on hardware; WKWebView intended | Unavailable | macOS launcher and mocked Apple-silicon logic tests only. |
| Linux x86_64 / ARM64 | Untested on hardware; Qt backend intended | Unavailable | Linux launcher checksum/bootstrap checks are static. Native system libraries and a graphical session are required. |
| Other Python versions | No additional compatibility claim | — | Use the pinned private runtime or verify the full dependency set. |
| Android / iOS | Unsupported | — | No remote chat API. LAN pairing is enrollment only. |

Windows skips POSIX permission checks, symlink checks where privileges are missing, and optional TLS identity tests when `cryptography` is absent. These are limitations, not passing evidence. The earlier upstream result (468 tests and a browser e2e run) describes the retired Tk/browser-preview interface.

## 1. Distribution today

Source ZIP or git clone plus launchers for Windows, macOS and Linux. The launchers offer a pinned, SHA-256-verified private Python runtime after two explicit confirmations, then install `requirements.txt`. Linux additionally installs `pywebview[qt]==6.2.1`; Qt's platform/system libraries remain an OS prerequisite. Creating a LAN host identity needs the optional `cryptography` package; the app does not download it automatically.

`python AIassist.py` opens the native web UI; `--web` remains an alias. There is no Tk fallback and no separate `web_workspace.py` browser server.

## 2. Packaging roadmap

| Step | What | Why this order |
| --- | --- | --- |
| P1 | **CI matrix** (GitHub Actions): Windows x64, macOS arm64 + x64, Ubuntu x64 (+ arm64 runner); Python 3.9 and the pinned 3.13 runtime; native UI checks in isolated graphical sessions; Node frontend tests. | Turns "untested" rows into evidence before shipping installers. |
| P1 | **Release process:** semantic version tags, a changelog, release notes, and a **signed release manifest** (file list + SHA-256, signed with a minisign/Ed25519 key kept offline). The update checker compares against the latest *tagged release* manifest instead of `main`, and verifies the signature. | Today the checker reports any development copy as "different"; tags give stable/beta channels. |
| P2 | **Self-contained bundles** built in CI from the same pinned python-build-standalone runtime: Windows `.zip` + per-user installer (no admin), macOS `.app` (notarized, Developer ID signed), Linux AppImage (and Flatpak later). Include the pinned pywebview/PDF dependencies and each platform's native web runtime. | Removes the "find a Python" step without new dependencies. |
| P2 | **Code signing:** Authenticode (Windows), Developer ID + notarization (macOS), signed AppImage/checksums (Linux). | Required for SmartScreen/Gatekeeper and for update verification. |
| Deferred upstream proposal | **Optional Tauri 2 shell** (not the current architecture): system WebView, Python core as a sidecar bound to loopback, launch token passed in-process (never on a command line), strict capability allowlist, no remote URLs. | Native window + OS integration with a small bundle; only after the web workspace reaches parity. |

Updates never install themselves: the person downloads, verifies (the app shows the verified signature) and starts the new copy. That keeps "nothing goes online without two confirmations" true for updates too.

## 3. User data across upgrades

- The data folder never moves silently (`%LOCALAPPDATA%\AI Assist\chats`, `~/.aiassist/chats`, `AI_ASSIST_DATA_DIR`). A future XDG location on Linux would be opt-in with a migration that copies, verifies, then switches.
- Before any format change: add a `schema` field read tolerantly; write a full backup zip (`ChatStore.export_all`) before rewriting files; keep older readers able to skip, not destroy, newer records (they already never overwrite damaged files).
- Settings files keep their names; unknown keys are preserved by `read_json` users.

## 4. Mobile feasibility plan (separate track)

What will **not** happen: a desktop Python sidecar on iOS/Android, shell execution, the developer agent's file access, or the firewall lock. Mobile OSes forbid or sandbox these, and they are exactly the dangerous tools.

**Track A: companion client (phone talks to the desktop).** A future separate API design; no remote chat API exists in the current desktop app. Prerequisites in `docs/threat-model.md` §7 (pairing, TLS-PSK, scopes, explicit LAN listener). Scope: chat and read-only views. Delivery: the existing responsive page in the phone's browser first; a thin native shell later if needed.

**Track B: on-device inference (phone works alone, offline).**

| Runtime | Android | iOS | Notes |
| --- | --- | --- | --- |
| llama.cpp (GGUF) | yes | yes (Metal) | Broadest model support; same model files as Ollama |
| LiteRT-LM (Google) | yes, primary | yes (Swift API) | Production focus on Gemma-class models ([overview](https://ai.google.dev/edge/litert-lm/overview)) |
| MLC LLM | yes (Vulkan/OpenCL) | yes (Metal) | Compile step per model |
| Apple Foundation Models | — | iOS 18+ on recent devices | System model (~3B); no model choice |

Realistic expectations: 1–4B-parameter models at 4-bit quantization, short contexts, battery and thermal limits. Recommended spike: llama.cpp in a minimal native chat app reusing the same chat JSON format, so conversations can be exported between devices by file (no network).

## 5. Platform notes for contributors

- Keep core modules independent of UI toolkits. Load pywebview only inside the desktop entry point.
- Use the frontend's system font stack and shared Night/Day tokens.
- The app process may bind loopback only. Only explicitly enabled pairing uses a separate gated child; it exposes no desktop API.
