"""Scoped Ollama and Colibri memory handoff without native UI objects."""
import json
import urllib.request

from coding_agent import AgentCancelled
import colibri_launcher as launcher


def _cancelled(cancel):
    if cancel.is_set():
        raise AgentCancelled("Two-engine work stopped.")


def unload_worker(host, model):
    """Release only the selected worker on its configured Ollama server."""
    payload = {"model": model, "stream": False, "keep_alive": 0}
    request = urllib.request.Request(host.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise RuntimeError("Ollama returned too much unload output.")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("error") or result.get("done") is not True:
        raise RuntimeError("Ollama did not confirm that the selected worker was unloaded.")


def memory_callbacks(snapshot, host, model, cancel):
    """Capture provider settings; never stop remote or unknown Colibri servers."""
    state = {"url": snapshot["url"], "start_attempted": False}
    managed = launcher.is_local_url(snapshot["url"]) and bool(snapshot["directory"])
    handoff = snapshot["manage_memory"] and managed and launcher.is_local_url(host)

    def before_worker():
        _cancelled(cancel)
        if handoff:
            launcher.stop_server(snapshot["directory"])
            _cancelled(cancel)
            # A worker loaded while Colibri owned the GPU can remain on CPU.
            # Reload only that worker after releasing Colibri's allocation.
            unload_worker(host, model)
        _cancelled(cancel)

    def before_review():
        _cancelled(cancel)
        if handoff:
            unload_worker(host, model)
        _cancelled(cancel)
        if managed:
            state["start_attempted"] = True
            report = launcher.start_server(snapshot["directory"], timeout=300, cancel_event=cancel)
            if snapshot["follow_report"]:
                state["url"] = launcher.server_url(report) or state["url"]
        _cancelled(cancel)

    def after_review():
        if handoff and state["start_attempted"]:
            launcher.stop_server(snapshot["directory"])

    return state, before_worker, before_review, after_review
