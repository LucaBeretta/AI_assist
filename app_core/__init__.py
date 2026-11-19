"""AI Assist's application core: everything that is not a window.

Modules here must never import tkinter (or any other UI toolkit). The desktop
window (Tk) and the local web workspace (webui/) are two presentations of the
same services, so business rules live here once:

- ``ollama`` / ``providers``: talking to the inference engines.
- ``conversation`` / ``chat_service``: building requests, saving chats.
- ``privacy``: whether prompts stay on this computer.
- ``settings`` / ``paths`` / ``instance_lock``: where data lives and who may write it.
- ``streaming``: stoppable streamed requests.

The internet gate (network_gate.py) stays a standalone root module: Python
programs AI Assist starts load it by file path, without this package.
``test_app_core_boundaries.py`` checks that importing this package loads no UI toolkit.
"""
