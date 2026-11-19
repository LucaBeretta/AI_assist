"""Manual LAN pairing; no listener, identity generation or discovery at startup."""
from copy import deepcopy
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

import network_gate
from app_core import settings
from app_core.paths import ensure_private_directory
from peer_pairing import TrustStore, permitted_ipv4, pair_with_host
from sharing_control import ManualSharingController
from webui.api_utils import json_method


class PairingProcess:
    def __init__(self, address, directory):
        self.address, self.directory = address, directory
        self.process = None
        self.answers = queue.Queue()
        self.lock = threading.RLock()
        self.details = {}

    def _read(self):
        output = self.process.stdout
        try:
            while True:
                line = output.readline(8193)
                if not line or len(line) > 8192:
                    break
                self.answers.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.answers.put({"ok": False, "error": "The pairing service stopped."})

    def _answer(self):
        try:
            value = self.answers.get(timeout=8)
        except queue.Empty:
            raise RuntimeError("The pairing service did not respond.") from None
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise RuntimeError(value.get("error", "Invalid pairing response.") if isinstance(value, dict) else "Invalid pairing response.")
        # Explicit allowlist: private keys and trust tokens never enter the bridge.
        self.details = {key: value[key] for key in ("address", "port", "verification_code", "pin", "expires_at")}
        return deepcopy(self.details)

    def start(self):
        environment = network_gate.child_environment("LAN pairing")
        if environment is None:
            raise RuntimeError("The internet gate must be active before enabling LAN sharing.")
        environment["PYTHONIOENCODING"] = "utf-8"
        ensure_private_directory(self.directory)
        with self.lock:
            self.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("pairing_worker.py")),
                self.address, str(self.directory)], env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._read, daemon=True, name="pairing-control").start()
            self._answer()

    def new_pin(self):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                raise ValueError("Enable LAN sharing first.")
            self.process.stdin.write('{"command":"new_pin"}\n')
            self.process.stdin.flush()
            return self._answer()

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        with self.lock:
            process = self.process
            if process is None:
                return
            try:
                if process.poll() is None:
                    try:
                        process.stdin.write('{"command":"close"}\n')
                        process.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=2)
            finally:
                process.stdin.close()
                process.stdout.close()
                self.process, self.details = None, {}


class SharingApi:
    def _init_sharing(self):
        self._sharing_service = None
        self._sharing_closed = False
        self._sharing_lock = threading.RLock()
        self._sharing_directory = self._store.directory / "pairing"
        self._sharing_address = None
        self._sharing = ManualSharingController(self._create_pairing_service)

    def _create_pairing_service(self):
        self._sharing_service = PairingProcess(self._sharing_address, self._sharing_directory)
        return self._sharing_service

    def _paired_records(self):
        # TrustStore owns the format; expose metadata only, without contacting peers.
        records = TrustStore(self._sharing_directory)._read("paired_servers.json")
        return {key: {"address": value.get("address"), "port": value.get("port")}
                for key, value in records.items() if isinstance(value, dict)}

    @json_method
    def sharing_state(self):
        with self._sharing_lock:
            enabled = self._sharing.enabled
            service = self._sharing_service
            if enabled and (service is None or not service.alive()):
                self._sharing.disable()
                enabled = False
            return {"ok": True, "enabled": enabled, "state": self._sharing.state,
                    "host": deepcopy(service.details) if enabled else None, "peers": self._paired_records()}

    @json_method
    def set_sharing(self, enabled, address=None):
        if type(enabled) is not bool:
            raise ValueError("Sharing must be true or false.")
        if enabled and (not isinstance(address, str) or not permitted_ipv4(address)):
            raise ValueError("Enter this computer's explicit private LAN IPv4 address.")
        def work(progress, cancel):
            if enabled and not network_gate.allow_external("LAN pairing listener", address,
                    "Discovery metadata and pairing credentials over verified TLS; no chats, files or commands.",
                    "Enable manual LAN pairing until you turn it off or close AI Assist."):
                raise RuntimeError("LAN sharing was blocked.")
            with self._sharing_lock:
                if self._sharing_closed or cancel.is_set():
                    return {"ok": True, "enabled": False}
                self._sharing_address = address
                if enabled:
                    self._sharing.enable(user_initiated=True)
                    if cancel.is_set() or self._sharing_closed:
                        self._sharing.disable()
                else:
                    self._sharing.disable()
                return self.sharing_state()
        with self._lock:
            if self._closed or self._sharing_closed:
                raise ValueError("The app is closing.")
            return self._jobs.start("sharing", work, key="sharing")

    @json_method
    def new_pairing_pin(self):
        def work(progress, cancel):
            with self._sharing_lock:
                if not self._sharing.enabled or self._sharing_closed:
                    raise ValueError("Enable LAN sharing first.")
                self._sharing_service.new_pin()
                return self.sharing_state()
        return self._jobs.start("sharing", work, key="sharing")

    @json_method
    def pair_device(self, address, port, verification_code, pin):
        # The core validates the address, port, certificate code and PIN before connecting.
        def work(progress, cancel):
            if not network_gate.allow_external("Pair LAN device", str(address),
                    "The six-digit PIN and this device's random ID, sent only after checking the target TLS code.",
                    "Pair the device you selected; no chat content is sent.") or cancel.is_set():
                raise RuntimeError("Pairing was blocked.")
            with self._sharing_lock:
                if self._sharing_closed:
                    raise ValueError("The app is closing.")
                ensure_private_directory(self._sharing_directory)
                path = self._sharing_directory / "client.json"
                identity = settings.read_json(path).get("id") or uuid.uuid4().hex
                settings.write_json(path, {"id": identity})
                peer = pair_with_host(address, port, verification_code, pin, identity, TrustStore(self._sharing_directory))
                return {"ok": True, "peer_id": peer, "peers": self._paired_records()}
        with self._lock:
            if self._closed or self._sharing_closed:
                raise ValueError("The app is closing.")
            return self._jobs.start("sharing", work, key="sharing")

    def _close_sharing(self):
        self._sharing_closed = True
        with self._sharing_lock:
            self._sharing.close()
