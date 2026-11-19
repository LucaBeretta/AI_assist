"""Stopping a program AI Assist started, together with everything it started.

An agent command, a colibri install or a build can leave children behind
(download workers, compilers, servers). Killing only the direct child would
orphan them, so the whole tree is stopped.
"""

import os
import signal
import subprocess


def terminate_tree(process, grace=1.0):
    """Stop ``process`` and its children, then reap it.

    On Windows ``taskkill /T /F`` ends the tree at once. Elsewhere the process
    must have been started with ``start_new_session=True``: its group gets
    SIGTERM, ``grace`` seconds to exit, then SIGKILL."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=grace)
        except OSError:
            pass  # Already gone, or not ours to signal; the wait below still reaps it.
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
