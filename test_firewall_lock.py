"""The firewall lock: which programs it covers, what it reports, and the scripts it runs.
No test changes the firewall: the scripts are checked by PowerShell's parser only."""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import firewall_lock as lock
import network_gate

OLLAMA = [r"C:\Ollama\ollama.exe", r"C:\Ollama\ollama app.exe"]


def status(rules=(), enabled="True", local_rules="True"):
    return {"profiles": [{"name": name, "enabled": enabled, "local_rules": local_rules}
                         for name in ("Domain", "Private", "Public")], "rules": list(rules)}


def rule(program, enabled="True", remote=lock.INTERNET_RANGES, action="Block"):
    return {"program": program, "enabled": enabled, "remote": list(remote), "action": action, "direction": "Outbound"}


class ProgramTests(unittest.TestCase):
    def test_every_program_in_the_ollama_and_colibri_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            ollama = Path(folder, "Programs", "Ollama")
            (ollama / "lib" / "ollama").mkdir(parents=True)
            for name in ("ollama.exe", "ollama app.exe", "lib/ollama/llama-server.exe", "notes.txt"):
                Path(ollama, name).write_text("x")
            colibri = Path(folder, "colibri")
            (colibri / ".git").mkdir(parents=True)
            (colibri / "c").mkdir()
            Path(colibri, "c", "engine.exe").write_text("x")
            Path(colibri, ".git", "hook.exe").write_text("x")
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}), patch("firewall_lock.shutil.which", return_value=None):
                programs = lock.engine_programs(str(colibri))
        self.assertEqual(sorted(Path(program).name for program in programs),
                         ["engine.exe", "llama-server.exe", "ollama app.exe", "ollama.exe"])

    def test_a_shared_folder_on_path_is_not_swept(self):
        with tempfile.TemporaryDirectory() as folder:
            shims = Path(folder, "shims")
            shims.mkdir()
            for name in ("ollama.exe", "git.exe", "python.exe"):
                Path(shims, name).write_text("x")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(Path(folder, "missing"))}), \
                    patch("firewall_lock.shutil.which", return_value=str(shims / "ollama.exe")):
                programs = lock.engine_programs()
        self.assertEqual([Path(program).name for program in programs], ["ollama.exe"])


