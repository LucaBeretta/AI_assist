"""Standard-library client for Colibri's OpenAI-compatible HTTP server."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import http.client
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app_core.streaming import open_stream


def normalize_url(value):
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/v1")):
        raise ValueError("Use a Colibri server URL such as http://127.0.0.1:8000/v1.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Choose a valid server port.") from error
    return value if parsed.path == "/v1" else value + "/v1"


class ColibriClient:
    def __init__(self, url, key=""):
        self.url = normalize_url(url)
        self.key = key

    def request(self, path, payload=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        return urllib.request.Request(self.url + path, headers=headers,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None)

    @contextmanager
    def open(self, request, timeout, cancel_event=None):
        with ExitStack() as connection:
            try:
                response = connection.enter_context(open_stream(request, timeout, cancel_event))
            except urllib.error.HTTPError as error:
                # Server errors may echo request data. Do not display raw bodies or keys.
                code = error.code
                error.close()
                detail = {401: "Check the Colibri API key.", 403: "Check the API key and server access settings.",
                          400: "Check model support, context size, and tool-calling support.",
                          404: "Check the server URL and selected model.",
                          429: "The server is busy; retry later."}.get(code, "Check the Colibri server log.")
                raise RuntimeError(f"Colibri returned HTTP {code}. {detail}") from error
            except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
                raise RuntimeError("Could not reach Colibri. Start coli serve and check the server URL.") from error
            yield response

    def json(self, path, payload=None, timeout=15):
        try:
            with self.open(self.request(path, payload), timeout) as response:
                data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Response is too large.")
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("error"):
                raise ValueError("Invalid API response.")
            return result
        except (ValueError, OSError, http.client.HTTPException) as error:
            raise RuntimeError("Colibri returned an invalid or interrupted response.") from error

    def models(self):
        data = self.json("/models").get("data")
        if not isinstance(data, list):
            raise RuntimeError("Colibri returned an invalid model list.")
        return sorted({item["id"] for item in data if isinstance(item, dict)
                       and isinstance(item.get("id"), str) and item["id"].strip()})

    def metadata(self, model):
        # /v1/models does not promise Ollama capability flags or native context size.
        if model not in self.models():
            raise RuntimeError("The selected model is no longer served by Colibri.")
        return dict(name=model, capabilities=[], context_length=None, family=None,
                    parameter_size=None, quantization=None, source="Colibri /v1/models")

    def review(self, messages, model, cancel_event, max_tokens=768, timeout=120):
        """One bounded, text-only review request; its output cannot call tools.

        Keep a handle to the actual socket so Stop can interrupt pending reads
        instead of merely abandoning an HTTP worker while it waits for a model.
        A remote server may still ignore client disconnection; the application
        owns any additional lifecycle cleanup for a local managed server.
        """
        from coding_agent import _check_cancelled
        _check_cancelled(cancel_event)
        if not isinstance(model, str) or not model.strip() or len(model) > 2000:
            raise ValueError("Choose a Colibri review model.")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 256 <= max_tokens <= 1024:
            raise ValueError("Review output must be between 256 and 1,024 tokens.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not .1 <= timeout <= 120:
            raise ValueError("Review timeout must be between 0.1 and 120 seconds.")
        if (not isinstance(messages, list) or not messages or any(
                not isinstance(message, dict) or set(message) != {"role", "content"}
                or not isinstance(message["role"], str) or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str) for message in messages)):
            raise ValueError("Review messages must contain plain text without tool history.")
        payload = dict(model=model, messages=messages, stream=False, max_tokens=max_tokens)
        body = json.dumps(payload).encode("utf-8")
        if len(body) > 128000:
            raise ValueError("The review context is too large.")
        parsed = urllib.parse.urlsplit(self.url)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=timeout)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        completed, sockets = queue.Queue(maxsize=1), []
        abort = threading.Event()

        def request_worker():
            response = None
            try:
                connection.connect()
                sockets.append(connection.sock)
                if abort.is_set():
                    return
                _check_cancelled(cancel_event)
                connection.request("POST", parsed.path.rstrip("/") + "/chat/completions", body=body, headers=headers)
                response = connection.getresponse()
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Colibri review returned HTTP {response.status}. Check its model and server settings.")
                data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Review response is too large.")
                result = json.loads(data)
                if not isinstance(result, dict) or result.get("error"):
                    raise ValueError("Invalid review response.")
                choices = result.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise ValueError("Invalid review choices.")
                choice = choices[0]
                message = choice.get("message")
                if (choice.get("finish_reason") != "stop" or not isinstance(message, dict)
                        or message.get("role") != "assistant" or message.get("tool_calls") not in (None, [])
                        or message.get("function_call") is not None or not isinstance(message.get("content"), str)
                        or not message["content"].strip() or len(message["content"]) > 32000):
                    raise ValueError("Review was truncated, empty, or attempted a tool call.")
                completed.put((message["content"].strip(), None))
            except Exception as error:
                completed.put((None, error))
            finally:
                if response is not None:
                    response.close()
                connection.close()

        def disconnect():
            abort.set()
            for active in list(sockets):
                if active is not None:
                    try:
                        active.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    active.close()

        worker = threading.Thread(target=request_worker, daemon=True)
        deadline = time.monotonic() + timeout
        worker.start()
        try:
            while True:
                _check_cancelled(cancel_event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Colibri review exceeded its time limit.")
                try:
                    result, error = completed.get(timeout=min(.1, remaining))
                except queue.Empty:
                    continue
                _check_cancelled(cancel_event)
                if error is not None:
                    if isinstance(error, RuntimeError):
                        # Only our own status-code error reaches this branch;
                        # do not expose a server's potentially sensitive body.
                        raise error
                    raise RuntimeError("Colibri review failed or returned an invalid response.") from error
                return result
        finally:
            disconnect()
            worker.join(timeout=1)

    def chat(self, messages, model, on_chunk=None, on_metrics=None, cancel_event=None):
        payload = dict(model=model, messages=messages, stream=True,
                       stream_options={"include_usage": True})
        parts, usage = [], {}
        started = time.monotonic()
        done = False
        received = 0
        try:
            with self.open(self.request("/chat/completions", payload), 1800, cancel_event) as response:
                event = []
                while True:
                    line = response.readline(1024 * 1024 + 1)
                    if not line:
                        break
                    received += len(line)
                    if len(line) > 1024 * 1024 or received > 16 * 1024 * 1024:
                        raise RuntimeError("Colibri returned too much chat output.")
                    line = line.decode("utf-8").rstrip("\r\n")
                    if line:
                        if line.startswith("data:"):
                            event.append(line[5:].lstrip(" "))
                        continue
                    if not event:
                        continue
                    data, event = "\n".join(event), []
                    if data == "[DONE]":
                        done = True
                        break
                    chunk = json.loads(data)
                    if not isinstance(chunk, dict) or chunk.get("error"):
                        raise RuntimeError("Colibri reported a streaming error.")
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices", [])
                    if choices:
                        choice = choices[0]
                        if choice.get("finish_reason") == "length":
                            raise RuntimeError("Colibri reached its output/context limit. Reduce the prompt or increase the server limits.")
                        text = choice.get("delta", {}).get("content")
                        if text is not None and not isinstance(text, str):
                            raise ValueError("Invalid content.")
                        if text:
                            parts.append(text)
                            if on_chunk:
                                on_chunk(text)
            if not done:
                raise RuntimeError("Colibri disconnected before finishing the response. Please retry.")
        except (ValueError, OSError, http.client.HTTPException, AttributeError, TypeError, IndexError) as error:
            raise RuntimeError("Colibri returned an invalid or interrupted stream.") from error
        content = "".join(parts)
        if not content.strip():
            raise RuntimeError("Colibri returned no answer text.")
        if on_metrics:
            metrics = {"total_duration": (time.monotonic() - started) * 1e9}
            for source, target in (("prompt_tokens", "prompt_eval_count"), ("completion_tokens", "eval_count")):
                if type(usage.get(source)) is int and usage[source] >= 0:
                    metrics[target] = usage[source]
            on_metrics(metrics)
        return content

    def agent_chat(self, host, model, messages, context_length, cancel_event):
        from coding_agent import _request_messages, _check_cancelled, TOOLS
        messages, prediction = _request_messages(messages, context_length)
        messages = deepcopy(messages)
        pending = []
        for message_index, message in enumerate(messages):
            message.pop("thinking", None)
            if message["role"] == "assistant":
                pending = []
                for index, call in enumerate(message.get("tool_calls", [])):
                    call.setdefault("id", f"call_{message_index}_{index}")
                    call["type"] = "function"
                    arguments = call["function"].get("arguments", {})
                    if not isinstance(arguments, str):
                        call["function"]["arguments"] = json.dumps(arguments)
                    pending.append(call["id"])
            elif message["role"] == "tool":
                if not pending:
                    raise RuntimeError("Saved tool history is incomplete. Start a new developer chat.")
                message["tool_call_id"] = pending.pop(0)
                message.pop("tool_name", None)
        payload = dict(model=model, messages=messages, tools=TOOLS, stream=False, max_tokens=prediction)
        completed = queue.Queue()

        def worker():
            try:
                completed.put((self.json("/chat/completions", payload, timeout=1800), None))
            except Exception as error:
                completed.put((None, error))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            _check_cancelled(cancel_event)
            try:
                result, error = completed.get(timeout=.1)
                if error:
                    raise error
                choice = result["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise RuntimeError("Colibri truncated the agent response. Increase server limits before retrying.")
                message = choice["message"]
                if message.get("content") is None:
                    message["content"] = ""
                return {"message": message}
            except queue.Empty:
                continue
            except (KeyError, IndexError, TypeError, AttributeError) as error:
                raise RuntimeError("Colibri returned an invalid agent response.") from error
