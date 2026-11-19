"""End-to-end check of the web workspace in a real browser, against the real local Ollama.

    python tools/web_e2e.py [--browser PATH] [--model gemma3:270m] [--out shots/]

It starts web_workspace.py on a throwaway data folder (your saved chats are
never touched), signs in with the printed single-use link, and drives a
headless Edge or Chrome over the DevTools protocol (standard library only):
a real streamed answer, Stop, dialogs and focus, the read-only developer
view, the Day theme, a phone-sized screen, and any script error or CSP
violation. Screenshots go to --out.

Needs: Ollama running with --model installed (a tiny one such as gemma3:270m
keeps it quick), and Edge or Chrome. It is not part of the unit suite.
"""
import argparse, base64, glob, json, os, shutil, signal, socket, struct, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path


def find_browser():
    candidates = [shutil.which(name) for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome")]
    candidates += sorted(glob.glob(r"C:\Program Files (x86)\Microsoft\Edge*\**\msedge.exe", recursive=True), reverse=True)
    candidates += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
parser.add_argument("--browser", default=None, help="Edge or Chrome executable (found automatically when omitted)")
parser.add_argument("--model", default="gemma3:270m", help="an installed Ollama model; small is fast")
parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "aiassist-e2e-shots"), help="screenshot folder")
options = parser.parse_args()
REPO = Path(__file__).resolve().parent.parent
EDGE = options.browser or find_browser()
if not EDGE:
    raise SystemExit("No Edge or Chrome was found. Pass --browser PATH.")
OUT = Path(options.out)
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix="aiassist-e2e-data-"))
PROFILE = tempfile.mkdtemp(prefix="aiassist-e2e-edge-")
MODEL = options.model
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


# ------------------------------------------------------------ a developer chat to browse (written before the server starts)
sys.path.insert(0, str(REPO))
os.environ["AI_ASSIST_DATA_DIR"] = str(DATA)
from chat_store import ChatStore  # noqa: E402
project = Path(tempfile.mkdtemp(prefix="aiassist-e2e-project-"))
(project / "src").mkdir()
(project / "src" / "app.py").write_bytes(b"def greet(name):\n    return f'Hello, {name}!'\n\nprint(greet('world'))\n")
(project / "README.md").write_bytes(b"# Demo\n")
(project / ".env").write_bytes(b"SECRET=do-not-show\n")
store = ChatStore(DATA)
dev = store.new_record(model=MODEL, mode="dev", title="Add a greeting", project_path=str(project))
dev["messages"] = [{"role": "user", "content": "Add a greeting function"}, {"role": "assistant", "content": "Added `greet` in src/app.py and ran it."}]
dev["activity"] = [{"type": "request_start", "text": "Developer task started."},
                   {"type": "changed", "text": "Updated src/app.py", "path": "src/app.py",
                    "diff": "--- src/app.py\n+++ src/app.py\n@@ -1 +1,4 @@\n-print('hi')\n+def greet(name):\n+    return f'Hello, {name}!'\n+\n+print(greet('world'))\n"},
                   {"type": "command_succeeded", "text": "Command exited with code 0.\nHello, world!", "command": "python src/app.py"}]
store.save(dev)

# ------------------------------------------------------------ server
server = subprocess.Popen([sys.executable, "web_workspace.py", "--no-browser", "--port", "0"], cwd=REPO,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          env=dict(os.environ, PYTHONUNBUFFERED="1"))
link = None
for _ in range(40):
    line = server.stdout.readline()
    if "#launch=" in line:
        link = line.strip()
        break
check("web_workspace.py prints a single-use loopback link", link and link.startswith("http://127.0.0.1:"), link or "no link")