class SummaryTests(unittest.TestCase):
    def summary(self, current, **options):
        return lock.summarize(current, OLLAMA, **options)

    def test_what_the_lock_reports(self):
        self.assertEqual(self.summary({"unavailable": "Windows only"})["state"], "unavailable")
        self.assertEqual(self.summary({"error": "boom"})["state"], "error")
        off = self.summary(status())
        self.assertEqual((off["state"], off["can_turn_on"], off["has_rules"]), ("off", True, False))
        on = self.summary(status([rule(program) for program in OLLAMA]))
        self.assertEqual((on["state"], on["ollama_locked"]), ("on", True))
        self.assertTrue(all(row["active"] for row in on["programs"]))
        self.assertEqual(self.summary(status([rule(OLLAMA[1])]))["state"], "partial")
        turned_off = status([rule(program, enabled="False") for program in OLLAMA])
        lifted = self.summary(turned_off, lift_until=time.time() + 600)
        self.assertEqual((lifted["state"], lifted["ollama_locked"]), ("lifted", False))
        self.assertEqual(self.summary(turned_off, lift_until=time.time() - 1)["state"], "disabled")
        self.assertEqual(self.summary(status([rule(program, remote=["1.0.0.0-9.255.255.255"]) for program in OLLAMA]))["state"], "changed")
        self.assertEqual(self.summary(status([rule(program, action="Allow") for program in OLLAMA]))["state"], "changed")
        firewall_off = self.summary(status([rule(program) for program in OLLAMA], enabled="False"))
        self.assertEqual((firewall_off["state"], firewall_off["ollama_locked"]), ("firewall off", False))
        self.assertEqual(self.summary(status(local_rules="False"))["state"], "ignored")
        self.assertEqual(lock.summarize(status(), [])["can_turn_on"], False)

    def test_windows_may_write_the_same_ranges_differently(self):
        written = [entry for entry in lock.INTERNET_RANGES if entry not in ("2000::/3", "11.0.0.0-126.255.255.255")]
        written += ["2000::-3fff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "11.0.0.0-63.255.255.255", "64.0.0.0-126.255.255.255"]
        written.remove("128.0.0.0-169.253.255.255")
        written.append("128.0.0.0-169.253.255.255")
        self.assertEqual(self.summary(status([rule(program, remote=written) for program in OLLAMA]))["state"], "on")

    def test_rules_for_removed_programs_are_listed(self):
        summary = self.summary(status([rule(program) for program in OLLAMA] + [rule(r"C:\Ollama\old.exe")]))
        self.assertEqual(summary["state"], "on")
        self.assertEqual([row["path"] for row in summary["programs"] if row.get("gone")], [r"C:\Ollama\old.exe"])

    def test_the_blocked_ranges_are_exactly_what_the_gate_calls_internet(self):
        spans = lock._intervals(lock.INTERNET_RANGES)

        def blocked(text):
            import ipaddress
            address = ipaddress.ip_address(text)
            return any(version == address.version and first <= int(address) <= last for version, first, last in spans)

        for text in ("1.1.1.1", "8.8.8.8", "9.255.255.255", "10.0.0.1", "11.0.0.0", "100.64.0.1", "126.255.255.255",
                     "127.0.0.1", "128.0.0.0", "169.253.255.255", "169.254.0.1", "169.255.0.0", "172.15.255.255",
                     "172.16.0.1", "172.31.255.255", "172.32.0.0", "192.167.255.255", "192.168.0.1", "192.169.0.0",
                     "223.255.255.255", "2001:4860::8888", "fd00::1", "fe80::1", "::1"):
            self.assertEqual(blocked(text), network_gate.classify(text) == network_gate.INTERNET, text)


class ScriptTests(unittest.TestCase):
    def test_the_plan_travels_as_data_never_as_script(self):
        program = r"C:\Odd 'name'\$(calc).exe"
        script = lock._script(lock._checked(lock._TURN_ON), result=r"C:\result.txt", programs=[program],
                              ranges=list(lock.INTERNET_RANGES))
        self.assertNotIn("calc", script)
        encoded = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", script).group(1)
        self.assertEqual(json.loads(base64.b64decode(encoded))["programs"], [program])

    def captured_elevation(self, script="Write-Output 'lock'", wait=True):
        captured = {}

        def run(command, **options):
            captured.update(command=command, env=options["env"])
            return SimpleNamespace(returncode=0)

        with patch("firewall_lock.subprocess.run", side_effect=run):
            self.assertEqual(lock._elevated(script, wait=wait), 0)
        return base64.b64decode(captured["command"][-1]).decode("utf-16-le"), captured["env"]

    def test_administrator_approval_is_asked_with_the_script_outside_the_command_line(self):
        outer, environment = self.captured_elevation()
        self.assertIn("-Verb RunAs", outer)
        self.assertIn("$env:AI_ASSIST_ELEVATED", outer)
        self.assertNotIn("lock", outer)
        self.assertEqual(base64.b64decode(environment["AI_ASSIST_ELEVATED"]).decode("utf-16-le"), "Write-Output 'lock'")
        with self.assertRaises(OSError):
            lock._elevated("x" * 20000)

    def test_outcomes(self):
        with tempfile.TemporaryDirectory() as folder:
            result = Path(folder, "result.txt")
            ok, message = lock._outcome(lock.CANCELLED, result)
            self.assertFalse(ok)
            self.assertIn("administrator approval", message)
            result.write_text("ok", encoding="utf-8")
            self.assertEqual(lock._outcome(0, result), (True, ""))
            result.write_text("error: Access is denied.", encoding="utf-8")
            self.assertEqual(lock._outcome(2, result), (False, "Access is denied."))
        self.assertEqual(lock.turn_on([]), (False, "No Ollama or colibri programs were found to lock."))
        with self.assertRaises(ValueError):
            lock.lift(7)

    @unittest.skipUnless(os.name == "nt", "PowerShell")
    def test_every_script_parses(self):
        plan = dict(result=r"C:\result.txt")
        scripts = {
            "status": lock._STATUS_SCRIPT,
            "on": lock._script(lock._checked(lock._TURN_ON), programs=OLLAMA, ranges=list(lock.INTERNET_RANGES), **plan),
            "off": lock._script(lock._checked(lock._TURN_OFF), **plan),
            "relock": lock._script(lock._checked(lock._RELOCK), **plan),
            "lift": lock._script(lock._LIFT, seconds=300, **plan),
            "elevate and wait": self.captured_elevation()[0],
            "elevate": self.captured_elevation(wait=False)[0],
        }
        parser = ("$tokens = $null; $errors = $null\n"
                  "foreach ($name in $env:LOCK_SCRIPTS.Split('|')) {\n"
                  "  $text = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String([Environment]::GetEnvironmentVariable('LOCK_' + $name.Replace(' ', '_'))))\n"
                  "  [void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)\n"
                  "  Write-Output ($name + '=' + $errors.Count)\n}\n")
        environment = dict(os.environ, LOCK_SCRIPTS="|".join(scripts))
        environment.update({"LOCK_" + name.replace(" ", "_"): lock._encode(text) for name, text in scripts.items()})
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", lock._encode(parser)],
                                   env=environment, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.stdout.splitlines(), [f"{name}=0" for name in scripts])

    @unittest.skipUnless(os.name == "nt", "Windows Firewall")
    def test_reading_the_firewall_needs_no_administrator(self):
        current = lock.read_status()
        self.assertNotIn("error", current)
        self.assertEqual(len(current["profiles"]), 3)
        self.assertIsInstance(current["rules"], list)


