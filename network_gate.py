"""The internet gate: AI Assist goes online only after a person approves it twice.

``install()`` adds a Python audit hook, which cannot be removed afterwards.
Every DNS lookup, connection and datagram this process makes is sorted into
this computer, the local network, or the internet. Internet traffic is blocked
unless a person approved it for the current task; with no approval window
registered, it is blocked.

Requests that make another program go online are gated the same way: Ollama
downloads and uploads, Ollama cloud models, internet links opened in the
browser, and colibri model installs. Programs that run on their own, such as
commands the developer agent runs, are separate processes the hook cannot see.
"""

import base64
import collections
import contextlib
import dataclasses
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


LOCAL, LAN, INTERNET = "this computer", "local network", "internet"
GRANT_SECONDS = 60
DENIAL_SECONDS = 30
NOTICE_SECONDS = 10
MODEL_CHECK_SECONDS = 30
MAX_LOG_BYTES = 1024 * 1024
_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_SHOW_BYTES = 8 * 1024 * 1024

_LAN_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "224.0.0.0/24", "239.0.0.0/8", "255.255.255.255/32",
    "fc00::/7", "fe80::/10", "ff02::/16"))
_EVENTS = frozenset({
    "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
    "socket.gethostbyaddr", "socket.getnameinfo", "socket.connect", "socket.sendto",
    "socket.sendmsg", "urllib.Request", "webbrowser.open", "os.startfile", "import"})
# Ollama endpoints that make Ollama itself contact its online registry.
_OLLAMA_REGISTRY = {
    "/api/pull": ("Ollama model download", "Ollama downloads the model from its online registry."),
    "/api/push": ("Ollama model upload", "Ollama uploads the model to its online registry."),
    "/api/create": ("Ollama model creation", "Ollama may download a base model from its online registry."),
}
_OLLAMA_MODEL_PATHS = frozenset({"/api/chat", "/api/generate", "/api/embed", "/api/embeddings"})
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_GATE_FILE = os.path.normcase(os.path.abspath(__file__))
_APP_DIRECTORY = os.path.dirname(_GATE_FILE)
_UNREADABLE = object()

