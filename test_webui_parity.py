"""Tk parity adapters use fake providers, preserve approvals and keep jobs off the UI."""
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from coding_agent import AgentCancelled
from test_coding_agent import tool_response, final_response
import test_webui_app as fixtures


class ParityTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.project = self.store.directory / "project"
        self.project.mkdir()
        (self.project / "index.html").write_text("<h1>Local test</h1>", encoding="utf-8")
        self.service = SimpleNamespace(default_backend="Ollama", url="http://127.0.0.1:8000/v1", _key="test-key", _explicit_url=False,
            _folder=str(self.project), _lock=threading.Lock(), folder=lambda: str(self.project),
            _adopt=Mock(), availability=Mock(return_value={"installed": True, "running": False, "models": [], "state": "stopped"}))
        self.api._colibri = self.service

    def result(self, identity, kind="job_done"):
        with self.events.condition:
            def match():
                return next((event for event in self.events.items if event["type"] == kind and event.get("job_id") == identity), None)
            self.assertTrue(self.events.condition.wait_for(lambda: match() is not None, 3), self.events.items)
            return match()

    def test_updates_feedback_and_capabilities_reuse_existing_logic(self):
        with patch("webui.parity.check_update", return_value={"revision": "abc", "differences": [], "download": "https://example.com/source.zip"}) as check:
            identity = self.api.check_updates()["job_id"]
            self.assertEqual(self.result(identity)["result"]["revision"], "abc")
            check.assert_called_once_with(Path(__file__).parent.resolve())
        chat = self.api.new_chat()["chat"]["id"]
        record = self.store.load(chat)
        record["messages"] = [{"role": "assistant", "content": "Old answer"}]
        self.store.save(record)
        value = self.api.prepare_feedback(chat, 0, "Use exact values", "The count is correct")
        self.assertIn("Use exact values", value["text"])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.api.prepare_feedback(chat, 99, "bad")["ok"])
        with patch("webui.parity.fetch_model_capabilities", return_value={"capabilities": ["tools"], "context_length": 32768}) as fetch:
            self.assertEqual(self.api.model_capabilities("m")["metadata"]["context_length"], 32768)
            fetch.assert_called_once_with("http://localhost:11434", "m")

    def test_colibri_management_uses_the_launcher_and_cancellation_event(self):
        with patch("webui.parity.launcher.start_server", return_value={"server": {"state": "ready"}}) as start:
            identity = self.api.start_colibri()["job_id"]
            self.result(identity)
            self.assertIsInstance(start.call_args.kwargs["cancel_event"], threading.Event)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def install(folder, name, progress, cancel, ctx):
            progress("download", 5, 10)
            entered.set()
            self.assertTrue(cancel.wait(3))
            release.wait(3)
        with patch("webui.parity.launcher.stop_server") as stop, patch("webui.parity.launcher.install_model", side_effect=install) as install_mock:
            identity = self.api.install_colibri("chosen", 16384)["job_id"]
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.api.start_colibri()["ok"])
            self.assertTrue(self.api.cancel_job(identity)["active"])
            release.set()
            self.result(identity, "job_stopped")
            self.assertEqual(install_mock.call_args.args[1], "chosen")
            self.assertEqual(install_mock.call_args.kwargs["ctx"], 16384)
            stop.assert_called_once()
        self.service.url = "https://remote.example/v1"
        self.assertFalse(self.api.stop_colibri()["ok"])
        self.assertFalse(self.api.install_colibri("--bad")["ok"])

    def test_deployment_uses_the_chosen_project_and_blocks_agent_writes_until_done(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def deploy(folder, project, scope, domain, progress, cancel):
            progress("Build output")
            entered.set(); release.wait(3)
            return {"url": "https://test.vercel.app"}
        with patch("webui.parity.vercel_deploy.deploy_project", side_effect=deploy) as deploy_mock:
            identity = self.api.deploy_vercel(str(self.project), "test-project")["job_id"]
            self.assertTrue(entered.wait(2))
            chat = self.api.new_chat()["chat"]["id"]
            self.api.set_chat_model(chat, "fake:7b")
            self.assertFalse(self.api.run_agent(chat, "edit", str(self.project))["ok"])
            release.set()
            self.assertEqual(self.result(identity)["result"]["url"], "https://test.vercel.app")
            self.assertEqual(deploy_mock.call_args.args[:2], (self.project, "test-project"))

    def test_hybrid_review_runs_once_after_approval_without_tools(self):
        self.service.url, self.service._explicit_url = "https://review.example/v1", True
        self.service.folder = lambda: None
        self.api.set_hybrid_settings(True, "reviewer", False)
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "fake:7b")
        with patch("coding_agent._chat", side_effect=[tool_response("write_file", {"path": "a.py", "content": "value = 2\n"}), final_response()]) as worker, \
                patch("colibri_backend.ColibriClient.review", return_value="Add a focused check.") as review:
            task = self.api.run_agent(chat, "Write a.py", str(self.project))["task_id"]
            approval = self.events.wait("approval")
            review.assert_not_called()
            self.assertFalse((self.project / "a.py").exists())
            self.api.approve_action(task, approval["approval_id"], True)
            self.events.wait("agent_done")
            self.assertEqual(worker.call_count, 2)
            review.assert_called_once()
            self.assertEqual(review.call_args.args[1], "reviewer")
            self.assertEqual([item["role"] for item in review.call_args.args[0]], ["system", "user"])
        self.assertEqual((self.project / "a.py").read_text(), "value = 2\n")
        self.assertIn("advisory", self.store.load(chat)["messages"][-1]["content"])

    def test_cancelling_only_review_keeps_completed_worker_history(self):
        self.service.url, self.service._explicit_url = "https://review.example/v1", True
        self.service.folder = lambda: None
        self.api.set_hybrid_settings(True, "reviewer", False)
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "fake:7b")
        entered = threading.Event()
        def review(messages, model, cancel):
            entered.set(); cancel.wait(3)
            raise AgentCancelled("Stopped review")
        with patch("coding_agent._chat", return_value=final_response()), patch("colibri_backend.ColibriClient.review", side_effect=review):
            task = self.api.run_agent(chat, "Read only", str(self.project))["task_id"]
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.api.stop_colibri()["ok"])
            self.api.stop_task(task)
            self.events.wait("agent_stopped")
        saved = self.store.load(chat)
        self.assertIn("kept", saved["messages"][-1]["content"])
        self.assertEqual(saved["last_response"]["status"], "stopped")

    def test_job_errors_and_closed_delivery_do_not_escape_to_workers(self):
        with patch("webui.parity.check_update", side_effect=RuntimeError("offline")):
            identity = self.api.check_updates()["job_id"]
            self.assertIn("offline", self.result(identity, "job_error")["error"])
        identity = self.api._jobs.start("invalid", lambda progress, cancel: {"bad": object()})["job_id"]
        self.assertIsNone(self.result(identity, "job_error")["result"])
        self.api._close()
        self.assertFalse(self.api.check_updates()["ok"])

    def test_handoff_blocks_other_users_of_worker_until_review_finishes(self):
        self.api.set_hybrid_settings(True, "reviewer", True)
        chat = self.api.new_chat()["chat"]["id"]
        self.api.set_chat_model(chat, "fake:7b")
        other = self.api.new_chat()["chat"]["id"]
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def review(*args):
            entered.set(); release.wait(3)
            return "Checked source only."
        callbacks = ({"url": self.service.url}, Mock(), Mock(), Mock())
        with patch("hybrid_ui.memory_callbacks", return_value=callbacks), \
                patch("coding_agent._chat", return_value=final_response()), \
                patch("colibri_backend.ColibriClient.review", side_effect=review):
            self.assertTrue(self.api.run_agent(chat, "Inspect", str(self.project))["ok"])
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.api.send(other, "Hello", "fake:7b", None)["ok"])
            self.assertFalse(self.api.run_agent(other, "Inspect", str(self.project), quality_review=False)["ok"])
            release.set()
            self.events.wait("agent_done")
            for callback in callbacks[1:]:
                callback.assert_called_once()
        self.service.folder = lambda: None
        self.assertIn("Colibri folder", self.api.run_agent(other, "Inspect", str(self.project))["error"])


if __name__ == "__main__":
    unittest.main()
