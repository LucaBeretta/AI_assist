"""Verified archive, transaction and API tests. No live repository is contacted."""
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

import app_updater as updater
from update_checker import blob_hash
from webui.parity import ParityApi, update_dependencies
from webui.jobs import Jobs


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "app"
        self.root.mkdir()
        self.archive = self.root.parent / "repo.zip"
        self.revision = "2" * 40
        self.staging = self.root / ".update/staging" / self.revision
        self.files = {"AIassist.py": b"updated", "new.py": b"added", "assets/icon/test.png": b"\x89PNG\r\n"}
        self.expected = {k: blob_hash(v, binary=k.endswith(".png")) for k, v in self.files.items()}
        self.write("AIassist.py", b"edited")
        self.write("old.py", b"removed upstream")
        self.write("runtime/python/local.py", b"private")
        self.write("test_local.py", b"custom")

    def write(self, name, data):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def zip(self, extra=()):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, data in self.files.items():
                archive.writestr("source/" + name, data)
            for item in extra:
                archive.writestr(item, b"unexpected")

    def apply(self, revision=None):
        if revision:
            self.staging = self.staging.with_name(revision)
            self.files["AIassist.py"] = revision.encode()
            self.expected["AIassist.py"] = blob_hash(revision.encode())
        self.zip()
        updater.extract_archive(self.archive, self.staging, self.expected)
        return updater.apply_update(self.root, self.staging, updater.plan_update(self.root, self.expected))

    def test_apply_backup_restore_and_unshipped_files(self):
        state = self.apply()
        self.assertEqual(state["revision"], self.revision)
        backup = self.root / ".update/backup" / state["previous"]
        self.assertEqual((backup / "files/old.py").read_bytes(), b"removed upstream")
        self.assertFalse((self.root / "old.py").exists())
        self.assertEqual((self.root / "AIassist.py").read_bytes(), b"updated")
        self.assertEqual((self.root / "assets/icon/test.png").read_bytes(), self.files["assets/icon/test.png"])
        self.assertTrue(updater.can_rollback(self.root))
        updater.rollback(self.root)
        self.assertFalse(updater.can_rollback(self.root))
        self.assert_original()

    def assert_original(self):
        for name, data in {"AIassist.py": b"edited", "old.py": b"removed upstream",
                           "runtime/python/local.py": b"private", "test_local.py": b"custom"}.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / "new.py").exists())
        self.assertFalse((self.root / "assets/icon/test.png").exists())

    def test_reject_unsafe_members_even_when_unshipped(self):
        link = zipfile.ZipInfo("source/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for item in ("source/../outside", "/absolute", "C:/file", "source/a\\..\\file",
                     "second-root/doc", "source/payload:stream", link):
            with self.subTest(item=item):
                self.zip([item])
                with self.assertRaises(ValueError):
                    updater.extract_archive(self.archive, self.staging, self.expected)
                self.assertFalse(self.staging.exists())
                self.assert_original()

    def test_hash_mismatch_and_missing_shipped_file_abort(self):
        for files in ({**self.files, "AIassist.py": b"wrong"}, {"AIassist.py": b"updated"}):
            self.files = files
            self.zip()
            with self.assertRaises(ValueError):
                updater.extract_archive(self.archive, self.staging, self.expected)
            self.assert_original()

    def test_verified_windows_launchers_get_native_line_endings(self):
        self.files["Start AI Assist.cmd"] = b"@echo off\necho ready\n"
        self.expected["Start AI Assist.cmd"] = blob_hash(self.files["Start AI Assist.cmd"])
        self.apply()
        self.assertEqual((self.root / "Start AI Assist.cmd").read_bytes(), b"@echo off\r\necho ready\r\n")
        self.assertEqual(updater.plan_update(self.root, self.expected)["write"], [])

    def test_locked_nth_file_restores_prior_writes(self):
        original = updater.os.replace
        def replace(source, destination):
            if destination == self.root / "new.py":
                raise PermissionError("file locked")
            return original(source, destination)
        with patch.object(updater.os, "replace", side_effect=replace), self.assertRaises(PermissionError):
            self.apply()
        self.assert_original()
        self.assertFalse(updater.can_rollback(self.root))

    def test_dependency_failure_restores_deleted_files_and_previous_state(self):
        self.apply()
        original_state = (self.root / ".update/state.json").read_bytes()
        self.write("AIassist.py", b"edit after update")
        self.write("old.py", b"delete again")
        with self.assertRaisesRegex(RuntimeError, "pip"):
            updater.apply_update(self.root, self.staging, updater.plan_update(self.root, self.expected),
                                 after_files=Mock(side_effect=RuntimeError("pip failed")))
        self.assertEqual((self.root / "old.py").read_bytes(), b"delete again")
        self.assertEqual((self.root / "AIassist.py").read_bytes(), b"edit after update")
        self.assertEqual((self.root / ".update/state.json").read_bytes(), original_state)

    def test_failed_rollback_preserves_the_installed_version(self):
        self.apply()
        state = (self.root / ".update/state.json").read_bytes()
        unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == self.root / "new.py":
                raise PermissionError("file locked")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", locked), self.assertRaises(PermissionError):
            updater.rollback(self.root)
        for name, data in self.files.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / "old.py").exists())
        self.assertEqual((self.root / ".update/state.json").read_bytes(), state)
        self.assertTrue(updater.can_rollback(self.root))

    def test_only_two_backups_are_retained_and_restorable(self):
        for rev in "345":
            self.apply(rev * 40)
        backups = (self.root / ".update/backup").iterdir()
        self.assertEqual(len(list(backups)), 2)
        self.assertEqual(updater.rollback(self.root)["revision"], "4" * 40)
        self.assertEqual(updater.rollback(self.root)["revision"], "3" * 40)
        self.assertFalse(updater.can_rollback(self.root))

    def test_git_file_or_directory_blocks_writes(self):
        git = self.root / ".git"
        git.mkdir()
        self.assertTrue(updater.is_git_checkout(self.root))
        git.rmdir()
        git.write_text("gitdir: elsewhere")
        with self.assertRaisesRegex(ValueError, "git pull"):
            self.apply()
        with self.assertRaisesRegex(ValueError, "git pull"):
            updater.rollback(self.root)
        self.assert_original()

    def test_malformed_backup_cannot_target_user_files(self):
        state = self.apply()
        manifest = self.root / ".update/backup" / state["previous"] / "manifest.json"
        manifest.write_text(json.dumps({"files": {"runtime/python/local.py": None}, "state": {}}))
        with self.assertRaises(ValueError):
            updater.rollback(self.root)
        self.assertEqual((self.root / "runtime/python/local.py").read_bytes(), b"private")

    def test_cancelled_and_oversized_downloads(self):
        for length, cancel, exception in [(1, True, InterruptedError), (updater.LIMIT + 1, False, ValueError)]:
            response = BytesIO(b"a")
            response.headers = {"Content-Length": str(length)}
            with patch("urllib.request.urlopen", return_value=response) as fetch, self.assertRaises(exception):
                updater.download(self.revision, self.archive, Mock(), Mock(is_set=lambda: cancel))
            self.archive.unlink(missing_ok=True)
            self.assertTrue(fetch.call_args.args[0].endswith("/zip/" + self.revision))

    def test_git_and_concurrent_update_are_refused_by_api(self):
        api = ParityApi()
        api._lock = threading.RLock()
        api._jobs = Jobs(Mock())
        release = threading.Event()
        try:
            api._jobs.start("updates", lambda progress, cancel: release.wait(3), key="updates")
            self.assertIn("already running", api.install_update()["error"])
        finally:
            release.set()
        with patch("webui.parity.APP_ROOT", self.root):
            (self.root / ".git").mkdir()
            api._jobs = Jobs(Mock())
            self.assertIn("git pull", api.install_update()["error"])

    def test_system_python_gets_a_command_and_private_pip_needs_approval(self):
        with patch("webui.parity.subprocess.run") as run:
            self.assertIn("requirements.txt", update_dependencies(self.root))
            run.assert_not_called()
            python = self.root / "runtime/python/bin/python3"
            with patch("sys.executable", str(python)), patch("network_gate.allow_external", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "blocked"):
                    update_dependencies(self.root)
            self.assertFalse((self.root / ".update/requirements-pending").exists())
            with patch("sys.executable", str(python)), patch("network_gate.allow_external", return_value=True):
                self.assertIsNone(update_dependencies(self.root))
            # pip waits for the next start: packages are locked while the app runs on Windows.
            run.assert_not_called()
            self.assertTrue((self.root / ".update/requirements-pending").is_file())

    def test_api_job_applies_verified_download_and_restores_it(self):
        self.zip()
        api = ParityApi()
        api._lock = threading.RLock()
        finished, events = threading.Event(), []
        def emit(kind, **data):
            if kind != "job_progress":
                events.append((kind, data))
                finished.set()
        api._jobs = Jobs(emit)
        api._last_update = {"revision": self.revision, "can_install": True, "expected_files": self.expected}
        response = BytesIO(self.archive.read_bytes())
        response.headers = {}
        with patch("webui.parity.APP_ROOT", self.root), patch("urllib.request.urlopen", return_value=response):
            self.assertTrue(api.install_update()["ok"])
            self.assertTrue(finished.wait(5))
            self.assertEqual(events[-1][0], "job_done", events)
            self.assertTrue(api.update_status()["restart_ready"])
            finished.clear()
            self.assertTrue(api.rollback_update()["ok"])
            self.assertTrue(finished.wait(5))
            self.assertEqual(events[-1][0], "job_done", events)
            self.assertFalse(api.update_status()["can_rollback"])
        self.assert_original()