# Windows PowerShell 5.1 can show a toast through WinRT. The text arrives in
# environment variables, so it is never parsed as script.
_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$title = [System.Security.SecurityElement]::Escape($env:AI_ASSIST_NOTICE_TITLE)
$body = [System.Security.SecurityElement]::Escape($env:AI_ASSIST_NOTICE_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


class InternetBlocked(urllib.error.URLError):
    """Raised in place of internet access that a person did not approve."""

    def __str__(self):
        return str(self.reason)


@dataclasses.dataclass(frozen=True)
class Request:
    kind: str
    target: str
    purpose: str
    sends: str
    source: str
    scope: str


class _Task:
    def __init__(self, purpose, sends, seconds):
        self.purpose = purpose
        self.sends = sends
        self.deadline = time.monotonic() + seconds
        self.active = True

    def live(self):
        return self.active and time.monotonic() < self.deadline


class _Grant:
    def __init__(self, task):
        self.task = task
        self.expires = time.monotonic() + GRANT_SECONDS
        self.addresses = set()

    def valid(self):
        if self.task is not None:
            return self.task.live()
        return time.monotonic() < self.expires


_lock = threading.RLock()
_local = threading.local()
_installed = False
_approver = None
_listeners = []
_recent = collections.deque(maxlen=300)
_denied = {}
_pending = {}
_noticed = {}
_model_checks = {}
_last_allowed = None
_log_directory = None
_notices = queue.Queue()
_notice_thread = None


# ---------------------------------------------------------------- public API


def install(log_directory=None):
    """Turn the gate on for the rest of this process. It cannot be turned off."""
    global _installed, _log_directory
    with _lock:
        if log_directory is not None:
            _log_directory = Path(log_directory)
        if _installed:
            return
        if "_overlapped" in sys.modules:
            raise RuntimeError("asyncio was loaded before the internet gate, and its Windows connections cannot be checked.")
        sys.addaudithook(_hook)
        _installed = True


def installed():
    return _installed


def set_approver(approver):
    """``approver(request) -> bool`` asks a person; it may be called from any thread."""
    global _approver
    with _lock:
        _approver = approver


def add_listener(listener):
    """``listener(entry)`` receives each log entry, on the thread that made the request."""
    with _lock:
        _listeners.append(listener)


def remove_listener(listener):
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def recent():
    with _lock:
        return list(_recent)


def status():
    """Whether the gate is on, and the destination allowed in the last minute, if any."""
    with _lock:
        last = _last_allowed
    online = last[0] if last is not None and time.monotonic() - last[1] < GRANT_SECONDS else None
    return {"installed": _installed, "online": online}


@contextlib.contextmanager
def task(purpose, sends="", minutes=10):
    """Say why the code in this block may go online. Approvals given inside it end with it."""
    previous = getattr(_local, "task", None)
    current = _Task(purpose, sends, minutes * 60)
    _local.task = current
    try:
        yield
    finally:
        current.active = False
        _local.task = previous


def allow_external(kind, target, sends="", purpose=""):
    """Ask before starting a program that goes online by itself. True when approved,
    or when the gate is not installed."""
    if not _installed:
        return True
    try:
        with task(purpose, sends) if purpose else contextlib.nullcontext():
            _authorize(("external", kind, target), kind, target, sends)
    except InternetBlocked:
        return False
    return True


def log_path():
    if _log_directory is not None:
        return Path(_log_directory) / "network.log"
    override, local_data = os.environ.get("AI_ASSIST_DATA_DIR"), os.environ.get("LOCALAPPDATA")
    if override:
        directory = Path(override)
    elif local_data:
        directory = Path(local_data) / "AI Assist" / "chats"
    else:
        directory = Path.home() / ".aiassist" / "chats"
    return directory.expanduser() / "network.log"


# ---------------------------------------------------------------- where a host is


def classify(host):
    """LOCAL, LAN or INTERNET. Every name except ``localhost`` counts as internet:
    looking it up may ask a DNS server outside this network."""
    name = _normal(host)
    if name in ("", "localhost"):
        return LOCAL
    if name == "<broadcast>":
        return LAN
    address = _address(name)
    if address is None:
        return INTERNET
    if address.is_loopback or address.is_unspecified:
        return LOCAL
    return LAN if any(address in network for network in _LAN_NETWORKS) else INTERNET


def _normal(host):
    if host is None:
        return ""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    name = str(host).strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name.split("%", 1)[0]


def _address(name):
    try:
        address = ipaddress.ip_address(_normal(name))
    except ValueError:
        return None
    return getattr(address, "ipv4_mapped", None) or address


def _ip_text(name):
    address = _address(name)
    return address.compressed if address is not None else None


# ---------------------------------------------------------------- the hook


def _hook(event, args):
    if event not in _EVENTS or getattr(_local, "resolving", False):
        return
    try:
        _check(event, args)
    except InternetBlocked:
        raise
    except Exception as error:
        raise InternetBlocked(f"Internet access was blocked: the gate could not check {event} ({error}).") from None


def _check(event, args):
    if event == "import":
        if args and args[0] == "_overlapped":
            raise InternetBlocked("asyncio's Windows connections bypass the internet gate, so AI Assist does not load them.")
    elif event == "urllib.Request":
        _check_request(args[0], args[1] if len(args) > 1 else None)
    elif event in ("webbrowser.open", "os.startfile"):
        _check_link(args[0])
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
        _check_name(args[0])
    elif event in ("socket.gethostbyaddr", "socket.getnameinfo"):
        address = args[0]
        _check_reverse(address[0] if isinstance(address, tuple) and address else address)
    elif args[1] is not None:
        _check_address(args[0], args[1])


def _check_name(host):
    name = _normal(host)
    if classify(name) != INTERNET:
        return
    name = _ip_text(name) or name
    grant = _authorize(("host", name), "Internet connection", name)
    grant.addresses.update(_resolve(name))


def _check_reverse(host):
    name = _normal(host)
    if classify(name) != LOCAL:
        _authorize(("reverse", name), "Reverse DNS lookup", name)


def _check_address(sock, address):
    family = getattr(sock, "family", None)
    if hasattr(socket, "AF_UNIX") and family == socket.AF_UNIX:
        return
    if family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple) or not address:
        raise InternetBlocked("Internet access was blocked: AI Assist only opens connections the gate can check.")
    host = _normal(address[0])
    if classify(host) != INTERNET:
        return
    ip = _ip_text(host)
    if ip is None:
        # connect() with a name looks it up itself, without an audit event.
        _authorize(("host", host), "Internet connection", host)
        return
    if _address_granted(ip):
        return
    port = address[1] if len(address) > 1 else "?"
    grant = _authorize(("host", ip), "Internet connection", f"{ip} port {port}")
    grant.addresses.add(ip)


