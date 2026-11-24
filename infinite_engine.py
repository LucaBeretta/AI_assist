"""Make short clips with Infinite, the free node-based app for moving pictures and sound.

Infinite (https://github.com/n1m21n/Infinite, MIT licence) renders a plain-text patch (.inf)
to video without opening a window. When it is installed here, a local model writes the
patch from a description, Infinite checks it, the model fixes what Infinite reports, and
Infinite renders the clip into AI Assist's data folder.

Infinite is a separate program that the internet gate cannot see inside, so every patch is
checked here first: patches that read or write files, use a camera, microphone or MIDI
device, talk to other programs or the network, load plugins or run JavaScript are refused
before Infinite sees them. Nothing is downloaded.
"""

import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from app_core import paths
import network_gate

CHECK_TIMEOUT = 60
RENDER_TIMEOUT = 300
DESCRIBE_TIMEOUT = 180
MAX_DESCRIPTION_CHARS = 1500
MAX_PATCH_CHARS = 32_000
MAX_NODES = 80
MAX_VIDEO_BYTES = 24 * 1024 * 1024
LENGTHS = (4, 8, 12)
MAX_SIDE = 2048  # a picture's width or height; the clip takes the size of the picture its Output shows
FPS = 30
FIX_ROUNDS = 3
CONTEXT_LENGTH = 16384
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_GRACE_SECONDS = 15  # Infinite's own --timeout should stop it first; this is the last resort
CREDIT = "Infinite, by Naman Soni (n1m21n), https://infinitedevices.org"

# Types that reach outside the patch even without a file path: other programs, the network,
# hardware, native plugins and JavaScript. Types Infinite marks as hardware-driven, live input
# or machine-dependent are refused as well (see _blocked).
_BLOCKED_TYPES = {"Syphon In", "Syphon Out", "NDI In", "NDI Out", "OSC Receive", "OSC Send", "MIDI Out",
                  "MIDI Notes", "MIDI CC", "MIDI Trigger", "MIDI File", "Audio In", "Video In", "Keyboard",
                  "Brain Source", "Plugin", "Sketch", "Sketch 3D", "Hand Track", "Face Track", "Pose Track",
                  # Whole saved documents, which can name image and model files inside them.
                  "Scene 2D", "3D View"}
# Text controls that name a file, folder, device or address, such as Output's recordVideoPath.
_LOCATION_KEY = re.compile(r"path|file|folder|director|^dir|host|port|url|device|server|address|uuid|appname",
                           re.IGNORECASE)
_RECORDS = {"node", "cable", "geo", "aud", "note", "mod", "pal", "expr", "glob", "rev"}
_NODE_RECORDS = {"id", "pos", "flags", "f", "i", "b", "c", "s"}
_BLOCK = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Shown to the model with their controls: enough for pictures, 3D and sound in a short prompt.
MENU = (
    ("Pictures", ("Shape", "Ramp", "Noise", "Text", "Layer Stack", "Blend", "Feedback", "Trails",
                  "kaleidoscope", "symmetry", "bloom", "glitch", "twirl", "ripple", "pixelate",
                  "gaussianblur", "vignette", "color adjustments", "Color Ramp")),
    ("Movement", ("LFO", "Random", "Envelope", "Audio Analyze")),
    ("3D", ("Torus Knot", "Sphere", "Cube", "Transform", "Material", "Join Geometry", "Camera", "Light",
            "Render 3D")),
    ("Sound", ("Random Note Generator", "Arpeggiator", "Note Sequencer", "Chorder", "Analog", "FM", "Piano",
               "String", "Reverb", "Delay", "Audio Filter")),
    ("Endings", ("Output", "Audio Out")),
)
_MENU_CONTROLS = 14
_FIX_CONTROLS = 40
_CATALOGUE_FORMAT = 2  # bump when _compact keeps something new; older saved lists are then read again

