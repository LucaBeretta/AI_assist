"""Opt-in LAN discovery and verified pairing for AI Assist.

This module does NOT expose inference, files or commands. Discovery records
are untrusted hints. To pair, compare the certificate verification code shown
on the *target* device, then submit its short-lived PIN over the pinned TLS
connection. Hosts opt in explicitly; no listeners start on import.

Cryptography is optional for the rest of AI Assist, but needed to create a
node's own TLS identity: python -m pip install cryptography
"""

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import peer_inference_contract as contract

DISCOVERY_PORT = 49381
MAGIC = "ai-assist-discovery"
PROTOCOL = 1
MAX_PACKET = 1024
MAX_REQUEST = 512
PAIRING_TTL = 120
ATTEMPTS = 3
_PRIVATE = tuple(ipaddress.ip_network(n) for n in
                 ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class PairingError(ValueError):
    pass


def permitted_ipv4(address, *, loopback=False):
    """Only literal private IPv4 addresses; never DNS or public destinations."""
    try:
        ip = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return (any(ip in net for net in _PRIVATE) or
            (loopback and ip.is_loopback))


def _safe_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".pairing-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True)
        if os.name != "nt":
            os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class TrustStore:
    """Local trust records; tokens on clients, token digests on hosts."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def _read(self, file):
        path = self.directory / file
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}

    def save_client(self, peer_id, fingerprint, token, address, port):
        if not _safe_id(peer_id) or len(fingerprint) != 64 or not token:
            raise PairingError("Invalid paired peer record.")
        if not permitted_ipv4(address, loopback=True):
            raise PairingError("Refusing a non-local peer address.")
        with self._lock:
            data = self._read("paired_servers.json")
            data[peer_id] = {"fingerprint": fingerprint, "token": token,
                             "address": address, "port": port}
            _atomic_json(self.directory / "paired_servers.json", data)

    def save_server(self, peer_id, token):
        if not _safe_id(peer_id):
            raise PairingError("Invalid client identity.")
        with self._lock:
            data = self._read("paired_clients.json")
            data[peer_id] = hashlib.sha256(token.encode("utf-8")).hexdigest()
            _atomic_json(self.directory / "paired_clients.json", data)

    def revoke(self, peer_id, *, server=False):
        with self._lock:
            file = "paired_clients.json" if server else "paired_servers.json"
            data = self._read(file)
            data.pop(peer_id, None)
            _atomic_json(self.directory / file, data)
            if server:
                # A device that is no longer paired loses any AI permission with it.
                consent = self._read("ai_clients.json")
                if consent.pop(peer_id, None) is not None:
                    _atomic_json(self.directory / "ai_clients.json", consent)

    def allow_ai(self, peer_id, allowed=True):
        """Let one paired device use this computer's shared AI, or stop it. Pairing alone never does."""
        if not _safe_id(peer_id):
            raise PairingError("Invalid client identity.")
        with self._lock:
            if allowed and peer_id not in self._read("paired_clients.json"):
                raise PairingError("Pair this device before allowing it to use this computer's AI.")
            consent = self._read("ai_clients.json")
            if allowed:
                consent[peer_id] = True
            else:
                consent.pop(peer_id, None)
            _atomic_json(self.directory / "ai_clients.json", consent)

    def ai_allowed(self, peer_id):
        with self._lock:
            return _safe_id(peer_id) and self._read("ai_clients.json").get(peer_id) is True

    def client_record(self, peer_id):
        with self._lock:
            return self._read("paired_servers.json").get(peer_id)

    def authenticate_client(self, peer_id, token):
        if not _safe_id(peer_id) or not isinstance(token, str):
            return False
        with self._lock:
            expected = self._read("paired_clients.json").get(peer_id)
        if not isinstance(expected, str):
            return False
        return hmac.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest())


def create_identity(directory):
    """Generate an ECDSA TLS server identity only when sharing is enabled."""
    directory = Path(directory)
    cert_path, key_path = directory / "node-cert.pem", directory / "node-key.pem"
    if cert_path.exists() != key_path.exists():
        raise PairingError("Incomplete node identity. Restore the matching key and certificate.")
    if not cert_path.exists():
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
        except ImportError as exc:
            raise PairingError("To enable LAN pairing, install the optional 'cryptography' package.") from exc
        from datetime import datetime, timedelta, timezone
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI Assist LAN node")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
        # Never commit this directory: use a user-specific app data path.
        for path, body in (
            (key_path, key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())),
            (cert_path, cert.public_bytes(serialization.Encoding.PEM)),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="ascii"))
    fingerprint = hashlib.sha256(der).hexdigest()
    return cert_path, key_path, fingerprint


