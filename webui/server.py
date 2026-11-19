"""The web workspace's HTTP server: this computer only, authenticated, no dangerous tools.

Endpoints (all JSON, all under /api, all needing a session except /api/session):

    POST   /api/session                       exchange a launch code for a token
    GET    /api/status                        engine, privacy, what this interface can do
    GET    /api/models                        installed models and where they run
    GET    /api/models/details?name=          capabilities and context of one model
    GET    /api/hardware                      CPU, RAM, GPU (cached for two minutes)
    GET    /api/chats?query=&mode=            saved chats, newest first
    POST   /api/chats                         a new chat
    GET    /api/chats/<id>                    one chat with its messages
    PATCH  /api/chats/<id>                    rename
    DELETE /api/chats/<id>                    delete
    POST   /api/chats/<id>/messages           send; the answer streams as NDJSON events
    POST   /api/requests/<id>/cancel          stop a streaming answer
    GET    /api/memory  /  PUT /api/memory    shared notes
    GET    /api/projects                      folders of saved developer chats
    GET    /api/projects/<chat id>/files      their text files (agent exclusions apply)
    GET    /api/projects/<chat id>/file?path= one file, read-only
    GET    /api/log                           the internet gate's recent decisions
    PUT    /api/settings/engine               Ollama or Colibri

Nothing here runs commands, writes project files or approves internet access:
those stay in the desktop window, behind its reviewed, two-step confirmations.
"""

import http.server
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import socketserver
import threading
import time
import urllib.parse

from app_core import ollama, settings
from compute_telemetry import TelemetrySampler
from app_core.chat_service import ChatService, ServiceError
from app_core.privacy import is_cloud_model, privacy_status
from app_core.providers import configured_provider
from chat_store import ChatStoreError
import developer_tools
import network_gate
from webui.security import API_POLICY, COMMON_HEADERS, PAGE_POLICY, Sessions, request_problem

STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY_BYTES = 1024 * 1024
MAX_PROJECT_FILE_BYTES = 512_000
HARDWARE_SECONDS = 120
LOG_TAIL_BYTES = 256 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
_CHAT = r"(?P<chat>[0-9a-f-]{36})"


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _static_files():
    """Exactly the page files that exist now: no paths, no directory listing."""
    files = {}
    if STATIC_DIRECTORY.is_dir():
        for entry in os.scandir(STATIC_DIRECTORY):
            suffix = Path(entry.name).suffix
            if entry.is_file(follow_symlinks=False) and suffix in CONTENT_TYPES and re.fullmatch(r"[a-z0-9-]+\.[a-z]+", entry.name):
                files["/" + entry.name] = Path(entry.path)
    if "/index.html" in files:
        files["/"] = files["/index.html"]
    return files


