"""The future paired inference protocol stays data-only and bounded."""
import unittest
from peer_inference_contract import InvalidInferenceRequest, validate_cancel, validate_request


class InferenceContractTests(unittest.TestCase):
    def setUp(self):
        self.request = {"request_id": "stream_1", "model": "qwen2.5:7b", "messages": [
            {"role": "system", "content": "Respond carefully."},
            {"role": "user", "content": "Hello"}]}

    def test_valid_request(self):
        self.assertEqual(validate_request(self.request), self.request)

    def test_refuses_commands_files_tools_and_unknown_fields(self):
        for key, value in (("command", "rm -rf /"), ("file_path", "/secret"),
                           ("tools", []), ("download", "model"), ("host", "example.com")):
            with self.subTest(key=key), self.assertRaises(InvalidInferenceRequest):
                validate_request(dict(self.request, **{key: value}))
        for item in ({"role": "tool", "content": "x"},
                     {"role": "user", "content": "x", "tool_calls": []},
                     {"role": "user", "content": {"type": "image"}},
                     {"role": ["user"], "content": "x"}, {"role": {"name": "user"}, "content": "x"}):
            with self.subTest(item=item), self.assertRaises(InvalidInferenceRequest):
                validate_request(dict(self.request, messages=[item]))

    def test_rejects_oversized_and_malformed_data(self):
        cases = [dict(self.request, model="http://example.com"),
                 dict(self.request, messages=[]),
                 dict(self.request, messages=[{"role": "user", "content": "x"}] * 65),
                 dict(self.request, messages=[{"role": "user", "content": "x" * 65537}]),
                 dict(self.request, request_id="bad id"),
                 dict(self.request, request_id=1)]
        for payload in cases:
            with self.subTest(payload=str(payload)[:70]), self.assertRaises(InvalidInferenceRequest):
                validate_request(payload)

    def test_cancel_never_accepts_commands(self):
        self.assertEqual(validate_cancel({"request_id": "stream_1"}), "stream_1")
        for payload in ({"request_id": "stream_1", "command": "stop"},
                        {"request_id": "bad id"}, {}):
            with self.assertRaises(InvalidInferenceRequest):
                validate_cancel(payload)


if __name__ == "__main__":
    unittest.main()