def _address_granted(ip):
    grants = _grants()
    for key, grant in list(grants.items()):
        if not grant.valid():
            del grants[key]
        elif ip in grant.addresses:
            return True
    # DNS answers can rotate between the gate's lookup and the caller's.
    for key, grant in list(grants.items()):
        if key[0] == "host" and _ip_text(key[1]) is None:
            grant.addresses.update(_resolve(key[1]))
            if ip in grant.addresses:
                return True
    return False


def _resolve(name):
    ip = _ip_text(name)
    if ip is not None:
        return {ip}
    _local.resolving = True
    try:
        answers = socket.getaddrinfo(name, None)
    except OSError:
        return set()
    finally:
        _local.resolving = False
    return {text for text in (_ip_text(answer[4][0]) for answer in answers) if text}


def _check_request(url, data):
    parts = urllib.parse.urlsplit(str(url))
    host = _normal(parts.hostname)
    if classify(host) == INTERNET:
        host = _ip_text(host) or host
        _authorize(("host", host), "Internet connection", host)
        return
    path = (parts.path or "").rstrip("/")
    if path in _OLLAMA_REGISTRY:
        kind, sends = _OLLAMA_REGISTRY[path]
        model = _body_model(data)
        name = model if isinstance(model, str) else "a model"
        _authorize(("ollama", path, name), kind, f"{name} (Ollama contacts its online registry)", sends)
    elif path in _OLLAMA_MODEL_PATHS:
        model = _body_model(data)
        if model is _UNREADABLE:
            raise InternetBlocked("Internet access was blocked: AI Assist could not read which model this request uses.")
        if model is None:
            return
        remote = _remote_model(f"{parts.scheme}://{parts.netloc}", model)
        if remote is None:
            raise InternetBlocked(f"Ollama did not answer, so AI Assist could not confirm that {model} runs on "
                                  "this computer. Start Ollama and try again.")
        if remote:
            current = _current_task()
            _authorize(("ollama cloud", model), "Ollama cloud model", f"{model} (runs on Ollama's servers)",
                       current.sends if current and current.sends else "Your message and the conversation sent with it.")


def _body_model(data):
    if data is None:
        return None
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return _UNREADABLE
    raw = bytes(data)
    if len(raw) > _MAX_BODY_BYTES:
        return _UNREADABLE
    try:
        body = json.loads(raw)
    except ValueError:
        return _UNREADABLE
    if not isinstance(body, dict):
        return _UNREADABLE
    model = body.get("model", body.get("name"))
    return model.strip() if isinstance(model, str) and model.strip() else None


