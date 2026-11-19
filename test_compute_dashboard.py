"""Read-only compute dashboard data contract and privacy tests.

No GPU, Ollama or network service is required. Headless-safe: no Tk root.
"""
import unittest
from unittest.mock import patch

import compute_telemetry
from compute_dashboard import display_bytes, format_snapshot, normalize_peer_rows, usage_percent


class DashboardFormattingTests(unittest.TestCase):
    def test_missing_metrics_never_show_zero(self):
        snapshot = format_snapshot({"cpu": {"name": "CPU"}, "ram": {}, "models": {}})
        self.assertEqual(snapshot["cpu_usage"], "unavailable")
        self.assertIn("unavailable", snapshot["ram"])
        self.assertFalse(snapshot["models_available"])

    def test_valid_local_metrics(self):
        snapshot = format_snapshot({
            "device": "test-node", "platform": "Linux",
            "cpu": {"name": "Example CPU", "percent": 36.7, "logical_cores": 8},
            "ram": {"total_bytes": 8 * 1024 ** 3, "available_bytes": 3 * 1024 ** 3},
            "gpus": [{"name": "NVIDIA", "percent": 72.1,
                      "used_bytes": 5 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}],
            "models": {"available": True, "installed": ["model:7b"], "loaded": ["model:7b"]},
        })
        self.assertEqual(snapshot["cpu_usage"], "36.7%")
        self.assertEqual(snapshot["ram"], "5.0 GiB / 8.0 GiB")
        self.assertEqual(snapshot["gpus"][0]["percent"], "72.1%")
        self.assertEqual(snapshot["loaded"], ["model:7b"])

    def test_utilization_unknown_and_bounds(self):
        self.assertEqual(display_bytes(None), "unavailable")
        self.assertEqual(usage_percent(None), "unavailable")
        self.assertEqual(usage_percent(120), "unavailable")
        self.assertEqual(usage_percent(-1), "unavailable")


class PeerPrivacyTests(unittest.TestCase):
    def test_pairs_are_not_reported_as_online(self):
        rows = normalize_peer_rows({
            "node123": {"address": "192.168.1.3", "port": 49381,
                        "token": "HIGHLY_PRIVATE_SECRET",
                        "fingerprint": "certificate-secrets"},
        })
        self.assertEqual(len(rows), 1)
        self.assertIn("telemetry unavailable", rows[0]["state"])
        self.assertNotIn("token", repr(rows))
        self.assertNotIn("HIGHLY_PRIVATE_SECRET", repr(rows))
        self.assertNotIn("certificate-secrets", repr(rows))

    def test_unpaired_and_malformed_peer_records(self):
        self.assertEqual(normalize_peer_rows({}), [])
        self.assertEqual(normalize_peer_rows("not a dict"), [])
        self.assertEqual(normalize_peer_rows({"node": None}), [])


class TelemetryTests(unittest.TestCase):
    def test_remote_ollama_is_not_queried(self):
        with patch("compute_telemetry.urllib.request.urlopen") as opener:
            result = compute_telemetry._installed_models("http://192.168.1.21:11434")
        self.assertFalse(result["available"])
        opener.assert_not_called()

    def test_loopback_only_for_model_polling(self):
        self.assertTrue(compute_telemetry._is_loopback_ollama("http://127.0.0.1:11434"))
        for host in ("http://8.8.8.8", "http://192.168.1.4:11434",
                     "https://localhost:11434", "http://local.example"):
            self.assertFalse(compute_telemetry._is_loopback_ollama(host), host)

    def test_data_sampler_does_not_send_peer_requests(self):
        with patch("compute_telemetry.detect_hardware", return_value={
            "cpu": "Demo", "ram_total_bytes": 1024, "ram_available_bytes": 512,
            "gpus": [], "logical_processors": 8,
        }), patch("compute_telemetry._cpu_times", return_value=(100, 50)), patch(
            "compute_telemetry._memory", return_value=(1024, 512)), patch(
            "compute_telemetry._gpu_usage", return_value=[]), patch(
            "compute_telemetry._installed_models", return_value={
                "installed": ["demo:1b"], "loaded": [], "available": True, "note": ""
            }):
            telemetry = compute_telemetry.TelemetrySampler()
            data = telemetry.sample()
        self.assertIn("cpu", data)
        self.assertEqual(data["models"]["installed"], ["demo:1b"])
        self.assertNotIn("tokens", data)
        self.assertNotIn("prompts", data)


if __name__ == "__main__":
    unittest.main()