# The wire record that carries each kind of output.
_WIRE_FOR_KIND = {"image": "cable", "audio": "aud", "note": "note", "geometry": "geo", "camera": "geo", "light": "geo"}
_WIRES = {"cable", "geo", "aud", "note"}
# Names the examples use. A patch that wires one of them without making the node gets the node.
_EXAMPLE_NODES = {"out": ("Utility", "Output"), "speakers": ("Utility", "Audio Out"), "view": ("3D", "Render 3D"),
                  "cam": ("3D", "Camera"), "sun": ("3D", "Light"), "mix": ("Compositing", "Layer Stack"),
                  "hear": ("Modulators", "Audio Analyze"), "lfo": ("Modulators", "LFO")}
_CONTENT_KINDS = {"image", "audio", "geometry"}

EXAMPLES = """Example 1, a star that spins over a colour gradient:
```inf
infinite-patch 1
node 1 Source Ramp
  id sky
  c stopColor0 0.1 0.1 0.4
  c stopColor1 0.6 0.2 0.7
end
node 2 Source Shape
  id star
  i shapeType Star
  c fillColor 1 0.8 0.2
end
node 3 Compositing Layer Stack
  id mix
end
node 4 Modulators LFO
  id lfo
end
node 5 Utility Output
  id out
end
cable mix 0 sky
cable mix 1 star
cable out 0 mix
mod star sizeX lfo 0 0 1 0.5 0.2 0.45
mod star sizeY lfo 0 0 1 0.5 0.2 0.45
expr star rotation sin(t) * 90
```

Example 2, music with a shape that grows with the sound:
```inf
infinite-patch 1
node 1 Notes Random Note Generator
  id notes
end
node 2 Synths Analog
  id synth
  f volume 0.5
end
node 3 Utility Audio Out
  id speakers
end
node 4 Modulators Audio Analyze
  id hear
end
node 5 Source Shape
  id shape
  c fillColor 1 0.45 0.3
end
node 6 Utility Output
  id out
end
note synth 0 notes
aud speakers 0 synth
aud out audio synth
aud hear 0 synth
cable out 0 shape
mod shape sizeX hear 0 0 1 0.5 0.12 0.4
mod shape sizeY hear 0 0 1 0.5 0.12 0.4
```

Example 3, a golden 3D knot that turns:
```inf
infinite-patch 1
node 1 3D Torus Knot
  id knot
  c color 1 0.75 0.25
  f metallic 1
  f roughness 0.25
end
node 2 3D Transform
  id spin
end
node 3 3D Camera
  id cam
end
node 4 3D Light
  id sun
end
node 5 3D Render 3D
  id view
end
node 6 Utility Output
  id out
end
geo spin geo knot
geo view 0 spin
geo view camera cam
geo view light_1 sun
cable out 0 view
expr spin rotY t * 40
```"""

FORMAT = """You write patches for Infinite, a node-based app for moving pictures and sound. \
Reply with one complete patch in a ```inf code block and nothing else.

The format has one record per line:
infinite-patch 1                          the first line, always
node <number> <Category> <Type>           starts a node; its controls follow, then end
  id <word>                               a name to use instead of the number
  f <key> <number>                        a number
  i <key> <whole number or option name>   for example: i shapeType Star
  b <key> <0 or 1>                        on or off
  c <key> <red> <green> <blue>            a colour, each 0 to 1
  s <key> <text>                          text, to the end of the line
end
cable <to> <slot> <from>                  picture wire; the destination comes first
geo <to> <slot> <from>                    3D shape, camera or light wire
aud <to> <slot> <from>                    sound wire
note <to> <slot> <from>                   note wire
mod <to> <key> <from> <output> <polarity> <depth> <centre> <low> <high>
                                          a modulator moves a control between low and high (real values)
expr <to> <key> <formula of t>            t is the time in seconds, for example sin(t * 2) * 90

Rules:
- Write every node first, then the wires. Inside a node only id, f, i, b, c and s lines are allowed.
- Wire only ids of nodes in your patch.
- The picture ends in a Utility Output: cable out 0 <picture>.
- Each input takes one cable. To show several pictures together, wire them into a Compositing Layer Stack \
(cable mix 0 <back>, cable mix 1 <front>) and wire the Layer Stack to the Output, as in example 1.
- 3D needs a shape, a 3D Camera, a 3D Light and a 3D Render 3D, wired as in example 3. To turn a 3D shape, \
send it through a 3D Transform and move its rotY with expr.
- Sound plays through a Utility Audio Out (aud speakers 0 <sound>) and is heard in the clip only when it is \
also wired into the Output: aud out audio <sound>.
- Use only the node types and controls listed below. Leave every control you do not change out.
- Only controls marked ~ can be moved by mod. expr works on any number control.
- Slots are numbers or the input names listed, for example geo view camera cam.
- Never set file paths or anything that reads files, devices or the network.
- AI Assist chooses the clip's length."""


