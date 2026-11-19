"""The web workspace's page: its unit tests (Node), and rules every page file must follow."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest

HERE = Path(__file__).resolve().parent
STATIC = HERE / "webui" / "static"
TESTS = HERE / "webui" / "tests"
SCRIPTS = sorted(STATIC.glob("*.js"))


class FrontendUnitTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed; the page's unit tests need it")
    def test_node_unit_tests_pass(self):
        files = [str(path) for path in sorted(TESTS.glob("*.test.mjs"))]
        self.assertTrue(files)
        completed = subprocess.run(["node", "--test", *files], cwd=HERE, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout[-4000:] + completed.stderr[-2000:])
        self.assertRegex(completed.stdout, r"(?m)^\S* ?fail 0$")


class PageRuleTests(unittest.TestCase):
    """Model answers and file contents are untrusted. None of these may turn text into markup or code."""

    FORBIDDEN = {
        "innerHTML": r"\binnerHTML\b", "outerHTML": r"\bouterHTML\b", "insertAdjacentHTML": r"insertAdjacentHTML",
        "document.write": r"document\.write", "eval": r"\beval\s*\(", "new Function": r"new\s+Function\b",
        "string timers": r"set(?:Timeout|Interval)\(\s*[\"'`]", "DOMParser": r"\bDOMParser\b",
        "createContextualFragment": r"createContextualFragment", "style attribute": r"\bstyle\s*:\s*[\"'`]",
        "javascript: URLs": r"javascript:", "anchor elements": r"h\(\s*[\"']a[\"']",
        "localStorage tokens": r"localStorage\.setItem\([^)]*(?:token|session)",
    }

    def test_scripts_never_build_markup_from_text(self):
        for path in SCRIPTS:
            source = path.read_text(encoding="utf-8")
            for name, pattern in self.FORBIDDEN.items():
                with self.subTest(file=path.name, rule=name):
                    self.assertIsNone(re.search(pattern, source), f"{path.name} uses {name}")

    def test_nothing_is_loaded_from_the_internet(self):
        allowed = {"http://www.w3.org/2000/svg"}  # an XML namespace name, never fetched
        for path in sorted(STATIC.iterdir()):
            for url in re.findall(r"https?://[^\s\"'`)<>]+", path.read_text(encoding="utf-8")):
                with self.subTest(file=path.name, url=url):
                    self.assertIn(url, allowed)

    def test_the_page_has_no_inline_script_or_style(self):
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page))
        self.assertIsNone(re.search(r"<style\b", page))
        self.assertIsNone(re.search(r"\sstyle\s*=", page))
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", page))

    def test_every_module_import_resolves_to_a_served_file(self):
        names = {path.name for path in STATIC.iterdir()}
        for path in SCRIPTS:
            for target in re.findall(r"""from\s+["']\./([^"']+)["']""", path.read_text(encoding="utf-8")):
                with self.subTest(file=path.name, imports=target):
                    self.assertIn(target, names)
                    self.assertRegex(target, r"^[a-z0-9-]+\.[a-z]+$")  # the server serves flat, lowercase names only

    def test_every_script_is_type_checked_by_its_header(self):
        for path in SCRIPTS:
            with self.subTest(file=path.name):
                self.assertTrue(path.read_text(encoding="utf-8").startswith("// @ts-check"))


if __name__ == "__main__":
    unittest.main()
