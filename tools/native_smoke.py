"""Exercise the app's real WebView on an isolated macOS desktop."""

import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def wait_for(window, script, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.evaluate_js(script):
            return
        time.sleep(.1)
    raise AssertionError(f"Timed out waiting for the WebView: {script}")


def main():
    if sys.platform != "darwin":
        raise SystemExit("This smoke test uses an isolated macOS desktop.")

    with tempfile.TemporaryDirectory(prefix="ai-assist-native-") as folder:
        with patch.dict(os.environ, {"AI_ASSIST_DATA_DIR": folder}):
            return run_smoke(Path(folder).resolve())


def run_smoke(directory):
    import network_gate

    network_gate.install()
    import webview

    from app_core import runtime
    from webui.app import main as launch
    from webui.settings import save

    model = "smoke-test:1b"
    reply = "Native bridge smoke reply."
    failure = []
    native_start = webview.start
    save(directory, {"first_run_complete": True})

    def fake_reply(messages, model=None, on_chunk=None, **options):
        on_chunk(reply)
        return reply

    def drive(start_engine, **options):
        def scenario():
            window = webview.windows[0]
            try:
                if not window.events.loaded.wait(20):
                    raise AssertionError("The desktop page did not load.")
                start_engine()
                wait_for(window, "Boolean(window.app?.currentChat()?.id && !document.getElementById('send').disabled)")
                window.evaluate_js("window.app.setPrompt('Verify the native chat bridge.'); document.getElementById('send').click();")
                wait_for(window, "document.getElementById('messages').textContent.includes('Native bridge smoke reply.')")
            except Exception as error:
                failure.append(error)
            finally:
                window.destroy()

        return native_start(scenario, **options)

    colibri = SimpleNamespace(default_backend="Ollama", availability=lambda: {
        "installed": False, "running": False, "models": [], "host": "http://127.0.0.1:8000/v1",
    })
    with patch.object(webview, "start", side_effect=drive), patch("webui.app.ColibriService", return_value=colibri), \
            patch.multiple(runtime, ollama_url=lambda: "http://127.0.0.1:11434",
                           ensure_ollama=lambda progress: None, list_ollama_models=lambda: [model],
                           running_details=lambda: {model: {"context_length": 4096}}, request_ollama=fake_reply):
        result = launch()
    if failure:
        raise failure[0]
    if result:
        raise RuntimeError(f"The desktop launcher exited with status {result}.")
    print("WKWebView chat smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
