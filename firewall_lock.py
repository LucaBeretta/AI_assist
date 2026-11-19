"""The firewall lock: Windows Firewall keeps the AI engines off the internet.

Ollama runs as its own native programs, outside the reach of AI Assist's
internet gate. The lock adds Windows Firewall rules that block those programs,
and any native programs in the colibri folder, from internet addresses: even
when AI Assist is closed, and even when Ollama is used directly. This computer
and the local network stay reachable.

Reading the lock needs no special rights. Turning it on or off, lifting it for
a few minutes, or locking again each need administrator approval (one Windows
UAC prompt). A startup task turns the rules back on after a restart, so a lift
cut short by a shutdown does not leave them off.
"""

import base64
import ipaddress
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

import network_gate

GROUP = "AI Assist internet lock"
TASK = "AI Assist internet relock"
LIFT_MINUTES = (5, 15, 60)
CANCELLED = 1223
_MAX_PROGRAMS = 64
_SKIP_FOLDERS = {".git", "__pycache__", "node_modules"}
# Every address except this computer, private networks, link-local, multicast and reserved space.
INTERNET_RANGES = (
    "1.0.0.0-9.255.255.255", "11.0.0.0-126.255.255.255", "128.0.0.0-169.253.255.255",
    "169.255.0.0-172.15.255.255", "172.32.0.0-192.167.255.255", "192.169.0.0-223.255.255.255",
    "2000::/3", "64:ff9b::/96")

_STATUS_SCRIPT = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object {{
    [ordered]@{{ name = [string]$_.Name; enabled = [string]$_.Enabled; local_rules = [string]$_.AllowLocalFirewallRules }} }})
$rules = @(Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | ForEach-Object {{
    [ordered]@{{ program = [string]($_ | Get-NetFirewallApplicationFilter).Program
                remote = @(($_ | Get-NetFirewallAddressFilter).RemoteAddress | ForEach-Object {{ [string]$_ }})
                enabled = [string]$_.Enabled; action = [string]$_.Action; direction = [string]$_.Direction }} }})
