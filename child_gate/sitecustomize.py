"""Loaded at startup by Python programs that AI Assist starts (it puts this folder
on PYTHONPATH). Their internet requests then go through AI Assist's internet
gate. If the gate cannot start, the program is stopped instead of running
unchecked. Written for any Python version, so an old one stops cleanly too."""

import os
import sys

if os.environ.get("AI_ASSIST_GATE"):
    if sys.version_info < (3, 8):
        sys.stderr.write("AI Assist's internet gate needs Python 3.8 or newer, so this program was stopped.\n")
        sys.stderr.flush()
        os._exit(1)
    try:
        import importlib.util

        _path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "network_gate.py")
        _spec = importlib.util.spec_from_file_location("_ai_assist_network_gate", _path)
        _gate = importlib.util.module_from_spec(_spec)
        sys.modules[_spec.name] = _gate
        _spec.loader.exec_module(_gate)
        _gate.install_child()
    except Exception as _error:
        sys.stderr.write("AI Assist's internet gate could not start, so this program was stopped: %s\n" % (_error,))
        sys.stderr.flush()
        os._exit(1)
