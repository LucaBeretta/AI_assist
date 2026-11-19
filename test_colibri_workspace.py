"""The Tk app on colibri as agreed for the colibri branch: the Engine picker,
colibri started and stopped through its own launcher, chats that reopen on
their engine, the colibri tab (recommendations, install, context), the agent,
Stop, and backups. A fake coli launcher and gateway stand in for colibri."""

from contextlib import ExitStack
import gc
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from AIassist import AIAssistantGUI
from colibri_panel import card_text, comparison_text, download_text
from colibri_launcher import transfer_metrics
from test_colibri_launcher import CATALOG, FakeGateway, completion, join_workers, make_fake_colibri


def ollama_metadata(host, model):
    return {"name": model, "capabilities": ["completion", "tools"], "context_length": 8192, "family": "test",
            "parameter_size": "8B", "quantization": "Q4"}


class PanelTextTests(unittest.TestCase):
    def test_download_speed_and_remaining_time(self):
        self.assertIn("50.0 MB/s  |  About 2m 5s remaining",
                      download_text("model", 1e9, 2e9, 50e6, 125))
        self.assertIn("About 1h 1m remaining", download_text("model", 0, 2e9, eta=3661))
        self.assertEqual(download_text("model", None, None), "Downloading model: ? / ?")
        self.assertNotIn("MB/s", download_text("model", 0, 1, rate=0))
        for invalid in (True, -1, float("nan"), float("inf"), "50"):
            self.assertEqual(transfer_metrics({"install": {"rate": invalid, "eta_seconds": invalid}}),
                             (None, None))
        self.assertEqual(transfer_metrics({}), (None, None))
        self.assertEqual(transfer_metrics({"install": {"rate": 10, "eta_seconds": 0}}), (10, 0))

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
        text = comparison_text([{"name": "qwen3:8b"}], [coder, dict(glm, fits=True, status="good")])
        self.assertIn("Ollama the suggested fast pick is qwen3:8b", text)
        self.assertIn("up to GLM-5.2 (429.3 GB), slower", text)
        self.assertEqual(comparison_text([], [glm]), "No colibri model fits this PC's RAM and disk.")


class ColibriWorkspaceTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        self.threads_before = set(threading.enumerate())
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
            "COLIBRI_DIR": str(self.colibri), "COLIBRI_URL": "", "COLI_API_KEY": ""}))
        self.mocks.enter_context(patch("update_ui.UpdateChecker.check", lambda self: None))
        self.mocks.enter_context(patch("ollama_engine.ensure_ollama"))
        self.mocks.enter_context(patch("ollama_engine.list_ollama_models", return_value=["alpha:8b"]))
        self.mocks.enter_context(patch("ollama_engine.running_models", return_value={"alpha:8b"}))
        self.mocks.enter_context(patch("ollama_engine.set_model_running"))
        self.ollama_request = self.mocks.enter_context(patch("ollama_engine.request_ollama", return_value="Ollama answer."))
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
        self.assertEqual(self.app.backend, "Ollama")

    def close_app(self):
        for scheduled in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", scheduled)
        if not self.app.closed:
            self.app.close()
            deadline = time.monotonic() + 5
            while not self.app.closed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.app.saver.thread.join(timeout=2)
        join_workers(self.threads_before)
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
        self.app.send_button.invoke()

    def state(self):
        return json.loads((self.colibri / "c" / "state.json").read_text())

    def write_state(self, **changes):
        state = self.state()
        state.update(changes)
        (self.colibri / "c" / "state.json").write_text(json.dumps(state))

    def switch_to_colibri(self):
        self.app.engine_choice.set("Colibri")
        self.app.choose_engine()
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen3-coder-30b", "colibri startup")

    def open_project(self):
        project = self.folder / "project"
        project.mkdir(exist_ok=True)
        (project / "main.py").write_text("print('ciao')\n", encoding="utf-8")
        self.app.nav_buttons["dev"].invoke()
        self.app.project_directory.set(str(project))
        self.app.use_project_button.invoke()
        self.wait_for(lambda: not self.app.project_loading and bool(self.app.project_files), "project scan")
        return project

    def test_engine_switch_chat_and_reopen_on_the_right_engine(self):
        self.send("hello ollama")
        self.wait_for(lambda: not self.app.is_waiting and not self.app.pending_saves, "Ollama reply")
        ollama_chat = self.app.current_chat["id"]

        self.switch_to_colibri()
        self.assertEqual(self.state()["server"], "ready")  # AI Assist started colibri with its launcher
        self.assertEqual(self.app.connection_label.cget("text"), "Colibri connected")
        self.assertIn("Served by colibri", self.app.model_help.cget("text"))
        self.assertEqual(self.app.settings_menu.entrycget(1, "label"), "AI backend: Colibri...")
        self.assertEqual(str(self.app.download_models_button.cget("state")), "normal")
        self.assertEqual(str(self.app.run_all_button.cget("state")), "normal")

        self.send("ciao colibri")
        self.wait_for(lambda: not self.app.is_waiting and not self.app.pending_saves, "colibri reply")
        self.assertEqual(self.app.conversation[-1], {"role": "assistant", "content": "Risposta da colibri."})
        colibri_chat = self.app.current_chat["id"]
        saved = self.app.chat_store.load(colibri_chat)
        self.assertEqual((saved["backend"], saved["model"]), ("Colibri", "qwen3-coder-30b"))
        self.assertEqual(self.app.chat_store.load(ollama_chat)["backend"], "Ollama")

        self.app.engine_choice.set("Ollama")
        self.app.choose_engine()
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "alpha:8b", "Ollama again")
        self.app.open_chat(colibri_chat)
        self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen3-coder-30b", "chat reopen")
        self.assertEqual((self.app.backend, self.app.engine_choice.get()), ("Colibri", "Colibri"))
        self.send("ancora")
        self.wait_for(lambda: not self.app.is_waiting, "second colibri reply")
        self.assertEqual(self.app.conversation[-1]["content"], "Seconda risposta.")
        self.assertEqual(len(self.gateway.requests[-1]["messages"]), 3)

    def test_stop_all_stops_colibri_and_speed_is_shown(self):
        self.write_state(tps=3.4)
        self.switch_to_colibri()
        self.assertIn("~3.4 tok/s on the last answer", self.app.model_help.cget("text"))
        self.app.manage_models("stop_all")
        self.wait_for(lambda: not self.app.busy(), "colibri stop")
        self.assertEqual(self.state()["server"], "stopped")
        self.assertIn("colibri is stopped", self.app.model_help.cget("text"))
        # Sending a message starts it again.
        self.app.model_name.set("qwen3-coder-30b")
        self.app.active_model = "qwen3-coder-30b"
        self.app.update_controls()
        self.send("sei acceso?")
        self.wait_for(lambda: not self.app.is_waiting, "reply after restart")
        self.assertEqual(self.state()["server"], "ready")
        self.assertEqual(self.app.conversation[-1]["content"], "Risposta da colibri.")

    def test_colibri_tab_lists_installs_and_sets_context(self):
        self.switch_to_colibri()
        with patch("model_downloads.fetch_popular_models", return_value=[]), \
                patch("hardware_panel.detect_hardware", side_effect=RuntimeError("skip")):
            self.app.download_models_button.invoke()
            dialog = self.app.download_dialog
            dialog.window.withdraw()
            panel = dialog.colibri
            self.root.update()
            self.assertEqual(dialog.notebook.select(), str(panel.frame))  # opens on the colibri tab
            self.wait_for(lambda: not panel.loading and panel.card_buttons, "colibri cards")
            labels = {row["id"]: button.cget("text") for row, button in panel.card_buttons}
            self.assertEqual(labels, {"qwen3-coder-30b": "Use in chat", "glm-5.2": "Doesn't fit"})
            self.assertIn("Qwen3-Coder-30B-A3B is recommended", panel.comparison.cget("text"))
            self.assertFalse(dialog.ollama_actions.winfo_manager())

            panel.context_choice.set("32,768 tokens")
            self.wait_for(lambda: str(panel.context_button.cget("state")) == "normal", "apply enabled")
            panel.context_button.invoke()
            self.wait_for(lambda: panel.installing is None and not panel.loading, "context change", timeout=15)
            self.assertIn("restarted with context 32,768 tokens", panel.message.cget("text"))
            self.assertIn("setup --ctx 32768 --yes --json --no-browser",
                          (self.colibri / "c" / "calls.log").read_text().splitlines())

            other = dict(CATALOG[1], id="qwen36-35b", name="Qwen3.6-35B", fits=True, status="good",
                         family="qwen36", recommended=False)
            self.write_state(catalog=[CATALOG[0], other], step_seconds=0.1)
            panel.refresh()
            self.wait_for(lambda: not panel.loading and any(row["id"] == "qwen36-35b" for row, _ in panel.card_buttons),
                          "new catalog")
            panel.context_choice.set("Automatic")
            with patch("colibri_panel.messagebox.askyesno", return_value=True):
                panel.choose(next(row for row, _ in panel.card_buttons if row["id"] == "qwen36-35b"))
            self.wait_for(lambda: panel.installing is None and not panel.loading, "fake install", timeout=20)
            self.assertEqual(self.state()["model"], "qwen36-35b")
            self.assertEqual(json.loads((self.colibri / "c" / "setup.json").read_text())["ctx"], 0)
            self.assertIn("Qwen3.6-35B is set up and starting", panel.message.cget("text"))
            replacement = FakeGateway([], model="qwen36-35b")
            self.addCleanup(replacement.close)
            self.write_state(url=replacement.url)
            self.app.colibri_url = "http://127.0.0.1:8000/v1"  # follow colibri's reported port again
            panel.use()
            self.assertTrue(dialog.closed)
            self.wait_for(lambda: not self.app.busy() and self.app.model_name.get() == "qwen36-35b", "use installed model")

    def test_agent_on_colibri_asks_once_when_slow_and_reads_large_files_in_parts(self):
        self.write_state(tps=2.5)
        self.switch_to_colibri()
        project = self.open_project()
        (project / "big.html").write_text("".join(f"<p>riga {index}</p>\n" for index in range(6000)), encoding="utf-8")
        self.app.agent_file_limit = 128000
        self.gateway.replies[:] = [
            completion("", [{"id": "a", "type": "function",
                             "function": {"name": "read_file", "arguments": "{\"path\": \"big.html\"}"}}]),
            completion("big.html è un elenco di paragrafi."),
            completion("Ancora."),
        ]
        with patch("backend_ui.messagebox.askokcancel", return_value=False) as ask:
            self.send("Analizza big.html")
        self.assertIn("about 2.5 tokens per second", ask.call_args.args[1])
        self.assertFalse(self.app.is_waiting)
        self.assertIn("Agent not started", self.app.status_label.cget("text"))
        with patch("backend_ui.messagebox.askokcancel", return_value=True) as ask:
            self.send("Analizza big.html")
            self.wait_for(lambda: not self.app.is_waiting, "agent reply", timeout=20)
            self.send("E poi?")
            self.wait_for(lambda: not self.app.is_waiting, "second agent reply", timeout=20)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(self.app.conversation[-3]["content"], "big.html è un elenco di paragrafi.")
        tool = next(message for message in self.gateway.requests[1]["messages"] if message["role"] == "tool")
        self.assertIn("Part of a large file", tool["content"])
        self.assertIn("Continue with start_line=", tool["content"])
        activity = " ".join(item["text"] for item in self.app.dev_activity)
        self.assertIn("reads it in parts", activity)

    def test_agent_refuses_a_colibri_family_without_tools(self):
        self.switch_to_colibri()
        self.open_project()
        self.app.colibri_report = dict(self.app.colibri_report, family="inkling")
        self.send("Analizza")
        self.assertFalse(self.app.is_waiting)
        self.assertIn("does not support tool calls", self.app.status_label.cget("text"))

    def test_stop_keeps_the_partial_colibri_answer(self):
        self.gateway.stream_pause = 0.6
        self.gateway.replies[:] = [completion("Una risposta lunga che arriva lentamente.")]
        self.switch_to_colibri()
        self.send("ciao")
        self.wait_for(lambda: self.app.stream_card is not None, "first words", timeout=10)
        self.assertEqual(self.app.send_button.cget("text"), "Stop  ■")
        self.app.send_button.invoke()
        self.wait_for(lambda: not self.app.is_waiting, "stopped")
        self.assertIn("Stopped", self.app.status_label.cget("text"))
        self.assertEqual(self.app.conversation[-1]["role"], "assistant")
        self.assertTrue("Una risposta lunga".startswith(self.app.conversation[-1]["content"][:5]))

    def test_backup_from_chat_options(self):
        self.send("hello")
        self.wait_for(lambda: not self.app.is_waiting, "reply")
        target = self.folder / "backup.zip"
        with patch("workspace_ui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.chat_menu.invoke("Back up all chats...")
        self.assertTrue(target.is_file())
        self.assertIn("Backed up 1 chat to backup.zip", self.app.status_label.cget("text"))
        self.app.new_chat()
        self.root.update()
        self.assertEqual(str(self.app.chat_actions.cget("state")), "normal")
        self.assertEqual(self.app.chat_menu.entrycget("Delete chat", "state"), "disabled")

    def test_missing_colibri_folder_keeps_the_server_only_mode(self):
        with patch.dict(os.environ, {"COLIBRI_DIR": ""}), patch("colibri_launcher.find_directory", return_value=None):
            self.app.colibri_dir = None
            # Use the fixture's closed port, even when a real Colibri server is
            # running on the normal port on this development machine.
            self.app.colibri_url = self.gateway.url + "/v1"
            self.gateway.close()
            self.app.engine_choice.set("Colibri")
            self.app.choose_engine()
            self.wait_for(lambda: not self.app.busy(), "missing colibri")
            self.assertEqual(self.app.model_name.get(), "")
            self.assertEqual(self.app.connection_label.cget("text"), "Colibri unavailable")
            self.assertEqual(str(self.app.run_all_button.cget("state")), "disabled")
            with patch("model_downloads.fetch_popular_models", return_value=[]), \
                    patch("hardware_panel.detect_hardware", side_effect=RuntimeError("skip")):
                self.app.download_models_button.invoke()
                panel = self.app.download_dialog.colibri
                self.app.download_dialog.window.withdraw()
                self.wait_for(lambda: not panel.loading, "panel")
                self.assertIn("colibri is not installed", panel.summary.cget("text"))


if __name__ == "__main__":
    unittest.main()