class LockTests(unittest.TestCase):
    def finish(self, controller):
        deadline = time.monotonic() + 10
        finished = []
        while controller.busy and time.monotonic() < deadline:
            finished += controller.poll()
            time.sleep(0.02)
        return finished

    def test_reading_and_lifting_run_in_the_background(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = lock.Lock(lambda: None, Path(folder, "firewall-lift.json"))
            with patch("firewall_lock.read_status", return_value=status([rule(OLLAMA[0])])), \
                    patch("firewall_lock.engine_programs", return_value=OLLAMA[:1]), \
                    patch("firewall_lock.lift", return_value=(True, "")) as lifted:
                controller.refresh()
                self.finish(controller)
                self.assertEqual(controller.summary["state"], "on")
                self.assertIn("firewall lock is on", controller.blocks("Ollama model download"))
                self.assertIsNone(controller.blocks("colibri model download"))
                controller.run("lift", 15)
                self.assertEqual(self.finish(controller), [("lift", "")])
            lifted.assert_called_once_with(15)
            self.assertGreater(controller.lift_until(), time.time() + 800)

    def test_a_refused_change_reports_why(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = lock.Lock(lambda: None, Path(folder, "firewall-lift.json"))
            with patch("firewall_lock.read_status", return_value=status()), \
                    patch("firewall_lock.engine_programs", return_value=OLLAMA), \
                    patch("firewall_lock.turn_on", return_value=(False, "Windows did not get administrator approval.")):
                controller.run("on")
                self.assertEqual(self.finish(controller), [("on", "Windows did not get administrator approval.")])
            self.assertEqual(controller.summary["state"], "off")
            self.assertIsNone(controller.lift_until())


if __name__ == "__main__":
    unittest.main()