def verification_code(fingerprint):
    """16 hexadecimal characters (64 bits), checked against the target screen."""
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise PairingError("Invalid TLS certificate fingerprint.")
    return fingerprint[:16].upper()


class PairingWindow:
    def __init__(self):
        self._lock = threading.Lock()
        self._pin = None
        self._expiry = 0.0
        self._attempts = 0

    def open(self, seconds=PAIRING_TTL):
        if seconds <= 0 or seconds > 600:
            raise PairingError("Invalid pairing window.")
        with self._lock:
            self._pin = f"{secrets.randbelow(1_000_000):06d}"
            self._expiry = time.monotonic() + seconds
            self._attempts = ATTEMPTS
            return self._pin

    def verify_once(self, pin):
        with self._lock:
            if self._pin is None or time.monotonic() >= self._expiry or self._attempts <= 0:
                return False
            self._attempts -= 1
            if not isinstance(pin, str) or not hmac.compare_digest(self._pin, pin):
                if self._attempts == 0:
                    self._pin = None
                return False
            self._pin = None
            self._expiry = 0.0
            return True


def _json_packet(data):
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def parse_advertisement(raw, source):
    """Discovery is untrusted: never turn this data into a paired identity."""
    if len(raw) > MAX_PACKET or not permitted_ipv4(source):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if (not isinstance(obj, dict) or obj.get("magic") != MAGIC or
            type(obj.get("version")) is not int or obj["version"] != PROTOCOL or
            not _safe_id(obj.get("node_id")) or
            type(obj.get("port")) is not int or not 1 <= obj["port"] <= 65535):
        return None
    return {"address": source, "node_id": obj["node_id"], "port": obj["port"],
            "name": str(obj.get("name", ""))[:64], "verified": False}


class DiscoveryResponder:
    """Starts listening only when the host explicitly shares its LAN identity."""
    def __init__(self, node_id, name, port):
        if not _safe_id(node_id) or not 1 <= port <= 65535:
            raise PairingError("Invalid discovery node.")
        self.node_id, self.name, self.port = node_id, name[:64], port
        self._done = threading.Event()
        self._socket = None
        self._thread = None

    def start(self):
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", DISCOVERY_PORT))
            sock.settimeout(0.3)
        except BaseException:
            sock.close()
            raise
        self._socket = sock
        self._done.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _listen(self):
        response = _json_packet({"magic": MAGIC, "version": PROTOCOL,
                                 "node_id": self.node_id, "name": self.name,
                                 "port": self.port})
        while not self._done.is_set():
            try:
                payload, (address, source_port) = self._socket.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if payload != _json_packet({"magic": MAGIC, "version": PROTOCOL, "discover": True}):
                continue
            if not permitted_ipv4(address):
                continue
            try:
                self._socket.sendto(response, (address, source_port))
            except OSError:
                pass

    def stop(self):
        self._done.set()
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


def discover(timeout=1.5):
    """One user-initiated IPv4 LAN broadcast; returns UNVERIFIED candidates."""
    if not 0.1 <= timeout <= 10:
        raise ValueError("Timeout must be between 0.1 and 10 seconds.")
    results = {}
    query = _json_packet({"magic": MAGIC, "version": PROTOCOL, "discover": True})
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.sendto(query, ("255.255.255.255", DISCOVERY_PORT))
        stop_at = time.monotonic() + timeout
        while time.monotonic() < stop_at:
            sock.settimeout(max(.01, stop_at - time.monotonic()))
            try:
                payload, (address, _) = sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                break
            candidate = parse_advertisement(payload, address)
            if candidate:
                results[(candidate["address"], candidate["port"])] = candidate
    return list(results.values())


USAGE_MIN_INTERVAL = 1.0
# Shared AI: what a paired device may ask this computer's Ollama, and nothing else.
# Downloading, deleting or creating models, and Ollama's cloud models, are never offered.
AI_ROUTES = {("GET", "/api/tags"), ("GET", "/api/version"), ("POST", "/api/show"), ("POST", "/api/chat")}
MAX_AI_REQUEST = contract.MAX_REQUEST_BYTES
AI_TIMEOUT = 600
AI_CONCURRENT = 2
MAX_USAGE_BYTES = 8192


