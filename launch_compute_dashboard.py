"""Open AI Assist's compute dashboard without launching its chat workspace.

This entrypoint opens NO LAN listener or discovery service. It uses local-only
telemetry, and saved peer IDs are NOT interpreted as live connections.
"""

from pathlib import Path
from types import SimpleNamespace

import network_gate
from gui_runtime import GUIRuntimeError, create_root
from compute_dashboard import ComputeDashboard


def saved_peers():
    """Read only the default on-disk enrollment inventory, never peer secrets."""
    try:
        from chat_store import ChatStore
        from peer_pairing import TrustStore
        folder = ChatStore().directory.parent / "pairing"
        return TrustStore(folder)._read("paired_servers.json")
    except (OSError, ValueError, TypeError):
        return {}


def main():
    # A standalone monitor has no Internet approval UI: fail closed.
    network_gate.install()
    try:
        root = create_root()
    except GUIRuntimeError as error:
        raise SystemExit(str(error)) from error
    root.withdraw()
    dashboard = None
    try:
        dashboard = ComputeDashboard(SimpleNamespace(root=root), peer_records=saved_peers)
        root.wait_window(dashboard.window)
    finally:
        if dashboard is not None and not dashboard.closed:
            dashboard.close()
        root.destroy()


if __name__ == "__main__":
    main()