# ------------------------------------------------------------ minimal WebSocket + CDP client
class CDP:
    def __init__(self, url):
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(1)
        assert b" 101 " in response.split(b"\r\n")[0], response
        self.next_id, self.events = 0, []

    def _read(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("CDP closed")
            data += chunk
        return data

    def _recv_message(self):
        payload = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            payload += self._read(length)
            if first & 0x80:
                return json.loads(payload)

    def send(self, method, **params):
        self.next_id += 1
        data = json.dumps({"id": self.next_id, "method": method, "params": params}).encode()
        header = bytes([0x81])
        mask = os.urandom(4)
        if len(data) < 126:
            header += bytes([0x80 | len(data)])
        elif len(data) < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", len(data))
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", len(data))
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            message = self._recv_message()
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})
            self.events.append(message)

    def js(self, expression):
        result = self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def wait(self, expression, timeout=60, step=0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.js(expression)
            if value:
                return value
            time.sleep(step)
        return None

    def shot(self, name):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        (OUT / name).write_bytes(base64.b64decode(data))

    def key(self, key, code=None, keycode=0, modifiers=0, text=None):
        base = dict(key=key, code=code or key, windowsVirtualKeyCode=keycode, modifiers=modifiers)
        self.send("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
        self.send("Input.dispatchKeyEvent", type="keyUp", **base)


with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    DEBUG_PORT = probe.getsockname()[1]
edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={PROFILE}",
                         "--no-first-run", "--disable-background-networking", "--disable-component-update", "--disable-sync",
                         "--disable-extensions", "--window-size=1440,900", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        **({} if os.name == "nt" else {"start_new_session": True}))
try:
    targets = None
    for _ in range(60):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2))
            if any(t.get("type") == "page" for t in targets):
                break
        except OSError:
            time.sleep(0.25)
    page = next(t for t in targets if t.get("type") == "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    for domain in ("Page", "Runtime", "Log"):
        cdp.send(f"{domain}.enable")
    cdp.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    cdp.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])

    cdp.send("Page.navigate", url=link)
    check("status chip shows local only", cdp.wait("document.querySelector('#status-chips .chip')?.textContent.includes('local only')", 20),
          cdp.js("document.getElementById('status-chips').innerText"))
    check("signed in with the launch link", cdp.js("document.getElementById('sign-in').hidden && !document.getElementById('app').hasAttribute('inert')"))
    check("the launch code left the address bar", cdp.js("location.hash === '' && location.pathname === '/'"), cdp.js("location.href"))
    cdp.key("Tab", "Tab", 9)
    check("the first Tab reaches the skip link", cdp.js("document.activeElement?.classList.contains('skip-link')"),
          cdp.js("document.activeElement?.outerHTML.slice(0, 80)"))
    models = cdp.wait("[...document.querySelectorAll('select[aria-label=Model] option')].map(o => o.value).filter(Boolean).join(',')", 30)
    check("installed Ollama models are listed", models and MODEL in models, models)
    cdp.js(f"(() => {{ const s = document.querySelector('select[aria-label=Model]'); s.value = {json.dumps(MODEL)}; s.dispatchEvent(new Event('change')); }})()")
    check("the empty state offers suggestions", cdp.js("document.querySelectorAll('.suggestion').length === 4"))
    check("no menu button at desktop width", cdp.js("getComputedStyle(document.getElementById('nav-toggle')).display === 'none'"))
    cdp.shot("01-empty-dark.png")

    # A real streamed answer, typed and sent with Enter.
    cdp.js("document.getElementById('composer-input').focus()")
    cdp.send("Input.insertText", text="Reply with a short Python code block that prints hello, then one sentence.")
    cdp.key("Enter", "Enter", 13, text="\r")
    finished = cdp.wait("!document.querySelector('.message[aria-busy=true]') && document.querySelectorAll('.message.is-assistant').length === 1", 120)
    check("the finished answer replaces the stream", finished)
    check("the chat is saved with a title", cdp.wait("document.querySelector('.list-item[aria-current=true] .list-item-title')?.textContent.startsWith('Reply with')", 10))
    check("the composer is ready again", cdp.js("!document.getElementById('composer-input').disabled && document.activeElement?.id === 'composer-input'"))
    check("answer complete was announced", cdp.wait("document.getElementById('announcer').textContent === 'Answer complete.'", 5),
          cdp.js("document.getElementById('announcer').textContent"))
    check("no raw HTML elements came from the answer", cdp.js("document.querySelectorAll('.message-body script, .message-body img, .message-body iframe, .message-body a').length === 0"))
    cdp.js("document.querySelector('.view-body').scrollTop = 1e9")
    cdp.shot("02-answer-dark.png")
    has_code = cdp.js("document.querySelectorAll('.message.is-assistant .code-block').length")
    print("   code blocks rendered:", has_code)

    # Stop keeps the partial answer.
    cdp.js("document.getElementById('composer-input').focus()")
    cdp.send("Input.insertText", text="Write a very long, detailed essay about the history of rivers, at least 2000 words.")
    cdp.key("Enter", "Enter", 13, text="\r")
    seen = cdp.wait("(document.querySelector('.message[aria-busy=true] .message-body')?.textContent.length || 0) > 40", 60, 0.05)
    check("the answer is visible while it streams", seen)
    check("Send became Stop while streaming", cdp.js("document.querySelector('.composer button[type=submit]').getAttribute('aria-label') === 'Stop the answer'"))
    cdp.shot("02b-streaming.png")
    cdp.js("document.querySelector('.composer button[type=submit]').click()")
    stopped = cdp.wait("!document.querySelector('.message[aria-busy=true]')", 30)
    announced = cdp.js("document.getElementById('announcer').textContent")
    check("Stop ends the answer and keeps the partial text", stopped and "partial answer was kept" in announced, announced)
    saved = cdp.js("""(async () => {
        const token = sessionStorage.getItem('aiassist.session');
        const list = await (await fetch('/api/chats', {headers: {Authorization: 'Bearer ' + token}})).json();
        const id = list.chats.find(c => c.mode === 'chat').id;
        const chat = (await (await fetch('/api/chats/' + id, {headers: {Authorization: 'Bearer ' + token}})).json()).chat;
        return chat.messages.map(m => m.role).join(',') + '|' + chat.messages[3].content.length;
    })()""")
    check("the stopped answer is saved", saved and saved.startswith("user,assistant,user,assistant|"), saved)

    # Rename dialog: focus moves in, Escape cancels, focus returns.
    cdp.js("document.querySelector('[aria-label=\"Rename chat\"]').focus(); document.querySelector('[aria-label=\"Rename chat\"]').click()")
    check("the dialog opens with focus in its field", cdp.wait("document.getElementById('dialog').open && document.activeElement?.id === 'dialog-input'", 5))
    cdp.key("Escape", "Escape", 27)
    check("Escape closes it and focus returns", cdp.wait("!document.getElementById('dialog').open && document.activeElement?.getAttribute('aria-label') === 'Rename chat'", 5))

    # Every control has an accessible name.
    unnamed = cdp.js("[...document.querySelectorAll('button, select, input, textarea')].filter(e => e.offsetParent && !(e.getAttribute('aria-label') || e.textContent.trim() || e.labels?.length || e.title)).map(e => e.outerHTML.slice(0, 80))")
    check("every visible control has an accessible name", not unnamed, json.dumps(unnamed)[:300])
    # Developer chat: read-only, files without secrets, diffs and activity.
    cdp.js("document.querySelector('.nav-section[data-view=projects]').click()")
    cdp.wait("document.querySelector('.sidebar button.list-item')", 10)
    cdp.js("document.querySelector('.sidebar button.list-item').click()")
    files = cdp.wait("[...document.querySelectorAll('.tree-file')].map(b => b.textContent).join(',')", 15)
    check("the project tree lists files without secrets", files and "app.py" in files and ".env" not in files, files)
    cdp.js("[...document.querySelectorAll('.tree-file')].find(b => b.textContent === 'app.py').click()")
    check("a file opens read-only with line numbers", cdp.wait("document.querySelectorAll('.source-line').length === 4", 10))
    cdp.shot("03-project-source.png")
    cdp.js("document.getElementById('tab-changes').click()")
    check("applied diffs are shown", cdp.wait("document.querySelectorAll('.diff-line.add').length === 4", 5))
    cdp.js("document.getElementById('tab-activity').click()")
    check("command activity is shown", cdp.wait("!!document.querySelector('.activity-type.is-success')", 5),
          cdp.js("document.getElementById('project-panel')?.innerText.slice(0, 300)"))
    cdp.js("document.querySelector('.view-header .button').click()")
    check("the developer conversation is read-only", cdp.wait("document.querySelector('.composer').hidden && document.querySelector('.composer-notice').textContent.includes('Read-only here')", 10))

    # Backups: the daily one at start, and Back up now.
    cdp.js("document.querySelector('.nav-section[data-view=settings]').click()")
    check("the daily backup is listed", cdp.wait("document.querySelectorAll('#settings-backups .backup-row').length >= 1", 15))
    cdp.js("[...document.querySelectorAll('#settings-backups button')].find(b => b.textContent === 'Back up now').click()")
    check("Back up now reports the backup", cdp.wait("document.getElementById('toasts').textContent.includes('Backed up 2 chats')", 15),
          cdp.js("document.getElementById('toasts').textContent"))

    # Light theme, then a phone-sized screen.
    cdp.js("document.getElementById('theme-toggle').click()")
    check("the theme switches to Day", cdp.js("document.documentElement.dataset.theme === 'light' && getComputedStyle(document.body).backgroundColor === 'rgb(247, 248, 250)'"),
          cdp.js("getComputedStyle(document.body).backgroundColor"))
    cdp.js("document.querySelector('.nav-section[data-view=chats]').click()")
    cdp.wait("document.querySelectorAll('.sidebar button.list-item').length >= 2", 10)
    cdp.js("[...document.querySelectorAll('.sidebar .list-item')].find(b => b.textContent.includes('Reply with')).click()")
    cdp.wait("document.querySelectorAll('.message').length >= 4", 10)
    cdp.shot("04-chat-light.png")
    cdp.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    time.sleep(0.5)
    check("phone width has no horizontal scroll", cdp.js("document.documentElement.scrollWidth <= window.innerWidth"),
          cdp.js("document.documentElement.scrollWidth + ' vs ' + window.innerWidth"))
    check("the sidebar becomes a drawer", cdp.js("getComputedStyle(document.getElementById('nav-toggle')).display !== 'none'"))
    check("the details panel closes on a phone", cdp.js("!document.getElementById('app').classList.contains('inspector-open')"))
    cdp.shot("05-phone.png")
    cdp.js("document.getElementById('inspector-toggle').click()")
    cdp.js("document.getElementById('nav-toggle').click()")
    time.sleep(0.4)
    check("only one drawer is open at a time", cdp.js("document.getElementById('app').className === 'app nav-open'"),
          cdp.js("document.getElementById('app').className"))
    cdp.shot("06-phone-drawer.png")
    cdp.key("Escape", "Escape", 27)
    check("Escape closes the drawer", cdp.wait("!document.getElementById('app').classList.contains('nav-open')", 3))

    # Integrated compute monitor: real authenticated local metrics, no scrolling.
    cdp.send("Emulation.setDeviceMetricsOverride", width=800, height=480, deviceScaleFactor=1, mobile=False)
    cdp.js("document.querySelector('.nav-section[data-view=compute]').click()")
    check("Compute view opens in the modern web workspace",
          cdp.wait("!!document.querySelector('.compute-grid') && !!document.querySelector('.compute-reading')", 10))
    check("Compute monitor renders all five fixed panels",
          cdp.js("document.querySelectorAll('.compute-grid .compute-card').length === 5"))
    check("Compute monitor shows local-only telemetry",
          cdp.wait("document.querySelector('.compute-state')?.textContent?.includes('LOCAL')", 35))
    check("Compute monitor has no internal scrollbars",
          cdp.js("(() => { let el=document.querySelector('.compute-view'); return el && el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1; })()"))
    cdp.shot("07-compute-monitor.png")

    # Problems the page reported: exceptions, CSP violations, console errors.
    problems = [e for e in cdp.events if e.get("method") == "Runtime.exceptionThrown"
                or (e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] == "error")
                or (e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] == "error")]
    check("no script errors or CSP violations", not problems, json.dumps(problems)[:800])