def _finite(value, low=0.0, high=None):
    if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
        return None
    if value < low or (high is not None and value > high):
        return None
    return value


def sanitize_usage(value):
    """The only usage fields that cross the network, re-validated on both sides.

    CPU, RAM and GPU load, the device and model names: no chats, files, prompts,
    paths or settings. Unknown readings stay None rather than becoming zero."""
    if not isinstance(value, dict):
        raise PairingError("Invalid usage report.")
    cpu, ram = value.get("cpu") or {}, value.get("ram") or {}
    if not isinstance(cpu, dict) or not isinstance(ram, dict):
        raise PairingError("Invalid usage report.")
    total = _finite(ram.get("total_bytes"), 1)
    available = _finite(ram.get("available_bytes"))
    if total is None or available is None or available > total:
        total = available = None
    gpus = []
    for gpu in (value.get("gpus") or [])[:2]:
        if not isinstance(gpu, dict):
            continue
        gpu_total = _finite(gpu.get("total_bytes"), 1)
        used = _finite(gpu.get("used_bytes"))
        if gpu_total is None or used is None or used > gpu_total:
            gpu_total = used = None
        gpus.append({"name": str(gpu.get("name") or "GPU")[:96], "percent": _finite(gpu.get("percent"), 0, 100),
                     "used_bytes": used, "total_bytes": gpu_total})
    loaded = [str(item)[:96] for item in (value.get("loaded") or [])[:8] if isinstance(item, str)]
    return {"name": str(value.get("name") or "")[:64], "system": str(value.get("system") or "")[:32],
            "time": _finite(value.get("time")),
            "cpu": {"name": str(cpu.get("name") or "")[:96], "percent": _finite(cpu.get("percent"), 0, 100)},
            "ram": {"total_bytes": total, "available_bytes": available}, "gpus": gpus, "loaded": loaded}


def _shared_ai_body(route, request):
    """The only bodies a paired device may send to a shared Ollama, rebuilt here field by field."""
    if route == "/api/chat":
        valid = contract.validate_request(request)
        return {"model": valid["model"], "messages": valid["messages"], "stream": True}
    if not isinstance(request, dict) or set(request) != {"model"}:
        raise contract.InvalidInferenceRequest("Only a model name is accepted.")
    model = request["model"]
    if not isinstance(model, str) or contract._MODEL_RE.fullmatch(model) is None or "://" in model:
        raise contract.InvalidInferenceRequest("Invalid installed model identifier.")
    return {"model": model}


def _cloud_model(name):
    lowered = name.lower()
    return lowered.endswith("-cloud") or lowered.endswith(":cloud")


