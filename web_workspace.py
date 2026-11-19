"""AI Assist's web workspace (preview): chat in your browser, on this computer only.

    python web_workspace.py [--port 8765] [--no-browser] [--tab] [--host 127.0.0.1|::1]

It needs no Tkinter. It serves one page to this computer's browser and opens
it, signed in by a single-use link, in its own app window (Edge or Chrome) or,
with --tab or without those browsers, in a normal tab. Press Enter in this
window to open it again; press Ctrl+C to stop.

The developer agent runs here with the same two-step reviews as the desktop
app, in project folders chosen there (Developer > Open project).

What it does not do (yet), on purpose:

- **Approve internet access.** There is no approval window here, so the
  internet gate blocks every internet request. Use the desktop app
  (AIassist.py) for model downloads, cloud models and update checks.

Only one AI Assist may use a data folder at a time: close the desktop window first.
"""

import argparse
import sys
import threading
import webbrowser

import network_gate


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI Assist's web workspace (preview), for this computer only.")
    parser.add_argument("--port", type=int, default=0, help="port on this computer (default: any free port)")
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1",
                        help="loopback address to listen on; other addresses are refused")
    parser.add_argument("--no-browser", action="store_true", help="print the link without opening a browser")
    parser.add_argument("--tab", action="store_true", help="open a normal browser tab instead of an app window")
    lan = parser.add_argument_group("optional paired Windows inference")
    lan.add_argument("--lan-peer-id", help="Already paired Windows node ID")
    lan.add_argument("--lan-client-id", help="Client ID used when pairing this Mac")
    args = parser.parse_args(argv)
    if bool(args.lan_peer_id) != bool(args.lan_client_id):
        parser.error("Use both --lan-peer-id and --lan-client-id to opt into paired LAN inference.")
    if not 0 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535, or 0 for any free port.")

    if not 0 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535, or 0 for any free port.")

    # Before anything else, so every connection this process makes is checked. No
    # approver is registered: internet requests are blocked, never silently allowed.
    network_gate.install()

    from app_core.instance_lock import AlreadyRunning, InstanceLock
    from app_core.paths import data_directory
    from chat_store import ChatStore, ChatStoreError
    from shared_memory import SharedMemory
    from webui.server import WebWorkspace, WorkspaceServer

    lock = InstanceLock(data_directory(), kind="AI Assist web workspace")
    try:
        lock.acquire()
    except AlreadyRunning as error:
        print(f"The web workspace could not start: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"The web workspace could not use the saved-chats folder: {error}", file=sys.stderr)
        return 1
    try:
        try:
            store = ChatStore()
        except ChatStoreError as error:
            print(f"The web workspace could not open saved chats: {error}", file=sys.stderr)
            return 1
        try:
            memory = SharedMemory(store.directory)
        except (OSError, ValueError) as error:
            print(f"Shared memory is unavailable: {error}", file=sys.stderr)
            memory = None
        if args.lan_peer_id:
            # This opt-in changes the inference provider only. The browser and
            # its authenticated API remain bound to this Mac's loopback interface.
            from lan_client import LANInferenceError, PairedLANProvider
            from peer_pairing import TrustStore
            from paired_node_watch import pairing_directory
            trust = TrustStore(pairing_directory())
            try:
                remote = PairedLANProvider(args.lan_peer_id, args.lan_client_id, store=trust)
            except LANInferenceError as error:
                print(f"Could not enable paired LAN inference: {error}", file=sys.stderr)
                return 1
            workspace = WebWorkspace(store, memory, provider_factory=lambda: remote)
        else:
            workspace = WebWorkspace(store, memory)

        def daily_backup():
            try:
                done = workspace.backup_if_due()
                if done:
                    print(f"Backed up {done[1]} chats to {done[0]}.", flush=True)
            except (OSError, ValueError) as error:
                print(f"The daily chat backup failed: {error}", file=sys.stderr, flush=True)

        threading.Thread(target=daily_backup, name="daily-backup", daemon=True).start()
        try:
            server = WorkspaceServer(workspace, args.host, args.port)
        except (OSError, ValueError) as error:
            print(f"The web workspace could not listen on {args.host}:{args.port}: {error}", file=sys.stderr)
            return 1

        def link():
            return f"{server.origin}/#launch={workspace.sessions.new_launch_code()}"

        def show(url):
            """Its own app window when Edge or Chrome is installed, else a browser tab."""
            if not args.tab:
                from webui.app_window import open_app_window
                try:
                    if open_app_window(url):
                        return
                except OSError as error:
                    print(f"Could not open an app window ({error}); using a browser tab.", file=sys.stderr)
            webbrowser.open(url)

        first = link()
        print("AI Assist web workspace (preview) is running on this computer only.")
        print(f"  Saved chats: {store.directory}")
        print("  Internet access: blocked (approvals happen in the desktop app).")
        if args.lan_peer_id:
            print("  Inference: authenticated paired Windows LAN node (prompts sent to Windows only).")
        else:
            print("  Inference: this computer only (LAN inference OFF).")
        print("  Developer agent: each edit and command waits for your approval on the page.")
        print()
        print("Open this single-use link in your browser (valid for 5 minutes):")
        print(f"  {first}")
        if args.no_browser:
            print("Press Enter for a new link. Press Ctrl+C or close this window to stop.")
        else:
            print("Closed the AI Assist window? Press Enter here to open it again.")
            print("Press Ctrl+C or close this window to stop.")
            show(first)

        def new_links():
            for _line in sys.stdin:
                fresh = link()
                if args.no_browser:
                    print(f"New single-use link: {fresh}", flush=True)
                else:
                    print("Opening AI Assist again...", flush=True)
                    show(fresh)

        if sys.stdin is not None and sys.stdin.isatty():
            threading.Thread(target=new_links, name="web-links", daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\nStopping the web workspace.")
        finally:
            server.server_close()
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
