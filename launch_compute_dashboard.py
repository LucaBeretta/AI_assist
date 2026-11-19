"""Open AI Assist's compute dashboard without launching its chat workspace.

This entrypoint opens NO LAN listener or discovery service. It uses local-only
telemetry, and saved peer IDs are NOT interpreted as live connections.
"""

from types import SimpleNamespace

from app_core.paths import data_directory
from compute_dashboard import ComputeDashboard, saved_peer_records
from gui_runtime import GUIRuntimeError, create_root
import network_gate


def saved_peers():
    """Read only the default on-disk enrollment inventory, never peer secrets."""
    try:
        return saved_peer_records(data_directory().resolve())
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
