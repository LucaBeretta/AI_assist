"""The rules for approving the developer agent's edits and commands (app_core.approvals)."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_core import approvals
from coding_agent import run_agent


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def command(**extra):
    return approvals.new_decision(dict(kind="run_command", command="pytest -q", cwd="/p", description="Run the tests", **extra))


class DecisionRuleTests(unittest.TestCase):
    def test_a_new_decision_is_declined_and_unanswered(self):
        decision = approvals.new_decision({"kind": "write_file", "path": "a.py"})
        self.assertEqual((decision["approved"], decision["event"].is_set()), (False, False))

    def test_an_edit_is_answered_once(self):
        decision = approvals.new_decision({"kind": "write_file", "path": "a.py"})
        self.assertTrue(approvals.resolve(decision, True, destination="/other/a.py"))
        self.assertEqual((decision["approved"], decision["destination"]), (True, "/other/a.py"))
        self.assertFalse(approvals.resolve(decision, False))
        self.assertTrue(decision["approved"])

    def test_a_command_needs_its_second_check_and_the_full_wait(self):
        clock = FakeClock()
        decision = command()
        with patch("app_core.approvals.network_gate.note_outside") as noted:
            self.assertFalse(approvals.resolve(decision, True, clock=clock))  # no second check yet
            self.assertIsNone(approvals.seconds_left(decision, clock))
            approvals.begin_second_check(decision, clock=clock)
            self.assertEqual(approvals.seconds_left(decision, clock), 3.0)
            clock.now += 2.9
            self.assertFalse(approvals.resolve(decision, True, clock=clock))  # too early
            self.assertFalse(decision["event"].is_set())
            clock.now += 0.1
            self.assertTrue(approvals.resolve(decision, True, clock=clock))
        self.assertTrue(decision["approved"])
        noted.assert_called_once_with("Developer agent command", "pytest -q", "Run the tests")

    def test_starting_the_second_check_again_does_not_restart_or_shorten_it(self):
        clock = FakeClock()
        decision = command()
        until = approvals.begin_second_check(decision, clock=clock)
        clock.now += 2
        self.assertEqual(approvals.begin_second_check(decision, seconds=0, clock=clock), until)

    def test_a_command_can_always_be_declined_and_is_never_logged_then(self):
        decision = command()
        with patch("app_core.approvals.network_gate.note_outside") as noted:
            self.assertTrue(approvals.resolve(decision, False))
        noted.assert_not_called()
        self.assertFalse(decision["approved"])

    def test_only_commands_have_a_second_check(self):
        with self.assertRaises(ValueError):
            approvals.begin_second_check(approvals.new_decision({"kind": "write_file"}))


class WaitingTests(unittest.TestCase):
    def answer_later(self, decide):
        def notify(decision):
            threading.Timer(0.05, decide, args=(decision,)).start()
        return notify

    def test_the_answer_reaches_the_agent(self):
        cancel = threading.Event()
        approve = approvals.approver(self.answer_later(lambda d: approvals.resolve(d, True)), cancel)
        self.assertIs(approve({"kind": "write_file"}), True)
        choose = approvals.approver(self.answer_later(lambda d: approvals.resolve(d, True, destination="/x/b.py")), cancel)
        self.assertEqual(choose({"kind": "write_file"}), {"approved": True, "destination": "/x/b.py"})
        decline = approvals.approver(self.answer_later(lambda d: approvals.resolve(d, False)), cancel)
        self.assertIs(decline({"kind": "write_file"}), False)

    def test_stop_closing_and_interface_errors_decline(self):
        shown = []
        cancel = threading.Event()
        threading.Timer(0.05, cancel.set).start()
        self.assertIs(approvals.approver(shown.append, cancel)({"kind": "write_file"}), False)
        self.assertTrue(shown[0]["event"].is_set())
        self.assertFalse(shown[0]["approved"])
        self.assertIs(approvals.approver(shown.append, threading.Event(), closed=lambda: True)({"kind": "write_file"}), False)

        def broken(decision):
            raise RuntimeError("window gone")
        self.assertIs(approvals.approver(broken, threading.Event())({"kind": "write_file"}), False)

    def test_an_approval_after_stop_is_not_used(self):
        cancel = threading.Event()

        def approve_then_stop(decision):
            approvals.resolve(decision, True)
            cancel.set()
        self.assertIs(approvals.approver(approve_then_stop, cancel)({"kind": "write_file"}), False)

    def test_the_agent_writes_only_after_approval_through_the_service(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            calls = iter([{"message": {"content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {
                "path": "hello.py", "content": "print('hi')\n"}}}]}}, {"message": {"content": "Done."}}])
            seen = []

            def show(decision):
                seen.append(decision["action"]["kind"])
                self.assertFalse((project / "hello.py").exists())
                approvals.resolve(decision, True, destination=str(project / "hello.py"))
            result = run_agent("http://127.0.0.1:11434", "m", project, [{"role": "user", "content": "go"}],
                               lambda event: None, approvals.approver(show, threading.Event()),
                               chat_fn=lambda *args: next(calls))
            self.assertEqual(seen, ["write_file"])
            self.assertEqual(result["assistant_text"], "Done.")
            self.assertEqual((project / "hello.py").read_bytes(), b"print('hi')\n")


if __name__ == "__main__":
    unittest.main()
