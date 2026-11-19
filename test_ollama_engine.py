"""Model context contracts retained from the legacy download dialog tests."""
import io
import json
import unittest
from unittest.mock import patch

from app_core import runtime


class ContextContractTests(unittest.TestCase):
    def test_recommended_context_is_sent_to_chat_and_default_context_is_preserved(self):
        for context in (None, 4096):
            with self.subTest(context=context), patch("app_core.ollama.urllib.request.urlopen", return_value=io.BytesIO(
                    b'{"message":{"content":"Hello"},"done":true}\n')) as opened:
                self.assertEqual(runtime.request_ollama([], "test:1b", context), "Hello")
                payload = json.loads(opened.call_args.args[0].data)
                if context is None:
                    self.assertNotIn("options", payload)
                else:
                    self.assertEqual(payload["options"]["num_ctx"], 4096)

    def test_preloading_uses_recommended_context_and_unloading_keeps_its_original_behavior(self):
        with patch("app_core.runtime.ollama_api") as api:
            runtime.set_model_running("test:1b", True, 4096)
            payload = api.call_args.args[1]
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertEqual(payload["keep_alive"], -1)
            runtime.set_model_running("test:1b", False, 4096)
            payload = api.call_args.args[1]
            self.assertEqual(payload["keep_alive"], 0)
            self.assertNotIn("options", payload)
