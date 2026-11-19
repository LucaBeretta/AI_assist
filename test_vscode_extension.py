import importlib.util
from pathlib import Path
import unittest


BRIDGE_PATH = Path(__file__).parent / "vscode-extension" / "agent_bridge.py"
SPEC = importlib.util.spec_from_file_location("ai_assist_vscode_bridge", BRIDGE_PATH)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class LocalHostTests(unittest.TestCase):
    def test_accepts_loopback_ollama_addresses(self):
        self.assertEqual(BRIDGE.local_host("http://localhost:11434/"), "http://localhost:11434")
        self.assertEqual(BRIDGE.local_host("http://127.0.0.1:11434"), "http://127.0.0.1:11434")
        self.assertEqual(BRIDGE.local_host("http://[::1]:11434"), "http://[::1]:11434")

    def test_rejects_nonlocal_and_non_http_endpoints(self):
        for host in ("https://localhost:11434", "http://192.168.1.20:11434", "http://example.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                BRIDGE.local_host(host)

    def test_rejects_credentials_paths_and_bad_ports(self):
        for host in ("http://user@localhost:11434", "http://localhost:11434/api", "http://localhost:bad"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                BRIDGE.local_host(host)


if __name__ == "__main__":
    unittest.main()
