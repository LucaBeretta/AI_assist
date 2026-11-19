"""Plain status and saved theme helpers, without native windows."""
import unittest
from unittest.mock import patch

import ollama_engine
from appearance import DARK_THEMES, toggled_theme
from status_values import generation_rate, is_cloud_model, is_local, placement, short_tokens


class HelperTests(unittest.TestCase):
    def test_toggle_pairs(self):
        self.assertEqual(toggled_theme("Night"), "Day")
        self.assertEqual(toggled_theme("Day"), "Night")
        self.assertEqual(toggled_theme("Dark"), "Day")
        self.assertEqual(toggled_theme("Light"), "Night")
        self.assertEqual(toggled_theme("Warm"), "Night")
        self.assertIn("Night", DARK_THEMES)

    def test_short_tokens(self):
        self.assertEqual(short_tokens(950), "950")
        self.assertEqual(short_tokens(6200), "6.2k")
        self.assertEqual(short_tokens(16384), "16k")
        self.assertEqual(short_tokens(131072), "131k")

    def test_placement(self):
        self.assertEqual(placement({"size": 100, "size_vram": 100}), "gpu")
        self.assertEqual(placement({"size": 100, "size_vram": 0}), "cpu")
        self.assertEqual(placement({"size": 100, "size_vram": 60}), "gpu 60% · cpu")
        self.assertEqual(placement(None), "")

    def test_privacy_helpers(self):
        self.assertTrue(is_local("http://localhost:11434"))
        self.assertTrue(is_local("127.0.0.1:11434"))
        self.assertFalse(is_local("http://192.168.1.20:11434"))
        self.assertTrue(is_cloud_model("gpt-oss:120b-cloud"))
        self.assertFalse(is_cloud_model("qwen3:14b"))

    def test_generation_rate(self):
        self.assertAlmostEqual(generation_rate({"eval_count": 50, "eval_duration": 2_000_000_000}), 25.0)
        self.assertIsNone(generation_rate({}))

    def test_running_details_reads_api_ps(self):
        reply = {"models": [{"name": "alpha:8b", "size": 8, "size_vram": 6, "context_length": 8192}, "junk"]}
        with patch("ollama_engine.ollama_api", return_value=reply):
            self.assertEqual(ollama_engine.running_details(),
                             {"alpha:8b": {"size": 8, "size_vram": 6, "context_length": 8192}})
        with patch("ollama_engine.ollama_api", return_value={}):
            with self.assertRaises(RuntimeError):
                ollama_engine.running_details()
