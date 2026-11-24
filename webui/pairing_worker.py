"""Gated child process: pairing only, controlled over private standard pipes."""
import json
from pathlib import Path
import sys
import time

# sitecustomize has already installed the parent's child gate before this script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from peer_pairing import PairingHost, PAIRING_TTL, verification_code


def local_usage():
    """This computer's load for paired clients: CPU, RAM, GPU and loaded model names only."""
    import compute_telemetry
    global _sampler
    if _sampler is None:
        _sampler = compute_telemetry.TelemetrySampler("http://127.0.0.1:11434")
    data = _sampler.sample()
    return {"name": str(data.get("device", "")).split(".")[0][:64], "system": data.get("system", ""), "time": data.get("time"),
            "cpu": data.get("cpu") or {}, "ram": data.get("ram") or {}, "gpus": data.get("gpus") or [],
            "loaded": list((data.get("models") or {}).get("loaded") or [])}


_sampler = None


def _saved_port(directory):
    try:
        value = json.loads((Path(directory) / "host.json").read_text(encoding="utf-8")).get("port")
        return value if type(value) is int else 0
    except (OSError, ValueError, AttributeError):
        return 0


def _save_port(directory, port):
    try:
        (Path(directory) / "host.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    except OSError:
        pass


def serve(address, directory, incoming, outgoing, factory=PairingHost, share_usage=False, usage=local_usage, share_ai=None):
    """``share_ai`` is this computer's Ollama URL (loopback only) to share with allowed devices, or None."""
    host = None
    def reply(value):
        outgoing.write(json.dumps(value, ensure_ascii=False) + "\n")
        outgoing.flush()
    try:
        options = {"port": _saved_port(directory)}
        if share_usage:
            options["usage"] = usage
        if share_ai:
            options["ollama"] = share_ai
        host = factory(address, directory, **options)
        if type(getattr(host, "port", None)) is int:
            _save_port(directory, host.port)
        if share_usage:
            try:
                usage()  # CPU load is a difference between readings: take the first one now.
            except Exception:
                pass
        host.start()
        def new_pin():
            return {"ok": True, "address": address, "port": host.port,
                    "verification_code": verification_code(host.fingerprint),
                    "pin": host.window.open(), "expires_at": time.time() + PAIRING_TTL}
        reply(new_pin())
        while True:
            line = incoming.readline(1025)
            if not line:
                break
            if len(line) > 1024:
                raise ValueError("Pairing control message is too large.")
            command = json.loads(line)
            if command == {"command": "close"}:
                break
            if command != {"command": "new_pin"}:
                raise ValueError("Unknown pairing control message.")
            reply(new_pin())
    except Exception as error:
        reply({"ok": False, "error": str(error)})
    finally:
        if host is not None:
            host.stop()


if __name__ == "__main__":
    flags = sys.argv[3:]
    shared = flags[flags.index("--share-ai") + 1] if "--share-ai" in flags[:-1] else None
    serve(sys.argv[1], sys.argv[2], sys.stdin, sys.stdout, share_usage="--share-usage" in flags, share_ai=shared)
