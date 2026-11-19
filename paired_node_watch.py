"""Opt-in notifications when an already-paired LAN inference node becomes ready.

A discovery packet is NEVER trusted as proof of availability: every check pins
the peer's full TLS certificate SHA-256 fingerprint, authenticates with the
per-client pairing token and verifies the node ID. Never starts LAN sharing,
Ollama, an internet connection or a local listener.

Run on the Mac: python paired_node_watch.py --watch
Stop with Ctrl+C. Notifications are transition-only; nothing runs at login.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import platform
import re
import ssl
import subprocess
import threading
import time

from app_core.paths import data_directory
from peer_pairing import TrustStore, permitted_ipv4

INFERENCE_PORT = 49382
MAX_BODY_BYTES = 4096
CHECK_INTERVAL_SECONDS = 15
REQUIRED_SUCCESSES = 2
REQUIRED_FAILURES = 2
CLIENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")


class NodeUnavailable(RuntimeError):
    """A node is offline, untrusted, or not permitting authenticated requests."""


def pairing_directory():
    return data_directory().parent / "pairing"


def private_peer_record(peer_id, record):
    """Return the validated connection settings; do not log or print tokens."""
    if not isinstance(peer_id, str) or CLIENT_ID_RE.fullmatch(peer_id) is None:
        raise NodeUnavailable("Invalid paired-node identity.")
    if not isinstance(record, dict):
        raise NodeUnavailable("Invalid saved paired-node data.")
    address, fingerprint, token = (record.get("address"), record.get("fingerprint"),
                                   record.get("token"))
    if not isinstance(address, str) or not permitted_ipv4(address):
        raise NodeUnavailable("Paired node must have a private LAN IPv4 address.")
    if (not isinstance(fingerprint, str)
            or FINGERPRINT_RE.fullmatch(fingerprint) is None
            or not isinstance(token, str) or len(token) < 32 or len(token) > 512):
        raise NodeUnavailable("Invalid pinned certificate or pairing token.")
    return address, fingerprint, token


def verify_peer_health(peer_id, record, client_id, *, port=INFERENCE_PORT, timeout=2):
    """Connect to a pinned TLS peer, then authorize a read-only health request.

    The token is sent only AFTER comparing the entire DER certificate digest.
    No DNS lookup, HTTP redirect, or public address is permitted.
    """
    address, fingerprint, token = private_peer_record(peer_id, record)
    if not isinstance(client_id, str) or CLIENT_ID_RE.fullmatch(client_id) is None:
        raise NodeUnavailable("Set a valid client ID matching the original pairing.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise NodeUnavailable("Invalid inference port.")
    if not 0.2 <= timeout <= 10:
        raise NodeUnavailable("Invalid connection timeout.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Full SHA-256 pin checked below BEFORE auth.
    conn = http.client.HTTPSConnection(address, port, timeout=timeout, context=context)
    try:
        conn.connect()
        cert = conn.sock.getpeercert(binary_form=True)
        if not cert or not hmac.compare_digest(
                hashlib.sha256(cert).hexdigest(), fingerprint):
            raise NodeUnavailable("The node's TLS identity changed; re-pair it.")
        conn.request("GET", "/v1/health", headers={
            "Authorization": "Bearer " + token,
            "X-AI-Assist-Client-ID": client_id,
            "Accept": "application/json",
            "Connection": "close",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise NodeUnavailable("The paired inference node is not available.")
        if response.getheader("Content-Type", "").split(";")[0].strip() != "application/json":
            raise NodeUnavailable("Unexpected health response.")
        data = response.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            raise NodeUnavailable("Oversized health response.")
        body = json.loads(data)
        expected_id = hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:24]
        if (not isinstance(body, dict) or body.get("node_id") != expected_id
                or body.get("node_id") != peer_id or body.get("sharing") is not True
                or body.get("service") != "inference-only"):
            raise NodeUnavailable("Node identity or sharing status did not match.")
        return True
    except (OSError, ssl.SSLError, ValueError, http.client.HTTPException) as exc:
        if isinstance(exc, NodeUnavailable):
            raise
        raise NodeUnavailable("The node is unavailable or its identity could not be verified.") from exc
    finally:
        conn.close()


class AvailabilityTransitions:
    """One notification when a node becomes available, never each poll."""

    def __init__(self, successes=REQUIRED_SUCCESSES, failures=REQUIRED_FAILURES):
        if successes < 1 or failures < 1:
            raise ValueError("Transition thresholds must be positive.")
        self.successes, self.failures = successes, failures
        self._states = {}

    def update(self, peer_id, available):
        online, streak = self._states.get(peer_id, (False, 0))
        streak = (max(0, streak) + 1 if available and online
                  else min(0, streak) - 1 if not available and not online
                  else 1 if available else -1)
        if available and not online and streak >= self.successes:
            self._states[peer_id] = (True, 0)
            return "online"
        if not available and online and streak <= -self.failures:
            self._states[peer_id] = (False, 0)
            return "offline"
        self._states[peer_id] = (online, streak)
        return None


def mac_notification(title, message):
    """Show a native macOS notification. Pass text as argv, never as script."""
    if platform.system() != "Darwin":
        return False
    script = "on run argv\n display notification (item 1 of argv) with title (item 2 of argv)\nend run"
    try:
        result = subprocess.run(
            ["osascript", "-e", script, "--", str(message)[:180], str(title)[:100]],
            capture_output=True, timeout=5, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def watch_paired_nodes(*, client_id, stop_event, notify=mac_notification,
                       store=None, interval=CHECK_INTERVAL_SECONDS, port=INFERENCE_PORT,
                       on_change=None):
    """Blocking watcher. Caller must start it through an explicit user action.

    A single watcher polls only previously paired nodes. No UDP broadcasts and
    no unverified peers. No notification on loss by default; when a node later
    comes back, it notifies once more.
    """
    if not isinstance(stop_event, threading.Event):
        raise TypeError("An explicit stop event is required.")
    if not 1 <= interval <= 300:
        raise ValueError("Polling interval must be between 1 and 300 seconds.")
    store = store if store is not None else TrustStore(pairing_directory())
    transitions = AvailabilityTransitions()
    while not stop_event.is_set():
        records = store._read("paired_servers.json")
        for peer_id, record in sorted(records.items()):
            if stop_event.is_set():
                break
            try:
                ok = verify_peer_health(peer_id, record, client_id, port=port)
            except (NodeUnavailable, OSError, ValueError):
                ok = False
            transition = transitions.update(peer_id, ok)
            if on_change is not None and transition:
                on_change(peer_id, transition)
            if transition == "online":
                notify("AI Assist · PC disponibile",
                       f"Il dispositivo associato {peer_id[:24]} è pronto per l'inferenza LAN.")
        stop_event.wait(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in Mac notifications for paired inference nodes.")
    parser.add_argument("--watch", action="store_true",
                        help="Explicitly enable monitoring for this session (Ctrl+C stops)")
    parser.add_argument("--client-id", required=True, help="Client ID used during pairing, e.g. mac_1")
    parser.add_argument("--data-dir", type=Path,
                        help="Private directory used for pairing on the Mac")
    parser.add_argument("--port", type=int, default=INFERENCE_PORT)
    args = parser.parse_args(argv)
    if not args.watch:
        parser.error("Monitoring is OFF by default. Pass --watch to opt in.")
    stop = threading.Event()
    store = TrustStore(args.data_dir or pairing_directory())
    if not store._read("paired_servers.json"):
        parser.error("No paired nodes: pair the Windows device first.")
    print("Monitoring trusted LAN nodes. Notifications on ONLINE transitions only.")
    print("No automatic model sharing or internet connection. Press Ctrl+C to stop.")
    try:
        watch_paired_nodes(client_id=args.client_id, stop_event=stop,
                           store=store, port=args.port)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
