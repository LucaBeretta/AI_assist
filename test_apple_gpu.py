"""Apple silicon GPU load from the I/O registry, parsed without a Mac or ioreg."""
import unittest
from unittest.mock import patch

import compute_telemetry

IOREG = '''+-o AGXAcceleratorG13X  <class AGXAcceleratorG13X, id 0x100000b0b, registered, matched, active, busy 0>
      "PerformanceStatistics" = {"In use system memory (driver)"=0,"Alloc system memory"=14924742656,"Tiler Utilization %"=42,"Renderer Utilization %"=41,"Device Utilization %"=42,"In use system memory"=8247443456}
      "model" = "Apple M1 Max"
'''


class AppleGpuTests(unittest.TestCase):
    def test_device_utilization_and_memory_in_use_are_read(self):
        self.assertEqual(compute_telemetry._apple_gpu_usage(lambda: IOREG),
                         {"percent": 42.0, "used_bytes": 8247443456})

    def test_missing_or_invalid_statistics_are_unknown_not_zero(self):
        self.assertIsNone(compute_telemetry._apple_gpu_usage(lambda: ""))
        self.assertIsNone(compute_telemetry._apple_gpu_usage(lambda: '"PerformanceStatistics" = {"recoveryCount"=0}'))
        odd = IOREG.replace('"Device Utilization %"=42', '"Device Utilization %"=250')
        self.assertEqual(compute_telemetry._apple_gpu_usage(lambda: odd)["percent"], None)
        def broken():
            raise OSError("no ioreg")
        self.assertIsNone(compute_telemetry._apple_gpu_usage(broken))

    def test_sample_fills_the_unified_gpu_on_macos_only(self):
        hardware = {"cpu": "Apple M1 Max", "ram_total_bytes": 64 * 2 ** 30, "ram_available_bytes": 30 * 2 ** 30,
                    "gpus": [{"name": "Apple M1 Max GPU (unified memory)", "total_bytes": 48 * 2 ** 30,
                              "free_bytes": 30 * 2 ** 30, "unified": True}]}
        with patch("compute_telemetry.detect_hardware", return_value=hardware), \
                patch("compute_telemetry._gpu_usage", return_value=[]), \
                patch("compute_telemetry._installed_models", return_value={"installed": [], "loaded": [], "available": False, "note": ""}), \
                patch("compute_telemetry._apple_gpu_usage", return_value={"percent": 42.0, "used_bytes": 8 * 2 ** 30}), \
                patch("compute_telemetry.platform.system", return_value="Darwin"), \
                patch("compute_telemetry._cpu_times", return_value=(100, 50)):
            gpu = compute_telemetry.TelemetrySampler().sample()["gpus"][0]
        self.assertEqual((gpu["percent"], gpu["used_bytes"]), (42.0, 8 * 2 ** 30))


if __name__ == "__main__":
    unittest.main()
