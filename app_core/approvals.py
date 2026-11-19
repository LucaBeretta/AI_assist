"""A person's approval of the developer agent's edits and commands, for every interface.

The agent (coding_agent.run_agent) asks through an ``approve_action`` callback
on its worker thread. ``approver()`` builds that callback: it creates a
*decision*, hands it to the interface to show, and waits for the person.

A decision is a plain dict, so any interface (and the existing tests) can read it::

    {"action": {...}, "approved": False, "event": threading.Event()}
    + "destination" when the person chose another file in Save As
    + "second_check_until" once a command's second check has started

The rules live here, not in a dialog, so no interface can skip them:

- **Declined unless approved.** Stop, closing the app, or an error in the
  interface leave the action declined. A decision is answered only once.
- **Two confirmations for commands.** A command runs outside the internet gate,
  so approving it needs ``begin_second_check()`` first and then, no sooner than
  ``COMMAND_CONFIRM_SECONDS`` later, ``resolve(approved=True)``. An earlier
  approval is refused and the decision stays open.
- **Every approved command is logged and announced** (network_gate.note_outside),
  wherever it was approved.
"""

import threading
import time

import network_gate

COMMAND_CONFIRM_SECONDS = 3


def new_decision(action):
    return {"action": dict(action), "approved": False, "event": threading.Event()}


def is_command(decision):
    return decision["action"].get("kind") == "run_command"


def begin_second_check(decision, seconds=COMMAND_CONFIRM_SECONDS, clock=time.monotonic):
    """Start a command's second check. Starting it again does not shorten or restart the wait."""
    if not is_command(decision):
        raise ValueError("Only commands have a second check.")
    if "second_check_until" not in decision:
        decision["second_check_until"] = clock() + max(0, seconds)
    return decision["second_check_until"]


def seconds_left(decision, clock=time.monotonic):
    """Seconds until a command may be approved; None before its second check began."""
    until = decision.get("second_check_until")
    return None if until is None else max(0.0, until - clock())


def resolve(decision, approved, destination=None, clock=time.monotonic):
    """Record the person's answer. Returns False, and changes nothing, when the
    decision was already answered or a command's approval comes too early."""
    if decision["event"].is_set():
        return False
    if approved and is_command(decision):
        left = seconds_left(decision, clock)
        if left is None or left > 0:
            return False
        action = decision["action"]
        network_gate.note_outside("Developer agent command", action.get("command", ""), action.get("description", ""))
    decision["approved"] = bool(approved)
    if approved and destination:
        decision["destination"] = destination
    decision["event"].set()
    return True


def wait_for(decision, notify, cancel_event, closed=lambda: False, poll=0.1):
    """Show ``decision`` through ``notify`` and wait for the person's answer.

    Returns what run_agent's ``approve_action`` must return: True, or
    {"approved": True, "destination": path} after a Save As choice, or False."""
    try:
        notify(decision)
    except Exception:
        resolve(decision, False)
        return False
    while not decision["event"].wait(poll):
        if cancel_event.is_set() or closed():
            resolve(decision, False)
            return False
    if not decision["approved"] or cancel_event.is_set():
        return False
    return {"approved": True, "destination": decision["destination"]} if decision.get("destination") else True


def approver(notify, cancel_event, closed=lambda: False):
    """An ``approve_action`` callback for coding_agent.run_agent. ``notify(decision)``
    must show the decision to a person; it is called on the agent's worker thread."""
    return lambda action: wait_for(new_decision(action), notify, cancel_event, closed)