class InfiniteError(ValueError):
    """A problem to show the person in plain words."""


def find_infinite():
    """The Infinite program to run without a window, or None when it is not installed.

    AI_ASSIST_INFINITE names one copy; Infinite's own INFINITE_BIN (and its older name
    INFINITE_APP) are honoured too. Then the places Infinite's installers use, then PATH.
    Nothing is started; only files are looked up.
    """
    candidates = [os.environ.get(name, "").strip() for name in ("AI_ASSIST_INFINITE", "INFINITE_BIN", "INFINITE_APP")]
    if sys.platform == "darwin":
        candidates += ["/Applications/Infinite.app", os.path.expanduser("~/Applications/Infinite.app")]
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        for root in (os.environ.get("ProgramFiles"), local, local and os.path.join(local, "Programs")):
            if root:
                candidates.append(os.path.join(root, "Infinite", "Infinite.exe"))
    candidates += [shutil.which(name) for name in (("Infinite.exe",) if os.name == "nt" else ("Infinite", "infinite"))]
    for candidate in candidates:
        if candidate and candidate.endswith(".app") and os.path.isdir(candidate):
            candidate = os.path.join(candidate, "Contents", "MacOS", "Infinite")
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return None


def _command(program):
    return list(program) if isinstance(program, (list, tuple)) else [program]


def _last_json(text):
    """Infinite always ends with one JSON object on its own line."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except ValueError:
                return None
            return result if isinstance(result, dict) else None
    return None


def _saved_result(path):
    try:
        with open(path, "rb") as saved:
            result = json.loads(saved.read(_MAX_RESULT_BYTES + 1)[:_MAX_RESULT_BYTES].decode("utf-8"))
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def run(program, arguments, folder, timeout, cancel=None, result_file=None):
    """Run Infinite with these arguments in ``folder``; return (exit code, its JSON result).

    Output goes to a file, not a pipe, so a chatty run never blocks. ``cancel`` stops it.
    ``result_file`` names the file a ``--json`` run writes its (large) result to."""
    folder = Path(folder)
    log = folder / "infinite-output.txt"
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    command = _command(program) + [str(item) for item in arguments]
    try:
        try:
            with open(log, "wb") as output:
                process = subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=subprocess.STDOUT, **options)
        except OSError as error:
            raise InfiniteError(f"Infinite could not be started: {error}") from error
        deadline = time.monotonic() + timeout + _GRACE_SECONDS
        try:
            while True:
                try:
                    code = process.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("Stopped.") from None
                    if time.monotonic() > deadline:
                        raise InfiniteError(f"Infinite did not finish within {timeout} seconds.") from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        with open(log, "rb") as output:
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 256 * 1024))
            text = output.read().decode("utf-8", errors="replace")
    finally:
        log.unlink(missing_ok=True)
    if code == 6:
        raise InfiniteError(f"Infinite did not finish within {timeout} seconds.")
    result = _saved_result(folder / result_file) if result_file is not None and code == 0 else None
    result = result or _last_json(text)
    if result is None:
        detail = " ".join(text.strip().splitlines()[-2:])[:300]
        raise InfiniteError(f"Infinite stopped without a result (exit code {code}). {detail}".strip())
    if code == 2:
        # A command line this version does not accept is AI Assist's mistake, never the patch's.
        message = next((item.get("message") for item in result.get("errors", []) if isinstance(item, dict)), "")
        raise InfiniteError(f"This version of Infinite did not accept AI Assist's request. {message}".strip())
    return code, result


def version(program, folder):
    """Infinite's version number, such as 2.1.0. Its catalogue is kept per version."""
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    try:
        done = subprocess.run(_command(program) + ["--version"], cwd=folder, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=CHECK_TIMEOUT, **options)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InfiniteError(f"Infinite could not be started: {error}") from error
    found = re.search(r"\d+(?:\.\d+)+", done.stdout)
    if done.returncode != 0 or not found:
        raise InfiniteError("Infinite did not say which version it is. Reinstall it from its GitHub page.")
    return found.group(0)


