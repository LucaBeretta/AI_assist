"""Manual, inference-only HTTPS node for paired AI Assist computers.

Run explicitly on the computer with Ollama/RTX; closing the process stops
sharing. Never imported as a listener. Pairing is performed separately with
peer_pairing.py. There is deliberately no file, command, browser or cloud API.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import ssl
import threading
import urllib.error
import urllib.request

from app_core import ollama
from app_core.privacy import is_cloud_model
from compute_telemetry import TelemetrySampler
from peer_pairing import TrustStore, PairingError, create_identity, permitted_ipv4

DEFAULT_PORT = 49382
MAX_REQUEST = 512 * 1024
MAX_RESPONSE_LINE = 1024 * 1024
MAX_GENERATED = 16 * 1024 * 1024
MODEL_NAME = re.compile(r"[A-Za-z0-9_.:/+-]{1,200}\Z")
OLLAMA = "http://127.0.0.1:11434"


def safe_model(name):
    return isinstance(name, str) and MODEL_NAME.fullmatch(name) is not None and not is_cloud_model(name)


def local_models():
    """Only locally installed non-cloud model names, no network registries."""
    return [name for name in ollama.list_models(OLLAMA) if safe_model(name)]


def model_is_local(name):
    """Reject cloud aliases by inspecting Ollama's own model metadata."""
    if not safe_model(name) or name not in local_models():
        return False
    try:
        info = ollama.api_json(OLLAMA, "/api/show", {"model": name}, timeout=5)
    except (OSError, RuntimeError, ValueError):
        return False
    return not bool(info.get("remote_host") or info.get("remote_model"))


def validate_chat(body):
    """Allow only Ollama text inference. Never accept arbitrary tool parameters."""
    if not isinstance(body, dict) or set(body) - {"model", "messages", "context_length"}:
        raise ValueError("Only model, messages and context_length are supported.")
    name, messages = body.get("model"), body.get("messages")
    if not safe_model(name):
        raise ValueError("Choose a locally installed, non-cloud model.")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 80:
        raise ValueError("Expected between 1 and 80 text messages.")
    total = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("Only text role/content messages are supported.")
        if message["role"] not in ("user", "assistant", "system") or not isinstance(message["content"], str):
            raise ValueError("Unsupported message role or content.")
        total += len(message["content"])
    if total > 240000:
        raise ValueError("Too much conversation context for LAN inference.")
    context = body.get("context_length")
    if context is not None and (type(context) is not int or not 2048 <= context <= 131072):
        raise ValueError("Invalid context window.")
    return name, messages, context


