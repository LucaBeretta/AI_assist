"""Tk workspace integration with temporary history, projects, and fake Ollama."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from AIassist import AIAssistantGUI


def model_metadata(host, model):
    return {"name": model, "capabilities": ["completion", "tools"],
            "context_length": 32768, "family": "test", "parameter_size": "8B",
            "quantization": "Q4_K_M"}


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.history_folder = self.folder / "history"
        self.project = self.folder / "project"
        self.project.mkdir()
        self.original_code = "print('original café')\n"
        (self.project / "main.py").write_text(self.original_code, encoding="utf-8")
        (self.project / "README.md").write_text("Workspace fixture\n", encoding="utf-8")
        (self.project / ".env").write_text("TEST_SECRET=excluded\n", encoding="utf-8")
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {
            "AI_ASSIST_DATA_DIR": str(self.history_folder), "OLLAMA_MODEL": "alpha:8b",
            "OLLAMA_HOST": "http://localhost:11434",
        }))
        self.ensure_ollama = self.mocks.enter_context(patch("AIassist.ensure_ollama"))
        self.list_models = self.mocks.enter_context(patch("AIassist.list_ollama_models",
                                                        return_value=["alpha:8b", "beta:3b"]))
        self.running_models = self.mocks.enter_context(patch("AIassist.running_models",
                                                           return_value={"alpha:8b", "beta:3b"}))
        self.set_running = self.mocks.enter_context(patch("AIassist.set_model_running"))
        self.request = self.mocks.enter_context(patch("AIassist.request_ollama", return_value="A local answer."))
        self.mocks.enter_context(patch("capabilities_view.fetch_model_capabilities", side_effect=model_metadata))
        self.agent_metadata = self.mocks.enter_context(patch("workspace_ui.fetch_model_capabilities",
                                                           side_effect=model_metadata))
        # This is a tripwire for any code path accidentally bypassing the fake APIs.
        self.mocks.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("Network access in workspace test")))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = None
        self.worker_finished = []
        self.worker_releases = []
        self.addCleanup(self.close_workspace)
        self.app = AIAssistantGUI(self.root)
        self.wait_for(lambda: not self.app.startup_pending and not self.app.busy(), "mock model startup")
        self.assertEqual(self.app.model_name.get(), "alpha:8b")
        self.assertEqual(self.app.chat_store.directory, self.history_folder.resolve())

    def close_workspace(self):
        for release in self.worker_releases:
            release.set()
        self.root.update_idletasks()
        for scheduled in self.root.tk.call("after", "info"):
            # A child widget can own the Tcl callback. Cancel its timer without
            # removing a command that the child will unregister on destruction.
            self.root.tk.call("after", "cancel", scheduled)
        if self.app is not None and not self.app.closed:
            self.app.close()
        elif self.app is None:
            self.root.destroy()
        for finished in self.worker_finished:
            self.assertTrue(finished.wait(1), "A mocked agent worker was left waiting for approval")
        self.assertEqual([str(error[1]) for error in self.callback_errors], [], "Tk callback failed")

    def wait_for(self, predicate, description="expected UI state", timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail(f"Timed out waiting for {description}; status={self.app.status_label.cget('text')!r}; "
                  f"callback errors={[str(error[1]) for error in self.callback_errors]!r}")

    def set_input(self, text):
        self.assertEqual(str(self.app.input_box.cget("state")), "normal")
        self.app.input_box.delete("1.0", "end")
        self.app.input_box.insert("1.0", text)

    def enter_project(self):
        self.app.nav_buttons["dev"].invoke()
        with patch("workspace_ui.filedialog.askdirectory", return_value=str(self.project)) as choose:
            self.app.choose_project_button.invoke()
        choose.assert_called_once()
        self.wait_for(lambda: not self.app.project_loading and bool(self.app.project_files), "explicit project scan")
        self.assertEqual(self.app.project_path, str(self.project.resolve()))

    def schedule_review(self, approve, snapshots):
        deadline = time.monotonic() + 3

        def poll():
            if not self.app.approvals:
                if time.monotonic() < deadline:
                    self.root.after(10, poll)
                return
            decision, window = self.app.approvals[0]
            texts = [widget.get("1.0", "end-1c") for widget in descendants(window)
                     if isinstance(widget, tk.Text)]
            snapshots.append({"content": (self.project / "main.py").read_text(encoding="utf-8"),
                              "resolved": decision["event"].is_set(), "review": "\n".join(texts)})
            label = "Apply change" if approve else "Reject"
            buttons = [widget for widget in descendants(window)
                       if isinstance(widget, tk.Button) and widget.cget("text") == label]
            if buttons:
                buttons[0].invoke()

        self.root.after(10, poll)

    def fake_reviewed_agent(self, decisions, final_text="Updated main.py and checked the result."):
        finished = threading.Event()
        self.worker_finished.append(finished)

        def run(host, model, project_root, messages, on_event, approve_action, **options):
            try:
                on_event({"type": "tool_call", "text": "Read main.py\nExact café output  ", "path": "main.py",
                          "unpersisted_details": {"sample": "excluded metadata"}})
                replacement = "print('reviewed 工具')\n"
                action = {"kind": "write_file", "path": "main.py", "content": replacement,
                          "diff": "--- main.py\n+++ main.py\n-print('original café')\n+print('reviewed 工具')\n"}
                approved = approve_action(action)
                decisions.append(approved)
                if approved and not options["cancel_event"].is_set():
                    Path(project_root, "main.py").write_text(replacement, encoding="utf-8")
                    on_event({"type": "tool_result", "text": "Wrote main.py", "path": "main.py"})
                if options["cancel_event"].is_set():
                    raise RuntimeError("Developer agent cancelled.")
                return {"assistant_text": final_text}
            finally:
                finished.set()

        return run

    def test_new_chat_and_resume_preserve_messages_draft_and_context(self):
        self.assertEqual(self.app.chat_store.list_chats(), [], "Startup should not create empty chat files")
        self.app.model_contexts["alpha:8b"] = 8192
        self.request.return_value = "Exact café answer\n```python\nprint('工具')\n```"
        self.set_input("Explain this project")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "chat response")
        original_id = self.app.current_chat["id"]
        messages = [{"role": "user", "content": "Explain this project"},
                    {"role": "assistant", "content": self.request.return_value}]
        self.assertEqual(self.app.chat_store.load(original_id)["messages"], messages)
        draft = "  Follow up later\nwith café and 工具  "
        self.set_input(draft)
        self.wait_for(lambda: self.app.chat_store.load(original_id)["draft"] == draft, "debounced draft save")
        original = self.app.chat_store.load(original_id)
        self.app.new_chat_button.invoke()
        new_id = self.app.current_chat["id"]
        self.assertNotEqual(new_id, original_id)
        self.assertEqual(self.app.current_chat["title"], "New chat")
        self.assertEqual(self.app.heading_label.cget("text"), "New chat")
        self.assertEqual(self.app.conversation, [])
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "")
        self.assertEqual(len(self.app.chat_store.list_chats()), 2)
        self.assertEqual(self.app.chat_store.load(new_id)["messages"], [])
        self.assertEqual(self.app.chat_store.load(new_id)["draft"], "")
        self.assertEqual(self.app.chat_store.load(original_id), original)
        self.assertEqual(self.app.history_buttons[0].cget("text"), "New chat")
        self.assertEqual(self.app.history_buttons[0].winfo_manager(), "pack")
        original_button = next(button for button in self.app.history_buttons if button.cget("text") == original["title"])
        original_button.invoke()
        self.assertEqual(self.app.current_chat["id"], original_id)
        self.assertEqual(self.app.conversation, messages)
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), draft)
        self.assertEqual(self.app.model_contexts["alpha:8b"], 8192)
        self.request.assert_called_once_with([messages[0]], "alpha:8b", context_length=8192)

    def test_new_chat_is_visible_after_clearing_a_nonmatching_history_search(self):
        self.app.history_search.set("a query that never matches New chat")
        self.wait_for(lambda: not self.app.history_buttons, "empty filtered history")
        self.app.new_chat_button.invoke()
        identity = self.app.current_chat["id"]
        self.assertEqual(self.app.history_search.get(), "")
        self.assertTrue(self.app.history_buttons, "A new saved chat must have a sidebar row")
        self.assertEqual(self.app.history_buttons[0].cget("text"), "New chat")
        self.assertEqual(self.app.history_buttons[0].winfo_manager(), "pack")
        self.assertEqual(self.app.history_buttons[0].cget("bg"), self.app.COLORS["sidebar_hover"])
        self.assertEqual(self.app.chat_store.load(identity)["messages"], [])

    def test_model_change_saves_draft_under_original_model(self):
        self.app.model_contexts["alpha:8b"] = 8192
        alpha_draft = "Unfinished alpha work\n  café"
        self.set_input(alpha_draft)
        self.app.model_name.set("beta:3b")
        self.app.select_model()
        self.wait_for(lambda: not self.app.busy(), "new model startup")
        records = self.app.chat_store.list_chats()
        self.assertEqual(len(records), 1)
        alpha = records[0]
        self.assertEqual((alpha["model"], alpha["draft"], alpha["context_length"]),
                         ("alpha:8b", alpha_draft, 8192))
        self.assertEqual(self.app.active_model, "beta:3b")
        self.assertIsNone(self.app.current_chat)
        beta_draft = "Separate beta notes"
        self.set_input(beta_draft)
        self.app.open_chat(alpha["id"])
        self.assertEqual(self.app.model_name.get(), "alpha:8b")
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), alpha_draft)
        beta = next(record for record in self.app.chat_store.list_chats() if record["model"] == "beta:3b")
        self.assertEqual(beta["draft"], beta_draft)
        self.assertEqual(self.app.chat_store.load(alpha["id"])["model"], "alpha:8b")

    def test_project_scan_disables_model_changes_without_relabeling_history(self):
        self.enter_project()
        self.set_input("Keep the alpha developer draft")
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.app.project_loading = True
        self.app.update_controls()
        self.assertEqual(str(self.app.model_picker.cget("state")), "disabled")
        self.assertFalse(self.app.select_recommended_model("beta:3b", 8192))
        self.assertEqual(self.app.model_name.get(), "alpha:8b")
        self.app.persist_current()
        saved = self.app.chat_store.load(identity)
        self.assertEqual(saved["model"], "alpha:8b")
        self.assertEqual(saved["draft"], "Keep the alpha developer draft")
        self.app.project_loading = False
        self.app.update_controls()

    def test_resuming_default_context_does_not_inherit_another_chats_setting(self):
        configured = self.app.chat_store.create(model="alpha:8b", title="Configured context")
        configured["context_length"] = 8192
        self.app.chat_store.save(configured)
        default = self.app.chat_store.create(model="alpha:8b", title="Default context")
        self.app.open_chat(configured["id"])
        self.assertEqual(self.app.model_contexts["alpha:8b"], 8192)
        self.app.open_chat(default["id"])
        self.assertNotIn("alpha:8b", self.app.model_contexts)
        self.app.persist_current()
        self.assertIsNone(self.app.chat_store.load(default["id"])["context_length"])

    def test_capabilities_navigation_preserves_draft_and_returns_to_chat(self):
        draft = "Keep these chat notes\n雪"
        self.set_input(draft)
        self.app.nav_buttons["capabilities"].invoke()
        identity = self.app.current_chat["id"]
        self.assertEqual(self.app.current_section, "capabilities")
        self.assertEqual(self.app.heading_label.cget("text"), "Capabilities")
        self.assertEqual(self.app.chat_store.load(identity)["draft"], draft)
        self.app.nav_buttons["chat"].invoke()
        self.assertEqual(self.app.current_section, "chat")
        self.assertEqual(self.app.current_chat["id"], identity)
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), draft)

    def test_developer_scans_only_chosen_project_and_pins_selected_context(self):
        import workspace_ui

        with patch("workspace_ui.list_project_files", wraps=workspace_ui.list_project_files) as scan:
            self.app.nav_buttons["dev"].invoke()
            self.assertEqual(self.app.current_mode, "dev")
            self.assertEqual(self.app.dev_panel.winfo_manager(), "pack")
            scan.assert_not_called()
            self.assertEqual(str(self.app.send_button.cget("state")), "disabled")
            with patch("workspace_ui.filedialog.askdirectory", return_value=str(self.project)):
                self.app.choose_project_button.invoke()
            self.wait_for(lambda: not self.app.project_loading and bool(self.app.project_files), "chosen project files")
            scan.assert_called_once_with(str(self.project.resolve()))
        self.assertEqual({item["path"] for item in self.app.project_files}, {"main.py", "README.md"})
        self.assertEqual(self.app.project_context, "")
        index = next(index for index, item in enumerate(self.app.project_files) if item["path"] == "main.py")
        self.app.files_list.selection_set(index)
        self.app.attach_button.invoke()
        self.wait_for(lambda: not self.app.project_loading and bool(self.app.project_context), "pinned file context")
        self.assertEqual(self.app.project_selected_paths, ["main.py"])
        self.assertIn("--- Project file: main.py ---", self.app.project_context)
        self.assertIn(self.original_code, self.app.project_context.replace("\r\n", "\n"))
        self.assertNotIn("TEST_SECRET", self.app.project_context)
        self.set_input("Resume this project later")
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        saved = self.app.chat_store.load(identity)
        self.assertEqual(saved["project_files"], ["main.py"])
        self.assertNotIn("project_context", saved)
        self.app.nav_buttons["chat"].invoke()
        self.assertEqual(self.app.current_mode, "chat")
        self.assertEqual(self.app.dev_panel.winfo_manager(), "")
        self.app.open_chat(identity)
        self.wait_for(lambda: not self.app.project_loading, "restored project context")
        self.assertEqual(self.app.current_mode, "dev")
        self.assertEqual(self.app.project_selected_paths, ["main.py"])
        self.assertIn(self.original_code, self.app.project_context.replace("\r\n", "\n"))

    def test_code_artifact_saves_only_after_selecting_a_temporary_destination(self):
        code = "# café\nprint('工具')  \n\n"
        response = "A code example:\n```python filename=../../untrusted.py\n" + code + "```\n"
        self.app.display_message("AI Assist", response, "ai")
        self.assertEqual(self.app.artifacts[0]["code"], code)
        self.assertEqual(self.app.code_preview.get("1.0", "end-1c"), code)
        destination = self.folder / "chosen.py"
        self.assertFalse(destination.exists())
        with patch("workspace_ui.filedialog.asksaveasfilename", return_value=str(destination)) as choose:
            self.app.save_code_button.invoke()
        self.assertEqual(choose.call_args.kwargs["initialfile"], "untrusted.py")
        self.assertEqual(destination.read_text(encoding="utf-8"), code)
        self.assertEqual(self.app.status_label.cget("text"), "Code saved.")
        self.app.display_message("AI Assist", "```python\nprint('unfinished')", "ai")
        with patch("workspace_ui.filedialog.asksaveasfilename") as choose:
            self.app.save_selected_artifact()
        choose.assert_not_called()
        self.assertIn("complete code block", self.app.status_label.cget("text"))

    def test_agent_waits_for_review_and_saves_response_history_and_activity(self):
        self.enter_project()
        self.app.model_contexts["alpha:8b"] = 8192
        decisions, snapshots = [], []
        fake = self.fake_reviewed_agent(decisions)
        self.schedule_review(True, snapshots)
        with patch("workspace_ui.run_agent", side_effect=fake) as run:
            self.set_input("Improve main.py")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "reviewed agent response")
        self.assertEqual(decisions, [True])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["content"], self.original_code)
        self.assertFalse(snapshots[0]["resolved"])
        self.assertIn("+print('reviewed 工具')", snapshots[0]["review"])
        self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), "print('reviewed 工具')\n")
        self.assertEqual(run.call_args.args[1:3], ("alpha:8b", str(self.project.resolve())))
        self.assertEqual(run.call_args.kwargs["context_length"], 8192)
        identity = self.app.current_chat["id"]
        saved = self.app.chat_store.load(identity)
        self.assertEqual(saved["messages"], [{"role": "user", "content": "Improve main.py"},
                                             {"role": "assistant", "content": "Updated main.py and checked the result."}])
        self.assertEqual(saved["activity"], self.app.dev_activity)
        self.assertEqual(saved["activity"][0], {"type": "tool_call", "text": "Read main.py\nExact café output  ",
                                                  "path": "main.py"})
        self.assertEqual(saved["activity"][-1]["type"], "tool_result")
        self.assertIn(identity, [record["id"] for record in self.app.history_records])
        self.assertEqual(self.app.approvals, [])
        self.app.new_chat_button.invoke()
        self.app.open_chat(identity)
        self.wait_for(lambda: not self.app.project_loading, "agent chat restored")
        self.assertEqual(self.app.dev_activity, saved["activity"])
        self.assertIn("Wrote main.py", self.app.dev_log.get("1.0", "end-1c"))

    def test_rejecting_review_keeps_the_project_file_unchanged(self):
        self.enter_project()
        decisions, snapshots = [], []
        self.schedule_review(False, snapshots)
        with patch("workspace_ui.run_agent", side_effect=self.fake_reviewed_agent(decisions, "No changes were applied.")):
            self.set_input("Propose a change")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "declined agent response")
        self.assertEqual(decisions, [False])
        self.assertEqual(snapshots[0]["content"], self.original_code)
        self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), self.original_code)
        self.assertEqual(self.app.current_chat["messages"][-1]["content"], "No changes were applied.")

    def test_resumed_agent_followup_preserves_native_tool_provenance(self):
        self.enter_project()
        native = []
        received = []

        def run(host, model, project_root, messages, on_event, approve_action, **options):
            received.append(deepcopy(messages))
            if len(received) == 1:
                native.extend(deepcopy(messages) + [
                    {"role": "assistant", "content": "", "thinking": "Inspect café before explaining.",
                     "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "main.py"}}}]},
                    {"role": "tool", "tool_name": "read_file", "content": self.original_code},
                    {"role": "assistant", "content": "main.py prints a greeting."},
                ])
                on_event({"type": "tool_result", "text": self.original_code})
                return {"messages": deepcopy(native), "assistant_text": "main.py prints a greeting."}
            return {"messages": deepcopy(messages) + [{"role": "assistant", "content": "It prints original café."}],
                    "assistant_text": "It prints original café."}

        with patch("workspace_ui.run_agent", side_effect=run):
            self.set_input("Read main.py")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "native agent history")
            identity = self.app.current_chat["id"]
            self.assertEqual(self.app.chat_store.load(identity)["agent_history"], native)
            self.app.new_chat_button.invoke()
            self.app.open_chat(identity)
            self.wait_for(lambda: not self.app.project_loading, "restored native agent history")
            self.assertEqual(self.app.agent_history, native)
            self.set_input("What did the file say?")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "agent follow-up response")
        self.assertEqual(received[1][1:-1], [item for item in native if item["role"] != "system"])
        self.assertEqual(received[1][-1], {"role": "user", "content": "What did the file say?"})
        self.assertEqual(sum(item["role"] == "system" for item in received[1]), 1)
        saved = self.app.chat_store.load(identity)
        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant", "user", "assistant"])
        native_tool = next(item for item in saved["agent_history"] if item["role"] == "tool")
        self.assertEqual(native_tool, {"role": "tool", "tool_name": "read_file", "content": self.original_code})

    def test_stopping_a_pending_review_releases_the_worker_without_a_write(self):
        self.enter_project()
        decisions = []
        with patch("workspace_ui.run_agent", side_effect=self.fake_reviewed_agent(decisions)):
            self.set_input("Wait for review")
            self.app.send_button.invoke()
            self.wait_for(lambda: bool(self.app.approvals), "pending approval dialog")
            decision, _ = self.app.approvals[0]
            self.assertFalse(decision["event"].is_set())
            self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), self.original_code)
            self.app.stop_agent_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting and self.worker_finished[-1].is_set(), "cancelled agent worker")
        self.assertEqual(decisions, [False])
        self.assertTrue(decision["event"].is_set())
        self.assertTrue(self.app.agent_cancel.is_set())
        self.assertEqual(self.app.approvals, [])
        self.assertFalse(self.app.agent_active)
        self.assertIn("cancelled", self.app.status_label.cget("text").lower())
        self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), self.original_code)
        self.assertEqual(self.app.current_chat["messages"], [{"role": "user", "content": "Wait for review"}])

    def test_active_agent_can_return_from_capabilities_to_its_stop_control(self):
        self.enter_project()
        entered, released, finished = threading.Event(), threading.Event(), threading.Event()
        self.worker_finished.append(finished)

        def run(host, model, project_root, messages, on_event, approve_action, **options):
            try:
                entered.set()
                options["cancel_event"].wait(3)
                released.set()
                raise RuntimeError("Developer agent cancelled.")
            finally:
                finished.set()

        with patch("workspace_ui.run_agent", side_effect=run):
            self.set_input("Inspect until stopped")
            self.app.send_button.invoke()
            self.wait_for(entered.is_set, "running agent")
            self.app.nav_buttons["capabilities"].invoke()
            self.assertEqual(self.app.current_section, "capabilities")
            self.app.nav_buttons["dev"].invoke()
            self.assertEqual(self.app.current_section, "dev")
            self.assertEqual(self.app.dev_panel.winfo_manager(), "pack")
            self.assertEqual(str(self.app.stop_agent_button.cget("state")), "normal")
            self.app.stop_agent_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting and released.is_set(), "agent stopped after navigation")

    def test_model_without_native_tools_has_readable_guidance(self):
        self.enter_project()
        metadata = model_metadata("", "alpha:8b")
        metadata["capabilities"] = ["completion"]
        with patch("workspace_ui.fetch_model_capabilities", return_value=metadata), patch("workspace_ui.run_agent") as run:
            self.set_input("Inspect the project")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "tool capability validation")
        run.assert_not_called()
        status = self.app.status_label.cget("text")
        self.assertIn("does not advertise tool calling", status)
        self.assertIn("tool-capable model", status)
        self.assertEqual(self.app.current_chat["messages"], [{"role": "user", "content": "Inspect the project"}])
        self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), self.original_code)

    def test_model_metadata_failure_remains_readable_and_keeps_user_history(self):
        self.enter_project()
        error = "Could not reach Ollama to inspect this model. Retry when it is running."
        with patch("workspace_ui.fetch_model_capabilities", side_effect=RuntimeError(error)), patch("workspace_ui.run_agent") as run:
            self.set_input("Inspect after reconnecting")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "metadata failure")
        run.assert_not_called()
        self.assertEqual(self.app.status_label.cget("text"), error)
        saved = self.app.chat_store.load(self.app.current_chat["id"])
        self.assertEqual(saved["messages"], [{"role": "user", "content": "Inspect after reconnecting"}])
        self.assertFalse(self.app.agent_active)
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")

    def test_stop_finishes_the_agent_while_metadata_lookup_is_still_pending(self):
        self.enter_project()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.worker_releases.append(release)
        self.worker_finished.append(finished)

        def inspect(host, model):
            entered.set()
            try:
                release.wait(3)
                return model_metadata(host, model)
            finally:
                finished.set()

        with patch("workspace_ui.fetch_model_capabilities", side_effect=inspect), patch("workspace_ui.run_agent") as run:
            self.set_input("Stop before metadata arrives")
            self.app.send_button.invoke()
            self.wait_for(entered.is_set, "blocked metadata lookup")
            self.app.stop_agent_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "cancelled metadata preflight", timeout=1.5)
            self.assertFalse(finished.is_set(), "Cancellation should finish before the blocked metadata lookup")
            self.assertIn("stopped", self.app.status_label.cget("text").lower())
            self.assertFalse(self.app.agent_active)
            release.set()
            self.wait_for(finished.is_set, "released metadata lookup")
        run.assert_not_called()
        self.assertEqual(self.app.current_chat["messages"], [{"role": "user", "content": "Stop before metadata arrives"}])


if __name__ == "__main__":
    unittest.main()
