"""Static checks for the launchers' private-Python manifest and wiring."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
PLATFORMS = {"aarch64-apple-darwin", "x86_64-apple-darwin",
             "x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"}


def manifest():
    base, entries = None, {}
    for line in (ROOT / "python-runtime.txt").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if fields[0] == "base":
            base = fields[1]
        else:
            entries[fields[0]] = fields[1:]
    return base, entries


class RuntimeManifestTests(unittest.TestCase):
    def test_base_is_a_pinned_https_release(self):
        base, _ = manifest()
        self.assertRegex(base, r"^https://github\.com/astral-sh/python-build-standalone/releases/download/\d{8}$")

    def test_every_platform_has_matching_file_and_digest(self):
        base, entries = manifest()
        release = base.rsplit("/", 1)[1]
        self.assertEqual(set(entries), PLATFORMS)
        versions = set()
        for platform, fields in entries.items():
            self.assertEqual(len(fields), 2, platform)
            file, digest = fields
            match = re.fullmatch(r"cpython-(3\.\d+\.\d+)\+(\d{8})-" + re.escape(platform) + r"-install_only\.tar\.gz", file)
            self.assertIsNotNone(match, file)
            self.assertEqual(match.group(2), release, "every file must come from the base release")
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            versions.add(match.group(1))
        self.assertEqual(len(versions), 1, "all platforms should use the same Python version")


class LauncherWiringTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell.exe"), "PowerShell check is Windows-only")
    def test_optional_dependency_failures_allow_tk_to_continue(self):
        text = (ROOT / "install_runtime.ps1").read_text(encoding="utf-8")
        optional = text.split("    # Optional web dependencies", 1)[1].split("    Write-Host 'Private Python installed.'", 1)[0]
        for failing_call in (1, 2):
            script = ("$ErrorActionPreference='Stop'; $root=(Get-Location).Path; $privatePython='FakePython'; $global:count=0; "
                      "function FakePython { $global:count++; $global:LASTEXITCODE=0; "
                      f"if ($global:count -eq {failing_call}) {{ $global:LASTEXITCODE=1 }} }}\n"
                      + optional[optional.index("    try {"):] + "\nWrite-Output 'TK_CONTINUES'; exit 0")
            result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
                                    cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("TK_CONTINUES", result.stdout)
            self.assertIn("Tk remains available", result.stdout)
            self.assertIn("-m pip install -r", result.stdout)

    def test_mac_optional_dependency_failure_returns_success(self):
        text = (ROOT / "Start AI Assist.command").read_text(encoding="utf-8")
        optional = text.split('    if ! "$PRIVATE_PYTHON" -m ensurepip', 1)[1].split("    fi", 1)[0]
        self.assertIn("return 0", optional)
        self.assertNotIn("return 1", optional)
        self.assertIn("requirements.txt", optional)

    def test_launchers_use_the_manifest_and_verify_checksums(self):
        mac = (ROOT / "Start AI Assist.command").read_text(encoding="utf-8")
        windows = (ROOT / "Start AI Assist.cmd").read_text(encoding="utf-8")
        installer = (ROOT / "install_runtime.ps1").read_text(encoding="utf-8")
        self.assertIn("python-runtime.txt", mac)
        self.assertIn("shasum -a 256", mac)
        self.assertIn("install_runtime.ps1", windows)
        self.assertIn("python-runtime.txt", installer)
        self.assertIn("Get-FileHash -Algorithm SHA256", installer)
        for text in (mac, windows):
            self.assertIn("gui_runtime.py", text)

    def test_private_runtime_is_not_committed_and_line_endings_are_pinned(self):
        self.assertIn("/runtime/", (ROOT / ".gitignore").read_text(encoding="utf-8").split())
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(attributes, r"\*\.cmd\s+text eol=crlf")
        self.assertRegex(attributes, r"\*\.command\s+text eol=lf")


if __name__ == "__main__":
    unittest.main()
