"""Every desktop API method must be callable from the page.

pywebview turns each public method into a JavaScript function whose parameters keep the Python names
(webview/js/api.js, _createApi). Its body calls ``window.pywebview...``, so a parameter called
``window`` hides the page's window, and the call fails before it reaches Python.
"""

import inspect
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest

from app_core.runtime import StreamStopped, run_stoppable
from chat_store import ChatStore
from webui.app import Api

try:
    import webview
except ImportError:  # pragma: no cover - pywebview is in requirements.txt
    webview = None

# Names the body of pywebview's generated function uses; a parameter with one of them breaks the call.
STUB_NAMES = {"window", "Math", "Promise", "Array", "arguments"}

NODE_HARNESS = r"""
const fs = require("fs");
const [apiPath, listPath] = process.argv.slice(1);
globalThis.window = globalThis;
const rejections = [];
process.on("unhandledRejection", reason => rejections.push(String(reason)));
(0, eval)(fs.readFileSync(apiPath, "utf8"));
const calls = [];
window.pywebview._checkValue = () => {};
window.pywebview._jsApiCallback = (name, params) => calls.push([name, params]);
const list = JSON.parse(fs.readFileSync(listPath, "utf8"));
window.pywebview._createApi(list);
const failed = [];
for (const {func, params} of list) {
  const args = params.map((_, index) => "value" + index);
  const target = func.split(".").reduce((object, key) => object[key], window.pywebview.api);
  const before = calls.length;
  try {
    target(...args);
  } catch (error) {
    failed.push(func + ": " + error.message);
    continue;
  }
  const last = calls[calls.length - 1];
  if (calls.length !== before + 1 || last[0] !== func || JSON.stringify(last[1]) !== JSON.stringify(args)) {
    failed.push(func + ": the call did not reach Python");
  }
}
setTimeout(() => console.log(JSON.stringify({failed, rejections})), 0);
"""


def exposed_functions(api):
    """The function list pywebview sends to the page (webview.util.inject_pywebview, get_functions)."""
    seen, functions = set(), {}

    def walk(obj, base):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        for name in dir(obj):
            if name.startswith("_"):
                continue
            full_name = f"{base}.{name}" if base else name
            try:
                attr = getattr(obj, name)
            except Exception:
                continue
            if not getattr(attr, "_serializable", True):
                continue
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                functions[full_name] = list(inspect.getfullargspec(attr).args)[1:]
            elif inspect.isclass(attr) or (not callable(attr) and hasattr(attr, "__module__")):
                walk(attr, full_name)

    walk(api, "")
    return functions


class PageCallsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        engine = SimpleNamespace(request_ollama=lambda *args, **kwargs: "", run_stoppable=run_stoppable,
                                 StreamStopped=StreamStopped, ollama_url=lambda: "http://localhost:11434",
                                 list_ollama_models=lambda: [], running_details=lambda: {},
                                 ensure_ollama=lambda callback: None)
        colibri = SimpleNamespace(default_backend="Ollama", availability=lambda: {
            "installed": False, "running": False, "models": [], "host": "http://127.0.0.1:8000/v1",
            "state": "offline", "context_length": None, "tokens_per_second": None})
        events = SimpleNamespace(emit=lambda *args, **kwargs: None, lock=threading.Lock())
        self.api = Api(events, ChatStore(self.folder / "chats"), engine, colibri=colibri)
        self.addCleanup(self.api._close)
        self.functions = exposed_functions(self.api)

    def test_pairing_methods_are_exposed(self):
        for name in ("set_sharing", "new_pairing_pin", "pair_device", "send"):
            self.assertIn(name, self.functions)

    def test_no_parameter_hides_a_name_the_page_function_uses(self):
        clashes = {name: sorted(STUB_NAMES.intersection(params)) for name, params in self.functions.items()}
        self.assertEqual({name: names for name, names in clashes.items() if names}, {})

    @unittest.skipUnless(webview is not None and shutil.which("node"), "needs pywebview and Node")
    def test_every_page_function_reaches_python(self):
        source = (Path(webview.__file__).parent / "js" / "api.js").read_text(encoding="utf-8")
        api_path, list_path = self.folder / "api.js", self.folder / "functions.json"
        api_path.write_text(source % {"token": "test", "platform": "test", "uid": "test",
                                      "js_api_endpoint": "test"}, encoding="utf-8")
        list_path.write_text(json.dumps([{"func": name, "params": params} for name, params in self.functions.items()]),
                             encoding="utf-8")
        result = subprocess.run(["node", "-e", NODE_HARNESS, str(api_path), str(list_path)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["rejections"], [])


if __name__ == "__main__":
    unittest.main()
