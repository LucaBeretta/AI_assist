"""The Tk app on the colibri engine: switching, chatting, reopening, and the
colibri tab of the model dialog. A fake coli launcher and gateway stand in for
colibri; Ollama is mocked as in the other workspace tests."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from AIassist import AIAssistantGUI
import colibri_client
from colibri_panel import card_text, comparison_text
from test_colibri_client import CATALOG, FakeGateway, completion, make_fake_colibri


def ollama_metadata(host, model):
    return {"name": model, "capabilities": ["completion"], "context_length": 8192, "family": "test",
            "parameter_size": "8B", "quantization": "Q4"}


class PanelTextTests(unittest.TestCase):
    def test_cards_and_comparison(self):
        coder = dict(CATALOG[0], tools=True)
        glm = dict(CATALOG[1], tools=True)
        engine, sizes, fit = card_text(coder)
        self.assertIn("In RAM", engine)
        self.assertIn("Coding agent tools: yes", sizes)
        self.assertTrue(fit.startswith("Runs, with a small expert cache"))
        engine, _, fit = card_text(glm)
        self.assertIn("RAM + SSD", engine)
        self.assertIn("Does not fit", fit)
        big = dict(glm, fits=True, status="good")
        text = comparison_text([{"name": "qwen3:8b"}], [coder, big])
        self.assertIn("Ollama the suggested fast pick is qwen3:8b", text)
        self.assertIn("Qwen3-Coder-30B-A3B is recommended", text)
        self.assertIn("up to GLM-5.2 (429.3 GB), slower", text)
        self.assertEqual(comparison_text([], [glm]), "No colibri model fits this PC's RAM and disk.")


class ColibriWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.gateway = FakeGateway([completion("Risposta da colibri."), completion("Seconda risposta.")])
        self.addCleanup(self.gateway.close)
        self.colibri = make_fake_colibri(self.folder / "colibri", self.gateway.url,
                                         model="qwen3-coder-30b", ready=True, server="stopped")
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        self.mocks.enter_context(patch.dict(os.environ, {
            "AI_ASSIST_DATA_DIR": str(self.folder / "history"), "OLLAMA_MODEL": "alpha:8b",
            "COLIBRI_DIR": str(self.colibri), "AI_ASSIST_ENGINE": "",
        }))
        os.environ.pop("COLIBRI_HOST", None)
        self.mocks.enter_context(patch("AIassist.ensure_ollama"))
        self.mocks.enter_context(patch("AIassist.list_ollama_models", return_value=["alpha:8b"]))
        self.mocks.enter_context(patch("AIassist.running_models", return_value={"alpha:8b"}))
        self.mocks.enter_context(patch("AIassist.set_model_running"))
        self.ollama_request = self.mocks.enter_context(patch("AIassist.request_ollama", return_value="Ollama answer."))
        self.mocks.enter_context(patch("capabilities_view.fetch_model_capabilities", side_effect=ollama_metadata))
        self.mocks.enter_context(patch("workspace_ui.fetch_model_capabilities", side_effect=ollama_metadata))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = AIAssistantGUI(self.root)
        self.addCleanup(self.close_app)
        self.wait_for(lambda: not self.app.startup_pending and not self.app.busy(), "Ollama startup")
        self.assertEqual(self.app.backend_name, "ollama")

    def close_app(self):
        for scheduled in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", scheduled)
        if not self.app.closed:
            self.app.close()
        self.assertEqual([str(error[1]) for error in self.callback_errors], [])

    def wait_for(self, predicate, description, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.02)
        self.fail(f"Timed out waiting for {description}; status={self.app.status_label.cget('text')!r}")

    def send(self, text):
        self.app.input_box.delete("1.0", "end")
        self.app.input_box.insert("1.0", text)
        self.app.send_prompt()

    def state(self):
        return json.loads((self.colibri / "c" / "state.json").read_text())

    def switch_to_colibri(self):
        self.app.engine_choice.set("colibri")
        self.app.choose_engine()
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen3-coder-30b", "colibri startup")

    def test_switch_chat_and_reopen_on_the_right_engine(self):
        self.send("hello ollama")
        self.wait_for(lambda: not self.app.is_waiting, "Ollama reply")
        ollama_chat = self.app.current_chat["id"]

        self.switch_to_colibri()
        self.assertEqual(self.state()["server"], "ready")  # startup started the configured model
        self.assertEqual(self.app.connection_label.cget("text"), "colibri connected")
        self.assertIsNone(self.app.current_chat)
        self.assertEqual(colibri_client.load_setting("engine"), "colibri")

        self.send("ciao colibri")
        self.wait_for(lambda: not self.app.is_waiting, "colibri reply")
        self.assertEqual(self.app.conversation[-1], {"role": "assistant", "content": "Risposta da colibri."})
        self.assertEqual(self.gateway.requests[-1]["messages"], [{"role": "user", "content": "ciao colibri"}])
        colibri_chat = self.app.current_chat["id"]
        saved = self.app.chat_store.load(colibri_chat)
        self.assertEqual((saved["backend"], saved["model"]), ("colibri", "qwen3-coder-30b"))
        self.assertEqual(self.app.chat_store.load(ollama_chat)["backend"], "ollama")
        self.ollama_request.assert_called_once()

        # Back to Ollama, then reopen the colibri chat: the engine follows the chat.
        self.app.engine_choice.set("Ollama")
        self.app.choose_engine()
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "alpha:8b", "Ollama again")
        self.app.open_chat(colibri_chat)
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen3-coder-30b", "chat reopen")
        self.assertEqual(self.app.backend_name, "colibri")
        self.assertEqual(self.app.engine_choice.get(), "colibri")
        self.send("ancora")
        self.wait_for(lambda: not self.app.is_waiting, "second colibri reply")
        self.assertEqual(len(self.gateway.requests[-1]["messages"]), 3)
        self.assertEqual(self.app.conversation[-1]["content"], "Seconda risposta.")

    def test_developer_agent_runs_on_colibri(self):
        project = self.folder / "project"
        project.mkdir()
        (project / "main.py").write_text("print('ciao')\n", encoding="utf-8")
        self.gateway.replies[:] = [
            completion("", [{"id": "a", "type": "function",
                             "function": {"name": "read_file", "arguments": "{\"path\": \"main.py\"}"}}]),
            completion("main.py stampa ciao."),
        ]
        self.switch_to_colibri()
        self.app.nav_buttons["dev"].invoke()
        with patch("workspace_ui.filedialog.askdirectory", return_value=str(project)):
            self.app.choose_project_button.invoke()
        self.wait_for(lambda: not self.app.project_loading and bool(self.app.project_files), "project scan")
        self.send("Cosa fa main.py?")
        self.wait_for(lambda: not self.app.is_waiting, "agent on colibri", timeout=15)
        self.assertEqual(self.app.conversation[-1]["content"], "main.py stampa ciao.")
        self.assertEqual(len(self.gateway.requests), 2)
        self.assertEqual(self.gateway.requests[0]["model"], "qwen3-coder-30b")
        # The gateway's advertised 16K context bounds the request, not Ollama's 4K default.
        self.assertEqual(self.gateway.requests[0]["max_tokens"], 1024)
        saved = self.app.chat_store.load(self.app.current_chat["id"])
        self.assertEqual((saved["backend"], saved["mode"]), ("colibri", "dev"))
        self.assertEqual(saved["agent_history"][-2]["tool_name"], "read_file")

    def test_stop_all_and_capabilities_on_colibri(self):
        self.switch_to_colibri()
        self.app.show_section("capabilities")
        view = self.app.capabilities_view
        self.wait_for(lambda: view._metadata is not None, "colibri metadata")
        self.assertIn("tools", view._metadata["capabilities"])
        self.assertEqual(view.app_context_label.cget("text"), "Set by colibri setup (--ctx)")
        self.assertIn("received from colibri", view.status_label.cget("text"))
        self.app.manage_models("stop_all")
        self.wait_for(lambda: not self.app.busy(), "colibri stop")
        self.assertEqual(self.state()["server"], "stopped")
        self.assertIn("Stopped", self.app.model_help.cget("text"))

    def test_colibri_tab_lists_and_installs(self):
        with patch("model_downloads.fetch_popular_models", return_value=[]), \
                patch("hardware_panel.detect_hardware", side_effect=RuntimeError("skip")):
            self.app.open_downloads()
            dialog = self.app.download_dialog
            dialog.window.withdraw()
            panel = dialog.colibri
            self.wait_for(lambda: not panel.loading and panel.card_buttons, "colibri cards")
            self.assertIn(str(self.colibri.resolve()), panel.summary.cget("text"))
            labels = {row["id"]: button.cget("text") for row, button in panel.card_buttons}
            self.assertEqual(labels, {"qwen3-coder-30b": "Start & use", "glm-5.2": "Doesn't fit"})
            self.assertIn("Qwen3-Coder-30B-A3B is recommended", panel.comparison.cget("text"))
            # The Ollama download buttons do nothing on the colibri tab.
            dialog.notebook.select(panel.frame)
            self.root.update()
            self.assertIsNone(dialog.selected_variant())
            self.assertFalse(dialog.ollama_actions.winfo_manager())
            dialog.notebook.select(dialog.advisor.frame)
            self.root.update()
            self.assertEqual(dialog.ollama_actions.winfo_manager(), "pack")
            dialog.notebook.select(panel.frame)
            self.root.update()

            other = dict(CATALOG[1], id="qwen36-35b", name="Qwen3.6-35B", fits=True, status="good",
                         family="qwen36", recommended=False)
            state = self.state()
            state["catalog"] = [CATALOG[0], other]
            state["step_seconds"] = 0.1
            (self.colibri / "c" / "state.json").write_text(json.dumps(state))
            panel.refresh()
            self.wait_for(lambda: not panel.loading and len(panel.card_buttons) == 2
                          and any(row["id"] == "qwen36-35b" for row, _ in panel.card_buttons), "new catalog")
            with patch("colibri_panel.messagebox.askyesno", return_value=True):
                panel.choose(next(row for row, _ in panel.card_buttons if row["id"] == "qwen36-35b"))
            self.wait_for(lambda: panel.installing is None and not panel.loading, "fake install", timeout=20)
            self.assertEqual(self.state()["model"], "qwen36-35b")
            labels = {row["id"]: button.cget("text") for row, button in panel.card_buttons}
            self.assertEqual(labels["qwen36-35b"], "Use in chat")
            self.gateway.close()
            replacement = FakeGateway([], model="qwen36-35b")
            self.addCleanup(replacement.close)
            state = self.state()
            state["url"] = replacement.url
            (self.colibri / "c" / "state.json").write_text(json.dumps(state))
            panel.use()
            self.assertTrue(dialog.closed)
            self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen36-35b", "use installed model")
            self.assertEqual(self.app.backend_name, "colibri")

    def test_missing_colibri_explains_how_to_install(self):
        with patch.dict(os.environ, {"COLIBRI_DIR": ""}), patch("colibri_client.find_directory", return_value=None):
            self.app.colibri_dir = None
            self.app.engine_choice.set("colibri")
            self.app.choose_engine()
            self.wait_for(lambda: not self.app.busy(), "missing colibri")
            self.assertEqual(self.app.model_name.get(), "")
            self.assertIn("colibri was not found", self.all_text())
            self.assertEqual(self.app.connection_label.cget("text"), "Check colibri")

    def all_text(self):
        texts = []
        stack = [self.app.message_frame]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                texts.append(str(widget.cget("text")))
            except tk.TclError:
                if isinstance(widget, tk.Text):
                    texts.append(widget.get("1.0", "end"))
        return " ".join(texts)


if __name__ == "__main__":
    unittest.main()
