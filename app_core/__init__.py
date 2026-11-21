"""AI Assist's application core: everything that is not a window.

The desktop presentation lives in webui/. The core owns application rules
without importing any UI toolkit:

- ``ollama`` / ``providers``: talking to the inference engines.
- ``conversation`` / ``chat_service``: chat rules, sessions, drafts and reply recovery.
- ``privacy``: whether prompts stay on this computer.
- ``settings`` / ``paths`` / ``instance_lock``: where data lives and who may write it.
- ``streaming``: stoppable streamed requests.

The internet gate (network_gate.py) stays a standalone root module: Python
programs AI Assist starts load it by file path, without this package.
``test_app_core`` checks that importing the core loads no UI toolkit.
"""
