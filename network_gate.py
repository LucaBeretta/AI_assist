"""The internet gate: AI Assist goes online only after a person approves it twice.

``install()`` adds a Python audit hook, which cannot be removed afterwards.
Every DNS lookup, connection and datagram this process makes is sorted into
this computer, the local network, or the internet. Internet traffic is blocked
unless a person approved it for the current task; with no approval window
registered, it is blocked.

Requests that make another program go online are gated the same way: Ollama
downloads and uploads, Ollama cloud models, internet links opened in the
browser, and colibri model installs. Python programs that AI Assist starts
(colibri, and Python run by the developer agent's commands) load this gate
through child_gate/sitecustomize.py and send their requests back here for the
same approval (``child_environment``). Other programs run outside this
process: firewall_lock.py covers the AI engines, and every agent command needs
two confirmations.
"""

import base64
import collections
import contextlib
import dataclasses
import hmac
import importlib.machinery
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import sysconfig
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
# Extensions that open their own connections from native code, out of the hook's sight.
_NATIVE_NETWORK_MODULES = frozenset({"hf_xet", "hf_transfer", "pycurl"})
_MAX_MESSAGE_BYTES = 64 * 1024
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_GATE_FILE = os.path.normcase(os.path.abspath(__file__))
_APP_DIRECTORY = os.path.dirname(_GATE_FILE)
_CHILD_GATE_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child_gate")
_LIBRARIES = tuple({os.path.join(os.path.normcase(os.path.abspath(path)), "")
                    for path in (sysconfig.get_paths().get(name) for name in ("stdlib", "platstdlib", "purelib", "platlib"))
                    if path})
_TITLES = {"allowed": "AI Assist went online",
           "ran outside the gate": "AI Assist ran something the internet gate cannot check",
           "firewall lock on": "The firewall lock is on",
           "firewall lock off": "The firewall lock is off",
           "firewall lock lifted": "The firewall lock is lifted"}
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
_lock_check = None
_child_server = None
# In a Python program AI Assist started: (port, token, label) of the app that approves.
_parent = None
_process_grants = {}


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


def install_child():
    """Turn the gate on in a Python program AI Assist started (child_gate/sitecustomize.py).
    Its requests go to the app for approval; if the app does not answer, they are blocked."""
    global _installed, _parent, _log_directory
    port, _, token = os.environ.get("AI_ASSIST_GATE", "").partition(":")
    with _lock:
        if _installed:
            return
        _parent = (int(port), token, os.environ.get("AI_ASSIST_GATE_LABEL") or "a program AI Assist started")
        if os.environ.get("AI_ASSIST_GATE_LOG"):
            _log_directory = Path(os.environ["AI_ASSIST_GATE_LOG"]).parent
        sys.meta_path.insert(0, _ProactorFinder())
        sys.addaudithook(_hook)
        _installed = True


def child_environment(label, base=None):
    """The environment for a Python program AI Assist starts, so that its internet
    requests come back here for approval. None when the gate is off."""
    global _child_server
    if not _installed or _parent is not None:
        return None
    with _lock:
        if _child_server is None:
            server = _ChildServer(("127.0.0.1", 0), _ChildRequests)
            server.token = secrets.token_hex(16)
            threading.Thread(target=server.serve_forever, name="internet-gate-children", daemon=True).start()
            _child_server = server
        server = _child_server
    environment = dict(os.environ if base is None else base)
    paths = [_CHILD_GATE_DIRECTORY] + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
    environment.update(PYTHONPATH=os.pathsep.join(paths), AI_ASSIST_GATE_LABEL=label,
                       AI_ASSIST_GATE=f"{server.server_address[1]}:{server.token}",
                       AI_ASSIST_GATE_LOG=str(log_path()))
    return environment


def set_lock_check(check):
    """``check(kind) -> str or None``: why another program cannot go online right now."""
    global _lock_check
    _lock_check = check


def note(decision, kind, target, purpose="", notify=True):
    """Log, and announce, an event the gate does not decide itself, such as a firewall lock change."""
    if not _installed:
        return
    current = _current_task()
    _record(decision, Request(kind, _CONTROL.sub(" ", str(target))[:300],
                              purpose or (current.purpose if current else ""), "", _source(), ""), notify=notify)


def note_outside(kind, target, purpose=""):
    """Log and announce something that runs where the gate cannot see, such as an agent command."""
    note("ran outside the gate", kind, target, purpose)


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
    except (InternetBlocked, ImportError):
        raise
    except Exception as error:
        raise InternetBlocked(f"Internet access was blocked: the gate could not check {event} ({error}).") from None


