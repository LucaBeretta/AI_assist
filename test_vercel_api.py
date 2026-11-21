"""API deployment tests use fake HTTP responses and never publish files."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from vercel_api import VercelAPI, VercelAPIError, deployment_files, deploy_project_api
from vercel_deploy import DeploymentCancelled


class VercelAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.write("index.html", "hello")
        self.cancel = threading.Event()
        self.logs = []

    def write(self, name, content="ignored"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def deploy(self, domain=""):
        return deploy_project_api(self.root, "my-site", "my-team", domain,
                                  self.logs.append, self.cancel, token="test-secret")

    def test_uploads_content_digest_and_waits_for_ready(self):
        replies = [{"id": "prj_existing"}, {}, {"id": "dpl_123", "url": "my-site.vercel.app", "readyState": "BUILDING"},
                   {"readyState": "READY"}]
        with patch("vercel_api.VercelAPI.request", side_effect=replies) as request, patch.object(self.cancel, "wait", return_value=False):
            result = self.deploy()
        digest = hashlib.sha1(b"hello").hexdigest()
        self.assertEqual(request.call_args_list[1].args, ("POST", "/v2/files", b"hello", {"x-vercel-digest": digest}))
        body = request.call_args_list[2].args[2]
        self.assertEqual(body["project"], "prj_existing")
        self.assertEqual(body["projectSettings"], {})
        self.assertEqual(body["files"], [{"file": "index.html", "sha": digest, "size": 5}])
        self.assertEqual(body["target"], "production")
        self.assertEqual(result["url"], "https://my-site.vercel.app")
        self.assertNotIn("test-secret", "".join(self.logs))

    def test_build_failure_does_not_assign_domain(self):
        with patch("vercel_api.VercelAPI.request", side_effect=[{}, {}, {
                "id": "dpl_123", "url": "my-site.vercel.app", "readyState": "ERROR"}]) as request:
            with self.assertRaisesRegex(RuntimeError, "build error"):
                self.deploy("example.com")
        self.assertEqual(request.call_count, 3)

    def test_domain_verification_failure_retains_deployment(self):
        with patch("vercel_api.VercelAPI.request", side_effect=[{}, {}, {
                "id": "dpl_123", "url": "my-site.vercel.app", "readyState": "READY"},
                {"verified": False, "verification": [{"type": "TXT", "value": "challenge"}]}]):
            result = self.deploy("example.com")
        self.assertFalse(result["domain_assigned"])
        self.assertIn("challenge", result["domain_error"])
        self.assertEqual(result["url"], "https://my-site.vercel.app")

    def test_new_domain_is_added_then_aliased(self):
        with patch("vercel_api.VercelAPI.request", side_effect=[{}, {}, {
                "id": "dpl_123", "url": "my-site.vercel.app", "readyState": "READY"},
                VercelAPIError(404, "not_found", "Missing domain"), {"verified": True}, {}]) as request:
            result = self.deploy("example.com")
        self.assertTrue(result["domain_assigned"])
        self.assertEqual(request.call_args.args, ("POST", "/v2/deployments/dpl_123/aliases", {"alias": "example.com"}))

    def test_preflight_excludes_credentials_dependencies_and_ignored_files(self):
        for name in (".env", ".env.production", ".npmrc", "private.key", "node_modules/pkg/main.js",
                     ".git/config", ".aiassist/chats.json", "ignored.txt", "nested/skip.js", "nested/keep.js"):
            self.write(name)
        self.write(".gitignore", "ignored.txt\nnested/*.js\n")
        self.write(".vercelignore", "!nested/keep.js\n")
        names = {path.relative_to(self.root).as_posix() for path in deployment_files(self.root, self.cancel)}
        self.assertEqual(names, {"index.html", ".gitignore", ".vercelignore", "nested/keep.js"})

    def test_nested_globs_and_directory_exclusions(self):
        for name in ("src/a.js", "src/deep/b.js", "src/deep/keep.css", "cache/data", "nested/no.txt", "nested/yes.txt"):
            self.write(name)
        self.write(".gitignore", "src/**/*.js\ncache/\n")
        self.write("nested/.gitignore", "*.txt\n!yes.txt\n")
        names = {path.relative_to(self.root).as_posix() for path in deployment_files(self.root, self.cancel)}
        self.assertIn("src/deep/keep.css", names)
        self.assertIn("nested/yes.txt", names)
        self.assertFalse(names & {"src/a.js", "src/deep/b.js", "cache/data", "nested/no.txt"})

    def test_cancellation_and_missing_token_never_upload(self):
        with patch("vercel_api.VercelAPI.request") as request:
            with self.assertRaisesRegex(ValueError, "access token"):
                deploy_project_api(self.root, "my-site", "", "", self.logs.append, self.cancel, token="")
            self.cancel.set()
            with self.assertRaises(DeploymentCancelled):
                self.deploy()
        request.assert_not_called()

    def test_cancellation_during_monitoring_preserves_submitted_url_in_log(self):
        with patch("vercel_api.VercelAPI.request", side_effect=[{}, {}, {
                "id": "dpl_123", "url": "my-site.vercel.app", "readyState": "BUILDING"}]) as request:
            def stop_wait(seconds):
                self.cancel.set()
                return True
            with patch.object(self.cancel, "wait", side_effect=stop_wait):
                with self.assertRaises(DeploymentCancelled):
                    self.deploy()
        self.assertEqual(request.call_count, 3)
        self.assertIn("https://my-site.vercel.app", "".join(self.logs))

    def test_duplicate_content_is_uploaded_once(self):
        self.write("copy.html", "hello")
        with patch("vercel_api.VercelAPI.request", side_effect=[{}, {}, {
                "id": "dpl_123", "url": "my-site.vercel.app", "readyState": "READY"}]) as request:
            self.deploy()
        self.assertEqual(request.call_count, 3)
        self.assertEqual(len(request.call_args.args[2]["files"]), 2)

    def test_http_authorization_team_query_and_empty_upload_response(self):
        api = VercelAPI("test-secret", "team_123", self.cancel)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b""
        api.opener = MagicMock()
        api.opener.open.return_value = response
        self.assertEqual(api.request("POST", "/v2/files", b"data"), {})
        request = api.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.vercel.com/v2/files?teamId=team_123")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(request.data, b"data")
        self.assertEqual(VercelAPI("test-secret", "my-team", self.cancel).query, "slug=my-team")

    def test_api_errors_redact_tokens(self):
        api = VercelAPI("test-secret", "", self.cancel)
        api.opener = MagicMock()
        body = json.dumps({"error": {"code": "forbidden", "message": "Invalid test-secret"}}).encode()
        api.opener.open.side_effect = HTTPError("https://api.vercel.com", 403, "Forbidden", {}, io.BytesIO(body))
        with self.assertRaises(VercelAPIError) as error:
            api.request("POST", "/v13/deployments", {})
        self.assertNotIn("test-secret", str(error.exception))
        self.assertIn("permissions", str(error.exception))

    def test_first_static_deployment_supplies_settings_and_does_not_reference_missing_project(self):
        with patch("vercel_api.VercelAPI.request", side_effect=[
                VercelAPIError(404, "not_found", "No project"), {},
                {"id": "dpl_123", "url": "my-site.vercel.app", "readyState": "READY"}]) as request:
            self.deploy()
        body = request.call_args.args[2]
        self.assertEqual(body["projectSettings"], {"framework": None})
        self.assertNotIn("project", body)

    def test_blank_scope_selects_only_team(self):
        api = VercelAPI("test-secret", "", self.cancel)
        with patch.object(api, "request", return_value={"teams": [{"id": "team_abc", "slug": "beretta2"}]}):
            api.select_account("", self.logs.append)
        self.assertEqual(api.query, "teamId=team_abc")
        self.assertIn("beretta2", "".join(self.logs))

    def test_multiple_teams_require_selection_before_uploading(self):
        with patch("vercel_api.VercelAPI.request", return_value={"teams": [
                {"id": "team_a", "slug": "first"}, {"id": "team_b", "slug": "second"}]}) as request:
            with self.assertRaisesRegex(ValueError, "first, second"):
                deploy_project_api(self.root, "my-site", "", "", self.logs.append, self.cancel, token="test-secret")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args, ("GET", "/v2/teams"))

    def test_project_permission_error_stops_before_upload(self):
        with patch("vercel_api.VercelAPI.request", side_effect=VercelAPIError(403, "forbidden", "Check team")) as request:
            with self.assertRaisesRegex(VercelAPIError, "Check team"):
                self.deploy()
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
