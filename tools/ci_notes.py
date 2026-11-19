"""Turn the failures in a unittest log into GitHub Actions error notes.

GitHub shows a job's full log only to signed-in users, but its notes
(annotations) are public. Used by .github/workflows/tests.yml after a failed
test step: ``python tools/ci_notes.py unittest.log``. Standard library only.
"""

import re
import sys
from pathlib import Path

LIMIT = 10  # GitHub keeps at most 10 error notes per step.
_HEADER = re.compile(r"^(FAIL|ERROR): (\S+)(?: \((.+)\))?\s*$")


def _escape(text, title=False):
    """GitHub's workflow-command escaping; a title also escapes ':' and ','."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if title else text


def failures(log):
    """[(title, traceback tail)] for each FAIL or ERROR block, in order."""
    found = []
    blocks = re.split(r"^={70}$", log, flags=re.M)[1:]
    for block in blocks:
        lines = block.strip("\n").splitlines()
        header = _HEADER.match(lines[0]) if lines else None
        if not header:
            continue
        body = block.split("-" * 70, 1)[1] if "-" * 70 in block else ""
        body = body.split("\n" + "-" * 70, 1)[0]  # the last block ends before "Ran N tests"
        tail = [line for line in body.strip("\n").splitlines() if line.strip()][-15:]
        found.append((f"{header.group(1)}: {header.group(3) or header.group(2)}", "\n".join(tail)))
    return found


def notes(log, limit=LIMIT):
    """The ``::error`` lines for ``log``; a last note counts any failures left out."""
    found = failures(log)
    lines = [f"::error title={_escape(title, True)}::{_escape(tail)}" for title, tail in found[:limit]]
    if len(found) > limit:
        lines[-1] = f"::error title=More failures::{len(found) - limit + 1} more failures; see the full log."
    if not found:
        lines.append("::error title=Tests failed::No FAIL or ERROR block found; the run may have stopped early.")
    return lines


if __name__ == "__main__":
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    print("\n".join(notes(text)))
