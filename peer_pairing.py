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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class PairingHost:
    """HTTPS pairing endpoint ONLY. No inference or command endpoints exist."""
    def __init__(self, bind_ip, data_directory, *, name="AI Assist"):
        if not permitted_ipv4(bind_ip, loopback=True):
            raise PairingError("Bind to one explicit private LAN IPv4 address.")
        self.bind_ip = bind_ip
        self.store = TrustStore(data_directory)
        self.window = PairingWindow()
        self.cert, self.key, self.fingerprint = create_identity(data_directory)
        self.node_id = hashlib.sha256(self.fingerprint.encode()).hexdigest()[:24]
        self.name = name[:64]
        host = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass  # Never log secret request bodies.

            def do_POST(self):
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

        self.httpd = ThreadingHTTPServer((bind_ip, 0), Handler)
        self.httpd.daemon_threads = True
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
