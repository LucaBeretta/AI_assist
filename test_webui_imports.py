"""Import boundaries prevent the web API from depending on the legacy GUI."""
from pathlib import Path
import subprocess
import sys
import unittest


class ImportTests(unittest.TestCase):
    def test_api_and_engine_import_without_native_gui(self):
        script = '''
import importlib.abc
import sys
target = "tk" + "inter"
class BlockNativeGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "tk" + "inter":
            raise AssertionError("The web API imported the legacy GUI")
sys.meta_path.insert(0, BlockNativeGui())
import ollama_engine
import webui.app
assert not any(name.split(".")[0] == target for name in sys.modules)
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
