"""Apple silicon: unified memory counts as GPU memory for Ollama (Metal)."""

import os
import unittest
from unittest.mock import patch

import model_advisor
from model_advisor import GIB, apple_gpu_budget, detect_hardware, recommend_models

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               200000.
Pages active:                             500000.
Pages inactive:                           300000.
Pages speculative:                         50000.
Pages wired down:                         150000.
Pages purgeable:                           10000.
"""


def fake_run(memory):
    def run(arguments, timeout=15):
        if arguments == ["sysctl", "-n", "machdep.cpu.brand_string"]:
            return "Apple M3 Pro"
        if arguments == ["sysctl", "-n", "hw.memsize"]:
            return str(memory)
        if arguments == ["vm_stat"]:
            return VM_STAT
        raise AssertionError(f"unexpected command {arguments}")
    return run


def variant(name, gigabytes):
    return {"name": name, "size": f"{gigabytes} GB", "size_bytes": int(gigabytes * 1e9)}


class AppleSiliconTests(unittest.TestCase):
    def detect(self, memory):
        with patch("model_advisor.platform.system", return_value="Darwin"), \
                patch("model_advisor.platform.machine", return_value="arm64"), \
                patch("model_advisor._run", side_effect=fake_run(memory)), \
                patch("model_advisor.shutil.which", return_value=None), \
                patch("model_advisor.shutil.disk_usage", return_value=type("Usage", (), {"free": 500 * GIB})()), \
                patch("model_advisor.urllib.request.urlopen", side_effect=OSError("offline")), \
                patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""}):
            return detect_hardware("http://localhost:11434")

    def test_unified_memory_becomes_the_gpu(self):
        hardware = self.detect(32 * GIB)
        self.assertEqual(hardware["cpu"], "Apple M3 Pro")
        self.assertEqual(hardware["ram_total_bytes"], 32 * GIB)
        self.assertEqual(hardware["ram_available_bytes"], (200000 + 300000 + 50000 + 10000) * 16384)
        gpu = hardware["gpus"][0]
        self.assertTrue(gpu["unified"] and gpu["supported"])
        self.assertEqual(gpu["total_bytes"], apple_gpu_budget(32 * GIB))
        self.assertAlmostEqual(gpu["total_bytes"] / GIB, 21.33, places=1)
        self.assertTrue(any("Apple silicon" in note for note in hardware["notes"]))
        self.assertFalse(any("not verified" in note for note in hardware["notes"]))
        self.assertEqual(apple_gpu_budget(64 * GIB), 48 * GIB)

    def test_mac_gets_gpu_recommendations_above_8b(self):
        hardware = self.detect(32 * GIB)
        catalog = [{"name": "deepseek-r1", "description": "reasoning", "sizes": ["14b"], "capabilities": []}]
        with patch("model_advisor.fetch_model_variants", return_value=[variant("deepseek-r1:14b", 9.0)]):
            picks = recommend_models(hardware, catalog)
        self.assertTrue(picks, "a 14B model should fit a 32 GB Apple silicon Mac")
        self.assertEqual(picks[0]["name"], "deepseek-r1:14b")
        self.assertIn(picks[0]["fit"], ("Fits available GPU memory", "Run one model at a time"))  # GPU, not CPU fallback

    def test_intel_mac_and_failed_probe_fall_back(self):
        with patch("model_advisor.platform.system", return_value="Darwin"), \
                patch("model_advisor.platform.machine", return_value="arm64"), \
                patch("model_advisor._apple_hardware", side_effect=RuntimeError("sysctl failed")), \
                patch("model_advisor._fallback_ram", return_value=(16 * GIB, None)), \
                patch("model_advisor._nvidia_hardware", return_value=[]), \
                patch("model_advisor.urllib.request.urlopen", side_effect=OSError("offline")):
            hardware = detect_hardware("http://localhost:11434")
        self.assertEqual(hardware["gpus"], [])
        self.assertTrue(any("unavailable" in note for note in hardware["notes"]))


if __name__ == "__main__":
    unittest.main()