finally:
    # Close the browser through DevTools. On Windows the launched process may hand
    # over to a detached one, so anything still using this test's profile is stopped too.
    try:
        version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=5))
        CDP(version["webSocketDebuggerUrl"]).send("Browser.close")
    except (OSError, ValueError, KeyError, RuntimeError, ConnectionError):
        pass
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(edge.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(edge.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def stop_profile_processes():
        """Stop every process using this test's profile; how many were found."""
        if os.name == "nt":
            script = ("$found = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like $env:E2E_PROFILE }); "
                      "$found | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
                      "$found.Count")
            output = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                    env=dict(os.environ, E2E_PROFILE=f"*{PROFILE}*"), capture_output=True, text=True).stdout
            return int(output.strip() or 0) if output.strip().isdigit() else 0
        return 1 if subprocess.run(["pkill", "-9", "-f", PROFILE], capture_output=True).returncode == 0 else 0

    # Child processes can still be starting or exiting: repeat until a pass finds none.
    for _ in range(10):
        time.sleep(1)
        if not stop_profile_processes():
            break
    else:
        print(f"Warning: some browser processes using {PROFILE} could not be stopped.")
    server.kill()
    server.wait(timeout=10)
    server.stdout.close()
    time.sleep(1)  # let the browser release its profile files
    for folder in (DATA, project, PROFILE):
        shutil.rmtree(folder, ignore_errors=True)

failed = [name for name, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
