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

## State on 2026-11-21

- Merged: LucaBeretta/AI_assist#12, which added CI on all three systems and fixed the macOS and Windows temporary-path test failures (macOS `/var` -> `/private/var` link, Windows `RUNNER~1` short names).
- Open, green and mergeable:
  - LucaBeretta/AI_assist#13: Windows chat saves retry `os.replace` briefly when the file is in use (`chat_store._replace`).
  - LucaBeretta/AI_assist#14: a ruff check (`ruff.toml`, rules F and E9, ruff 0.17.6 in CI), with its findings cleaned up.

  Both also carry a fix for a test that fails intermittently on Windows (the fake `coli` lock in `test_colibri_launcher.py`).
- The repeated ollama.com approval popup on macOS was fixed on `main` by 378f1ca: one gated task now covers every catalog page.

## Product direction: the local network ("intranet")

There are three layers:

1. **See**: built. The Mission Control network scan in `lan_devices.py` runs only when the person clicks Scan.
2. **Monitor**: built. Paired devices can share live CPU, RAM and GPU load over TLS after PIN pairing (`peer_pairing.py`, `webui/sharing.py`). `share_usage.py` does the same for computers without a screen.
3. **Use**: not built. A paired computer runs AI for another one, for example a Windows PC with a powerful graphics card serving a Mac. It is planned as a "Compute location" control (This device / Auto / Choose device) in `docs/compute-routing.md`. The policy in `compute_routing.py` is written but nothing calls it.

Pointing AI Assist at another computer's Ollama URL already works, but that connection is unencrypted and unauthenticated. The next step is inference only for paired devices, over the existing pinned-TLS pairing channel.

The design is local-network only: no cloud relay and no router port-forwarding. Reaching a home machine from outside would be a separate decision for the owner.

## Known issues and backlog

- **Windows Smart App Control** blocks the launcher when AI Assist comes from a ZIP download, because the files carry the "from the internet" mark. Workarounds:
  - tick Unblock in the ZIP's Properties before extracting;
  - download with `git clone` instead;
  - run `python AIassist.py`.

  Never advise turning Smart App Control off: it cannot be turned back on without reinstalling Windows. The real fix is signed installers.
- **Mission Control** showed as a panel overlapping the chat message box when run on Linux with Qt at 1280x840, although its commit says it opens in its own window. This is not yet checked on macOS. Check before changing it, since Luigi owns this code.
- Bigger improvements discussed, each needing a decision from the owner or agreement with Luigi:
  - signed installers (a notarized macOS `.dmg` needs an Apple developer account; Windows needs a code-signing certificate);
  - a `src/` and `tests/` layout;
  - splitting `webui/app.py` and `app.js`;
  - branch protection that requires the **Tests** and **macOS desktop checks** workflows to pass before merging into `main`.
- A plain-language explainer page made for the owner (https://claude.ai/artifact/1UxAFvr1TBtcK1K6YjB6Q6) predates the network scan and usage sharing. Its local-network section needs updating.

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
