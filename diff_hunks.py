"""Stable review blocks and partial application using unified-diff grouping."""

import difflib


def hunks(diff):
    result = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("@@ "):
            result.append({"id": f"h{len(result) + 1}", "header": line.rstrip("\r\n"), "lines": []})
        elif result:
            result[-1]["lines"].append(line)
    return result


def validate_selection(available, selected):
    if (not isinstance(selected, list) or any(not isinstance(item, str) for item in selected)
            or len(selected) != len(set(selected)) or not set(selected) <= {item["id"] for item in available}):
        raise ValueError("Choose unique hunk IDs from this file approval.")


def apply_selected(original, proposed, selected):
    old, new = original.splitlines(keepends=True), proposed.splitlines(keepends=True)
    groups = list(difflib.SequenceMatcher(None, old, new).get_grouped_opcodes(3))
    available = [{"id": f"h{index + 1}"} for index in range(len(groups))]
    validate_selection(available, selected)
    output, cursor = [], 0
    for index, group in enumerate(groups):
        if f"h{index + 1}" not in selected:
            continue
        for tag, start, end, new_start, new_end in group:
            if tag != "equal":
                output.extend(old[cursor:start])
                output.extend(new[new_start:new_end])
                cursor = end
    output.extend(old[cursor:])
    return "".join(output)