class InferenceNode:
    """An explicitly started TLS listener with authenticated inference routes."""

    def __init__(self, bind, directory, port=DEFAULT_PORT, *, sampler=None):
        if not permitted_ipv4(bind, loopback=True):
            raise PairingError("Choose an explicit private LAN interface, not a public address.")
        if type(port) is not int or not 1 <= port <= 65535:
            raise PairingError("Invalid LAN inference port.")
        self.store = TrustStore(directory)
        if not self.store._read("paired_clients.json"):
            raise PairingError("Pair a client first; no device has permission to use inference.")
        cert, key, fingerprint = create_identity(directory)
        self.fingerprint = fingerprint
        self.node_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
        self.sampler = sampler or TelemetrySampler(OLLAMA)
        self.active = threading.Event()
        node = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "AI-Assist-Inference"
            sys_version = ""
            timeout = 15

            def log_message(self, *_args):
                pass  # Never log prompts, credentials or URLs.

            def send_data(self, status, obj):
                raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(raw)

            def allowed(self):
                # Private IPv4 and loopback only, browser origins always denied.
                if (not permitted_ipv4(self.client_address[0], loopback=True)
                        or self.headers.get("Origin") is not None
                        or self.headers.get("Sec-Fetch-Site") is not None
                        or self.headers.get("Transfer-Encoding") is not None):
                    self.send_data(403, {"error": "Untrusted LAN request."})
                    return False
                client_id = self.headers.get("X-AI-Assist-Client-ID")
                auth = self.headers.get("Authorization", "")
                if not isinstance(auth, str) or not auth.startswith("Bearer "):
                    self.send_data(401, {"error": "Pair the requesting device first."})
                    return False
                if not node.store.authenticate_client(client_id, auth[7:]):
                    self.send_data(401, {"error": "Pairing was not authorized or was revoked."})
                    return False
                return True

            def do_GET(self):
                if not node.active.is_set() or not self.allowed():
                    return
                try:
                    if self.path == "/v1/health":
                        # Fast, authenticated health check: no model download,
                        # filesystem read, or expensive Ollama probe.
                        self.send_data(200, {"node_id": node.node_id, "sharing": True,
                                             "service": "inference-only"})
                    elif self.path == "/v1/models":
                        self.send_data(200, {"models": local_models()})
                    elif self.path == "/v1/running":
                        running = ollama.running_details(ollama.api_json(OLLAMA, "/api/ps"))
                        self.send_data(200, {"running": {k: v for k, v in running.items() if safe_model(k)}})
                    elif self.path == "/v1/telemetry":
                        self.send_data(200, {"node_id": node.node_id, "metrics": node.sampler.sample()})
                    else:
                        self.send_data(404, {"error": "Endpoint not found."})
                except (OSError, ValueError, RuntimeError):
                    self.send_data(503, {"error": "Local Ollama or hardware metrics unavailable."})

            def do_POST(self):
                if not node.active.is_set() or not self.allowed():
                    return
                if self.path != "/v1/chat":
                    self.send_data(404, {"error": "Endpoint not found."})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                    if not 1 <= size <= MAX_REQUEST:
                        raise ValueError("Request exceeds allowed size.")
                    body = json.loads(self.rfile.read(size))
                    name, messages, context = validate_chat(body)
                except (TypeError, ValueError, UnicodeError):
                    self.send_data(400, {"error": "Invalid inference-only JSON request."})
                    return
                if not model_is_local(name):
                    self.send_data(403, {"error": "Not an installed local-only Ollama model."})
                    return
                payload = {"model": name, "messages": messages, "stream": True,
                           "keep_alive": -1}
                if context is not None:
                    payload["options"] = {"num_ctx": context}
                request = urllib.request.Request(OLLAMA + "/api/chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    upstream = urllib.request.urlopen(request, timeout=120)
                except (OSError, urllib.error.URLError, ValueError):
                    self.send_data(503, {"error": "Local Ollama could not start inference."})
                    return
                with upstream:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    consumed = 0
                    try:
                        while node.active.is_set():
                            line = upstream.readline(MAX_RESPONSE_LINE + 1)
                            consumed += len(line)
                            if (not line or len(line) > MAX_RESPONSE_LINE or consumed > MAX_GENERATED):
                                raise ValueError("Ollama stream incomplete or too large.")
                            obj = json.loads(line)
                            if not isinstance(obj, dict) or obj.get("error"):
                                raise ValueError("Ollama inference failed.")
                            message = obj.get("message")
                            piece = message.get("content", "") if isinstance(message, dict) else ""
                            if not isinstance(piece, str):
                                raise ValueError("Ollama returned an invalid chunk.")
                            event = {"type": "chunk", "text": piece} if piece else None
                            if event is not None:
                                self.wfile.write(json.dumps(event).encode() + b"\n")
                                self.wfile.flush()
                            if obj.get("done") is True:
                                metrics = {key: obj[key] for key in
                                           ("eval_count", "eval_duration", "prompt_eval_count", "prompt_eval_duration")
                                           if type(obj.get(key)) is int and 0 <= obj[key]}
                                self.wfile.write(json.dumps({"type": "done", "metrics": metrics}).encode() + b"\n")
                                self.wfile.flush()
                                return
                        raise ValueError("Sharing was stopped.")
                    except (OSError, ValueError, TypeError):
                        # If the peer disconnected, stop reading and close upstream.
                        try:
                            self.wfile.write(b'{"type":"error","error":"Inference interrupted."}\n')
                            self.wfile.flush()
                        except OSError:
                            pass

        self.httpd = ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        self.active.set()
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name="manual-lan-inference", daemon=True)
        self.thread.start()

    def stop(self):
        self.active.clear()
        if self.thread is not None:
            self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manually serve paired AI inference (Ctrl+C stops).")
    parser.add_argument("--bind", required=True, help="This Windows computer's private LAN IPv4")
    parser.add_argument("--data-dir", required=True, help="Same private directory used when pairing")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    node = InferenceNode(args.bind, args.data_dir, args.port)
    node.start()
    try:
        print(f"LAN inference ON at {args.bind}:{node.port} (encrypted; paired devices only)")
        print("Only text inference, model listings and read-only telemetry are available.")
        print("Press Ctrl+C to stop and close the listener.")
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        print("LAN inference OFF")


if __name__ == "__main__":
    main()
