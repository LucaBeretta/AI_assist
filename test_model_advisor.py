"""Hardware discovery and recommendation estimates; no model downloads."""

import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import model_advisor as advisor


CATALOGUE = [{"name": name} for name in (
    "qwen3.5", "qwen3-coder", "deepseek-r1", "gemma4", "gpt-oss", "qwen2.5-coder", "gemma3",
)]
VARIANTS = {
    "qwen3.5": [("0.8b", 1.2), ("2b", 2.7), ("4b", 3.3), ("9b", 6.6), ("27b", 17)],
    "qwen3-coder": [("30b", 19), ("480b", 290)],
    "deepseek-r1": [("1.5b", 1.1), ("7b", 4.7), ("8b", 5.2), ("14b", 9), ("70b", 43)],
    "gemma4": [("e2b", 4.6), ("e4b", 6.6), ("12b", 8), ("31b", 19)],
    "gpt-oss": [("20b", 14), ("120b", 65)],
    "qwen2.5-coder": [("1.5b", .986), ("3b", 1.9), ("7b", 4.7), ("32b", 20)],
    "gemma3": [("1b", .815), ("4b", 3.3), ("12b", 8.1)],
}


def variants(family):
    return [{"name": f"{family}:{tag}", "size": f"{size} GB", "size_bytes": int(size * 1e9)}
            for tag, size in VARIANTS.get(family, [])]


def rig():
    return {
        "cpu": "Intel Core i9-14900KF", "ram_total_bytes": 128 * advisor.GIB,
        "ram_available_bytes": 90 * advisor.GIB,
        "gpus": advisor._parse_nvidia("NVIDIA GeForce RTX 4090, 24564, 11491, 610.88, 8.9"),
        "disk_free_bytes": 900 * advisor.GIB, "model_directory": "C:/models",
        "server_local": True, "notes": [],
    }


class DetectionTests(unittest.TestCase):
    def test_nvidia_memory_is_mib_and_exceeds_windows_32bit_adapter_limit(self):
        gpu = rig()["gpus"][0]
        self.assertEqual(gpu["total_bytes"], 24564 * advisor.MIB)
        self.assertEqual(gpu["free_bytes"], 11491 * advisor.MIB)
        self.assertGreater(gpu["total_bytes"], 20 * advisor.GIB)
        self.assertTrue(gpu["supported"])

    def test_nvidia_unsupported_driver_and_bad_values_do_not_claim_support(self):
        self.assertFalse(advisor._parse_nvidia("RTX 4090, 24564, 12000, 530.0, 8.9")[0]["supported"])
        self.assertFalse(advisor._parse_nvidia("GTX 1080, 8192, 7000, 560.0, 6.1")[0]["supported"])
        self.assertIsNone(advisor._parse_nvidia("Unknown GPU, 8192, N/A, N/A")[0]["supported"])
        self.assertEqual(advisor._parse_nvidia("Bad GPU, N/A, N/A, 610.0"), [])

    def test_nvidia_query_falls_back_when_compute_cap_field_is_unavailable(self):
        with patch("model_advisor.shutil.which", return_value="nvidia-smi"), patch(
                "model_advisor._run", side_effect=[RuntimeError("Unsupported field"),
                                                    "NVIDIA GeForce RTX 4090, 24564, 11491, 610.88"]):
            self.assertTrue(advisor._nvidia_hardware()[0]["supported"])

    def test_partial_discovery_keeps_cpu_ram_but_ignores_adapterram_for_vram(self):
        windows = {"cpu": "Test CPU", "logical_processors": 32,
                   "ram_total_bytes": 128 * advisor.GIB, "ram_available_bytes": 80 * advisor.GIB,
                   "gpu_names": ["NVIDIA GeForce RTX 4090"], "AdapterRAM": 4293918720}
        status = io.BytesIO(json.dumps({"version": "0.40.14"}).encode())
        with patch("model_advisor.platform.system", return_value="Windows"), patch(
                "model_advisor._windows_hardware", return_value=windows), patch(
                "model_advisor._nvidia_hardware", side_effect=RuntimeError("Unavailable")), patch(
                "model_advisor.shutil.disk_usage", return_value=SimpleNamespace(free=12345678)), patch(
                "model_advisor.urllib.request.urlopen", return_value=status), patch.dict(os.environ, {"OLLAMA_MODELS": "C:/models"}):
            hardware = advisor.detect_hardware("http://127.0.0.1:11434")
        self.assertEqual(hardware["cpu"], "Test CPU")
        self.assertEqual(hardware["ram_total_bytes"], 128 * advisor.GIB)
        self.assertIsNone(hardware["gpus"][0]["total_bytes"])
        self.assertTrue(hardware["ollama_running"])
        self.assertEqual(hardware["disk_free_bytes"], 12345678)
        self.assertEqual(hardware["model_directory"], "C:/models")

    def test_missing_model_directory_uses_existing_parent_for_disk_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-created" / "models"
            self.assertEqual(advisor._existing_directory(missing), Path(directory).resolve())

    def test_remote_host_does_not_report_local_disk_as_server_disk(self):
        with patch("model_advisor._nvidia_hardware", return_value=[]), patch(
                "model_advisor.platform.system", return_value="Other"), patch(
                "model_advisor._fallback_ram", return_value=(16 * advisor.GIB, 10 * advisor.GIB)), patch(
                "model_advisor.urllib.request.urlopen", side_effect=OSError("Offline")):
            hardware = advisor.detect_hardware("http://192.0.2.1:11434")
        self.assertFalse(hardware["server_local"])
        self.assertIsNone(hardware["model_directory"])
        self.assertIsNone(hardware["disk_free_bytes"])
        self.assertTrue(any("remote server" in note for note in hardware["notes"]))


