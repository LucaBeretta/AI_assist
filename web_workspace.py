"""AI Assist's web workspace (preview): chat in your browser, on this computer only.

    python web_workspace.py [--port 8765] [--no-browser] [--host 127.0.0.1|::1]

It needs no Tkinter. It serves one page to this computer's browser and prints
a single-use link that signs the page in. Press Enter in this window for a new
link; press Ctrl+C to stop.

What it does not do (yet), on purpose:

- **Approve internet access.** There is no approval window here, so the
  internet gate blocks every internet request. Use the desktop app
  (AIassist.py) for model downloads, cloud models and update checks.
- **Run the developer agent.** Edits and commands need the desktop app's
  reviewed, two-step confirmations. Developer chats and their project files
  can be read here, not changed.

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
    args = parser.parse_args(argv)
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

        first = link()
        print("AI Assist web workspace (preview) is running on this computer only.")
        print(f"  Saved chats: {store.directory}")
        print("  Internet access: blocked (approvals happen in the desktop app).")
        print("  Developer agent: read-only here (edits and commands run in the desktop app).")
        print()
        print("Open this single-use link in your browser (valid for 5 minutes):")
        print(f"  {first}")
        print("Press Enter for a new link, Ctrl+C to stop.")
        if not args.no_browser:
            webbrowser.open(first)

        def new_links():
            for _line in sys.stdin:
                print(f"New single-use link: {link()}", flush=True)

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