def _blocked(item):
    if item["type"] in _BLOCKED_TYPES or item.get("hardware_driven") or item.get("live_input") \
            or item.get("machine_dependent"):
        return True
    return False


def _compact(described):
    """Keep what the prompt and the checks need from ``--describe --json`` (about 2.5 MB)."""
    types = {}
    for item in described.get("types", []):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            continue
        modulatable = {entry.get("key") for entry in item.get("modulatable", []) if isinstance(entry, dict)}
        controls = []
        for row in item.get("rows", []):
            if not isinstance(row, dict) or row.get("tag") not in ("f", "i", "b", "c", "s") or not row.get("key"):
                continue
            controls.append({"key": row["key"], "tag": row["tag"], "min": row.get("min"), "max": row.get("max"),
                             "options": [str(option) for option in row.get("options") or []][:24],
                             "mod": row["key"] in modulatable, "ui": bool(row.get("ui"))})
        types[item["type"]] = {
            "category": str(item.get("category", "")), "blocked": _blocked(item),
            "help": str(item.get("help") or "").split(". ")[0][:160],
            "controls": controls,
            "inputs": [{"slot": entry.get("slot"), "name": str(entry.get("name") or entry.get("slot")),
                        "kind": str(entry.get("kind"))} for entry in item.get("inputs", []) if isinstance(entry, dict)],
            "outputs": [{"output": entry.get("output"), "label": str(entry.get("label") or ""),
                         "kind": str(entry.get("kind"))} for entry in item.get("outputs", []) if isinstance(entry, dict)],
        }
    if len(types) < 20:
        raise InfiniteError("Infinite did not list its node types. Reinstall it from its GitHub page.")
    return types