class PairingHost:
    """HTTPS pairing endpoint, plus opt-in services for paired clients.

    ``usage`` is a callable returning this computer's load; when it is None, /usage
    does not exist. ``ollama`` is this computer's Ollama URL (loopback only); when it
    is None, /ollama/... does not exist. When set, paired clients may list models
    and chat (AI_ROUTES) through the pinned TLS connection, and nothing else: no
    files, commands, downloads or model changes."""
    def __init__(self, bind_ip, data_directory, *, name="AI Assist", usage=None, port=0, ollama=None):
        if not permitted_ipv4(bind_ip, loopback=True):
            raise PairingError("Bind to one explicit private LAN IPv4 address.")
        self.bind_ip = bind_ip
        self.store = TrustStore(data_directory)
        self.window = PairingWindow()
        self.cert, self.key, self.fingerprint = create_identity(data_directory)
        self.node_id = hashlib.sha256(self.fingerprint.encode()).hexdigest()[:24]
        self.name = name[:64]
        self.usage = usage
        if ollama is not None:
            parsed = urllib.parse.urlsplit(ollama)
            if parsed.scheme != "http" or not permitted_ipv4(parsed.hostname or "", loopback=True) \
                    or not ipaddress.IPv4Address(parsed.hostname).is_loopback:
                raise PairingError("Shared AI forwards only to Ollama on this computer (http://127.0.0.1:port).")
            ollama = f"http://{parsed.hostname}:{parsed.port or 11434}"
        self.ollama = ollama
        self._ai_slots = threading.BoundedSemaphore(AI_CONCURRENT)
        self._usage_lock = threading.Lock()
        self._usage_seen = {}
        host = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass  # Never log secret request bodies.

            def _paired_client(self):
                """The paired client's ID when this request carries its secret token, else None."""
                if self.headers.get("Origin") or not permitted_ipv4(self.client_address[0], loopback=True):
                    return None
                client = self.headers.get("X-AI-Assist-Client", "")
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                return client if host.store.authenticate_client(client, token) else None

            def _json_reply(self, status, data):
                payload = _json_packet(data)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _shared_ai(self, method):
                route = self.path[len("/ollama"):]
                if host.ollama is None or (method, route) not in AI_ROUTES:
                    self.send_error(404)
                    return
                client = self._paired_client()
                if client is None:
                    self.send_error(403)
                    return
                if not host.store.ai_allowed(client):
                    self._json_reply(403, {"error": "This computer has not allowed this device to use its AI."})
                    return
                body = None
                if method == "POST":
                    try:
                        size = int(self.headers.get("Content-Length", "-1"))
                    except ValueError:
                        size = -1
                    if size < 2 or size > MAX_AI_REQUEST:
                        self.send_error(413)
                        return
                    try:
                        # Data only (peer_inference_contract): plain-text messages, never tools,
                        # files, commands or Ollama options. This computer writes Ollama's request itself.
                        request = json.loads(self.rfile.read(size))
                        forward_body = _shared_ai_body(route, request)
                    except (UnicodeError, ValueError, RecursionError) as error:
                        # RecursionError: JSON nested too deeply for Python to read.
                        self._json_reply(400, {"error": str(error) if isinstance(error, contract.InvalidInferenceRequest)
                                               else "The request is not valid JSON."})
                        return
                    if _cloud_model(forward_body["model"]):
                        self._json_reply(403, {"error": "Cloud models are not shared. Choose a model on this computer."})
                        return
                    body = json.dumps(forward_body).encode("utf-8")
                if not host._ai_slots.acquire(blocking=False):
                    self._json_reply(429, {"error": "This computer is already answering other devices. Try again shortly."})
                    return
                try:
                    # Answers can take minutes; the pairing handler's short timeout is for pairing only.
                    self.connection.settimeout(AI_TIMEOUT)
                    forward = urllib.request.Request(host.ollama + route, data=body, method=method,
                                                     headers={"Content-Type": "application/json"} if body else {})
                    try:
                        upstream = urllib.request.urlopen(forward, timeout=AI_TIMEOUT)
                    except urllib.error.HTTPError as error:
                        detail = error.read(4096)
                        error.close()
                        self.send_response(error.code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(detail)))
                        self.end_headers()
                        self.wfile.write(detail)
                        return
                    except (OSError, urllib.error.URLError):
                        self._json_reply(502, {"error": "Ollama is not running on the shared computer."})
                        return
                    with upstream:
                        self.send_response(200)
                        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        # Stream as Ollama writes, so the reply appears word by word on the other device.
                        while True:
                            chunk = upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (OSError, ssl.SSLError):
                    pass  # The other device hung up or stopped the answer.
                finally:
                    host._ai_slots.release()

            def do_GET(self):
                if self.path.startswith("/ollama/"):
                    self._shared_ai("GET")
                    return
                # Live usage for paired clients only: TLS, a paired ID and its secret token.
                if self.path != "/usage" or host.usage is None:
                    self.send_error(404)
                    return
                client = self._paired_client()
                if client is None:
                    self.send_error(403)
                    return
                now = time.monotonic()
                with host._usage_lock:
                    if now - host._usage_seen.get(client, -USAGE_MIN_INTERVAL) < USAGE_MIN_INTERVAL:
                        self.send_error(429)
                        return
                    host._usage_seen[client] = now
                try:
                    payload = _json_packet(sanitize_usage(host.usage()))
                except Exception:
                    self.send_error(503)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.path.startswith("/ollama/"):
                    self._shared_ai("POST")
                    return
                if (self.path != "/pair" or self.headers.get("Origin") or
                        self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"):
                    self.send_error(403)
                    return
                if not permitted_ipv4(self.client_address[0], loopback=True):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    size = -1
                if size < 1 or size > MAX_REQUEST:
                    self.send_error(413)
                    return
                try:
                    obj = json.loads(self.rfile.read(size))
                except (UnicodeError, ValueError):
                    self.send_error(400)
                    return
                if not isinstance(obj, dict) or not _safe_id(obj.get("client_id")):
                    self.send_error(400)
                    return
                if not host.window.verify_once(obj.get("pin")):
                    self.send_error(403)
                    return
                token = secrets.token_urlsafe(32)
                host.store.save_server(obj["client_id"], token)
                payload = _json_packet({"node_id": host.node_id, "token": token})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        # Keep the previous port when it is free, so paired clients find this host again.
        try:
            self.httpd = ThreadingHTTPServer((bind_ip, port if type(port) is int and 1024 <= port <= 65535 else 0), Handler)
        except OSError:
            self.httpd = ThreadingHTTPServer((bind_ip, 0), Handler)
        self.httpd.daemon_threads = True
        # A client that hangs up after a failed certificate check is expected: do not print it.
        self.httpd.handle_error = lambda request, client_address: None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(self.cert), str(self.key))
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.responder = DiscoveryResponder(self.node_id, name, self.port)
        self._thread = None

    def start(self, *, advertise=True):
        if self._thread:
            return
        # Fail closed if discovery could not start.
        if advertise and not ipaddress.IPv4Address(self.bind_ip).is_loopback:
            self.responder.start()
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self.responder.stop()
        self.httpd.shutdown() if self._thread else None
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None


