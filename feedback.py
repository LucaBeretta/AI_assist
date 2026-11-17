"""Bounded feedback follow-ups and summaries of locally observed command checks."""


def _bounded_text(value, limit, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[Excerpt shortened.]"


def feedback_prompt(answer, feedback, criteria="", developer=False):
    """Prepare a follow-up; returning text never sends it or repeats an action."""
    answer = _bounded_text(answer, 2000, "The original answer")
    feedback = _bounded_text(feedback, 4000, "Feedback")
    criteria = _bounded_text(criteria, 2000, "Success criteria")
    if not feedback:
        raise ValueError("Describe what should improve before preparing feedback.")
    parts = [
        "Feedback on a previous answer in this conversation:",
        "--- Original answer (reference) ---\n" + answer,
        "--- My feedback ---\n" + feedback,
    ]
    if criteria:
        parts.append("--- Success criteria ---\n" + criteria)
    else:
        parts.append("Use my feedback above as the success criteria; ask if a requirement is unclear.")
    if developer:
        parts.append(
            "Inspect the current project before proposing revisions. Revise and check against the criteria "
            "using appropriate checks. Review the current state rather than automatically rerunning prior "
            "commands or edits. Report what you actually checked and what still needs my feedback."
        )
    else:
        parts.append(
            "Revise the answer and check it against the criteria. Explain any remaining uncertainty "
            "and ask for feedback on the revised result."
        )
    return "\n\n".join(parts)


def verification_summary(events):
    """Summarize trusted outcomes for the latest request, excluding model prose."""
    start = None
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("type") == "request_start":
            start = index
    if start is None:
        return "No command checks recorded for this task."
    outcomes = {"command_succeeded": 0, "command_failed": 0, "command_timed_out": 0}
    last_command = last_change = None
    for index, event in enumerate(events[start + 1:], start + 1):
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind in outcomes:
            outcomes[kind] += 1
            last_command = index
        elif kind == "changed":
            last_change = index
    if last_command is None:
        return "No command checks recorded for this task."
    summary = (f"Command checks: {outcomes['command_succeeded']} succeeded, "
               f"{outcomes['command_failed']} failed, {outcomes['command_timed_out']} timed out.")
    if last_change is not None and last_change > last_command:
        summary += "\nChanges were applied after the last command; check again."
    return summary