class RecommendationTests(unittest.TestCase):
    def test_laptop_moe_split_counts_all_weights_and_keeps_resource_guards(self):
        hardware = rig()
        hardware.update(ram_total_bytes=32 * advisor.GIB, ram_available_bytes=10 * advisor.GIB)
        hardware["gpus"] = [{"total_bytes": 16 * advisor.GIB, "free_bytes": 4 * advisor.GIB,
                              "supported": True}]
        coder = variants("qwen3-coder")[0]
        fit, reason = advisor._fit(hardware, coder)
        self.assertEqual(fit, "GPU + CPU split")
        self.assertIn("3.3B active", reason)
        self.assertIn("all weights", reason)
        cards = advisor.recommend_models(hardware, CATALOGUE)
        self.assertIn(coder["name"], [card["name"] for card in cards])
        self.assertIsNone(advisor._fit(hardware, variants("qwen3-coder")[1]))
        self.assertIsNone(advisor._fit(hardware, {**coder, "name": "unknown:30b"}))
        hardware["ram_available_bytes"] = advisor.GIB
        self.assertIsNone(advisor._fit(hardware, coder))
        hardware["ram_available_bytes"] = 10 * advisor.GIB
        hardware["disk_free_bytes"] = advisor.GIB
        self.assertIsNone(advisor._fit(hardware, coder))

    def test_moe_does_not_count_unified_memory_twice(self):
        hardware = rig()
        hardware.update(ram_total_bytes=16 * advisor.GIB, ram_available_bytes=16 * advisor.GIB)
        hardware["gpus"] = [{"total_bytes": 12 * advisor.GIB, "free_bytes": 12 * advisor.GIB,
                              "supported": True, "unified": True}]
        self.assertIsNone(advisor._fit(hardware, variants("qwen3-coder")[0]))

    def setUp(self):
        self.variants = patch("model_advisor.fetch_model_variants", side_effect=variants)
        self.variants.start()
        self.addCleanup(self.variants.stop)

    def test_4090_uses_real_variants_with_conditional_large_gpu_choices(self):
        cards = advisor.recommend_models(rig(), CATALOGUE)
        self.assertEqual(len(cards), 6)
        by_name = {card["name"]: card for card in cards}
        self.assertIn("qwen3.5:9b", by_name)
        self.assertIn("qwen3.5:27b", by_name)
        self.assertIn("qwen3-coder:30b", by_name)
        self.assertEqual(by_name["qwen3.5:9b"]["fit"], "Fits available GPU memory")
        self.assertEqual(by_name["qwen3-coder:30b"]["fit"], "Run one model at a time")
        for card in cards:
            self.assertEqual(card["context_length"], 4096)
            self.assertGreater(card["estimated_memory_bytes"], card["size_bytes"])
            self.assertIn(card["name"], " ".join(card["setup_steps"]))
            self.assertIn("4096", " ".join(card["setup_steps"]))
            self.assertEqual(card["source_url"], f"https://ollama.com/library/{card['name']}")

    def test_multiple_small_gpus_are_not_added_to_predict_one_big_model_fits(self):
        hardware = rig()
        hardware["gpus"] = [{"name": "GPU 1", "total_bytes": 8 * advisor.GIB,
                              "free_bytes": 8 * advisor.GIB, "supported": True}] * 2
        cards = advisor.recommend_models(hardware, CATALOGUE)
        coder = next(card for card in cards if card["name"] == "qwen3-coder:30b")
        self.assertEqual(coder["fit"], "GPU + CPU split")
        self.assertNotIn("qwen3.5:27b", [card["name"] for card in cards])
        self.assertTrue(all(card["estimated_memory_bytes"] <= 7 * advisor.GIB
                            for card in cards if "GPU memory" in card["fit"] or card["fit"] == "Run one model at a time"))

    def test_cpu_only_even_with_lots_of_ram_chooses_small_models(self):
        hardware = rig()
        hardware["gpus"] = []
        cards = advisor.recommend_models(hardware, CATALOGUE)
        self.assertTrue(cards)
        self.assertTrue(all(card["fit"] == "CPU / RAM fallback" for card in cards))
        self.assertTrue(all(advisor._ACTIVE_PARAMETERS.get(card["name"], advisor._parameters(card)) <= 8
                            for card in cards))
        self.assertNotIn("Larger general model", [card["purpose"] for card in cards])

    def test_unknown_gpu_memory_never_claims_gpu_fit(self):
        hardware = rig()
        hardware["gpus"] = [{"name": "Unknown GPU", "total_bytes": None, "supported": None}]
        cards = advisor.recommend_models(hardware, CATALOGUE)
        self.assertTrue(cards)
        self.assertTrue(all(card["fit"] == "CPU / RAM fallback" for card in cards))

    def test_gpu_disabled_in_configuration_is_not_used_for_fit(self):
        hardware = rig()
        hardware["gpus"][0]["available_to_ollama"] = False
        cards = advisor.recommend_models(hardware, CATALOGUE)
        self.assertTrue(cards)
        self.assertTrue(all(card["fit"] == "CPU / RAM fallback" for card in cards))

    def test_small_disk_and_low_ram_exclude_oversized_choices(self):
        hardware = rig()
        hardware["disk_free_bytes"] = 4 * advisor.GIB
        cards = advisor.recommend_models(hardware, CATALOGUE)
        self.assertTrue(cards)
        self.assertTrue(all(card["size_bytes"] + max(512 * advisor.MIB, card["size_bytes"] // 10)
                            <= hardware["disk_free_bytes"] for card in cards))
        hardware["gpus"] = []
        hardware["ram_total_bytes"] = 4 * advisor.GIB
        hardware["ram_available_bytes"] = advisor.GIB
        self.assertEqual(advisor.recommend_models(hardware, CATALOGUE), [])

    def test_remote_hardware_does_not_produce_unverified_recommendations(self):
        hardware = rig()
        hardware["server_local"] = False
        with patch("model_advisor.fetch_model_variants") as fetch:
            self.assertEqual(advisor.recommend_models(hardware, CATALOGUE), [])
            fetch.assert_not_called()

    def test_one_family_failure_does_not_prevent_other_cards(self):
        def partly_offline(family):
            if family == "qwen3.5":
                raise RuntimeError("Page unavailable")
            return variants(family)
        with patch("model_advisor.fetch_model_variants", side_effect=partly_offline):
            cards = advisor.recommend_models(rig(), CATALOGUE)
        self.assertTrue(cards)
        self.assertNotIn("qwen3.5", [card["name"].split(":")[0] for card in cards])
        self.assertEqual(len({card["name"] for card in cards}), len(cards))


if __name__ == "__main__":
    unittest.main()


class HuggingFaceSuggestionTests(unittest.TestCase):
    def model(self, repo, parameters, sizes, downloads=1000):
        return {"repo": repo, "parameters": parameters, "downloads": downloads,
                "variants": [{"name": f"hf.co/{repo}:{quant}", "quant": quant, "size_bytes": int(size * advisor.GIB)}
                             for quant, size in sizes.items()]}

    def test_the_preferred_quantization_that_fits_is_suggested(self):
        models = [self.model("org/Small-8B-GGUF", 8, {"Q4_K_M": 4.9, "Q8_0": 8.5}, 2500000),
                  self.model("org/Big-70B-GGUF", 70, {"Q4_K_M": 42.5, "Q3_K_M": 34.3}),
                  self.model("org/Mid-14B-GGUF", 14, {"Q8_0": 15.7, "Q5_K_M": 10.5})]
        cards = advisor.recommend_hugging_face(rig(), models)
        self.assertEqual([card["name"] for card in cards],
                         ["hf.co/org/Small-8B-GGUF:Q4_K_M", "hf.co/org/Mid-14B-GGUF:Q5_K_M"])
        first = cards[0]
        self.assertEqual(first["source"], "Hugging Face")
        self.assertEqual(first["source_url"], "https://huggingface.co/org/Small-8B-GGUF")
        self.assertIn("2,500,000 downloads", first["purpose"])
        self.assertIn("ollama pull hf.co/org/Small-8B-GGUF:Q4_K_M", " ".join(first["setup_steps"]))

    def test_large_models_are_not_suggested_for_cpu_only_computers(self):
        laptop = {"ram_total_bytes": 64 * advisor.GIB, "ram_available_bytes": 50 * advisor.GIB, "gpus": [],
                  "disk_free_bytes": 500 * advisor.GIB, "server_local": True}
        models = [self.model("org/Mid-27B-GGUF", 27, {"Q4_K_M": 16.5}),
                  self.model("org/Unknown-GGUF", None, {"Q4_K_M": 4.0}),
                  self.model("org/Tiny-1.5B-GGUF", 1.5, {"Q4_K_M": 1.1})]
        self.assertEqual([card["name"] for card in advisor.recommend_hugging_face(laptop, models)],
                         ["hf.co/org/Unknown-GGUF:Q4_K_M", "hf.co/org/Tiny-1.5B-GGUF:Q4_K_M"])

    def test_skips_names_already_suggested_and_remote_servers(self):
        models = [self.model("org/Small-8B-GGUF", 8, {"Q4_K_M": 4.9, "Q5_K_M": 5.7})]
        cards = advisor.recommend_hugging_face(rig(), models, skip={"hf.co/org/Small-8B-GGUF:Q4_K_M"})
        self.assertEqual([card["name"] for card in cards], ["hf.co/org/Small-8B-GGUF:Q5_K_M"])
        self.assertEqual(advisor.recommend_hugging_face({**rig(), "server_local": False}, models), [])
        self.assertEqual(len(advisor.recommend_hugging_face(rig(), [self.model(f"org/M{i}-7B-GGUF", 7, {"Q4_K_M": 4.4})
                                                                   for i in range(5)])), 3)

