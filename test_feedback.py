"""Feedback context bounds and evidence-based verification summaries."""

import unittest

from feedback import feedback_prompt, verification_summary


class FeedbackTests(unittest.TestCase):
    def test_followup_keeps_target_feedback_and_acceptance_criteria(self):
        prompt = feedback_prompt("Previous result", "Keep the headings shorter", "Under 150 words")
        for text in ("Previous result", "Keep the headings shorter", "Under 150 words",
                     "check it against the criteria", "ask for feedback"):
            self.assertIn(text, prompt)

    def test_followup_bounds_each_context_field(self):
        prompt = feedback_prompt("a" * 5000, "b" * 6000, "c" * 3000, developer=True)
        self.assertIn("a" * 2000, prompt)
        self.assertNotIn("a" * 2001, prompt)
        self.assertIn("b" * 4000, prompt)
        self.assertNotIn("b" * 4001, prompt)
        self.assertIn("c" * 2000, prompt)
        self.assertNotIn("c" * 2001, prompt)
        self.assertIn("Inspect the current project", prompt)
        self.assertIn("rather than automatically rerunning", prompt)

    def test_empty_feedback_and_nontext_fields_are_rejected(self):
        for feedback in ("", " \n\t", None, False):
            with self.subTest(feedback=feedback), self.assertRaises(ValueError):
                feedback_prompt("Previous result", feedback)
        with self.assertRaises(ValueError):
            feedback_prompt(None, "Improve this")
        with self.assertRaises(ValueError):
            feedback_prompt("Result", "Improve this", criteria=[])

    def test_optional_criteria_does_not_invent_requirements(self):
        prompt = feedback_prompt("Answer", "Explain the tradeoff")
        self.assertIn("Use my feedback above as the success criteria", prompt)
        self.assertNotIn("--- Success criteria ---", prompt)

    def test_only_latest_request_command_outcomes_count(self):
        events = [
            {"type": "request_start"}, {"type": "command_succeeded"},
            {"type": "request_start"}, {"type": "command_failed"},
            {"type": "command_timed_out"}, {"type": "command_succeeded"},
        ]
        self.assertEqual(verification_summary(events),
                         "Command checks: 1 succeeded, 1 failed, 1 timed out.")

    def test_model_claims_and_generic_tool_output_are_not_verification(self):
        events = [{"type": "request_start"},
                  {"type": "assistant", "text": "All tests passed."},
                  {"type": "tool_result", "text": "Command exited with code 0."},
                  {"type": "changed"}]
        self.assertEqual(verification_summary(events), "No command checks recorded for this task.")
        self.assertEqual(verification_summary([{"type": "command_succeeded"}]),
                         "No command checks recorded for this task.")

    def test_later_edits_need_another_check(self):
        events = [{"type": "request_start"}, {"type": "command_succeeded"}, {"type": "changed"}]
        self.assertIn("Changes were applied after the last command; check again.", verification_summary(events))
        events.append({"type": "command_failed"})
        self.assertNotIn("after the last command", verification_summary(events))
        self.assertIn("1 failed", verification_summary(events))


if __name__ == "__main__":
    unittest.main()