class UpdateJobTests(unittest.TestCase):
    def test_cancel_before_apply_prevents_any_mutation(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        modified = Mock()
        events = []
        def emit(kind, **data):
            events.append(kind)
            if kind != "job_progress":
                finished.set()
        jobs = Jobs(emit)
        def work(progress, cancel):
            entered.set()
            release.wait(3)
            progress(cancellable=False)
            modified()
        identity = jobs.start("install_update", work, key="updates", finish_on_close=True)["job_id"]
        self.assertTrue(entered.wait(3))
        jobs.cancel(identity)
        release.set()
        self.assertTrue(finished.wait(3))
        modified.assert_not_called()
        self.assertEqual(events, ["job_stopped"])

    def test_close_waits_for_an_update_that_has_started_applying(self):
        entered, release = threading.Event(), threading.Event()
        modified = Mock()
        jobs = Jobs(Mock())
        def work(progress, cancel):
            progress(cancellable=False)
            entered.set()
            release.wait(3)
            self.assertFalse(cancel.is_set())
            modified()
        identity = jobs.start("install_update", work, key="updates", finish_on_close=True)["job_id"]
        self.assertTrue(entered.wait(3))
        jobs.cancel(identity)
        finished = jobs.durable_jobs()
        jobs.close()
        self.assertFalse(finished[0].is_set())
        release.set()
        self.assertTrue(finished[0].wait(3))
        modified.assert_called_once()
