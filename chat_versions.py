"""Answer tails anchored after a message index (-1 means before the first).

Branches include the active tail. Prefix fingerprints prevent a fork from one
conversation path being offered in an unrelated path at the same numeric index.
The companion fields preserve stable version numbers as an answer grows.
"""

from copy import deepcopy
import hashlib
import json


def prefix(record, index):
    data = json.dumps(record["messages"][:index + 1], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sync(record):
    branches = record.setdefault("branches", {})
    prefixes = record.setdefault("_branch_prefixes", {})
    active = record.setdefault("_branch_active", {})
    for key, tails in branches.items():
        index = int(key)
        if index >= len(record["messages"]):
            continue
        fingerprint = prefix(record, index)
        hashes = prefixes.setdefault(key, [fingerprint] * len(tails))
        tail = record["messages"][index + 1:]
        choice = active.get(key)
        if type(choice) is not int or not 0 <= choice < len(tails) or hashes[choice] != fingerprint:
            choice = next((n for n, item in enumerate(tails) if hashes[n] == fingerprint and item == tail), None)
        if choice is None:
            choice = len(tails)
            tails.append([])
            hashes.append(fingerprint)
        tails[choice] = deepcopy(tail)
        active[key] = choice


def fork(record, index, tail):
    sync(record)
    key = str(index)
    fingerprint = prefix(record, index)
    branches = record["branches"]
    if key not in branches:
        branches[key] = [deepcopy(record["messages"][index + 1:])]
        record["_branch_prefixes"][key] = [fingerprint]
    branches[key].append(deepcopy(tail))
    record["_branch_prefixes"][key].append(fingerprint)
    record["_branch_active"][key] = len(branches[key]) - 1
    replace_tail(record, index, tail)


def replace_tail(record, index, tail):
    old = record["messages"]
    updated = old[:index + 1] + deepcopy(tail)
    summary = record.get("summary")
    if summary and old[:summary["upto"]] != updated[:summary["upto"]]:
        record["summary"] = None
    if old != updated:
        record["_agent_history_stale"] = True
    record["messages"] = updated
    for key in list(record["_branch_active"]):
        if int(key) > index:
            del record["_branch_active"][key]


def choices(record, index):
    key = str(index)
    fingerprint = prefix(record, index)
    tails = record.get("branches", {}).get(key, [])
    hashes = record.get("_branch_prefixes", {}).get(key, [fingerprint] * len(tails))
    return [n for n, value in enumerate(hashes) if value == fingerprint]


def versions(record):
    result = {}
    for key in record.get("branches", {}):
        if int(key) >= len(record["messages"]):
            continue
        candidates = choices(record, int(key))
        choice = record.get("_branch_active", {}).get(key)
        if choice in candidates:
            result[key] = [candidates.index(choice), len(candidates)]
    return result


def switch(record, index, number):
    sync(record)
    candidates = choices(record, index)
    if type(number) is not int or not 0 <= number < len(candidates):
        raise ValueError("Choose an existing answer version (zero-based).")
    key = str(index)
    choice = candidates[number]
    replace_tail(record, index, record["branches"][key][choice])
    record["_branch_active"][key] = choice
    sync(record)
