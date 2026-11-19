"""Gated child process: pairing only, controlled over private standard pipes."""
import json
from pathlib import Path
import sys
import time

# sitecustomize has already installed the parent's child gate before this script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from peer_pairing import PairingHost, PAIRING_TTL, verification_code


def serve(address, directory, incoming, outgoing, factory=PairingHost):
    host = None
    def reply(value):
        outgoing.write(json.dumps(value, ensure_ascii=False) + "\n")
        outgoing.flush()
    try:
        host = factory(address, directory)
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
    serve(sys.argv[1], sys.argv[2], sys.stdin, sys.stdout)
