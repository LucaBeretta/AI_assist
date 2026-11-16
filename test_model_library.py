"""Checks for live-library parsing and Ollama's streamed download protocol."""

import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch

from model_library import (
    DownloadCancelled, fetch_model_variants, fetch_popular_models, pull_model,
)


def model_row(name, description, pills, pulls="12.3M"):
    # These classes and nested spans match the official library's HTML.
    badges = "".join(f'<span class="inline-flex rounded-md bg-indigo-50">{pill}</span>'
                     for pill in pills)
    return f'''<li class="flex items-baseline border-b py-6">
      <a href="/library/{name}" class="group w-full space-y-5">
        <div title="{name}"><h2><span>{name}</span></h2><p>{description}</p></div>
        <div>{badges}<p><span><svg><path /></svg><span>{pulls}</span>
        <span class="hidden sm:flex">&nbsp;Pulls</span></span><span>12 Tags</span></p></div>
      </a></li>'''


def variant_row(tag, size):
    return f'''<div class="group px-4 py-3">
      <a href="/library/{tag}" class="md:hidden flex flex-col">
        <div><span>{tag}</span></span></div>
        <span><span class="font-mono">6995872bfe4c</span> &middot; {size} &middot; 128K context window</span>
      </a>
      <div class="hidden md:flex"><a href="/library/{tag}">{tag}</a>
        <input class="command hidden" value="{tag}" />
        <p class="col-span-2">{size}</p><p>128K</p><div>Text</div></div></div>'''


def response(contents):
    return io.BytesIO(contents.encode("utf-8") if isinstance(contents, str) else contents)


class LibraryTests(unittest.TestCase):
    def test_popularity_order_description_counts_and_non_chat_filtering(self):
        html = '<div id="repo"><ul>' + "".join([
            model_row("llama3.1", "Chat &amp; coding", ["tools", "8b", "70b"], "121.1M"),
            model_row("nomic-embed-text", "Embedding only", ["embedding", "137m"]),
            model_row("cloud-only", "Remote model", ["cloud"]),
            model_row("deepseek-r1", "Reasoning model", ["thinking", "8b", "14b"], "95.1M"),
            model_row("other", "Another model", ["3b"]),
        ]) + '</ul></div>'
        with patch("model_library.urllib.request.urlopen", return_value=response(html)) as opened:
            models = fetch_popular_models(limit=2)
        self.assertEqual([model["name"] for model in models], ["llama3.1", "deepseek-r1"])
        self.assertEqual(models[0]["description"], "Chat & coding")
        self.assertEqual(models[0]["pulls"], "121.1M")
        self.assertEqual(models[1]["sizes"], ["8b", "14b"])
        self.assertIn("thinking", models[1]["capabilities"])
        self.assertIn("sort=popular", opened.call_args.args[0].full_url)

    def test_changed_or_empty_library_has_actionable_error(self):
        with patch("model_library.urllib.request.urlopen", return_value=response("<html>New format</html>")):
            with self.assertRaisesRegex(RuntimeError, "website may have changed"):
                fetch_popular_models()

    def test_variants_have_real_tags_and_sizes_without_cloud_or_quantized_noise(self):
        html = "".join([
            variant_row("deepseek-r1:latest", "5.2GB"),
            variant_row("deepseek-r1:8b", "5.2GB"),
            variant_row("deepseek-r1:1.5b", "1.1GB"),
            variant_row("deepseek-r1:14b", "9.0GB"),
            variant_row("deepseek-r1:8b-q4_K_M", "5.2GB"),
            variant_row("deepseek-r1:671b-cloud", "404GB"),
            variant_row("deepseek-r1:cloud", "Cloud"),
        ])
        with patch("model_library.urllib.request.urlopen", return_value=response(html)):
            variants = fetch_model_variants("deepseek-r1")
        self.assertEqual([item["name"] for item in variants], [
            "deepseek-r1:1.5b", "deepseek-r1:8b", "deepseek-r1:latest", "deepseek-r1:14b",
        ])
        self.assertEqual(variants[1]["size"], "5.2 GB")
        self.assertEqual(variants[1]["size_bytes"], 5_200_000_000)

    def test_quantized_tags_remain_available_if_no_readable_alias_exists(self):
        with patch("model_library.urllib.request.urlopen", return_value=response(
                variant_row("example:7b-q4_0", "4.1GB"))):
            variants = fetch_model_variants("example")
        self.assertEqual(variants[0]["name"], "example:7b-q4_0")

    def test_no_local_variants_and_invalid_family_names(self):
        with patch("model_library.urllib.request.urlopen", return_value=response(
                variant_row("example:cloud", "Cloud"))):
            with self.assertRaisesRegex(RuntimeError, "No downloadable local variants"):
                fetch_model_variants("example")
        for name in ("../private", "https://example.com", "bad/name", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                fetch_model_variants(name)


class DownloadTests(unittest.TestCase):
    def test_pull_uses_streaming_api_and_reports_bytes_until_explicit_success(self):
        events = [{"status": "pulling manifest"},
                  {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 50},
                  {"status": "success"}]
        stream = response("\n".join(json.dumps(event) for event in events) + "\n")
        received = []
        with patch("model_library.urllib.request.urlopen", return_value=stream) as opened:
            pull_model("http://127.0.0.1:11434/", "deepseek-r1:8b", received.append)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/pull")
        self.assertEqual(json.loads(request.data), {"model": "deepseek-r1:8b", "stream": True})
        self.assertEqual(received, events)
        self.assertTrue(stream.closed)

    def test_eof_never_counts_as_download_success(self):
        for payload in ("", '{"status":"pulling manifest"}\n', '\n\n'):
            with self.subTest(payload=payload), patch(
                    "model_library.urllib.request.urlopen", return_value=response(payload)):
                with self.assertRaisesRegex(RuntimeError, "before Ollama confirmed success"):
                    pull_model("http://localhost:11434", "example:8b", lambda event: None)

    def test_invalid_and_error_streams_fail(self):
        for payload in ('not-json\n', '[]\n', '{}\n',
                        '{"error":"manifest not found"}\n',
                        '{"status":"pulling","total":"lots"}\n'):
            with self.subTest(payload=payload), patch(
                    "model_library.urllib.request.urlopen", return_value=response(payload)):
                with self.assertRaises(RuntimeError):
                    pull_model("http://localhost:11434", "example:8b", lambda event: None)

    def test_cancel_before_network_and_between_progress_messages(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("model_library.urllib.request.urlopen") as opened:
            with self.assertRaises(DownloadCancelled):
                pull_model("http://localhost:11434", "example:8b", lambda event: None, cancelled)
            opened.assert_not_called()
        cancelled.clear()
        stream = response('{"status":"pulling manifest"}\n{"status":"success"}\n')
        with patch("model_library.urllib.request.urlopen", return_value=stream):
            with self.assertRaises(DownloadCancelled):
                pull_model("http://localhost:11434", "example:8b", lambda event: cancelled.set(), cancelled)
        self.assertTrue(stream.closed)

    def test_http_and_connection_errors_are_reported(self):
        failures = [urllib.error.HTTPError("http://localhost", 404, "Not found", {}, None),
                    urllib.error.URLError("Connection refused"), TimeoutError("Timed out")]
        for failure in failures:
            with self.subTest(failure=failure), patch(
                    "model_library.urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    pull_model("http://localhost:11434", "example:8b", lambda event: None)


if __name__ == "__main__":
    unittest.main()
