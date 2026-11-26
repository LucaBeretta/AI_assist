# CLAUDE.md

Context for Claude sessions on this repository. The repository guidelines (structure, commands, tests, commit style) are in AGENTS.md and apply in full:

@AGENTS.md

This repository is public: keep secrets and personal details out of this file.

## Working with the owner

- The owner (Luca Beretta) is not a developer. Explain in plain language with short steps, avoid jargon, and say what a change means for people using the app. They write in English or Italian; answer in the language of their message. They often follow along from a phone.
- The way of working they asked for: they describe an idea ("envisioning"). Claude reads the code and explains in plain words whether it fits, what it would change (privacy, internet popup, other features), roughly how big it is, and whether it collides with Luigi's work. The owner decides. Claude then builds it on its own branch, checks it on Windows, macOS and Linux CI, and opens a pull request the owner can merge from a phone. Split big ideas into small pull requests. Never push to `main`.
- Luigi writes most of the code and pushes straight to `main`, several times a day. Before starting, read `git log origin/main`. Before reporting, merge the latest `main` into open pull request branches and re-run CI. Don't restructure files he changes often (`webui/app.py`, `webui/static/app.js`) or move folders unless he agrees.

## Git and GitHub in cloud sessions

- Open and edit pull requests with the REST API, because `gh pr create` (GraphQL) is blocked: `gh api repos/LucaBeretta/AI_assist/pulls -f title=... -f head=BRANCH -f base=main -F body=@file.md`.
- The clone is shallow and fetches only `main`. After pushing a new branch, run `git config --add remote.origin.fetch +refs/heads/BRANCH:refs/remotes/origin/BRANCH` and `git fetch origin`, or status checks report the branch as unpushed.
- CI runs on every push and pull request:
  - `.github/workflows/tests.yml`: the full suite on Windows, macOS and Linux with Python 3.13 and Node 24.
  - `.github/workflows/macos.yml` (Luigi's): the macOS headless suite, plus an optional native WKWebView smoke test.

  A pull request's `pull_request` run tests the branch merged into the current `main`, so new code on `main` can fail it.
- Cloud sessions cannot delete branches (`git push --delete` and the refs API are refused), so ask the owner to delete them on GitHub's Branches page.
- Each pull request's CI checks it alone. When several are open, check that they also merge together (`git merge-tree --write-tree A B`) and that the ruff check still passes, then suggest an order to the owner.

## Git and GitHub on the owner's Windows PC

- `gh` is not installed. `git push` works. Read pull requests and check runs through the public REST API with `curl`. The owner opens a new pull request from a link like `https://github.com/LucaBeretta/AI_assist/compare/main...BRANCH?expand=1`.
- Keep the owner's checkout on `main` and work on pull request branches in separate worktrees.

## State on 2026-11-24

- Merged on 21 November:
  - LucaBeretta/AI_assist#11 (Luigi): chat streaming moved into `app_core/chat_service.py`; settings reorganised.
  - LucaBeretta/AI_assist#12: CI on all three systems.
  - LucaBeretta/AI_assist#13: brief Windows retries for chat saves and the checkpoint folder rename (`app_core.paths.replace_retrying`).
  - LucaBeretta/AI_assist#14: a ruff check (`ruff.toml`, rules F and E9, ruff 0.17.6 in CI).
  - LucaBeretta/AI_assist#15: this file.
  - LucaBeretta/AI_assist#16, #17 and #18: the modernization backlog marked historical, the PDF worker seeing user-site packages, and a list of served Colibri models for the reviewer.
- Merged from 22 to 24 November:
  - LucaBeretta/AI_assist#20 and #29: Hugging Face suggestions (see below). #19's wording change went in with #20, so #19 was closed.
  - LucaBeretta/AI_assist#21: an "orbital" look for the separate Mission Control window, made with Zencoder on the owner's PC. It includes explicitly drawn usage meters, because the native ones lost their fill in the night theme.
  - LucaBeretta/AI_assist#22: Developer uses an installed Blender in the background (`developer_tools.find_blender`, a short hint in `coding_agent._system_prompt`).
  - LucaBeretta/AI_assist#27 (owner): **Generate** — a local WebGL quick animation from a photo, and AI video through a local ComfyUI (Wan2.2 TI2V 5B) on `127.0.0.1:8188`.
  - A `stickers/` community landing page with a QR code that points to `https://stickers-bay.vercel.app/`. Its join link in `stickers/join-config.js` is still empty.
  - LucaBeretta/AI_assist#31: Developer works on the scene open in Blender through an add-on (`blender_addon/ai_assist_bridge.py`, `blender_bridge.py`). It is off until Connect is clicked, listens on 127.0.0.1 only, and every change is approved.
  - LucaBeretta/AI_assist#28: pairing and approval prompts in Mission Control, NVIDIA GPU temperature and power draw, and the tap-to-join NFC sticker artwork. A live check with two computers is still outstanding.
  - LucaBeretta/AI_assist#33: chat reads also retry briefly on Windows (`app_core.paths.read_bytes_retrying`).
  - LucaBeretta/AI_assist#34: `character_builder.py` and `blender_character.py` build the person's own 3D character with the Blender on this computer. There is no screen for it yet.
  - LucaBeretta/AI_assist#36 and #37: model download sizes in decimal GB, as ollama.com shows them (RAM and VRAM stay binary); sharper Quick animation photos, with a Node test of `photo-shader.js`.
  - LucaBeretta/AI_assist#24: a non-modal "Was this useful?" prompt after a saved answer or a finished Developer task. Answers go to a local journal, incognito chats are left out, and shared memory changes only with an explicit opt-in.
  - LucaBeretta/AI_assist#26, #32 and #35: the first part of the network's **use** layer (see below).
- Waiting on the owner:
  - Delete the leftover branch `live-check-huggingface` on GitHub; cloud sessions cannot delete branches.
- Five old branches that were never merged are archived as tags under `archive/`: `modernization/web-workstation`, `integration/compute-dashboard-web`, `code-quality-pass`, `feature/continuous-user-feedback` (closed: no feedback prompt after replies and tasks; #24 later restored the idea on current `main`) and `mac-mission-control` (split into #11 and `mac-mission-control-only`). Each tag's message says why. Restore one with `git push origin "archive/NAME^{commit}:refs/heads/NAME"`.
- `docs/modernization-backlog.md` is marked historical. The other milestone documents (`architecture-audit.md`, `architecture-decision.md`) also predate the removal of Tk.

## Hugging Face models

- The model suggestion step reads ollama.com and huggingface.co under one approval: `network_gate.task(..., hosts=(...))` declares both sites, and the first one reached asks once for both. Any other site still asks on its own.
- `model_library.fetch_hugging_face_models` reads the most downloaded GGUF chat models. `model_advisor.recommend_hugging_face` picks the first of Q4_K_M, Q5_K_M, Q4_K_S, Q6_K, Q8_0, IQ4_XS, Q3_K_M that fits, and shows up to three as `hf.co/<repo>:<quant>`.
- A file name's quantization is not always a pullable tag. `model_library.hugging_face_tag_available` asks the registry first (`/v2/<repo>/manifests/<quant>`, as Ollama does), and refused tags are never suggested.
- Cloud sessions cannot reach huggingface.co. To check against the live site, push a temporary branch with a workflow that runs on GitHub's runners (they have internet and can install Ollama), then ask the owner to delete the branch. That is how the registry check and a real `ollama pull` were verified.

## Product direction: the local network ("intranet")

There are three layers:

1. **See**: built. The Mission Control network scan in `lan_devices.py` runs only when the person clicks Scan.
2. **Monitor**: built. Paired devices can share live CPU, RAM and GPU load over TLS after PIN pairing (`peer_pairing.py`, `webui/sharing.py`). `share_usage.py` does the same for computers without a screen.
3. **Use**: started, with no screen yet. A paired computer runs AI for another one, for example a Windows PC with a powerful graphics card serving a Mac.
   - `PairingHost(ollama="http://127.0.0.1:11434")` lets a paired device list models and chat with this computer's Ollama through the pinned-TLS pairing channel (#32). `open_shared_ai` is the client side.
   - Only `/api/tags`, `/api/version`, `/api/show` and `/api/chat` are forwarded. Chat requests are data only (`peer_inference_contract.validate_request`, #26), and this computer writes Ollama's request itself. Cloud models are refused.
   - Pairing alone is not enough: each device needs its own permission (`TrustStore.allow_ai`), which unpairing removes.
   - `ai_network.py` (#35) maps this computer and the paired ones, with each one's models, load and status in plain words. It picks a computer with `compute_routing.choose_route`: this computer by default, never a silent fallback.
   - Nothing turns sharing on yet. Next come the switch on the sharing computer and the "Compute location" control (This device / Auto / Choose device) planned in `docs/compute-routing.md`.
   - An early Mac -> Windows prototype, built on the removed Tk interface, is in the `archive/integration/compute-dashboard-web` tag (`lan_inference.py`, `lan_client.py`, `docs/mac-windows-lan-inference.md`): a reference, not code to merge.

Pointing AI Assist at another computer's Ollama URL also works, but that connection is unencrypted and unauthenticated. The paired route replaces it once it has a screen.

The design is local-network only: no cloud relay and no router port-forwarding. Reaching a home machine from outside would be a separate decision for the owner.

## Known issues and backlog

- **Windows Smart App Control** blocks the launcher when AI Assist comes from a ZIP download, because the files carry the "from the internet" mark. Workarounds:
  - tick Unblock in the ZIP's Properties before extracting;
  - download with `git clone` instead;
  - run `python AIassist.py`.

  Never advise turning Smart App Control off: it cannot be turned back on without reinstalling Windows. The real fix is signed installers.
- **Windows briefly refuses renames and reads.** It can refuse to replace a file, rename a folder, or open a file that is being replaced, for a moment while antivirus, search indexing or another thread holds it. Use `app_core.paths.replace_retrying` and `read_bytes_retrying` for app data rather than `os.replace` and `open`. GitHub's Windows runners rarely show this; a real PC does.
- **Mission Control** showed as a panel overlapping the chat message box when run on Linux with Qt at 1280x840, although its commit says it opens in its own window. This is not yet checked on macOS. Check before changing it, since Luigi owns this code.
- Bigger improvements discussed, each needing a decision from the owner or agreement with Luigi:
  - signed installers (a notarized macOS `.dmg` needs an Apple developer account; Windows needs a code-signing certificate);
  - a `src/` and `tests/` layout;
  - splitting `webui/app.py` and `app.js`;
  - branch protection that requires the **Tests** and **macOS desktop checks** workflows to pass before merging into `main`.
- A plain-language explainer page made for the owner: https://claude.ai/artifact/1UxAFvr1TBtcK1K6YjB6Q6. Last updated early on 24 November (Hugging Face, Blender, Generate, Mission Control, and the network's see / monitor / use steps). It does not yet cover #24, #28 or #31 to #37.

## Running the app in a cloud Linux container

The steps below worked on 2026-11-21. There is no model here: Ollama shows as offline, but the UI, the internet popup and Mission Control's CPU and RAM readings work.

```bash
apt-get install -y libegl1 libgl1 libopengl0 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libfontconfig1 libdbus-1-3 fonts-dejavu-core xdotool x11-utils
python -m pip install -r requirements.txt 'pywebview[qt]==6.2.1'
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
DISPLAY=:99 QTWEBENGINE_DISABLE_SANDBOX=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu PYWEBVIEW_GUI=qt AI_ASSIST_DATA_DIR="$(mktemp -d)" python AIassist.py &
DISPLAY=:99 import -window root screenshot.png       # look at it
DISPLAY=:99 xdotool mousemove X Y click 1            # drive it
```

On first start, the welcome page asks to reach ollama.com. Click Block, then "Skip for now".