def pair_with_host(address, port, target_verification_code, pin, client_id, store):
    """TLS-pin the target *before* submitting any PIN; never trust UDP metadata."""
    if not permitted_ipv4(address, loopback=True) or type(port) is not int or not 1 <= port <= 65535:
        raise PairingError("Refusing a non-LAN address or invalid port.")
    if not _safe_id(client_id):
        raise PairingError("Invalid client ID.")
    if not isinstance(target_verification_code, str) or not re.fullmatch(
            r"[0-9A-Fa-f]{16}", target_verification_code):
        raise PairingError("Enter the 16-character code shown on the target device.")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6}", pin):
        raise PairingError("Enter the six-digit PIN shown on the target device.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Next line explicitly pins the out-of-band certificate.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(address, port, timeout=5, context=context)
    try:
        conn.connect()
        fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(verification_code(fingerprint),
                                   target_verification_code.upper()):
            raise PairingError("Target certificate code differs. Stop: possible spoofing.")
        body = _json_packet({"client_id": client_id, "pin": pin})
        conn.request("POST", "/pair", body=body, headers={"Content-Type": "application/json",
                                                           "Accept": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise PairingError(f"Pairing rejected (HTTP {response.status}).")
        data = response.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise PairingError("Pairing response too large.")
        payload = json.loads(data)
        if not isinstance(payload, dict) or not _safe_id(payload.get("node_id")) or not isinstance(payload.get("token"), str) or len(payload["token"]) < 32:
            raise PairingError("Invalid pairing response.")
        store.save_client(payload["node_id"], fingerprint, payload["token"], address, port)
        return payload["node_id"]
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, PairingError):
            raise
        raise PairingError(f"Pairing connection failed: {exc}") from exc
    finally:
        conn.close()


def fetch_usage(record, client_id, timeout=2.0):
    """Ask one paired host for its usage. The TLS certificate must match the one
    pinned at pairing time before the token is sent."""
    if not isinstance(record, dict):
        raise PairingError("Invalid paired peer record.")
    address, port, token, pinned = (record.get(key) for key in ("address", "port", "token", "fingerprint"))
    if not permitted_ipv4(address, loopback=True) or type(port) is not int or not 1 <= port <= 65535:
        raise PairingError("Refusing a non-LAN address or invalid port.")
    if not _safe_id(client_id) or not isinstance(token, str) or not isinstance(pinned, str) or len(pinned) != 64:
        raise PairingError("Invalid paired peer record.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Pinned explicitly below, before any credential is sent.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(address, port, timeout=timeout, context=context)
    try:
        conn.connect()
        fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(fingerprint, pinned):
            raise PairingError("This device's certificate changed. Pair it again.")
        conn.request("GET", "/usage", headers={"X-AI-Assist-Client": client_id,
                                               "Authorization": "Bearer " + token, "Accept": "application/json"})
        response = conn.getresponse()
        if response.status == 404:
            raise UsageUnavailable("Usage sharing is off on this device.")
        if response.status != 200:
            raise PairingError(f"Usage request rejected (HTTP {response.status}).")
        data = response.read(MAX_USAGE_BYTES + 1)
        if len(data) > MAX_USAGE_BYTES:
            raise PairingError("Usage report too large.")
        return sanitize_usage(json.loads(data))
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, PairingError):
            raise
        raise PeerOffline(f"Could not reach this device: {exc}") from exc
    finally:
        conn.close()


def open_shared_ai(record, client_id, method, path, body=None, timeout=AI_TIMEOUT):
    """Send one request to a paired computer's shared Ollama and return the open response.

    The TLS certificate must match the one pinned at pairing time before the token
    is sent. The caller reads the response (it streams for /api/chat) and closes it.
    """
    if (method, path) not in AI_ROUTES:
        raise PairingError("Only listing models and chatting are shared.")
    if not isinstance(record, dict):
        raise PairingError("Invalid paired peer record.")
    address, port, token, pinned = (record.get(key) for key in ("address", "port", "token", "fingerprint"))
    if not permitted_ipv4(address, loopback=True) or type(port) is not int or not 1 <= port <= 65535:
        raise PairingError("Refusing a non-LAN address or invalid port.")
    if not _safe_id(client_id) or not isinstance(token, str) or not isinstance(pinned, str) or len(pinned) != 64:
        raise PairingError("Invalid paired peer record.")
    payload = None
    if method == "POST":
        # Refuse here what the shared computer would refuse, before connecting.
        payload = json.dumps(contract.validate_request(body) if path == "/api/chat"
                             else _shared_ai_body(path, body)).encode("utf-8")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Pinned explicitly below, before any credential is sent.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(address, port, timeout=timeout, context=context)
    try:
        conn.connect()
        fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(fingerprint, pinned):
            raise PairingError("This device's certificate changed. Pair it again.")
        headers = {"X-AI-Assist-Client": client_id, "Authorization": "Bearer " + token, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, "/ollama" + path, body=payload, headers=headers)
        response = conn.getresponse()
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        conn.close()
        raise PeerOffline(f"Could not reach this device: {exc}") from exc
    except PairingError:
        conn.close()
        raise
    if response.status == 200:
        return response
    detail = response.read(4096)
    conn.close()
    try:
        message = json.loads(detail).get("error")
    except (ValueError, AttributeError):
        message = None
    # Ollama's own errors (such as an unknown model) carry a JSON message; a plain 404 means sharing is off.
    if response.status == 404 and not message:
        raise AIUnavailable("This device is not sharing its AI right now.")
    raise PairingError(message or f"The shared computer refused the request (HTTP {response.status}).")


class AIUnavailable(PairingError):
    pass


class UsageUnavailable(PairingError):
    pass


class PeerOffline(PairingError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI Assist opt-in LAN pairing prototype")
    sub = parser.add_subparsers(dest="cmd", required=True)
    host = sub.add_parser("host", help="Advertise and open short-lived pairing window")
    host.add_argument("--bind", required=True, help="Private LAN IPv4 address on this machine")
    host.add_argument("--data-dir", required=True, help="PRIVATE directory outside the repository")
    host.add_argument("--name", default="AI Assist")
    sub.add_parser("discover", help="Broadcast one opt-in local discovery query")
    peer = sub.add_parser("pair", help="Pair with a trusted discovered target")
    peer.add_argument("--address", required=True)
    peer.add_argument("--port", required=True, type=int)
    peer.add_argument("--data-dir", required=True)
    peer.add_argument("--client-id", required=True)
    peer.add_argument("--verify", required=True, help="16-digit target screen verification code")
    args = parser.parse_args(argv)

    if args.cmd == "discover":
        for candidate in discover():
            print(f'{candidate["address"]}:{candidate["port"]}  {candidate["name"]}  [UNVERIFIED]')
        return
    if args.cmd == "pair":
        import getpass
        pin = getpass.getpass("Six-digit PIN shown on TARGET device: ")
        peer_id = pair_with_host(args.address, args.port, args.verify, pin,
                                 args.client_id, TrustStore(args.data_dir))
        print("Paired with:", peer_id, "(no inference service enabled)")
        return

    service = PairingHost(args.bind, args.data_dir, name=args.name)
    service.start()
    try:
        print(f"Sharing enabled on {args.bind}:{service.port}")
        print("Certificate verification code:", verification_code(service.fingerprint))
        print("Pairing PIN (expires after 120s):", service.window.open())
        print("Press Enter to open a new PIN window; Ctrl+C to stop sharing.")
        while True:
            input()
            print("New pairing PIN (expires after 120s):", service.window.open())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        service.stop()


if __name__ == "__main__":
    main()
