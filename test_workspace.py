"""Tk workspace integration with temporary history, projects, and fake Ollama."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import gc
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import ANY, patch

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
        gc.collect()
        self.addCleanup(gc.collect)
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
        self.mocks.enter_context(patch("update_checker.UpdateChecker.check", lambda self: None))
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

    def test_settings_contains_update_check_and_replaces_sidebar_link(self):
        self.assertEqual(str(self.root.cget("menu")), str(self.app.menubar))
        self.assertEqual(self.app.menubar.entrycget(1, "label"), "Settings")
        self.assertFalse(hasattr(self.app, "settings_button"))
        self.assertFalse(hasattr(self.app, "backend_button"))
        self.assertFalse(hasattr(self.app, "updates_button"))
        self.assertEqual(self.app.settings_menu.entrycget(1, "label"), "AI backend: Ollama...")
        self.assertEqual(self.app.settings_menu.entrycget(3, "label"), "Check for updates...")

        self.app.settings_menu.invoke(0)
        self.assertEqual(self.app.appearance.window.title(), "Settings")
        update_button = next(
            widget for widget in descendants(self.app.appearance.window)
            if widget.winfo_class() == "Button" and widget.cget("text") == "Check for updates"
        )
        self.assertTrue(update_button.winfo_exists())
        self.assertEqual(self.app.settings_menu.entrycget(4, "label"), "Hourly status...")

    def test_hourly_status_window_exposes_an_off_by_default_mic_toggle(self):
        report = self.app.hourly_status
        self.assertFalse(report.enabled)
        window = report.open()
        self.assertTrue(window.winfo_exists())
        self.assertFalse(report.enabled_var.get())
        self.assertIn("no audio is saved or sent", report.state_label.cget("text"))
        report._close_window()

    def test_model_controls_keep_navigation_and_chat_available(self):
        self.root.deiconify()
        self.wait_for(lambda: self.app.sidebar_canvas.winfo_height() > 1, "sidebar layout")
        previous = (self.app.current_section, self.app.model_name.get())
        history_height = self.app.sidebar_canvas.winfo_height()
        self.app.model_controls_button.invoke()
        self.wait_for(lambda: self.app.model_controls_window.state() == "normal", "model controls")
        self.assertTrue(self.app.context_picker.winfo_ismapped())
        self.assertEqual(self.app.sidebar_canvas.winfo_height(), history_height)
        self.assertEqual((self.app.current_section, self.app.model_name.get()), previous)
        self.app.hide_model_controls()
        self.assertEqual(self.app.model_controls_window.state(), "withdrawn")

    def test_composer_is_labelled_and_has_a_visible_boundary(self):
        for theme in ("Light", "Dark", "Warm"):
            self.app.appearance.apply(theme, "Modern")
            self.assertEqual(self.app.composer_label.cget("text"), "Message")
            self.assertEqual(self.app.composer_frame.cget("highlightbackground"), self.app.COLORS["input_border"])
            self.assertEqual(self.app.input_box.cget("selectforeground"), self.app.COLORS["ink"])
        self.app.model_name.set("")
        self.app.update_controls()
        self.assertIn("Choose a model", self.app.composer_hint.cget("text"))
        self.assertEqual(str(self.app.send_button.cget("state")), "disabled")

    def test_suggestion_cards_reflow_in_the_developer_workspace(self):
        self.root.deiconify()
        self.root.geometry("1280x860")
        self.wait_for(lambda: self.app.suggestion_buttons[-1].grid_info().get("column") == 1,
                      "two-column chat suggestions")
        self.app.show_section("dev")
        self.root.geometry("1060x700")
        self.wait_for(lambda: self.app.suggestion_buttons[-1].grid_info().get("row") == len(self.app.suggestion_buttons) - 1,
                      "one-column developer suggestions")
        self.assertTrue(all(button.winfo_x() + button.winfo_width() <= button.master.winfo_width()
                            for button in self.app.suggestion_buttons))
        self.assertFalse(self.callback_errors)

    def test_developer_landing_does_not_automatically_resume_old_work(self):
        old = self.app.chat_store.create(model="alpha:8b", mode="dev", project_path=str(self.project))
        old["title"] = "create a three.js file"
        self.app.chat_store.save(old)
        self.app.refresh_history()
        with patch("workspace_ui.list_project_files") as scan:
            self.app.show_section("dev")
            scan.assert_not_called()
        self.assertIsNone(self.app.current_chat)
        self.assertEqual(self.app.project_path, "")
        self.assertEqual(self.app.heading_label.cget("text"), "Developer")
        resume = next(widget for widget in descendants(self.app.empty_state)
                      if isinstance(widget, tk.Button) and widget.cget("text") == old["title"])
        resume.invoke()
        self.wait_for(lambda: self.app.project_ready, "explicit developer resume")
        self.assertEqual(self.app.current_chat["id"], old["id"])

    def test_developer_session_is_kept_when_switching_sections(self):
        self.enter_project()
        identity = self.app.current_chat["id"]
        self.app.show_section("chat")
        self.app.show_section("dev")
        self.wait_for(lambda: self.app.project_ready, "active developer session")
        self.assertEqual(self.app.current_chat["id"], identity)
        self.assertEqual(self.app.project_path, str(self.project.resolve()))

    def test_filtered_files_preview_and_pin_the_selected_file(self):
        self.enter_project()
        self.app.file_search.set("MAIN")
        self.assertEqual(self.app.files_list.get(0, "end"), ("main.py",))
        self.app.files_list.selection_set(0)
        self.app.preview_project_file()
        self.wait_for(lambda: self.app.source_title.cget("text") == "main.py", "embedded source")
        self.assertEqual(self.app.source_preview.get("1.0", "end-1c").replace("\r\n", "\n"), self.original_code)
        self.assertEqual(self.app.dev_tabs.select(), str(self.app.source_tab))
        self.app.attach_button.invoke()
        self.wait_for(lambda: not self.app.project_loading, "filtered file pin")
        self.assertEqual(self.app.project_selected_paths, ["main.py"])
        self.app.file_search.set("README")
        self.app.files_list.selection_set(0)
        self.app.preview_project_file()
        self.wait_for(lambda: self.app.source_title.cget("text") == "README.md", "second source")
        self.assertEqual(self.app.source_preview.get("1.0", "end-1c").strip(), "Workspace fixture")

    def test_applied_diffs_are_saved_and_restored_in_changes(self):
        self.enter_project()
        diff = "--- main.py\n+++ main.py\n@@ -1 +1 @@\n-old\n+new\n"
        self.app.log_activity(dict(type="changed", text="Updated main.py", path="main.py", diff=diff))
        self.assertEqual(self.app.changes_preview.get("1.0", "end-1c"), diff)
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.assertEqual(self.load_saved(identity)["activity"][-1]["diff"], diff)
        self.app.start_new_chat()
        self.assertEqual(self.app.applied_changes, [])
        self.app.open_chat(identity)
        self.wait_for(lambda: self.app.project_ready, "changes restored")
        self.assertEqual(self.app.changes_preview.get("1.0", "end-1c"), diff)
        self.assertEqual((self.project / "main.py").read_text(encoding="utf-8"), self.original_code)

    def test_feedback_preserves_draft_and_waits_for_explicit_send(self):
        self.set_input("Explain this idea")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "original answer")
        original = deepcopy(self.app.conversation)
        self.set_input("Keep this unsent draft.  ")
        button = next(widget for widget in descendants(self.app.message_frame)
                      if isinstance(widget, tk.Button) and widget.cget("text") == "Give feedback")
        button.invoke()
        window = self.app.feedback_window
        window.feedback_text.insert("1.0", "Use a concrete example.")
        window.criteria_text.insert("1.0", "Under 100 words and easy to understand.")
        window.apply_button.invoke()
        draft = self.app.input_box.get("1.0", "end-1c")
        self.assertTrue(draft.startswith("Keep this unsent draft.  \n\n"))
        self.assertIn(self.request.return_value, draft)
        self.assertIn("Use a concrete example.", draft)
        self.assertIn("Under 100 words", draft)
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(self.app.conversation, original)
        self.assertIsNone(self.app.feedback_window)
        self.assertEqual(self.load_saved(self.app.current_chat["id"])["draft"], draft)
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "revised answer")
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(self.app.conversation[-2]["content"], draft)
        self.assertEqual(self.app.conversation[:2], original)

    def test_feedback_keyword_opens_local_feedback_without_a_request(self):
        self.set_input("Explain this idea")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "original answer")
        self.set_input("/feedback")
        self.app.send_button.invoke()
        self.assertIsNotNone(self.app.feedback_window)
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(len(self.app.conversation), 2)
        self.app.feedback_window.apply_button.invoke()
        self.assertIsNotNone(self.app.feedback_window, "Empty feedback must not create a follow-up")
        self.app.new_chat()
        self.assertIsNone(self.app.feedback_window)

    def test_developer_verification_uses_actual_events_and_persists(self):
        self.enter_project()
        self.app.log_activity(dict(type="request_start", text="Task started"))
        self.app.log_activity(dict(type="assistant", text="All tests passed."))
        self.assertIn("No command checks", self.app.verification_label.cget("text"))
        self.app.log_activity(dict(type="command_succeeded", text="Command exited with code 0.",
                                   command="python -m unittest"))
        self.app.log_activity(dict(type="changed", text="Updated main.py", path="main.py", diff="+change"))
        self.assertIn("1 succeeded", self.app.verification_label.cget("text"))
        self.assertIn("check again", self.app.verification_label.cget("text"))
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.load_saved(identity)
        self.app.start_new_chat()
        self.app.open_chat(identity)
        self.wait_for(lambda: self.app.project_ready, "verification restored")
        self.assertIn("check again", self.app.verification_label.cget("text"))
        with patch("workspace_ui.run_agent") as run:
            self.app.verify_button.invoke()
            run.assert_not_called()
        self.assertIn("appropriate", self.app.input_box.get("1.0", "end-1c"))
        self.app.log_activity(dict(type="request_start", text="Next task started"))
        self.assertIn("No command checks", self.app.verification_label.cget("text"))

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
            deadline = time.monotonic() + 5
            while not self.app.closed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            if not self.app.closed:
                self.fail(f"Close stalled: {self.app.status_label.cget('text')}; pending={self.app.pending_saves}; callbacks={self.callback_errors}")
            self.app.saver.thread.join(timeout=2)
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

    def load_saved(self, identity):
        self.app.saver.jobs.join()
        self.app.process_saves()
        return self.app.chat_store.load(identity)

    def list_saved(self):
        self.app.saver.jobs.join()
        self.app.process_saves()
        return self.app.chat_store.list_chats()

    def test_context_picker_persists_choice_and_can_restore_server_default(self):
        self.app.start_new_chat()
        self.app.context_choice.set("32768")
        self.app.change_context()
        self.assertEqual(self.app.model_contexts["alpha:8b"], 32768)
        identity = self.app.current_chat["id"]
        self.assertEqual(self.load_saved(identity)["context_length"], 32768)
        self.app.context_choice.set("Server default")
        self.app.change_context()
        self.assertNotIn("alpha:8b", self.app.model_contexts)
        self.assertIsNone(self.load_saved(identity)["context_length"])

    def test_generated_html_has_browser_preview_action(self):
        self.app.display_message("AI Assist", "```html\n<h1>Preview me</h1>\n```", "ai")
        self.assertEqual(str(self.app.preview_code_button.cget("state")), "normal")
        with patch.object(self.app.html_previews, "preview_block", return_value="http://127.0.0.1:1234/index.html") as preview:
            self.app.preview_code_button.invoke()
        self.assertIn("Preview me", preview.call_args.args[0]["code"])
        buttons = [widget for widget in descendants(self.app.message_frame)
                   if isinstance(widget, tk.Button) and widget.cget("text") == "Preview"]
        self.assertEqual(len(buttons), 1)

    def test_selected_project_html_is_previewed_in_browser(self):
        (self.project / "page.html").write_text("<h1>Project</h1>", encoding="utf-8")
        self.enter_project()
        index = next(index for index, item in enumerate(self.app.project_files) if item["path"] == "page.html")
        self.app.files_list.selection_set(index)
        with patch.object(self.app.html_previews, "preview_file", return_value="http://127.0.0.1:1234/page.html") as preview:
            self.app.preview_file_button.invoke()
        preview.assert_called_once_with(str(self.project.resolve()), "page.html")

    def test_deploy_button_runs_vercel_flow_and_displays_result(self):
        (self.project / "index.html").write_text("<h1>Website</h1>", encoding="utf-8")
        self.app.open_deploy()
        dialog = self.app.deploy_dialog
        dialog.folder.set(str(self.project))
        dialog.project.set("my-site")
        dialog.domain.set("example.com")
        dialog.token.set("test-token")
        result = dict(url="https://my-site-abcd.vercel.app", domain="example.com", domain_assigned=True, domain_error="")
        with patch("deploy_dialog.deploy_project", return_value=result) as deploy:
            dialog.deploy_button.invoke()
            self.wait_for(lambda: not dialog.running and dialog.result is not None, "Vercel deployment result")
        self.assertEqual(deploy.call_args.args[:4], (self.project.resolve(), "my-site", "", "example.com"))
        self.assertEqual(deploy.call_args.kwargs["token"], "test-token")
        self.assertIn("Deployment created", dialog.status.cget("text"))
        with patch("deploy_dialog.webbrowser.open") as browser:
            dialog.open_button.invoke()
            browser.assert_called_once_with(result["url"])
        self.assertFalse(self.app.busy())

    def test_deployment_can_be_stopped_and_does_not_freeze_workspace(self):
        from vercel_deploy import DeploymentCancelled
        (self.project / "index.html").write_text("Website", encoding="utf-8")
        self.app.open_deploy()
        dialog = self.app.deploy_dialog
        dialog.folder.set(str(self.project))
        dialog.project.set("my-site")
        entered, finished = threading.Event(), threading.Event()
        dialog.token.set("test-token")
        self.worker_finished.append(finished)

        def deploy(folder, project, scope, domain, output, cancel, *, token):
            entered.set()
            try:
                cancel.wait(3)
                raise DeploymentCancelled("Stopped locally")
            finally:
                finished.set()

        with patch("deploy_dialog.deploy_project", side_effect=deploy):
            dialog.start()
            self.wait_for(entered.is_set, "deployment worker")
            self.assertTrue(self.app.busy())
            self.assertEqual(str(self.app.choose_project_button.cget("state")), "disabled")
            dialog.cancel_button.invoke()
            self.wait_for(lambda: not dialog.running, "deployment cancellation")
        self.assertIn("Stopped locally", dialog.status.cget("text"))
        self.assertFalse(self.app.busy())

    def test_deploy_rejects_desktop_folder_before_starting_worker(self):
        self.app.open_deploy()
        dialog = self.app.deploy_dialog
        dialog.folder.set(str(self.project))
        dialog.project.set("my-site")
        with patch("deploy_dialog.deploy_project") as deploy:
            dialog.start()
        deploy.assert_not_called()
        self.assertFalse(dialog.running)
        self.assertIn("Tkinter", dialog.status.cget("text"))

    def test_shared_note_is_sent_to_a_new_chat(self):
        self.app.shared_memory.remember("Remember that my preferred language is Python.")
        self.app.start_new_chat()
        self.set_input("Which language should we use?")
        self.app.send_prompt()
        self.wait_for(lambda: not self.app.is_waiting, "shared memory response")
        sent = self.request.call_args.args[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("my preferred language is Python", sent[0]["content"])

    def test_add_to_shared_memory_saves_locally_without_model_claims(self):
        self.set_input("Add to shared memory: I prefer Python")
        self.app.send_prompt()
        self.request.assert_not_called()
        from shared_memory import SharedMemory
        self.assertEqual(SharedMemory(self.app.chat_store.directory).notes, "I prefer Python")
        self.assertIn("Saved to shared memory", self.app.conversation[-1]["content"])
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "")

    def test_add_this_to_memory_saves_previous_assistant_reply(self):
        self.app.conversation.append(dict(role="assistant", content="Use semicolon separators for exports."))
        self.set_input("Add this to shared memory")
        self.app.send_prompt()
        self.request.assert_not_called()
        self.assertEqual(self.app.shared_memory.notes, "Use semicolon separators for exports.")

    def test_memory_message_button_persists_the_selected_message(self):
        self.app.display_message("AI Assist", "Use UTF-8 encoding.", "ai")
        button = next(widget for widget in descendants(self.app.message_frame)
                      if isinstance(widget, tk.Button) and widget.cget("text") == "Add to memory")
        button.invoke()
        self.assertEqual(self.app.shared_memory.notes, "Use UTF-8 encoding.")

    def test_disabled_or_failed_memory_save_keeps_prompt_and_reports_error(self):
        self.set_input("Add to shared memory: Keep this note")
        self.app.shared_memory.save("", False)
        self.app.send_prompt()
        self.assertIn("disabled", self.app.status_label.cget("text"))
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "Add to shared memory: Keep this note")
        self.app.shared_memory.save("", True)
        with patch.object(self.app.shared_memory, "save", side_effect=OSError("disk full")):
            self.app.send_prompt()
        self.assertIn("disk full", self.app.status_label.cget("text"))
        self.request.assert_not_called()
        self.assertFalse(self.app.conversation)

    def test_shared_memory_is_sent_to_developer_agent(self):
        self.app.shared_memory.save("Use semicolon separators for CSV files.")
        self.enter_project()
        with patch("workspace_ui.run_agent", return_value={"assistant_text": "Ready"}) as agent:
            self.set_input("Create a CSV export")
            self.app.send_prompt()
            self.wait_for(lambda: not self.app.is_waiting, "developer shared memory")
        sent = agent.call_args.args[3]
        self.assertTrue(any("Use semicolon separators" in item.get("content", "") for item in sent))

    def test_shared_memory_editor_saves_and_can_disable_recall(self):
        self.app.open_shared_memory()
        window = self.app.memory_window
        editor = next(widget for widget in descendants(window) if isinstance(widget, tk.Text))
        editor.insert("1.0", "Prefer small dependencies.")
        checkbox = next(widget for widget in descendants(window) if isinstance(widget, tk.Checkbutton))
        checkbox.invoke()
        save = next(widget for widget in descendants(window) if isinstance(widget, tk.Button) and widget.cget("text") == "Save")
        save.invoke()
        self.assertEqual(self.app.shared_memory.notes, "Prefer small dependencies.")
        self.assertFalse(self.app.shared_memory.enabled)

    def test_deleted_chat_is_removed_from_shared_recall(self):
        self.set_input("I use semicolon CSV exports.")
        self.app.conversation.append(dict(role="user", content="I use semicolon CSV exports."))
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.load_saved(identity)
        self.assertIn("semicolon", self.app.shared_memory.context("CSV", self.app.history_records))
        with patch("workspace_ui.messagebox.askyesno", return_value=True):
            self.app.delete_chat()
        self.assertEqual(self.app.shared_memory.context("CSV", self.app.history_records), "")

    def test_new_file_review_opens_save_picker_and_cancel_releases_agent(self):
        decision = dict(approved=False, event=threading.Event(), action=dict(kind="write_file",
            path="new.py", destination=str(self.project / "new.py"), content="pass\n", exists=False))
        with patch("workspace_ui.filedialog.asksaveasfilename", return_value="") as picker:
            self.app.review_action(decision)
        picker.assert_called_once()
        self.assertTrue(decision["event"].is_set())
        self.assertFalse(decision["approved"])
        self.assertFalse(self.app.approvals)
        self.assertFalse((self.project / "new.py").exists())

    def test_commands_need_a_second_confirmation_after_a_pause(self):
        decision = dict(approved=False, event=threading.Event(), action=dict(
            kind="run_command", command="python -m pytest", cwd=str(self.project), description="Run the tests"))
        with patch.object(type(self.app), "COMMAND_CONFIRM_SECONDS", 1), \
                patch("workspace_ui.network_gate.note_outside") as noted:
            self.app.review_action(decision)
            window = self.app.approvals[-1][1]
            run = next(widget for widget in descendants(window)
                       if isinstance(widget, tk.Button) and widget.cget("text") == "Run command")
            run.invoke()
            labels = [widget.cget("text") for widget in descendants(window) if isinstance(widget, tk.Label)]
            self.assertIn("Second check: run this command?", labels)
            self.assertTrue(any("outside the internet gate" in text for text in labels))
            self.assertEqual((run.cget("text"), str(run.cget("state"))), ("Run (1)", "disabled"))
            run.invoke()
            self.assertFalse(decision["event"].is_set())
            self.wait_for(lambda: run.cget("text") == "Run", "unlocked Run button")
            run.invoke()
        self.assertTrue(decision["approved"])
        noted.assert_called_once_with("Developer agent command", "python -m pytest", "Run the tests")
        self.assertFalse(self.app.approvals)

    def test_an_early_command_approval_is_refused_by_the_core_and_the_dialog_stays(self):
        decision = dict(approved=False, event=threading.Event(), action=dict(
            kind="run_command", command="rm -rf build", cwd=str(self.project), description="Clean"))
        with patch("workspace_ui.network_gate.note_outside") as noted:
            self.app.review_action(decision)
            window = self.app.approvals[-1][1]
            # AI Assist's own reading is shown beside the model's words.
            labels = [widget.cget("text") for widget in descendants(window) if isinstance(widget, tk.Label)]
            self.assertIn("WHAT AI ASSIST CAN TELL", labels)
            self.assertTrue(any(text.startswith("⚠ Deletes files: rm build") for text in labels), labels)
            self.assertTrue(any("The AI says: “Clean”" in text for text in labels), labels)
            self.app.resolve_approval(decision, window, True)  # as if a button skipped both checks
            self.assertFalse(decision["event"].is_set())
            self.assertTrue(window.winfo_exists())
            self.assertEqual(len(self.app.approvals), 1)
            reject = next(widget for widget in descendants(window)
                          if isinstance(widget, tk.Button) and widget.cget("text") == "Reject")
            reject.invoke()
        self.assertEqual((decision["event"].is_set(), decision["approved"]), (True, False))
        noted.assert_not_called()
        self.assertFalse(self.app.approvals)

    def test_picking_another_file_returns_destination_for_fresh_review(self):
        chosen = self.folder / "chosen.py"
        decision = dict(approved=False, event=threading.Event(), action=dict(kind="write_file",
            path="new.py", destination=str(self.project / "new.py"), content="pass\n", exists=False))
        with patch("workspace_ui.filedialog.asksaveasfilename", return_value=str(chosen)):
            self.app.review_action(decision)
        self.assertTrue(decision["event"].is_set())
        self.assertTrue(decision["approved"])
        self.assertEqual(decision["destination"], str(chosen.resolve()))
        self.assertFalse(chosen.exists())

    def test_typed_folder_is_created_selected_and_saved(self):
        self.app.show_section("dev")
        destination = self.folder / "new workspace" / "output"
        self.app.project_directory.set(str(destination))
        self.app.use_project_button.invoke()
        self.wait_for(lambda: self.app.project_ready, "typed destination folder")
        self.assertTrue(destination.is_dir())
        self.assertEqual(self.app.project_path, str(destination.resolve()))
        self.set_input("Create a README here")
        self.app.persist_current()
        saved = self.load_saved(self.app.current_chat["id"])
        self.assertEqual(saved["project_path"], str(destination.resolve()))

    def test_file_cannot_be_selected_as_output_folder(self):
        self.app.show_section("dev")
        self.app.project_directory.set(str(self.project / "main.py"))
        self.app.use_project_directory()
        self.assertFalse(self.app.project_ready)
        self.assertIn("Could not use this folder", self.app.status_label.cget("text"))

    def test_stream_is_visible_before_completion_and_final_message_appears_once(self):
        release = threading.Event()
        self.worker_releases.append(release)

        def stream(messages, model, on_chunk, on_metrics, **options):
            time.sleep(.06)
            on_chunk("Hello ")
            release.wait(3)
            on_chunk("world")
            on_metrics(dict(eval_count=10, eval_duration=2000000000))
            return "Hello world"

        self.request.side_effect = stream
        self.set_input("Stream a reply")
        self.app.send_prompt()
        self.wait_for(lambda: self.app.stream_card is not None, "partial streaming response")
        self.assertTrue(self.app.is_waiting)
        self.assertEqual(self.app.stream_text.get("1.0", "end-1c"), "Hello ")
        self.assertEqual(len(self.app.conversation), 1)
        release.set()
        self.wait_for(lambda: not self.app.is_waiting, "completed stream")
        self.assertIsNone(self.app.stream_card)
        self.assertEqual(self.app.conversation[-1]["content"], "Hello world")
        self.assertEqual(len(self.app.conversation), 2)
        self.assertIn("5.0 tokens/s", self.app.status_label.cget("text"))

    def test_slow_save_keeps_ui_responsive_and_pending_chat_can_be_reopened(self):
        entered, release = threading.Event(), threading.Event()
        self.worker_releases.append(release)
        original = self.app.chat_store.save

        def blocked(record):
            entered.set()
            release.wait(3)
            return original(record)

        with patch.object(self.app.chat_store, "save", side_effect=blocked):
            self.set_input("Draft waiting for disk")
            self.app.persist_current()
            identity = self.app.current_chat["id"]
            self.assertTrue(entered.wait(1))
            ticked = []
            self.root.after(1, lambda: ticked.append(True))
            self.wait_for(lambda: ticked, "UI timer during blocked disk write")
            self.app.new_chat_button.invoke()
            self.app.open_chat(identity)
            self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "Draft waiting for disk")
            release.set()
            self.app.saver.jobs.join()
        self.assertEqual(self.load_saved(identity)["draft"], "Draft waiting for disk")

    def test_failed_save_prevents_close_and_can_be_retried(self):
        self.set_input("Keep this draft after a disk failure")
        with patch.object(self.app.chat_store, "save", side_effect=OSError("disk full")):
            self.app.close()
            self.wait_for(lambda: not self.app.closing, "save error during close")
            self.assertFalse(self.app.closed)
            self.assertTrue(self.root.winfo_exists())
            self.assertIn("disk full", self.app.status_label.cget("text"))
            self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "Keep this draft after a disk failure")
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.assertEqual(self.load_saved(identity)["draft"], "Keep this draft after a disk failure")

    def test_failed_delete_restores_history_row(self):
        self.app.start_new_chat()
        identity = self.app.current_chat["id"]
        self.load_saved(identity)
        with patch.object(self.app.chat_store, "delete", side_effect=OSError("file locked")), patch(
                "workspace_ui.messagebox.askyesno", return_value=True):
            self.app.delete_chat()
            self.wait_for(lambda: not self.app.pending_saves, "delete failure")
        self.assertTrue(any(record["id"] == identity for record in self.app.history_records))
        self.assertIn("file locked", self.app.status_label.cget("text"))
        self.assertEqual(self.load_saved(identity)["id"], identity)

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
                    on_event({"type": "changed", "text": "Updated main.py", "path": "main.py", "diff": action["diff"]})
                    on_event({"type": "tool_result", "text": "Wrote main.py", "path": "main.py"})
                if options["cancel_event"].is_set():
                    raise RuntimeError("Developer agent cancelled.")
                return {"assistant_text": final_text}
            finally:
                finished.set()

        return run

    def test_new_chat_and_resume_preserve_messages_draft_and_context(self):
        self.assertEqual(self.list_saved(), [], "Startup should not create empty chat files")
        self.app.model_contexts["alpha:8b"] = 8192
        self.request.return_value = "Exact café answer\n```python\nprint('工具')\n```"
        self.set_input("Explain this project")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "chat response")
        original_id = self.app.current_chat["id"]
        messages = [{"role": "user", "content": "Explain this project"},
                    {"role": "assistant", "content": self.request.return_value}]
        self.assertEqual(self.load_saved(original_id)["messages"], messages)
        draft = "  Follow up later\nwith café and 工具  "
        self.set_input(draft)
        self.wait_for(lambda: self.load_saved(original_id)["draft"] == draft, "debounced draft save")
        original = self.load_saved(original_id)
        self.app.new_chat_button.invoke()
        new_id = self.app.current_chat["id"]
        self.assertNotEqual(new_id, original_id)
        self.assertEqual(self.app.current_chat["title"], "New chat")
        self.assertEqual(self.app.heading_label.cget("text"), "New chat")
        self.assertEqual(self.app.conversation, [])
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), "")
        self.assertEqual(len(self.list_saved()), 2)
        self.assertEqual(self.load_saved(new_id)["messages"], [])
        self.assertEqual(self.load_saved(new_id)["draft"], "")
        self.assertEqual(self.load_saved(original_id), original)
        self.assertEqual(self.app.history_buttons[0].cget("text"), "New chat")
        self.assertEqual(self.app.history_buttons[0].winfo_manager(), "pack")
        original_button = next(button for button in self.app.history_buttons if button.cget("text") == original["title"])
        original_button.invoke()
        self.assertEqual(self.app.current_chat["id"], original_id)
        self.assertEqual(self.app.conversation, messages)
        self.assertEqual(self.app.input_box.get("1.0", "end-1c"), draft)
        self.assertEqual(self.app.model_contexts["alpha:8b"], 8192)
        self.request.assert_called_once_with([messages[0]], "alpha:8b", context_length=8192,
                                             on_chunk=ANY, on_metrics=ANY)

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
        self.assertEqual(self.load_saved(identity)["messages"], [])

    def test_model_change_saves_draft_under_original_model(self):
        self.app.model_contexts["alpha:8b"] = 8192
        alpha_draft = "Unfinished alpha work\n  café"
        self.set_input(alpha_draft)
        self.app.model_name.set("beta:3b")
        self.app.select_model()
        self.wait_for(lambda: not self.app.busy(), "new model startup")
        records = self.list_saved()
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
        beta = next(record for record in self.list_saved() if record["model"] == "beta:3b")
        self.assertEqual(beta["draft"], beta_draft)
        self.assertEqual(self.load_saved(alpha["id"])["model"], "alpha:8b")

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
        saved = self.load_saved(identity)
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
        self.assertIsNone(self.load_saved(default["id"])["context_length"])

    def test_capabilities_navigation_preserves_draft_and_returns_to_chat(self):
        draft = "Keep these chat notes\n雪"
        self.set_input(draft)
        self.app.nav_buttons["capabilities"].invoke()
        identity = self.app.current_chat["id"]
        self.assertEqual(self.app.current_section, "capabilities")
        self.assertEqual(self.app.heading_label.cget("text"), "Capabilities")
        self.assertEqual(self.load_saved(identity)["draft"], draft)
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
        saved = self.load_saved(identity)
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
        saved = self.load_saved(identity)
        self.assertEqual(saved["messages"], [{"role": "user", "content": "Improve main.py"},
                                             {"role": "assistant", "content": "Updated main.py and checked the result."}])
        self.assertEqual(saved["activity"], self.app.dev_activity)
        self.assertEqual(saved["activity"][0]["type"], "request_start")
        self.assertEqual(saved["activity"][1], {"type": "tool_call", "text": "Read main.py\nExact café output  ",
                                                  "path": "main.py"})
        self.assertEqual(saved["activity"][-1]["type"], "tool_result")
        self.assertIn("+print('reviewed", self.app.changes_preview.get("1.0", "end-1c"))
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
        self.assertEqual(self.app.applied_changes, [])

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
            self.assertEqual(self.load_saved(identity)["agent_history"], native)
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
        saved = self.load_saved(identity)
        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant", "user", "assistant"])
        native_tool = next(item for item in saved["agent_history"] if item["role"] == "tool")
        self.assertEqual(native_tool, {"role": "tool", "tool_name": "read_file", "content": self.original_code})

    def test_an_error_is_explained_in_plain_words_with_its_fix(self):
        self.request.side_effect = RuntimeError("Could not connect to Ollama at http://localhost:11434. "
                                                "Start Ollama or run 'ollama serve', then try again.")
        self.set_input("Hello")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "failed request")
        texts = [widget.cget("text") for widget in descendants(self.app.message_frame)
                 if isinstance(widget, (tk.Label, tk.Button))]
        self.assertIn("Ollama is not answering", texts)
        self.assertIn("Details:", texts)
        self.assertTrue(self.app.status_label.cget("text").startswith("Ollama is not answering. Could not connect"))
        button = next(widget for widget in descendants(self.app.message_frame)
                      if isinstance(widget, tk.Button) and widget.cget("text") == "Check again")
        with patch.object(self.app, "manage_models") as manage:
            button.invoke()
        manage.assert_called_once_with("refresh")

    def test_a_model_that_is_not_loaded_says_why_the_first_answer_is_slow(self):
        self.app.loaded_models = set()
        self.set_input("Hello")
        self.app.send_button.invoke()
        self.assertIn("Loading alpha:8b into memory first", self.app.status_label.cget("text"))
        self.wait_for(lambda: not self.app.is_waiting, "answer")

    def test_after_the_ai_changes_files_the_status_says_how_to_undo(self):
        self.enter_project()

        def run(host, model, root, messages, on_event, approve, **options):
            target = (self.project / "main.py").resolve()
            options["checkpoint"].before_write(target, target.read_bytes())
            target.write_bytes(b"print('changed')\n")
            options["checkpoint"].after_write(target, b"print('changed')\n")
            return {"messages": messages + [{"role": "assistant", "content": "Done."}], "assistant_text": "Done."}

        with patch("workspace_ui.run_agent", side_effect=run):
            self.set_input("Change main.py")
            self.app.send_button.invoke()
            self.wait_for(lambda: not self.app.is_waiting, "agent finished")
        self.assertIn("The AI changed 1 file: Changes > Undo the AI's last task puts them back.",
                      self.app.status_label.cget("text"))
        self.assertEqual(str(self.app.undo_button.cget("state")), "normal")

    def test_a_chat_answers_from_a_folder_of_documents_and_lists_its_sources(self):
        documents_folder = self.folder / "manuals"
        documents_folder.mkdir()
        (documents_folder / "water.md").write_text("To purify water, boil it for one full minute.", encoding="utf-8")
        (documents_folder / ".env").write_text("SECRET=never", encoding="utf-8")
        self.assertEqual(self.app.documents_button.cget("text"), "Use documents...")
        self.assertTrue(self.app.documents_button.winfo_manager())
        with patch("workspace_ui.filedialog.askdirectory", return_value=str(documents_folder)):
            self.app.documents_button.invoke()
        self.wait_for(lambda: not self.app.documents_indexing, "documents read")
        self.assertIn("Documents ready: 1 file from manuals", self.app.status_label.cget("text"))
        self.assertIn("Documents: manuals", self.app.chat_subtitle.cget("text"))
        self.assertEqual(self.app.documents_button.cget("text"), "Documents: manuals")
        self.set_input("How long should I boil water to purify it?")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "answer with documents")
        sent = self.request.call_args.args[0]
        system = "\n".join(item["content"] for item in sent if item["role"] == "system")
        self.assertIn("boil it for one full minute", system)
        self.assertNotIn("SECRET", system)
        saved = self.load_saved(self.app.current_chat["id"])
        self.assertEqual(saved["library_path"], str(documents_folder.resolve()))
        self.assertEqual(saved["messages"][-1]["content"],
                         "A local answer.\n\n---\nSources from your documents:\n[1] water.md, from line 1")
        self.app.chat_menu.invoke("Stop using documents")
        self.assertNotIn("library_path", self.load_saved(self.app.current_chat["id"]))

    def test_back_up_now_and_restore_a_deleted_chat_from_the_window(self):
        self.set_input("A chat worth keeping")
        self.app.conversation.append(dict(role="user", content="A chat worth keeping"))
        self.app.persist_current()
        identity = self.app.current_chat["id"]
        self.load_saved(identity)
        self.app.chat_menu.invoke("Restore chats from a backup...")
        window = self.app.backup_window
        self.assertTrue(window.window.winfo_exists())
        window.backup_button.invoke()
        self.wait_for(lambda: not self.app.backup_running and window.items, "backup written")
        self.assertIn("Backed up", self.app.status_label.cget("text"))
        with patch("workspace_ui.messagebox.askyesno", return_value=True):
            self.app.delete_chat()
        self.wait_for(lambda: not (self.app.chat_store.directory / f"{identity}.json").exists(), "chat deleted")
        with patch("backup_ui.messagebox.askyesno", return_value=True):
            window.restore_button.invoke()
        self.wait_for(lambda: not self.app.backup_running, "restore finished")
        self.assertIn("1 chat added", self.app.status_label.cget("text"))
        self.assertTrue(any(record["id"] == identity for record in self.app.history_records))
        self.assertIn("Restore finished", window.status.cget("text"))

    def test_undo_puts_back_the_ais_last_task_after_confirmation(self):
        self.enter_project()
        target = (self.project / "main.py").resolve()
        original = target.read_bytes()
        point = self.app.checkpoints.begin(self.app.current_chat["id"], self.app.project_path)
        point.before_write(target, original)
        target.write_bytes(b"print('changed by the AI')\n")
        point.after_write(target, b"print('changed by the AI')\n")
        self.app.refresh_changes()
        self.app.update_controls()
        self.assertEqual(str(self.app.undo_button.cget("state")), "normal")
        with patch("workspace_ui.messagebox.askyesno", return_value=False) as asked:
            self.app.undo_button.invoke()
        self.assertIn(str(target), asked.call_args.args[1])
        self.assertEqual(target.read_bytes(), b"print('changed by the AI')\n")
        with patch("workspace_ui.messagebox.askyesno", return_value=True):
            self.app.undo_button.invoke()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.app.dev_activity[-1]["type"], "undone")
        self.assertIn("Undid the AI's changes to 1 file.", self.app.dev_activity[-1]["text"])
        self.assertEqual(str(self.app.undo_button.cget("state")), "disabled")

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
        saved = self.load_saved(self.app.current_chat["id"])
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