class WebWorkspace:
    """The application behind the API: the chat service plus read-only views."""

    def __init__(self, store, shared_memory=None, provider_factory=None):
        self.store = store
        self.directory = store.directory
        self.shared_memory = shared_memory
        self.provider_factory = provider_factory or (lambda: configured_provider(self.directory))
        self.service = ChatService(store, self.provider_factory, shared_memory)
        self.sessions = Sessions()
        self.requests = {}
        self.lock = threading.Lock()
        self.hardware = None
        self.compute_sampler = TelemetrySampler()
        self.compute_snapshot = None

    def provider(self):
        return self.provider_factory()

    # ------------------------------------------------------------ views

    def status(self, model=""):
        provider = self.provider()
        info = provider.info()
        gate = network_gate.status()
        text, role = privacy_status(gate, model, info.endpoint)
        memory = self.shared_memory
        return {
            "engine": info.name, "endpoint": info.endpoint, "endpoint_local": info.local,
            "manages_lifecycle": info.manages_lifecycle,
            "gate": {"installed": gate["installed"], "online": gate["online"]},
            "privacy": {"text": text, "role": role},
            # What this interface does; the desktop window does the rest.
            "capabilities": {"chat": True, "developer_agent": False, "internet_approvals": False,
                             "project_files": "read-only"},
            "memory": {"available": memory is not None, "enabled": bool(memory and memory.enabled)},
            "data_directory": str(self.directory),
        }

    def models(self):
        provider = self.provider()
        try:
            names = provider.list_models()
        except (RuntimeError, ValueError, OSError) as error:
            raise HttpError(503, str(error)) from None
        try:
            running = provider.running()
        except (RuntimeError, ValueError, OSError):
            running = {}
        return {"engine": provider.name, "models": [
            {"name": name, "running": name in running, "placement": ollama.placement(running.get(name)),
             "size_vram": (running.get(name) or {}).get("size_vram"),
             "context_length": (running.get(name) or {}).get("context_length"), "cloud": is_cloud_model(name)}
            for name in names]}

    def model_details(self, name):
        if not name:
            raise HttpError(400, "Choose a model.")
        try:
            return self.provider().model_details(name)
        except (RuntimeError, ValueError, OSError) as error:
            raise HttpError(503, str(error)) from None

    def hardware_report(self):
        with self.lock:
            cached = self.hardware
        if cached is not None and time.monotonic() - cached[0] < HARDWARE_SECONDS:
            return cached[1]
        import model_advisor
        report = model_advisor.detect_hardware(self.provider().info().endpoint)
        with self.lock:
            self.hardware = (time.monotonic(), report)
        return report

    def compute_report(self):
        """Read-only local telemetry, authenticated by the existing web session.

        No remote device contact or LAN listeners. Cache bounds repeated browser
        refreshes; the sampler serializes its own native hardware probes.
        """
        with self.lock:
            cached = self.compute_snapshot
        if cached is not None and time.monotonic() - cached[0] < 4.0:
            return cached[1]
        local = self.compute_sampler.sample()
        peers = []
        try:
            from peer_pairing import TrustStore
            records = TrustStore(self.directory.parent / "pairing")._read("paired_servers.json")
            for peer_id in sorted(records)[:32]:
                if isinstance(peer_id, str) and isinstance(records[peer_id], dict):
                    peers.append({"id": peer_id[:32], "status": "paired; telemetry unavailable"})
        except (OSError, ValueError, TypeError):
            pass
        report = {"local": local, "peers": peers, "network": "Local only; LAN sharing off by default",
                  "remote_telemetry": False}
        with self.lock:
            self.compute_snapshot = (time.monotonic(), report)
        return report

    def memory_view(self):
        from shared_memory import MAX_NOTES
        memory = self.shared_memory
        if memory is None:
            raise HttpError(503, "Shared memory is unavailable. Check the saved-history folder.")
        return {"enabled": memory.enabled, "notes": memory.notes, "max": MAX_NOTES}

    def save_memory(self, body):
        memory = self.shared_memory
        if memory is None:
            raise HttpError(503, "Shared memory is unavailable. Check the saved-history folder.")
        notes, enabled = body.get("notes"), body.get("enabled")
        with self.service.lock:
            memory.save(notes, enabled)
        return self.memory_view()

    def add_memory_note(self, note):
        memory = self.shared_memory
        if memory is None:
            raise HttpError(503, "Shared memory is unavailable. Check the saved-history folder.")
        if not isinstance(note, str):
            raise HttpError(400, "Enter a note to remember.")
        with self.service.lock:
            memory.add(note)  # ValueError (disabled, empty, too long) becomes a 400.
        return self.memory_view()

    def projects(self):
        seen, result = set(), []
        for chat in self.service.list(mode="dev"):
            path = chat.get("project_path") or ""
            if not path:
                continue
            result.append({"chat_id": chat["id"], "title": chat["title"], "project_path": path,
                           "updated_at": chat["updated_at"], "exists": Path(path).is_dir(),
                           "latest_for_folder": path not in seen})
            seen.add(path)
        return {"projects": result}

    def _project_root(self, chat_id):
        record = self.service.get(chat_id)
        # Only a folder the person chose for a developer chat, in the desktop window.
        if record["mode"] != "dev" or not record.get("project_path"):
            raise HttpError(404, "This chat has no project folder.")
        return record["project_path"]

    def project_files(self, chat_id):
        root = self._project_root(chat_id)
        try:
            return {"project_path": root, "files": developer_tools.list_project_files(root, limit=2000)}
        except ValueError as error:
            raise HttpError(404, str(error)) from None

    def project_file(self, chat_id, path):
        root = self._project_root(chat_id)
        try:
            content = developer_tools.read_project_file(root, path, max_bytes=MAX_PROJECT_FILE_BYTES)
        except ValueError as error:
            raise HttpError(404, str(error)) from None
        return {"path": path, "content": content}

    def gate_log(self):
        path = network_gate.log_path()
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - LOG_TAIL_BYTES))
                data = handle.read(LOG_TAIL_BYTES)
        except FileNotFoundError:
            return {"entries": [], "path": str(path)}
        except OSError as error:
            raise HttpError(503, f"Could not read the connection log: {error}") from None
        entries = []
        for line in data.decode("utf-8", errors="replace").splitlines()[-200:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # The first line of a tail may be cut.
            if isinstance(entry, dict):
                entries.append({key: str(entry.get(key, "")) for key in
                                ("time", "decision", "kind", "target", "purpose", "source", "scope")})
        return {"entries": entries, "path": str(path)}

    def set_engine(self, body):
        backend = body.get("backend")
        if backend not in settings.BACKENDS:
            raise HttpError(400, "Choose Ollama or Colibri.")
        with self.service.lock:
            if self.service.active:
                raise HttpError(409, "Wait for the answer in progress before switching engines.")
            from colibri_backend import normalize_url
            path = self.directory / "backend.settings"
            _, url = settings.load_backend(path, normalize_url)
            settings.save_backend(path, backend, url)
        return self.status()


class WorkspaceServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # Never share the port: on Windows SO_REUSEADDR would let another program bind it too.
    allow_reuse_address = False

    def __init__(self, workspace, host="127.0.0.1", port=0):
        if host not in LOOPBACK_HOSTS:
            raise ValueError("The web workspace only listens on this computer (127.0.0.1 or ::1).")
        self.address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        self.workspace = workspace
        self.static = _static_files()
        super().__init__((host, port), Handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # TCPServer's bind, without HTTPServer's reverse-DNS lookup of the address.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]

    @property
    def authority(self):
        host = "[::1]" if self.address_family == socket.AF_INET6 else "127.0.0.1"
        return f"{host}:{self.server_port}"

    @property
    def origin(self):
        return "http://" + self.authority


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AIAssist"
    sys_version = ""
    timeout = 120
    # While a model loads nothing streams; a heartbeat line also notices a closed page.
    HEARTBEAT_SECONDS = 15

    ROUTES = [
        ("POST", r"/api/session", "session", False),
        ("GET", r"/api/status", "status", True),
        ("GET", r"/api/models", "models", True),
        ("GET", r"/api/models/details", "model_details", True),
        ("GET", r"/api/hardware", "hardware", True),
        ("GET", r"/api/compute", "compute", True),
        ("GET", r"/api/chats", "list_chats", True),
        ("POST", r"/api/chats", "create_chat", True),
        ("GET", rf"/api/chats/{_CHAT}", "get_chat", True),
        ("PATCH", rf"/api/chats/{_CHAT}", "rename_chat", True),
        ("DELETE", rf"/api/chats/{_CHAT}", "delete_chat", True),
        ("POST", rf"/api/chats/{_CHAT}/messages", "send_message", True),
        ("POST", r"/api/requests/(?P<request>[0-9a-f]{16})/cancel", "cancel", True),
        ("GET", r"/api/memory", "get_memory", True),
        ("PUT", r"/api/memory", "put_memory", True),
        ("POST", r"/api/memory/notes", "add_memory_note", True),
        ("GET", r"/api/projects", "projects", True),
        ("GET", rf"/api/projects/{_CHAT}/files", "project_files", True),
        ("GET", rf"/api/projects/{_CHAT}/file", "project_file", True),
        ("GET", r"/api/log", "gate_log", True),
        ("PUT", r"/api/settings/engine", "set_engine", True),
    ]

    # Paths, queries and bodies can hold file names and search text: never logged.
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_OPTIONS(self):
        # No CORS: a preflight is never answered with permission.
        self.send_json(405, {"error": "Not allowed."})

    def do_HEAD(self):
        self.send_json(405, {"error": "Not allowed."})

    @property
    def workspace(self):
        return self.server.workspace

    # ------------------------------------------------------------ plumbing

    def dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        api = path.startswith("/api/")
        problem = request_problem(self.headers, self.server.authority, api)
        if problem is not None:
            self.send_json(problem[0], {"error": problem[1]})
            return
        if not api:
            self.serve_static(method, path)
            return
        self.query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        allowed = [route for route in self.ROUTES if re.fullmatch(route[1], path)]
        route = next((route for route in allowed if route[0] == method), None)
        if route is None:
            self.send_json(405 if allowed else 404, {"error": "Not allowed." if allowed else "Not found."})
            return
        _, pattern, name, needs_session = route
        if needs_session and not self.workspace.sessions.valid(self.headers.get("Authorization")):
            self.send_json(401, {"error": "This page's session ended. Open the link printed where AI Assist started."})
            return
        try:
            body = self.read_body() if method in ("POST", "PUT", "PATCH") else {}
            result = getattr(self, "api_" + name)(body, **re.fullmatch(pattern, path).groupdict())
            if result is not None:
                self.send_json(200, result)
        except HttpError as error:
            self.send_json(error.status, {"error": str(error)})
        except ServiceError as error:
            self.send_json(error.status, {"error": str(error)})
        except (ChatStoreError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
        except network_gate.InternetBlocked as error:
            self.send_json(403, {"error": str(error)})
        except (ConnectionError, TimeoutError):
            self.close_connection = True  # The page went away mid-answer.
        except OSError as error:
            self.send_json(500, {"error": f"AI Assist could not complete this: {error.strerror or error}"})
        except Exception:
            self.send_json(500, {"error": "AI Assist hit an unexpected problem. Details stay on this computer."})

    def read_body(self):
        if self.headers.get("Transfer-Encoding"):
            raise HttpError(411, "Send a Content-Length.")
        if self.headers.get_content_type() != "application/json":
            raise HttpError(415, "Send JSON.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise HttpError(400, "Invalid Content-Length.") from None
        if not 0 <= length <= MAX_BODY_BYTES:
            raise HttpError(413, "The request is too large.")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "Send valid JSON.") from None
        if not isinstance(body, dict):
            raise HttpError(400, "Send a JSON object.")
        return body

    def security_headers(self, policy):
        for name, value in COMMON_HEADERS:
            self.send_header(name, value)
        self.send_header("Content-Security-Policy", policy)

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.security_headers(API_POLICY)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if status >= 400:
            # A refused request's body may be unread; never reuse the connection after it.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, method, path):
        target = self.server.static.get(path)
        if method != "GET" or target is None:
            self.send_json(404 if method == "GET" else 405, {"error": "Not found." if method == "GET" else "Not allowed."})
            return
        data = target.read_bytes()
        self.send_response(200)
        self.security_headers(PAGE_POLICY)
        self.send_header("Content-Type", CONTENT_TYPES[target.suffix])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def param(self, name):
        values = self.query.get(name)
        return values[-1] if values else ""

    # ------------------------------------------------------------ endpoints

    def api_session(self, body):
        token = self.workspace.sessions.exchange(body.get("launch"))
        if token is None:
            raise HttpError(401, "This link was already used or has expired. Open the newest link printed where "
                                 "AI Assist started.")
        return {"token": token}

    def api_status(self, body):
        return self.workspace.status(self.param("model"))

    def api_models(self, body):
        return self.workspace.models()

    def api_model_details(self, body):
        return self.workspace.model_details(self.param("name"))

    def api_hardware(self, body):
        return self.workspace.hardware_report()

    def api_compute(self, body):
        return self.workspace.compute_report()

    def api_list_chats(self, body):
        mode = self.param("mode") or None
        return {"chats": self.workspace.service.list(self.param("query"), mode)}

    def api_create_chat(self, body):
        model = body.get("model", "")
        if not isinstance(model, str) or len(model) > 300:
            raise HttpError(400, "Choose a valid model name.")
        record = self.workspace.service.create(model, self.workspace.provider().name)
        return {"chat": record}

    def api_get_chat(self, body, chat):
        return {"chat": self.workspace.service.get(chat)}

    def api_rename_chat(self, body, chat):
        return {"chat": ChatService.summary(self.workspace.service.rename(chat, body.get("title")))}

    def api_delete_chat(self, body, chat):
        self.workspace.service.delete(chat)
        return {"deleted": chat}

    def api_cancel(self, body, request):
        cancel = self.workspace.requests.get(request)
        if cancel is None:
            raise HttpError(404, "That answer already finished.")
        cancel.set()
        return {"stopping": request}

    def api_get_memory(self, body):
        return self.workspace.memory_view()

    def api_put_memory(self, body):
        return self.workspace.save_memory(body)

    def api_add_memory_note(self, body):
        return self.workspace.add_memory_note(body.get("note"))

    def api_projects(self, body):
        return self.workspace.projects()

    def api_project_files(self, body, chat):
        return self.workspace.project_files(chat)

    def api_project_file(self, body, chat):
        return self.workspace.project_file(chat, self.param("path"))

    def api_gate_log(self, body):
        return self.workspace.gate_log()

    def api_set_engine(self, body):
        return self.workspace.set_engine(body)

    def api_send_message(self, body, chat):
        """Stream one answer as newline-delimited JSON. This handler thread is the
        only writer; the service's events reach it through a queue."""
        workspace = self.workspace
        text, model, context = body.get("content"), body.get("model", ""), body.get("context_length")
        if context is not None and (type(context) is not int or not 2048 <= context <= 1048576):
            raise HttpError(400, "Choose a context between 2,048 and 1,048,576 tokens, or the server default.")
        backend = workspace.provider().name
        workspace.service.prepare(chat, text, model, backend)
        request_id = secrets.token_hex(8)
        cancel = threading.Event()
        workspace.requests[request_id] = cancel
        events = queue.Queue()
        try:
            self.send_response(200)
            self.security_headers(API_POLICY)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.write_event({"type": "start", "request": request_id})
        except OSError:
            workspace.service.release(chat)
            workspace.requests.pop(request_id, None)
            raise
        threading.Thread(target=workspace.service.send, args=(chat, text, model, backend, events.put, cancel, context),
                         name="web-answer", daemon=True).start()
        connected = True
        try:
            while True:
                try:
                    event = events.get(timeout=self.HEARTBEAT_SECONDS)
                except queue.Empty:
                    event = {"type": "waiting"}
                if event["type"] == "done":
                    saved = event["chat"]
                    last = saved["messages"][-1] if saved["messages"] else None
                    event = {"type": "done", "stopped": event["stopped"], "chat": ChatService.summary(saved),
                             "message": last if last and last["role"] == "assistant" else None,
                             "local": event.get("local", False)}
                if connected:
                    try:
                        self.write_event(event)
                    except OSError:
                        # The page closed: stop generating; the partial answer is kept, as with Stop.
                        connected = False
                        cancel.set()
                if event["type"] in ("done", "error"):
                    return None
        finally:
            workspace.requests.pop(request_id, None)

    def write_event(self, event):
        self.wfile.write(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()