def _remote_model(base, model):
    """True for an Ollama cloud model, including a local alias of one; None when unknown."""
    if model.lower().endswith(("-cloud", ":cloud")):
        return True
    key = (base, model)
    now = time.monotonic()
    with _lock:
        cached = _model_checks.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    request = urllib.request.Request(base + "/api/show", data=json.dumps({"model": model}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(_MAX_SHOW_BYTES + 1)
        info = json.loads(raw) if len(raw) <= _MAX_SHOW_BYTES else None
    except urllib.error.HTTPError as error:
        error.close()
        # A missing model cannot run anywhere; Ollama's API does not pull on demand.
        info = {} if error.code == 404 else None
    except (OSError, ValueError):
        info = None
    if not isinstance(info, dict):
        return None
    remote = bool(info.get("remote_host") or info.get("remote_model"))
    with _lock:
        _model_checks[key] = (now + MODEL_CHECK_SECONDS, remote)
    return remote


def _check_link(target):
    text = os.fsdecode(target) if isinstance(target, (bytes, os.PathLike)) else str(target)
    if "://" in text:
        host = urllib.parse.urlsplit(text).hostname or ""
    elif text.startswith(("\\\\", "//")):
        # A UNC path opens a file share on another computer.
        host = re.split(r"[\\/]", text.lstrip("\\/"), maxsplit=1)[0]
    elif _SCHEME.match(text):
        host = None
    else:
        return
    if host is None or classify(host) == INTERNET:
        _authorize(("link", text), "Open outside AI Assist", text,
                   "Another program, usually the browser, opens this address from the internet.")


# ---------------------------------------------------------------- approvals


def _grants():
    grants = getattr(_local, "grants", None)
    if grants is None:
        grants = _local.grants = {}
    return grants


def _current_task():
    current = getattr(_local, "task", None)
    return current if current is not None and current.live() else None


def _authorize(key, kind, target, sends=""):
    grants = _grants()
    grant = grants.get(key)
    if grant is not None and grant.valid():
        return grant
    grants.pop(key, None)
    current = _current_task()
    request = Request(
        kind=kind, target=target, purpose=current.purpose if current else "",
        sends=sends or (current.sends if current else ""), source=_source(),
        scope="until this task finishes" if current else f"for {GRANT_SECONDS} seconds")
    if not _ask(key, request):
        raise InternetBlocked(f"Internet access was not approved, so it was blocked ({kind}: {target}). "
                              "Settings > Connection log lists every request.")
    grant = grants[key] = _Grant(current)
    return grant


def _ask(key, request):
    main = threading.current_thread() is threading.main_thread()
    with _lock:
        now = time.monotonic()
        for old in [old for old, until in _denied.items() if until <= now]:
            del _denied[old]
        approver, pending, owner, outcome = _approver, None, False, None
        if key in _denied:
            outcome = "blocked: denied moments ago"
        elif approver is None:
            outcome = "blocked: no approval window"
        elif key in _pending:
            if main:
                outcome = "blocked: already waiting for review"
            else:
                pending = _pending[key]
        else:
            pending = _pending[key] = [threading.Event(), False]
            owner = True
    if outcome is not None:
        _record(outcome, request, notify=True)
        return False
    if not owner:
        pending[0].wait()
        return pending[1]
    _notice("AI Assist asks to go online", f"{request.kind}: {request.target}. Open AI Assist to review it.", ("ask", key))
    try:
        approved = approver(request) is True
    except Exception:
        approved = False
    with _lock:
        _pending.pop(key, None)
        if not approved:
            _denied[key] = time.monotonic() + DENIAL_SECONDS
    pending[1] = approved
    pending[0].set()
    _record("allowed" if approved else "blocked", request, notify=approved)
    return approved


def _source():
    frame = sys._getframe(1)
    while frame is not None:
        path = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if path != _GATE_FILE and os.path.dirname(path) == _APP_DIRECTORY:
            return f"{os.path.basename(frame.f_code.co_filename)} line {frame.f_lineno}"
        frame = frame.f_back
    return "code outside AI Assist's own files"


# ---------------------------------------------------------------- log and notifications


def _record(decision, request, notify=False):
    global _last_allowed
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "decision": decision, "kind": request.kind,
             "target": request.target, "purpose": request.purpose, "sends": request.sends,
             "source": request.source, "scope": request.scope}
    with _lock:
        _recent.append(entry)
        listeners = list(_listeners)
        if decision == "allowed":
            _last_allowed = (request.target, time.monotonic())
        _write_log(entry)
    for listener in listeners:
        try:
            listener(entry)
        except Exception:
            pass
    if notify:
        title = "AI Assist went online" if decision == "allowed" else "AI Assist blocked internet access"
        reason = f" ({request.purpose})" if request.purpose else ""
        _notice(title, f"{request.kind}: {request.target}{reason}", (decision, request.target))


def _write_log(entry):
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_LOG_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def notification_command(title, body):
    """The command that shows a system notification here, with its environment, or (None, None)."""
    title = _CONTROL.sub(" ", str(title))[:120]
    body = _CONTROL.sub(" ", str(body))[:300]
    if os.name == "nt":
        script = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
        environment = dict(os.environ, AI_ASSIST_NOTICE_TITLE=title, AI_ASSIST_NOTICE_BODY=body)
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", script], environment
    if sys.platform == "darwin":
        return ["osascript", "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, body], None
    if shutil.which("notify-send"):
        return ["notify-send", "--app-name=AI Assist", "--", title, body], None
    return None, None


def _notice(title, body, key):
    global _notice_thread
    now = time.monotonic()
    with _lock:
        last = _noticed.get(key)
        if last is not None and now - last < NOTICE_SECONDS:
            return
        _noticed[key] = now
        if _notice_thread is None:
            _notice_thread = threading.Thread(target=_show_notices, name="internet-gate-notices", daemon=True)
            _notice_thread.start()
    _notices.put((title, body))


def _show_notices():
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    while True:
        title, body = _notices.get()
        command, environment = notification_command(title, body)
        if command is None:
            continue
        try:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
            process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
