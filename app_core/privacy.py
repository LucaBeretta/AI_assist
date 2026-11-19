"""Whether prompts stay on this computer, in one place for every interface.

"Local" follows the internet gate's own rule (network_gate.classify), so the
status bar, the web workspace and the gate always agree: all of 127.0.0.0/8
and ::1 are this computer.
"""

import time
import urllib.parse

import network_gate

FIREWALL_WARNINGS = {"lifted": "firewall lifted until {until}", "partial": "firewall lock incomplete",
                     "changed": "firewall lock changed", "disabled": "firewall lock off",
                     "firewall off": "windows firewall off"}


def host_of(url):
    try:
        return urllib.parse.urlsplit(url if "://" in url else "http://" + url).hostname
    except ValueError:
        return None


def is_local_url(url):
    """True only when ``url`` points at this computer."""
    host = host_of(url or "")
    return bool(host) and network_gate.classify(host) == network_gate.LOCAL


def is_cloud_model(name):
    """Ollama's cloud models run on Ollama's servers: names end in -cloud or :cloud."""
    name = (name or "").lower()
    return name.endswith("-cloud") or name.endswith(":cloud")


def privacy_status(gate, model, url, firewall=None):
    """(text, role) for the privacy indicator. ``gate`` is network_gate.status();
    ``firewall`` is the firewall lock's summary, if any. Roles: success, amber."""
    if gate["installed"] and gate["online"]:
        text, role = f"online now: {gate['online']}", "amber"
    elif is_cloud_model(model):
        text = "cloud model: each request needs approval" if gate["installed"] else "cloud model: prompts leave this computer"
        role = "amber"
    elif url and not is_local_url(url):
        text, role = "remote server", "amber"
    else:
        text = "local only · internet locked" if gate["installed"] else "local only"
        role = "success"
    summary = firewall or {}
    state = summary.get("state")
    if state == "on":
        text += " · firewall on"
    elif state in FIREWALL_WARNINGS and summary.get("has_rules"):
        until = summary.get("lift_until")
        text += " · " + FIREWALL_WARNINGS[state].format(until=time.strftime("%H:%M", time.localtime(until)) if until else "")
        role = "amber"
    return text, role
