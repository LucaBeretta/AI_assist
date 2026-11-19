"""Validation shared by Vercel deployments; nothing here contacts Vercel."""

from pathlib import Path
import tempfile
import threading
import unittest

from vercel_deploy import DeploymentCancelled, check_cancelled, domain_name, validate_project


class VercelValidationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "index.html").write_text("<h1>Website</h1>", encoding="utf-8")

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

    def test_normalizes_domains_and_accepts_a_web_project(self):
        self.assertEqual(domain_name(" Example.COM. "), "example.com")
        self.assertEqual(domain_name(""), "")
        root, project, scope, domain = validate_project(self.root, "my-site", "my-team", "Example.com")
        self.assertEqual((root, project, scope, domain), (self.root.resolve(), "my-site", "my-team", "example.com"))

    def test_cancellation_is_reported_only_once_requested(self):
        cancel = threading.Event()
        check_cancelled(cancel)
        cancel.set()
        with self.assertRaises(DeploymentCancelled):
            check_cancelled(cancel)


if __name__ == "__main__":
    unittest.main()
