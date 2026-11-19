"""Stream protocol and performance metadata checks, without an Ollama server."""

import io
import json
import unittest
from unittest.mock import patch

from ollama_engine import request_ollama
from response_metrics import metrics_summary, response_metrics


class StreamingTests(unittest.TestCase):
    def request(self, frames, **options):
        wire = b"\n".join(json.dumps(frame, ensure_ascii=False).encode("utf-8") for frame in frames)
        with patch("ollama_engine.urllib.request.urlopen", return_value=io.BytesIO(wire)) as opened:
            answer = request_ollama([], "test:8b", **options)
            self.assertTrue(json.loads(opened.call_args.args[0].data)["stream"])
            return answer

    def test_chunks_arrive_in_order_and_final_metrics_are_separate(self):
        chunks, metrics = [], []
        answer = self.request([
            {"message": {"thinking": "Hidden reasoning", "content": ""}, "done": False},
            {"message": {"content": "Hello "}, "done": False},
            {"message": {"content": "世界"}, "done": True, "eval_count": 10, "eval_duration": 2000000000},
        ], on_chunk=chunks.append, on_metrics=metrics.append)
        self.assertEqual(answer, "Hello 世界")
        self.assertEqual(chunks, ["Hello ", "世界"])
        self.assertEqual(metrics, [{"eval_count": 10, "eval_duration": 2000000000}])

    def test_incomplete_stream_and_server_error_are_not_success(self):
        for frames, reason in (([{"message": {"content": "Partial"}}], "closed"),
                               ([{"error": "Not enough memory"}], "Not enough memory"),
                               ([{"done": True}], "did not contain")):
            with self.subTest(frames=frames), self.assertRaisesRegex(RuntimeError, reason):
                self.request(frames)

    def test_malformed_and_oversized_frames_fail(self):
        for wire in (b"not json\n", b"\xff\n", b"[]\n", b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(wire)), patch("ollama_engine.urllib.request.urlopen", return_value=io.BytesIO(wire)):
                with self.assertRaises(RuntimeError):
                    request_ollama([], "test:8b")

    def test_metrics_ignore_invalid_values_and_zero_duration(self):
        self.assertEqual(response_metrics(dict(eval_count=True, eval_duration=float("nan"),
            load_duration=-1, total_duration=10**1000)), {})
        self.assertEqual(metrics_summary(dict(eval_count=10, eval_duration=0)), "")
        self.assertIn("5.0 tokens/s", metrics_summary(dict(eval_count=10, eval_duration=2000000000)))
