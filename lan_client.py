"""Pinned, authenticated LAN inference client for a paired AI Assist node.

Only connects to a previously paired private IPv4 peer. No discovery, network
listeners or Internet destinations. Each HTTPS connection verifies the entire
certificate BEFORE transmitting the pairing token or any conversation text.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import ssl

from app_core.streaming import StreamStopped
from app_core.providers import ProviderInfo
from lan_inference import DEFAULT_PORT, MAX_REQUEST, MAX_RESPONSE_LINE, MAX_GENERATED, safe_model, validate_chat
from paired_node_watch import CLIENT_ID_RE, NodeUnavailable, pairing_directory, private_peer_record
from peer_pairing import TrustStore

MAX_JSON = 128 * 1024


class LANInferenceError(RuntimeError):
    """Paired node cannot complete this inference request."""


class PairedLANProvider:
    """ChatProvider for a user-selected, already paired inference-only node."""

    name = "Paired LAN"

    def __init__(self, peer_id, client_id, *, store=None, port=DEFAULT_PORT, timeout=10):
        if not isinstance(client_id, str) or CLIENT_ID_RE.fullmatch(client_id) is None:
            raise LANInferenceError("Enter the client ID used when pairing this Mac.")
        self.store = store if store is not None else TrustStore(pairing_directory())
        record = self.store.client_record(peer_id)
        if record is None:
            raise LANInferenceError("The requested LAN node has not been paired on this Mac.")
        try:
            address, fingerprint, token = private_peer_record(peer_id, record)
        except NodeUnavailable as error:
            raise LANInferenceError(str(error)) from None
        expected = hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        if not hmac.compare_digest(peer_id, expected):
            raise LANInferenceError("Saved node ID and certificate do not match; pair again.")
        if type(port) is not int or not 1 <= port <= 65535:
            raise LANInferenceError("Invalid private LAN inference port.")
        self.address, self.fingerprint, self.token = address, fingerprint, token
        self.peer_id, self.client_id, self.port, self.timeout = peer_id, client_id, port, timeout

    def info(self):
        return ProviderInfo(self.name, f"https://{self.address}:{self.port}", False, False)

    def _connect(self, *, timeout=None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Out-of-band full certificate pin below.
        conn = http.client.HTTPSConnection(
            self.address, self.port, timeout=timeout or self.timeout, context=context)
        try:
            conn.connect()
            cert = conn.sock.getpeercert(binary_form=True)
            if not cert or not hmac.compare_digest(hashlib.sha256(cert).hexdigest(), self.fingerprint):
                raise LANInferenceError("Node certificate changed. Stop and pair Windows again.")
            return conn
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            conn.close()
            raise LANInferenceError("The paired Windows inference node cannot be reached.") from error
        except BaseException:
            conn.close()
            raise

    def _response(self, method, path, data=None, *, timeout=None):
        # Path is controlled entirely by this class. Never follow redirects.
        conn = self._connect(timeout=timeout)
        headers = {
            "Authorization": "Bearer " + self.token,
            "X-AI-Assist-Client-ID": self.client_id,
            "Accept": "application/json" if data is None else "application/x-ndjson",
            "Connection": "close",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise LANInferenceError(
                    "The paired node refused the request (HTTP %d)." % response.status)
            return conn, response
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            conn.close()
            raise LANInferenceError("LAN inference connection was interrupted.") from error
        except BaseException:
            conn.close()
            raise

    def _json_get(self, path, limit=MAX_JSON):
        conn, response = self._response("GET", path)
        try:
            if response.getheader("Content-Type", "").split(";")[0].strip() != "application/json":
                raise LANInferenceError("Unexpected response type from the paired node.")
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise LANInferenceError("Paired-node response exceeded the limit.")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise LANInferenceError("Invalid paired-node response.")
            return payload
        except (ValueError, UnicodeError, OSError) as error:
            raise LANInferenceError("Invalid or incomplete response from the paired node.") from error
        finally:
            conn.close()

    def health(self):
        info = self._json_get("/v1/health", 4096)
        if not (info.get("node_id") == self.peer_id and info.get("sharing") is True
                and info.get("service") == "inference-only"):
            raise LANInferenceError("Node identity or sharing state does not match pairing.")
        return info

    def list_models(self):
        self.health()
        models = self._json_get("/v1/models").get("models")
        if not isinstance(models, list):
            raise LANInferenceError("Invalid model listing from the paired node.")
        return sorted({name for name in models[:100] if safe_model(name)})

    def running(self):
        items = self._json_get("/v1/running").get("running")
        if not isinstance(items, dict):
            raise LANInferenceError("Invalid running-model information.")
        return {name: info for name, info in items.items()
                if safe_model(name) and isinstance(info, dict)}

    def model_details(self, model):
        if not safe_model(model) or model not in self.list_models():
            raise LANInferenceError("Choose a model installed on the paired Windows node.")
        # The inference-only node does not disclose Ollama's model details.
        return {"name": model, "capabilities": ["completion"], "context_length": None,
                "note": "Paired Windows LAN model; private TLS connection."}

    def stream_chat(self, messages, model, *, context_length=None, on_chunk=None,
                    on_metrics=None, cancel_event=None):
        name, cleaned, context = validate_chat({
            "model": model, "messages": messages, "context_length": context_length})
        if cancel_event is not None and cancel_event.is_set():
            raise StreamStopped()
        self.health()  # Identity and sharing verified before sending conversation text.
        payload = json.dumps({"model": name, "messages": cleaned,
                              "context_length": context}, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST:
            raise LANInferenceError("Conversation exceeds the LAN request size limit.")
        conn, response = self._response("POST", "/v1/chat", payload, timeout=120)
        chunks, total, finished = [], 0, False
        try:
            if response.getheader("Content-Type", "").split(";")[0].strip() != "application/x-ndjson":
                raise LANInferenceError("Unexpected inference response type.")
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise StreamStopped()
                raw = response.readline(MAX_RESPONSE_LINE + 1)
                total += len(raw)
                if not raw or len(raw) > MAX_RESPONSE_LINE or total > MAX_GENERATED:
                    raise LANInferenceError("The LAN inference stream ended unexpectedly or exceeded limits.")
                try:
                    event = json.loads(raw)
                except (UnicodeError, ValueError) as error:
                    raise LANInferenceError("Invalid LAN inference stream event.") from error
                if not isinstance(event, dict):
                    raise LANInferenceError("Invalid LAN inference stream event.")
                if event.get("type") == "chunk" and isinstance(event.get("text"), str):
                    piece = event["text"]
                    chunks.append(piece)
                    if on_chunk:
                        on_chunk(piece)
                elif event.get("type") == "done" and isinstance(event.get("metrics"), dict):
                    if on_metrics:
                        on_metrics(event["metrics"])
                    finished = True
                    break
                elif event.get("type") == "error":
                    raise LANInferenceError("Windows reported an inference error.")
                else:
                    raise LANInferenceError("Unexpected inference event from paired node.")
            return "".join(chunks)
        except (OSError, http.client.HTTPException) as error:
            raise LANInferenceError("Windows stopped the inference connection.") from error
        finally:
            conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Use a previously paired Windows AI Assist inference node.")
    parser.add_argument("--peer-id", required=True, help="Node ID printed after pairing")
    parser.add_argument("--client-id", required=True, help="Mac ID used while pairing (e.g. mac_1)")
    parser.add_argument("--data-dir", help="Private Mac pairing directory (defaults to AI Assist pairing)")
    parser.add_argument("--models", action="store_true", help="List available Windows Ollama models")
    parser.add_argument("--prompt", help="Send one text prompt to a remote model")
    parser.add_argument("--model", help="Model for --prompt, e.g. qwen3-coder:30b")
    args = parser.parse_args(argv)
    if not args.models and not args.prompt:
        parser.error("Specify --models or --prompt and --model.")
    if args.prompt and not args.model:
        parser.error("--prompt requires --model.")
    store = TrustStore(args.data_dir) if args.data_dir else None
    try:
        node = PairedLANProvider(args.peer_id, args.client_id, store=store)
        if args.models:
            for name in node.list_models():
                print(name)
        if args.prompt:
            print("Windows LAN inference (pinned TLS; no remote tools):")
            node.stream_chat([{"role": "user", "content": args.prompt}], args.model,
                             on_chunk=lambda chunk: print(chunk, end="", flush=True))
            print()
    except (LANInferenceError, ValueError) as error:
        parser.exit(1, f"LAN inference unavailable: {error}\n")


if __name__ == "__main__":
    main()
