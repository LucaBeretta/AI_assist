"""Deployment orchestration uses fake Vercel calls; process tests run Python."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from vercel_deploy import DeploymentCancelled, deploy_project, domain_name, run_cli, validate_project, vercel_command


class VercelDeployTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "index.html").write_text("<h1>Website</h1>", encoding="utf-8")
        self.cancel = threading.Event()
        self.logs = []

    def fake_run(self, arguments, root, output, cancel, timeout):
        if "deploy" in arguments:
            return "https://my-site-abcd.vercel.app\n"
        return "OK"

    def test_deploys_production_then_connects_domain_under_chosen_team(self):
        with patch("vercel_deploy.vercel_command", return_value=["vercel"]), patch(
                "vercel_deploy.run_cli", side_effect=self.fake_run) as runner:
            result = deploy_project(self.root, "my-site", "my-team", "Example.com", self.logs.append, self.cancel)
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(commands[1], ["vercel", "link", "--yes", "--project", "my-site", "--scope", "my-team"])
        self.assertEqual(commands[2], ["vercel", "deploy", "--prod", "--yes", "--scope", "my-team"])
        self.assertIn(["vercel", "domains", "add", "example.com", "my-site", "--scope", "my-team"], commands)
        self.assertTrue(result["domain_assigned"])
        self.assertEqual(result["url"], "https://my-site-abcd.vercel.app")

    def test_domain_failure_retains_working_deployment_url(self):
        def run(arguments, *args):
            if "domains" in arguments:
                raise RuntimeError("Domain ownership needs verification")
            return self.fake_run(arguments, *args)

        with patch("vercel_deploy.vercel_command", return_value=["vercel"]), patch("vercel_deploy.run_cli", side_effect=run):
            result = deploy_project(self.root, "my-site", "", "example.com", self.logs.append, self.cancel)
        self.assertEqual(result["url"], "https://my-site-abcd.vercel.app")
        self.assertFalse(result["domain_assigned"])
        self.assertIn("verification", result["domain_error"])

    def test_login_failure_does_not_deploy_and_missing_url_is_not_success(self):
        with patch("vercel_deploy.vercel_command", return_value=["vercel"]), patch(
                "vercel_deploy.run_cli", side_effect=RuntimeError("not authenticated")) as runner:
            with self.assertRaisesRegex(RuntimeError, "vercel login"):
                deploy_project(self.root, "my-site", "", "", self.logs.append, self.cancel)
        self.assertEqual(runner.call_count, 1)
        with patch("vercel_deploy.vercel_command", return_value=["vercel"]), patch("vercel_deploy.run_cli", return_value="Done"):
            with self.assertRaisesRegex(RuntimeError, "did not return a deployment URL"):
                deploy_project(self.root, "my-site", "", "", self.logs.append, self.cancel)

    def test_validates_domains_project_names_and_non_web_projects_before_execution(self):
        for value in ("https://example.com", "example.com/path", "--force", "127.0.0.1", "foo;bar.com"):
            with self.subTest(domain=value), self.assertRaises(ValueError):
                domain_name(value)
        for project in ("UPPER", "--force", "foo;bar", "a---b"):
            with self.subTest(project=project), self.assertRaises(ValueError):
                validate_project(self.root, project)
        (self.root / "index.html").unlink()
        with self.assertRaisesRegex(ValueError, "Tkinter"):
            validate_project(self.root, "my-site")

    def test_missing_cli_and_windows_npm_shim_resolution(self):
        with patch("vercel_deploy.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "npm install"):
                vercel_command()
        shim = self.root / "vercel.cmd"
        script = self.root / "node_modules/vercel/dist/index.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        with patch("vercel_deploy.shutil.which", side_effect=lambda name: str(shim) if name == "vercel" else "node.exe"):
            self.assertEqual(vercel_command(), ["node.exe", str(script)])

    def test_windows_npm_shim_uses_package_bin_entry(self):
        package = self.root / "node_modules/vercel"
        script = package / "dist/custom-entry.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({"bin": {"vercel": "./dist/custom-entry.js"}}), encoding="utf-8")
        shim = self.root / "vercel.cmd"
        with patch("vercel_deploy.shutil.which", side_effect=lambda name: str(shim) if name == "vercel" else "node.exe"):
            self.assertEqual(vercel_command(), ["node.exe", str(script)])

    def test_windows_npm_shim_supports_current_cli_without_metadata(self):
        script = self.root / "node_modules/vercel/dist/vc.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        shim = self.root / "vercel.cmd"
        with patch("vercel_deploy.shutil.which", side_effect=lambda name: str(shim) if name == "vercel" else "node.exe"):
            self.assertEqual(vercel_command(), ["node.exe", str(script)])

    def test_runner_separates_stdout_and_stderr_and_keeps_arguments_literal(self):
        marker = "literal & argument; $(ignored)"
        output = run_cli([sys.executable, "-c", "import sys; print(sys.argv[1]); print('diagnostic', file=sys.stderr)", marker],
                         self.root, self.logs.append, self.cancel, timeout=5)
        self.assertEqual(output.strip(), marker)
        self.assertIn("diagnostic", self.logs)

    def test_runner_stops_on_timeout_and_preexisting_cancellation(self):
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            run_cli([sys.executable, "-c", "import time; time.sleep(20)"], self.root, self.logs.append, self.cancel, timeout=.1)
        self.cancel.set()
        with patch("vercel_deploy.subprocess.Popen") as process, self.assertRaises(DeploymentCancelled):
            run_cli([sys.executable], self.root, self.logs.append, self.cancel)
        process.assert_not_called()