def catalogue(program, folder, cancel=None):
    """Infinite's node types and controls, asked once per Infinite version and kept in ``folder``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = folder / f"catalogue-{version(program, folder)}-{_CATALOGUE_FORMAT}.json"
    try:
        types = json.loads(saved.read_text(encoding="utf-8"))
        if isinstance(types, dict) and len(types) >= 20:
            return types
    except (OSError, ValueError):
        pass
    described = folder / "describe.json"
    try:
        code, result = run(program, ["--describe", "--json", described.name], folder, DESCRIBE_TIMEOUT, cancel,
                           result_file=described.name)
    finally:
        described.unlink(missing_ok=True)
    if code != 0 or not result.get("ok"):
        raise InfiniteError("Infinite did not list its node types. Reinstall it from its GitHub page.")
    types = _compact(result)
    staging = saved.with_suffix(".tmp")
    staging.write_text(json.dumps(types), encoding="utf-8")
    paths.replace_retrying(staging, saved)
    return types


def _control_text(control):
    if control["options"]:
        detail = " (" + "|".join(control["options"]) + ")"
    elif control["min"] is not None and control["max"] is not None:
        detail = f" {control['min']:g}..{control['max']:g}"
    else:
        detail = ""
    return f"{control['tag']} {control['key']}{detail}{' ~' if control['mod'] else ''}"


def describe_type(name, item, limit=_FIX_CONTROLS, brief=False):
    """One line per node type: its category, controls, inputs and outputs.

    Choices and colours come first, then the controls Infinite shows as sliders. Infinite
    marks colours, sizes and text as "not a slider", so that mark only orders the list.
    ``brief`` (the prompt's short list) leaves picture sizes out: the clip keeps the size
    Infinite gives it."""
    controls = [control for control in item["controls"] if not control["key"].startswith("_")
                and not _LOCATION_KEY.search(control["key"])
                and not (brief and control["key"] in ("width", "height"))]
    controls.sort(key=lambda control: 0 if control["options"] else 1 if control["tag"] == "c" else 2 if control["ui"] else 3)
    shown = controls[:limit]
    text = f"{item['category']} {name}: " + (", ".join(map(_control_text, shown)) or "no controls")
    if len(controls) > limit:
        text += ", …"
    if item["inputs"]:
        text += " | inputs: " + ", ".join(f"{entry['name']} ({entry['kind']})" for entry in item["inputs"])
    if item["outputs"]:
        text += " | outputs: " + ", ".join(" ".join(filter(None, (str(entry["output"]), entry["label"])))
                                           + f" ({entry['kind']})" for entry in item["outputs"])
    return text


def system_prompt(types):
    lines = [FORMAT, "", "Node types (Category Type: controls | inputs | outputs):"]
    for group, names in MENU:
        lines.append(f"{group}:")
        lines += ["- " + describe_type(name, types[name], _MENU_CONTROLS, brief=True) for name in names
                  if name in types and not types[name]["blocked"]]
    lines += ["", EXAMPLES]
    return "\n".join(lines)


def extract_patch(reply):
    """The patch in a model's reply: the code block holding it, or the text from its first line."""
    reply = _THINKING.sub("", reply or "")
    for block in _BLOCK.findall(reply):
        if "infinite-patch" in block:
            reply = block
            break
    start = reply.find("infinite-patch")
    if start < 0:
        return ""
    return reply[start:].replace("\r\n", "\n").replace("\r", "\n").strip().removesuffix("```").strip() + "\n"


def _known_type(name, types):
    return name if name in types else next((known for known in types if known.lower() == name.lower()), None)


def _type_problem(name, types):
    allowed = [known for known, item in types.items() if not item["blocked"]]
    match = _known_type(name, types)
    if match is not None and types[match]["blocked"]:
        return f"'{name}' reaches outside the patch (files, devices, other programs or the network); use another node"
    if match is None:
        close = difflib.get_close_matches(name, allowed, n=3, cutoff=0.5)
        return f"there is no node type '{name}'" + (f"; did you mean {', '.join(close)}?" if close else "")
    return None


def _number(text):
    try:
        return float(text)
    except ValueError:
        return 0.0


def _nodes(lines, types):
    """Each node's number and id mapped to its type (None when unknown), and the line of the first wire."""
    names, inside, current, first_wire = {}, False, None, None
    for index, raw in enumerate(lines):
        words = raw.split()
        if not words or words[0].startswith("#"):
            continue
        if inside:
            if words[0] == "end":
                inside = False
            elif words[0] == "id" and len(words) > 1:
                names[words[1]] = current
            continue
        if words[0] == "node" and len(words) >= 4:
            inside, current = True, _known_type(" ".join(words[3:]), types)
            names[words[1]] = current
        elif words[0] in _RECORDS and first_wire is None:
            first_wire = index
    return names, first_wire


def _references(words):
    """The node names a top-level line refers to."""
    if words[0] in _WIRES and len(words) >= 4:
        return [words[1], words[3]]
    if words[0] in ("mod", "pal") and len(words) >= 4:
        return [words[1], words[3]]
    if words[0] == "expr" and len(words) >= 2:
        return [words[1]]
    return []


def _output_kind(item, output):
    outputs = item["outputs"]
    if output is None:
        return outputs[0]["kind"] if outputs else None
    return next((entry["kind"] for entry in outputs if str(entry["output"]) == output or entry["label"] == output), None)


def tidy(text, types):
    """Fix the slips small models make that have only one right answer; return the patch and notes.

    A control written with the wrong number tag (f for a whole-number or on/off control, or the
    reverse) gets its own tag. A name from the examples (out, speakers, view, cam, sun, mix, hear,
    lfo) that is wired but never made gets its node. A wire written with the wrong record, such as
    a sound sent with cable, gets the record its source makes, and moves to the first input of
    that kind when its slot takes another kind. Everything else is left for Infinite to report."""
    lines = text.splitlines()
    notes = []
    inside, current = False, None
    for index, raw in enumerate(lines):
        words = raw.split()
        if not words:
            continue
        if not inside:
            if words[0] == "node" and len(words) >= 4:
                inside, current = True, _known_type(" ".join(words[3:]), types)
            continue
        if words[0] == "end":
            inside = False
        elif words[0] in ("f", "i", "b") and len(words) > 2 and current:
            tag = next((control["tag"] for control in types[current]["controls"] if control["key"] == words[1]), None)
            if tag in ("f", "i", "b") and tag != words[0]:
                lines[index] = raw.replace(words[0], tag, 1)
                notes.append(f"wrote {current} {words[1]} with {tag}")

    names, first_wire = _nodes(lines, types)
    numbers = [int(name) for name in names if name.isdigit()]
    added = []
    for name in dict.fromkeys(ref for raw in lines if raw.split() for ref in _references(raw.split())):
        kind = _EXAMPLE_NODES.get(name)
        if name in names or kind is None or kind[1] not in types or types[kind[1]]["blocked"]:
            continue
        number = max(numbers, default=0) + 1
        numbers.append(number)
        added += [f"node {number} {kind[0]} {kind[1]}", f"  id {name}", "end"]
        names[name] = kind[1]
        notes.append(f"added the {kind[1]} node '{name}' that the wires use")
    if added:
        at = len(lines) if first_wire is None else first_wire
        lines[at:at] = added

    for index, raw in enumerate(lines):
        words = raw.split()
        if len(words) < 4 or words[0] not in _WIRES:
            continue
        target, source = names.get(words[1]), names.get(words[3])
        if not target or not source:
            continue
        kind = _output_kind(types[source], words[4] if len(words) > 4 else None)
        record = _WIRE_FOR_KIND.get(kind)
        if record is None:
            continue
        inputs = types[target]["inputs"]
        slot = next((entry for entry in inputs if str(entry["slot"]) == words[2] or entry["name"] == words[2]), None)
        if slot is not None and slot["kind"] != kind:
            matching = next((entry for entry in inputs if entry["kind"] == kind), None)
            if matching is not None:
                words[2] = matching["name"]
        if record != words[0] or words[2] != raw.split()[2]:
            notes.append(f"wired {words[3]} into {words[1]} {words[2]} as {kind}")
            words[0] = record
            lines[index] = " ".join(words)

    # A corrected wire can repeat one the patch already has; the same wire twice is one wire.
    seen = set()
    for index, raw in enumerate(lines):
        words = tuple(raw.split())
        if words and words[0] in _WIRES:
            if words in seen:
                lines[index] = None
                notes.append("left out a repeated wire: " + " ".join(words))
            seen.add(words)
    return "\n".join(line for line in lines if line is not None) + "\n", notes


def check_patch(text, types):
    """Problems that stop a patch before Infinite sees it, as dictionaries like Infinite's own errors."""
    problems = []
    if not text.strip():
        return [{"line": 0, "code": "E_NO_PATCH", "message": "the reply has no patch starting with infinite-patch 1"}]
    if len(text) > MAX_PATCH_CHARS:
        return [{"line": 0, "code": "E_TOO_LONG", "message": f"the patch is longer than {MAX_PATCH_CHARS:,} characters"}]
    nodes, inside, first = 0, False, True
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        record = words[0]
        if first:
            first = False
            if line != "infinite-patch 1":
                problems.append({"line": number, "code": "E_HEADER", "message": "the first line must be infinite-patch 1"})
            continue
        if inside:
            if record == "end":
                inside = False
            elif record not in _NODE_RECORDS:
                problems.append({"line": number, "code": "E_RECORD",
                                 "message": f"'{record}' is not a node control; use id, f, i, b, c or s, then end"})
            elif record == "s" and len(words) > 2 and _LOCATION_KEY.search(words[1]):
                problems.append({"line": number, "code": "E_LOCATION",
                                 "message": f"'{words[1]}' names a file, device or address; leave it out"})
            elif record == "f" and len(words) > 2 and words[1].lower() in ("width", "height") \
                    and _number(words[2]) > MAX_SIDE:
                problems.append({"line": number, "code": "E_TOO_BIG",
                                 "message": f"keep {words[1]} at {MAX_SIDE} or less"})
            continue
        if record == "node":
            nodes += 1
            inside = True
            if len(words) < 4:
                problems.append({"line": number, "code": "E_RECORD",
                                 "message": "write node <number> <Category> <Type>"})
                continue
            problem = _type_problem(" ".join(words[3:]), types)
            if problem:
                problems.append({"line": number, "code": "E_NODE_TYPE", "message": problem})
        elif record not in _RECORDS:
            problems.append({"line": number, "code": "E_RECORD",
                             "message": f"'{record}' is not a record; use node, cable, geo, aud, note, mod or expr"})
    if inside:
        problems.append({"line": 0, "code": "E_RECORD", "message": "the last node has no end line"})
    if nodes > MAX_NODES:
        problems.append({"line": 0, "code": "E_TOO_LONG", "message": f"use at most {MAX_NODES} nodes"})
    return problems


def _plain_problems(problems):
    lines = []
    for problem in problems[:12]:
        where = f"line {problem['line']}: " if problem.get("line") else ""
        hint = f" ({problem['hint']})" if problem.get("hint") else ""
        lines.append(f"- {where}{problem.get('message', problem.get('code', 'problem'))}{hint}")
    return "\n".join(lines)


def _types_in(text, types):
    found = []
    for raw in text.splitlines():
        words = raw.split()
        if len(words) >= 4 and words[0] == "node":
            name = _known_type(" ".join(words[3:]), types)
            if name and name not in found and not types[name]["blocked"]:
                found.append(name)
    return found


def fix_request(text, problems, types):
    """Ask the model for a corrected patch, with the real controls of the node types it used."""
    lines = ["Infinite found these problems in the patch:", _plain_problems(problems), "",
             "The node types in your patch and their controls:"]
    lines += ["- " + describe_type(name, types[name]) for name in _types_in(text, types)[:12]]
    lines += ["", "Reply with the whole corrected patch in one ```inf code block."]
    return "\n".join(lines)


_LOOSE_END_HINTS = {
    "image": "wire it into the picture, for example into a Layer Stack: cable mix 1 {name}; or leave it out",
    "audio": "wire it into the sound, for example aud out audio {name}; or leave it out",
    "geometry": "wire it into the Render 3D, for example geo view 0 {name}; or leave it out",
}


def _loose_ends(result, text, types):
    """Infinite's warnings about pictures, 3D shapes or sounds that reach no Output, as problems."""
    lines = text.splitlines()
    names, _ = _nodes(lines, types)
    ids = {}  # node number -> its id, so a hint can name the node the way the patch does
    for index, raw in enumerate(lines):
        words = raw.split()
        if len(words) >= 2 and words[0] == "node" and index + 1 < len(lines):
            following = lines[index + 1].split()
            if len(following) == 2 and following[0] == "id":
                ids[words[1]] = following[1]
    problems = []
    for warning in result.get("warnings", []):
        if not isinstance(warning, dict) or warning.get("code") != "W_UNUSED_NODE":
            continue
        number = str(warning.get("node"))
        kind = names.get(number)
        made = [entry["kind"] for entry in types[kind]["outputs"] if entry["kind"] in _CONTENT_KINDS] if kind else []
        if made:
            hint = _LOOSE_END_HINTS[made[0]].format(name=ids.get(number, number))
            problems.append({**warning, "hint": hint})
    return problems


def clean_description(description):
    if not isinstance(description, str) or not description.strip() or len(description) > MAX_DESCRIPTION_CHARS:
        raise InfiniteError(f"Describe the clip using 1 to {MAX_DESCRIPTION_CHARS:,} characters.")
    return description.strip()


def make_clip(description, ask, program, folder, *, seconds=4, types=None, progress=None, cancel=None):
    """Have ``ask(messages) -> reply`` write a patch, check and fix it with Infinite, then render it.

    ``progress(stage, **values)`` hears each step: catalogue, writing, fixing (attempt, rounds),
    checking and rendering (seconds). Returns the patch text, the path of the MP4 in ``folder``, how many replies it took and
    Infinite's warnings. A patch that still fails after FIX_ROUNDS corrections is an error."""
    description = clean_description(description)
    if seconds not in LENGTHS:
        raise InfiniteError("Choose a length of 4, 8 or 12 seconds.")
    report = progress or (lambda stage, **values: None)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if types is None:
        report("catalogue")
        types = catalogue(program, folder.parent, cancel)
    network_gate.note_outside("Infinite", "Check and render a patch written for your description",
                              "Make a clip on this computer with the Infinite app.")
    patch_path = folder / "patch.inf"
    messages = [{"role": "system", "content": system_prompt(types)},
                {"role": "user", "content": f"Make a {seconds}-second clip: {description}"}]
    for attempt in range(FIX_ROUNDS + 1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Stopped.")
        if attempt:
            report("fixing", attempt=attempt, rounds=FIX_ROUNDS)
        else:
            report("writing")
        reply = ask(messages)
        text = extract_patch(reply)
        if text:
            text, _ = tidy(text, types)
        problems = check_patch(text, types)
        if not problems:
            patch_path.write_text(text, encoding="utf-8")
            report("checking")
            code, result = run(program, ["--validate", patch_path.name, "--for-render"], folder, CHECK_TIMEOUT, cancel)
            if code == 0 and result.get("ok"):
                # A picture or sound that reaches nothing is usually a forgotten wire; the last round renders anyway.
                problems = [] if attempt == FIX_ROUNDS else _loose_ends(result, text, types)
            else:
                problems = [error for error in result.get("errors", []) if isinstance(error, dict)] or \
                    [{"line": 0, "message": "the patch needs a camera, microphone or MIDI device" if code == 4
                      else f"Infinite refused the patch (exit code {code})"}]
        if not problems:
            break
        if attempt == FIX_ROUNDS:
            if text:
                patch_path.write_text(text, encoding="utf-8")
            raise InfiniteError("The model could not write a patch that Infinite accepts. Try a simpler "
                                "description, or choose a bigger model. Last problem: "
                                + _plain_problems(problems[:1]).removeprefix("- "))
        # Only the patch goes back, not the model's reasoning, so each round stays small.
        messages += [{"role": "assistant", "content": f"```inf\n{text}```" if text else reply[:2000]},
                     {"role": "user", "content": fix_request(text, problems, types)}]
    video = folder / "clip.mp4"
    report("rendering", seconds=seconds)
    code, result = run(program, ["--render", patch_path.name, video.name, "--duration", seconds, "--fps", FPS,
                                 "--timeout", RENDER_TIMEOUT], folder, RENDER_TIMEOUT, cancel)
    if code != 0 or not result.get("ok"):
        raise InfiniteError("Infinite could not render the clip: "
                            + (_plain_problems(result.get("errors", [])[:1]).removeprefix("- ") or f"exit code {code}"))
    if not video.is_file() or video.stat().st_size > MAX_VIDEO_BYTES:
        raise InfiniteError("Infinite did not write a clip of 24 MiB or less.")
    with open(video, "rb") as header:
        if header.read(12)[4:8] != b"ftyp":
            raise InfiniteError("Infinite wrote a file that is not an MP4.")
    warnings = [str(item.get("message") or item.get("code")) for item in result.get("warnings", [])
                if isinstance(item, dict)][:5]
    credit = result.get("attribution", {}).get("credit_line") if isinstance(result.get("attribution"), dict) else None
    return {"patch": text, "video": str(video), "replies": attempt + 1, "warnings": warnings,
            "sound": bool((result.get("audio") or {}).get("frames")), "credit": credit or CREDIT}
