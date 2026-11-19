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


class TkCase(unittest.TestCase):
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
        for name, value in (("_approver", None), ("_listeners", []), ("_recent", collections.deque(maxlen=300)),
                            ("_lock_check", None)):
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


class GateWindowTests(TkCase):
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


class FakeLock:
    def __init__(self, summary):
        self.summary, self.busy, self.runs, self.refreshed = summary, None, [], 0

    def refresh(self):
        self.refreshed += 1

    def run(self, action, minutes=None):
        self.runs.append((action, minutes))
        self.busy = action

    def poll(self):
        return []

    def blocks(self, kind):
        return None


def lock_summary(state, has_rules=True, programs=(("C:\\Ollama\\ollama.exe", True),)):
    return {"state": state, "detail": f"{state} detail", "has_rules": has_rules, "can_turn_on": True,
            "lift_until": None, "ollama_locked": state == "on",
            "programs": [{"path": path, "covered": active, "active": active} for path, active in programs]}


class FirewallLockWindowTests(TkCase):
    def open(self, summary):
        fake = FakeLock(summary)
        ui = gate_ui.FirewallLockUI(self.app, fake)
        self.addCleanup(ui.close)
        ui.open()
        return ui, fake

    def states(self, ui):
        buttons = [ui.on_button, *ui.lift_buttons, ui.relock_button, ui.off_button]
        return [str(button.cget("state")) for button in buttons]

    def test_off_offers_turning_on_and_waits_for_approval(self):
        ui, fake = self.open(lock_summary("off", has_rules=False, programs=(("C:\\Ollama\\ollama.exe", False),)))
        self.assertEqual(fake.refreshed, 1)
        self.assertEqual(network_gate._lock_check, fake.blocks)
        self.assertEqual(self.states(ui), ["normal", "disabled", "disabled", "disabled", "disabled", "disabled"])
        self.assertIn("open    C:\\Ollama\\ollama.exe", ui.programs_text.get("1.0", "end"))
        ui.on_button.invoke()
        self.assertEqual(fake.runs, [("on", None)])
        self.assertIn("administrator approval", ui.state_label.cget("text"))
        self.assertEqual(set(self.states(ui)), {"disabled"})

    def test_on_offers_lifting_and_turning_off(self):
        ui, fake = self.open(lock_summary("on"))
        self.assertEqual(ui.state_label.cget("text"), "On")
        self.assertEqual(self.states(ui), ["disabled", "normal", "normal", "normal", "disabled", "normal"])
        self.assertIn("locked  C:\\Ollama\\ollama.exe", ui.programs_text.get("1.0", "end"))
        ui.lift_buttons[1].invoke()
        self.assertEqual(fake.runs, [("lift", 15)])

    def test_lifted_offers_locking_again(self):
        ui, fake = self.open(lock_summary("lifted"))
        self.assertEqual(self.states(ui), ["disabled", "disabled", "disabled", "disabled", "normal", "normal"])
        ui.relock_button.invoke()
        self.assertEqual(fake.runs, [("relock", None)])

    def test_a_firewall_that_is_off_offers_nothing_but_removal(self):
        ui, _ = self.open(lock_summary("firewall off"))
        self.assertEqual(self.states(ui), ["disabled", "disabled", "disabled", "disabled", "disabled", "normal"])
        self.assertEqual(ui.state_label.cget("text"), "Windows Firewall is off")


if __name__ == "__main__":
    unittest.main()
