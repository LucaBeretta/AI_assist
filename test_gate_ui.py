"""The two-step approval window, the thread bridge, and the connection log."""

import collections
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from AIassist import OllamaAssistantBase
import gate_ui
import network_gate

REQUEST = network_gate.Request(kind="Internet connection", target="api.github.com",
                               purpose="Check GitHub for a newer version", sends="Nothing personal",
                               source="update_checker.py line 24", scope="until this task finishes")


class GateWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = SimpleNamespace(
            root=self.root, COLORS=OllamaAssistantBase.COLORS,
            button=lambda parent, text, command, *colors, **options: tk.Button(parent, text=text, command=command))
        for name, value in (("_approver", None), ("_listeners", []), ("_recent", collections.deque(maxlen=300))):
            patcher = patch.object(network_gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pump(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                self.fail("timed out")
            self.root.update()
            time.sleep(0.02)

    def test_allowing_takes_two_steps_and_a_pause(self):
        answers = []
        dialog = gate_ui.ApprovalDialog(self.app, REQUEST, answers.append, confirm_seconds=1)
        texts = [label.cget("text") for frame in dialog.window.winfo_children()
                 for label in frame.winfo_children() if isinstance(label, tk.Label)]
        self.assertIn("api.github.com", texts)
        self.assertIn("Check GitHub for a newer version", texts)
        dialog.continue_or_allow()
        self.assertEqual(str(dialog.allow_button.cget("state")), "disabled")
        dialog.continue_or_allow()
        self.assertEqual(answers, [])
        self.pump(lambda: dialog.unlocked)
        self.assertEqual(dialog.allow_button.cget("text"), "Allow")
        dialog.allow_button.invoke()
        self.assertEqual(answers, [True])
        self.assertFalse(dialog.window.winfo_exists())

    def test_closing_escape_and_enter_block(self):
        answers = []
        dialog = gate_ui.ApprovalDialog(self.app, REQUEST, answers.append)
        for sequence in ("<Escape>", "<Return>"):
            self.assertTrue(dialog.window.bind(sequence))
        dialog.continue_or_allow()
        dialog.block()
        dialog.block()
        self.assertEqual(answers, [False])

    def test_undeclared_purpose_is_called_out(self):
        request = network_gate.Request("Internet connection", "1.1.1.1 port 80", "", "", "somewhere", "for 60 seconds")
        dialog = gate_ui.ApprovalDialog(self.app, request, lambda answer: None)
        values = {row[0]: row for row in dialog.rows()}
        self.assertTrue(values["Why"][2])
        self.assertIn("Not declared", values["Why"][1])
        dialog.block()

    def test_worker_threads_wait_for_a_person(self):
        bridge = gate_ui.GateUI(self.app)
        self.addCleanup(bridge.close)
        results = []
        worker = threading.Thread(target=lambda: results.append(bridge.approve(REQUEST)))
        worker.start()
        self.pump(lambda: bridge.dialog is not None)
        self.assertFalse(bridge.approve(REQUEST), "a second review on the window thread is refused")
        bridge.dialog.block()
        worker.join(5)
        self.assertEqual(results, [False])

    def test_closing_the_app_blocks_waiting_requests(self):
        bridge = gate_ui.GateUI(self.app)
        results = []
        workers = [threading.Thread(target=lambda: results.append(bridge.approve(REQUEST))) for _ in range(2)]
        for worker in workers:
            worker.start()
        self.pump(lambda: bridge.dialog is not None)
        bridge.close()
        for worker in workers:
            worker.join(5)
        self.assertEqual(results, [False, False])
        self.assertIsNone(network_gate._approver)
        self.assertFalse(bridge.approve(REQUEST))

    def test_connection_log_lists_past_and_new_requests(self):
        entry = {"time": "2026-11-18 16:40:02", "decision": "blocked", "kind": "Internet connection",
                 "target": "example.com", "purpose": "", "sends": "", "source": "x.py line 1", "scope": ""}
        network_gate._recent.append(entry)
        log = gate_ui.ConnectionLog(self.app)
        log.open()
        self.assertIn("BLOCKED", log.text.get("1.0", "end"))
        for listener in list(network_gate._listeners):
            listener(dict(entry, decision="allowed", target="api.github.com"))
        self.pump(lambda: "api.github.com" in log.text.get("1.0", "end"))
        log.close()
        self.root.update()
        self.assertEqual(network_gate._listeners, [])


if __name__ == "__main__":
    unittest.main()
