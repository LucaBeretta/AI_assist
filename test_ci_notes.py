"""tools/ci_notes.py: unittest failures become public GitHub notes."""

import unittest

from tools.ci_notes import failures, notes

LOG = """test_a (m.T.test_a) ... ok
test_b (m.T.test_b) ... FAIL
test_c (m.T.test_c) ... ERROR

======================================================================
FAIL: test_b (m.T.test_b)
Checks b.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "m.py", line 3, in test_b
    self.assertEqual(1, 2)
AssertionError: 1 != 2

======================================================================
ERROR: test_c (m.T.test_c)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "m.py", line 9, in test_c
    open("x:y")
OSError: 100% bad, really

----------------------------------------------------------------------
Ran 3 tests in 0.010s

FAILED (failures=1, errors=1)
"""


class CiNotesTests(unittest.TestCase):
    def test_each_failure_keeps_its_name_and_the_end_of_its_traceback(self):
        found = failures(LOG)
        self.assertEqual([title for title, _ in found], ["FAIL: m.T.test_b", "ERROR: m.T.test_c"])
        self.assertTrue(found[0][1].endswith("AssertionError: 1 != 2"))
        self.assertTrue(found[1][1].endswith("OSError: 100% bad, really"))
        self.assertNotIn("Ran 3 tests", found[1][1])

    def test_notes_are_single_escaped_lines(self):
        lines = notes(LOG)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("::error title=FAIL%3A m.T.test_b::Traceback"))
        self.assertIn("%0AAssertionError: 1 != 2", lines[0])
        self.assertIn("100%25 bad, really", lines[1])
        self.assertTrue(all("\n" not in line for line in lines))

    def test_too_many_failures_end_with_a_count_and_none_is_still_reported(self):
        many = "".join(f"{'=' * 70}\nFAIL: t{i} (m.T.t{i})\n{'-' * 70}\nAssertionError: {i}\n\n" for i in range(12))
        lines = notes(many, limit=10)
        self.assertEqual(len(lines), 10)
        self.assertIn("3 more failures", lines[-1])
        self.assertIn("No FAIL or ERROR block", notes("Ran 0 tests\n\nOK\n")[0])


if __name__ == "__main__":
    unittest.main()
