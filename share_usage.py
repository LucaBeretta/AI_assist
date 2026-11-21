"""Share this computer's CPU, RAM and GPU usage with paired AI Assist devices,
without the desktop app. Made for headless machines such as a Raspberry Pi.

Run it over SSH from the AI Assist folder:

    python3 share_usage.py

It prints an address, port, certificate code and PIN. On your Mac, open AI
Assist > Settings > Privacy & network > Pair another device and enter them.
Mission Control on the Mac then shows this computer's live usage.

What is shared, and with whom: only CPU, RAM and GPU load, this computer's name
and its loaded Ollama model names, only to devices paired with a PIN, only over
TLS with the certificate shown here. No chats, files or commands exist on this
service. Sharing stops when you press Ctrl+C or close the SSH session.
"""

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_core.paths import data_directory, ensure_private_directory  # noqa: E402
import lan_devices  # noqa: E402
import peer_pairing  # noqa: E402


def pick_address(requested=None):
    if requested:
        return requested
    found = lan_devices.interfaces()
    main = lan_devices.main_interface(found, lan_devices.gateway())
    if main is None:
        raise SystemExit("This computer has no private network address. Connect it to your network first.")
    return main["address"]


def show(host, pin):
    code = peer_pairing.verification_code(host.fingerprint)
    print()
    print("  Pair from AI Assist on your Mac: Settings > Privacy & network > Pair another device")
    print()
    print(f"    Address           {host.bind_ip}")
    print(f"    Port              {host.port}")
    print(f"    Certificate code  {code}")
    print(f"    PIN               {pin}   (valid for {peer_pairing.PAIRING_TTL // 60} minutes, 3 tries)")
    print()
    print("  Press Enter for a new PIN. Press Ctrl+C to stop sharing.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Share this computer's CPU, RAM and GPU usage with paired AI Assist devices.")
    parser.add_argument("--address", help="this computer's private LAN IPv4 address (found automatically if omitted)")
    parser.add_argument("--name", default=socket.gethostname().split(".")[0], help="name shown in Mission Control")
    args = parser.parse_args(argv)

    try:
        import cryptography  # noqa: F401  (needed to create this computer's certificate)
    except ImportError:
        raise SystemExit("Missing the 'cryptography' package. On Raspberry Pi OS run:\n"
                         "  sudo apt install python3-cryptography")
    from webui.pairing_worker import local_usage, _saved_port, _save_port

    address = pick_address(args.address)
    directory = data_directory() / "pairing"
    ensure_private_directory(directory)
    try:
        local_usage()  # CPU load is a difference between readings: take the first one now.
    except Exception:
        pass
    def usage():
        return dict(local_usage(), name=args.name)
    host = peer_pairing.PairingHost(address, directory, name=args.name, usage=usage, port=_saved_port(directory))
    _save_port(directory, host.port)
    host.start()
    print(f"Sharing {args.name}'s usage with paired AI Assist devices.")
    show(host, host.window.open())
    try:
        while True:
            line = sys.stdin.readline()
            if not line:  # no keyboard (started in the background): keep sharing
                while True:
                    time.sleep(3600)
            show(host, host.window.open())
    except KeyboardInterrupt:
        print("\nStopped sharing.")
    finally:
        host.stop()


if __name__ == "__main__":
    main()