ConvertTo-Json -InputObject ([ordered]@{{ profiles = $profiles; rules = $rules }}) -Depth 5 -Compress
"""

_TURN_ON = r"""
foreach ($program in $plan.programs) {
    if (-not [IO.Path]::IsPathRooted($program) -or [IO.Path]::GetExtension($program) -ne '.exe' -or
            -not (Test-Path -LiteralPath $program -PathType Leaf)) { throw "Not a program file: $program" }
}
Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
foreach ($program in $plan.programs) {
    New-NetFirewallRule -DisplayName ('AI Assist internet lock: ' + [IO.Path]::GetFileName($program)) -Group $group `
        -Direction Outbound -Action Block -Program $program -RemoteAddress @($plan.ranges) -Profile Any `
        -Description 'Blocks this program from internet addresses; this computer and the local network stay reachable. Managed by AI Assist.' | Out-Null
}
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $shell -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -Command "Get-NetFirewallRule -Group ''' + $group + ''' | Enable-NetFirewallRule"')
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $task -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal `
    -Description 'Turns the AI Assist internet lock back on after a restart.' -Force | Out-Null
"""

_TURN_OFF = r"""
Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
"""

_RELOCK = r"""
Get-NetFirewallRule -Group $group | Enable-NetFirewallRule
"""

_LIFT = r"""
try {
    Get-NetFirewallRule -Group $group | Disable-NetFirewallRule
    Set-Content -LiteralPath $plan.result -Value 'ok' -Encoding UTF8
    Start-Sleep -Seconds $plan.seconds
} catch {
    Set-Content -LiteralPath $plan.result -Value ('error: ' + $_.Exception.Message) -Encoding UTF8
} finally {
    Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Enable-NetFirewallRule -ErrorAction SilentlyContinue
}
"""


# ---------------------------------------------------------------- what to lock


def ollama_folder():
    """Ollama's install folder: the one its installer uses, or the one on PATH if it
    holds a whole Ollama install (not a shim folder shared with other programs)."""
    local_data = os.environ.get("LOCALAPPDATA")
    found = shutil.which("ollama")
    candidates = [Path(local_data) / "Programs" / "Ollama"] if local_data else []
    if found:
        candidates.append(Path(found).resolve().parent)
    for folder in candidates:
        if (folder / "ollama.exe").is_file() and ((folder / "ollama app.exe").is_file() or (folder / "lib" / "ollama").is_dir()):
            return folder
    return None


def engine_programs(colibri_directory=None):
    """Native engine programs to lock: every .exe in Ollama's folder and in the colibri folder.
    A lone ollama.exe on PATH is locked by itself."""
    programs = []
    folder = ollama_folder()
    if folder is None and shutil.which("ollama"):
        programs.append(str(Path(shutil.which("ollama")).resolve()))
    for root in (folder, colibri_directory):
        if not root or not Path(root).is_dir():
            continue
        for current, folders, files in os.walk(root):
            folders[:] = sorted(name for name in folders if name not in _SKIP_FOLDERS)
            programs.extend(str(Path(current, name).resolve()) for name in sorted(files) if name.lower().endswith(".exe"))
    return list(dict.fromkeys(programs))[:_MAX_PROGRAMS]


# ---------------------------------------------------------------- reading the lock


def _encode(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _hidden():
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}


def read_status(timeout=90):
    """Windows Firewall profiles and the lock's rules, as plain data."""
    if os.name != "nt":
        return {"unavailable": "The firewall lock needs Windows."}
    try:
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encode(_STATUS_SCRIPT)],
                                   capture_output=True, timeout=timeout, **_hidden())
        if completed.returncode != 0:
            return {"error": f"PowerShell stopped with exit code {completed.returncode}"}
        data = json.loads(completed.stdout.decode("utf-8", errors="replace").lstrip("﻿"))
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {"error": str(error) or type(error).__name__}
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list) or not isinstance(data.get("rules"), list):
        return {"error": "Windows Firewall returned an unexpected answer."}
    return data


def _intervals(entries):
    """Merged (first, last) address numbers covered by firewall address entries."""
    spans = []
    for entry in entries:
        text = str(entry).strip()
        try:
            if "-" in text:
                first, last = (ipaddress.ip_address(part.strip()) for part in text.split("-", 1))
            else:
                network = ipaddress.ip_network(text, strict=False)
                first, last = network.network_address, network.broadcast_address
        except ValueError:
            return None
        spans.append((first.version, int(first), int(last)))
    merged = []
    for version, first, last in sorted(spans):
        if merged and merged[-1][0] == version and first <= merged[-1][2] + 1:
            merged[-1] = (version, merged[-1][1], max(merged[-1][2], last))
        else:
            merged.append((version, first, last))
    return merged


def _blocks_internet(rule):
    return (rule.get("action") == "Block" and rule.get("direction") == "Outbound"
            and _intervals(rule.get("remote") or []) == _intervals(INTERNET_RANGES))


def summarize(status, programs, lift_until=None, now=None):
    """What the lock does right now, in words, from read_status() and engine_programs()."""
    now = time.time() if now is None else now
    rows = []
    summary = {"state": "error", "detail": "", "programs": rows, "ollama_locked": False, "lift_until": None,
               "has_rules": False, "can_turn_on": False}
    if "unavailable" in status:
        return dict(summary, state="unavailable", detail=status["unavailable"])
    if "error" in status:
        return dict(summary, detail=f"Could not read Windows Firewall: {status['error']}")
    rules = {os.path.normcase(str(rule.get("program"))): rule for rule in status["rules"] if isinstance(rule, dict)}
    wanted = {os.path.normcase(program) for program in programs}
    for program in programs:
        rule = rules.get(os.path.normcase(program))
        rows.append({"path": program, "covered": rule is not None,
                     "active": bool(rule and rule.get("enabled") == "True" and _blocks_internet(rule))})
    for key, rule in rules.items():
        if key not in wanted:
            rows.append({"path": str(rule.get("program")), "covered": True, "active": rule.get("enabled") == "True",
                         "gone": True})
    enabled = [rule for rule in rules.values() if rule.get("enabled") == "True"]
    summary.update(has_rules=bool(rules), can_turn_on=bool(programs),
                   ollama_locked=any(os.path.basename(path) == "ollama.exe" and rule.get("enabled") == "True"
                                     and _blocks_internet(rule) for path, rule in rules.items()))
    off = [str(profile.get("name")) for profile in status["profiles"] if profile.get("enabled") != "True"]
    ignored = [str(profile.get("name")) for profile in status["profiles"] if profile.get("local_rules") == "False"]
    if off:
        return dict(summary, state="firewall off", ollama_locked=False,
                    detail=f"Windows Firewall is off for: {', '.join(off)}. Its rules do nothing until you turn it "
                           "on in Windows Security > Firewall & network protection.")
    if ignored:
        return dict(summary, state="ignored", ollama_locked=False,
                    detail="This PC's Group Policy ignores local firewall rules, so the lock cannot work here.")
    if not rules:
        detail = ("Off. Ollama can reach the internet by itself, for example to check for updates."
                  if programs else "Off. No Ollama or colibri programs were found to lock.")
        return dict(summary, state="off", detail=detail)
    if any(not _blocks_internet(rule) for rule in rules.values()):
        return dict(summary, state="changed",
                    detail="Changed outside AI Assist: a rule no longer blocks every internet address. Turn the lock on again.")
    if not enabled:
        if lift_until and now < lift_until:
            return dict(summary, state="lifted", lift_until=lift_until,
                        detail=f"Lifted until {time.strftime('%H:%M', time.localtime(lift_until))}. "
                               "It turns back on by itself.")
        return dict(summary, state="disabled",
                    detail="Off: the rules are turned off and no lift timer is running. Lock again.")
    missing = [row for row in rows if not row.get("gone") and not row["active"]]
    if missing:
        return dict(summary, state="partial",
                    detail=f"Partly on: {len(missing)} program(s) are not locked, for example after an Ollama "
                           "update. Turn the lock on again to cover them.")
    return dict(summary, state="on",
                detail="On. Windows Firewall blocks these programs from internet addresses. This computer and "
                       "the local network still work.")


# ---------------------------------------------------------------- changing the lock


def _elevated(script, wait=True, timeout=600):
    """Run a PowerShell script as administrator after a UAC prompt; return its exit code.
    The script travels base64-encoded in an environment variable, so it is encoded
    only once and never parsed as part of a command line."""
    inner = _encode(script)
    if len(inner) > 30000:
        raise OSError("the firewall change is too large for one PowerShell command")
    finish = "$null = $process.Handle; $process.WaitForExit(); exit $process.ExitCode" if wait else "exit 0"
    outer = ("$shell = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
             "$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + $env:AI_ASSIST_ELEVATED\n"
             "try {\n"
             "    $process = Start-Process -FilePath $shell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru -ErrorAction Stop\n"
             f"}} catch {{ exit {CANCELLED} }}\n" + finish)
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encode(outer)],
                               env=dict(os.environ, AI_ASSIST_ELEVATED=inner), capture_output=True,
                               timeout=timeout, **_hidden())
    return completed.returncode


def _script(body, **plan):
    data = base64.b64encode(json.dumps(plan).encode("utf-8")).decode("ascii")
    return ("$ErrorActionPreference = 'Stop'\n"
            f"$plan = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}')) | ConvertFrom-Json\n"
            f"$group = '{GROUP}'\n$task = '{TASK}'\n" + body)


def _checked(body):
    return ("try {\n" + body + "\nSet-Content -LiteralPath $plan.result -Value 'ok' -Encoding UTF8\n}\n"
            "catch {\nSet-Content -LiteralPath $plan.result -Value ('error: ' + $_.Exception.Message) -Encoding UTF8\nexit 2\n}\n")


def _outcome(code, result):
    try:
        text = result.read_text(encoding="utf-8-sig").strip()
    except OSError:
        text = ""
    if code == CANCELLED:
        return False, "Windows did not get administrator approval, so nothing changed."
    if code == 0 and text == "ok":
        return True, ""
    return False, (text.removeprefix("error: ") or f"PowerShell stopped with exit code {code}.")


def _change(body, wait=True, **plan):
    if os.name != "nt":
        return False, "The firewall lock needs Windows."
    with tempfile.TemporaryDirectory(prefix="ai-assist-lock-") as folder:
        result = Path(folder, "result.txt")
        script = _script(body if not wait else _checked(body), result=str(result), **plan)
        try:
            code = _elevated(script, wait=wait)
            if not wait and code == 0:
                deadline = time.monotonic() + 120
                while not result.exists() and time.monotonic() < deadline:
                    time.sleep(0.5)
                time.sleep(0.2)
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"Could not run PowerShell: {error}"
        return _outcome(code, result)


def turn_on(programs):
    if not programs:
        return False, "No Ollama or colibri programs were found to lock."
    return _change(_TURN_ON, programs=list(programs), ranges=list(INTERNET_RANGES))


def turn_off():
    return _change(_TURN_OFF)


def relock():
    return _change(_RELOCK)


def lift(minutes):
    if minutes not in LIFT_MINUTES:
        raise ValueError(f"Lift the lock for one of {LIFT_MINUTES} minutes.")
    return _change(_LIFT, wait=False, seconds=minutes * 60)


# ---------------------------------------------------------------- state for the window


class Lock:
    """The lock's last known state. Reading and changing it run on worker threads;
    ``poll()`` on the window thread collects the results."""

    def __init__(self, colibri_directory=lambda: None, lift_file=None):
        self.colibri_directory = colibri_directory
        self.lift_file = Path(lift_file) if lift_file else network_gate.log_path().with_name("firewall-lift.json")
        self.summary = None
        self.busy = None
        self.events = queue.Queue()

    def lift_until(self):
        try:
            value = json.loads(self.lift_file.read_text(encoding="utf-8")).get("until")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, (int, float)) else None

    def refresh(self):
        if self.busy:
            return
        self.busy = "checking"
        directory = self.colibri_directory()
        threading.Thread(target=self._refresh, args=(directory,), name="firewall-lock", daemon=True).start()

    def _refresh(self, directory):
        summary = summarize(read_status(), engine_programs(directory), self.lift_until())
        self.events.put(("status", None, summary, ""))

    def run(self, action, minutes=None):
        if self.busy:
            return
        self.busy = action
        directory = self.colibri_directory()
        threading.Thread(target=self._run, args=(action, minutes, directory), name="firewall-lock", daemon=True).start()

    def _run(self, action, minutes, directory):
        try:
            if action == "on":
                ok, message = turn_on(engine_programs(directory))
            elif action == "off":
                ok, message = turn_off()
            elif action == "relock":
                ok, message = relock()
            else:
                ok, message = lift(minutes)
                if ok:
                    until = time.time() + minutes * 60
                    try:
                        self.lift_file.parent.mkdir(parents=True, exist_ok=True)
                        self.lift_file.write_text(json.dumps({"until": until}), encoding="utf-8")
                    except OSError:
                        pass
        except Exception as error:
            ok, message = False, str(error)
        summary = summarize(read_status(), engine_programs(directory), self.lift_until())
        self.events.put(("done", action, summary, message if not ok else ""))

    def poll(self):
        """Apply finished work; returns [(action, error message)] for finished changes."""
        finished = []
        while True:
            try:
                kind, action, summary, message = self.events.get_nowait()
            except queue.Empty:
                return finished
            self.summary, self.busy = summary, None
            if kind == "done":
                finished.append((action, message))

    def blocks(self, kind):
        """network_gate's lock check: Ollama cannot go online while its rules are on."""
        summary = self.summary
        if kind.startswith("Ollama") and summary and summary.get("ollama_locked"):
            return ("The firewall lock is on, so Ollama cannot reach the internet. Lift it for a few minutes under "
                    "Settings > Firewall lock (administrator approval), then try again.")
        return None