def _check(event, args):
    if event == "import":
        name = args[0] if args else ""
        if name in _NATIVE_NETWORK_MODULES:
            raise ImportError(f"{name} goes online from native code the internet gate cannot check, so it is not loaded.")
        # Programs AI Assist starts gate asyncio's Windows connections instead (_ProactorFinder).
        if name == "_overlapped" and _parent is None:
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
            grants.pop(key, None)
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
        _refuse_if_locked(kind, f"{name} (Ollama contacts its online registry)", sends)
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
            sends = current.sends if current and current.sends else "Your message and the conversation sent with it."
            _refuse_if_locked("Ollama cloud model", f"{model} (runs on Ollama's servers)", sends)
            _authorize(("ollama cloud", model), "Ollama cloud model", f"{model} (runs on Ollama's servers)", sends)


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
    # A program AI Assist started has no tasks, and asyncio looks names up on one
    # thread and connects on another, so its approvals cover the whole process.
    if _parent is not None:
        return _process_grants
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


def _refuse_if_locked(kind, target, sends):
    check = _lock_check
    reason = check(kind) if check is not None else None
    if reason:
        current = _current_task()
        _record("blocked: firewall lock", Request(kind, target, current.purpose if current else "", sends,
                                                  _source(), "not started"), notify=True)
        raise InternetBlocked(reason)


def _ask(key, request):
    if _parent is not None:
        return _ask_parent(request)
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
        filename = frame.f_code.co_filename
        path = os.path.normcase(os.path.abspath(filename))
        if path != _GATE_FILE:
            if _parent is None and os.path.dirname(path) == _APP_DIRECTORY:
                return f"{os.path.basename(filename)} line {frame.f_lineno}"
            # In a program AI Assist started, name the program's own code, not its libraries.
            if (_parent is not None and not filename.startswith("<") and not path.startswith(_LIBRARIES)
                    and "site-packages" not in path):
                folder = os.path.basename(os.path.dirname(filename))
                return f"{folder}/{os.path.basename(filename)} line {frame.f_lineno}"
        frame = frame.f_back
    return "code outside AI Assist's own files" if _parent is None else "the program's Python libraries"


# ---------------------------------------------------------------- Python programs AI Assist starts


class _ChildServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


class _ChildRequests(socketserver.StreamRequestHandler):
    """One approval request from a program AI Assist started, answered after a person decides."""

    def handle(self):
        try:
            self.connection.settimeout(30)
            message = json.loads(self.rfile.readline(_MAX_MESSAGE_BYTES + 1))
            token = str(message.get("token", "")).encode("utf-8") if isinstance(message, dict) else b""
            if not hmac.compare_digest(token, self.server.token.encode("ascii")):
                return
            self.connection.settimeout(None)
            approved = _ask_for_child(message)
            self.wfile.write(json.dumps({"approved": approved}).encode("utf-8") + b"\n")
        except (OSError, ValueError):
            pass


def _ask_for_child(message):
    def text(key, limit=500):
        return _CONTROL.sub(" ", str(message.get(key) or ""))[:limit]

    label = text("label", 80) or "a program AI Assist started"
    request = Request(kind=text("kind", 80), target=text("target", 300), purpose=text("purpose"),
                      sends=text("sends"), scope=text("scope", 120),
                      source=f"{label} (process {text('pid', 12)}): {text('source', 200)}")
    return _ask(("child", label, request.kind, request.target), request)


def _ask_parent(request):
    port, token, label = _parent
    message = dict(dataclasses.asdict(request), type="ask", token=token, label=label, pid=os.getpid())
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
            connection.sendall(json.dumps(message).encode("utf-8") + b"\n")
            connection.settimeout(None)
            with connection.makefile("rb") as reply:
                answer = json.loads(reply.readline(_MAX_MESSAGE_BYTES + 1) or b"{}")
    except (OSError, ValueError):
        _record("blocked: AI Assist is not running to approve it", request, notify=True)
        return False
    return isinstance(answer, dict) and answer.get("approved") is True


class _ProactorFinder:
    """asyncio's Windows event loop connects through IOCP, which raises no audit
    events. In programs AI Assist starts, its connect and sendto are gated instead."""

    def find_spec(self, name, path=None, target=None):
        if name != "asyncio.windows_events":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _ProactorLoader(spec.loader)
        return spec


class _ProactorLoader:
    def __init__(self, loader):
        self._loader = loader

    def __getattr__(self, name):
        return getattr(self._loader, name)

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        proactor = module.IocpProactor
        connect, sendto = proactor.connect, proactor.sendto

        def gated_connect(self, conn, address):
            _hook("socket.connect", (conn, address))
            return connect(self, conn, address)

        def gated_sendto(self, conn, buf, flags=0, addr=None):
            if addr is not None:
                _hook("socket.sendto", (conn, addr))
            return sendto(self, conn, buf, flags, addr)

        proactor.connect, proactor.sendto = gated_connect, gated_sendto


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
        title = _TITLES.get(decision) or ("AI Assist blocked internet access" if decision.startswith("blocked")
                                          else "AI Assist internet gate")
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
