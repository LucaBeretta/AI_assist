"""Create the person's own 3D character with the Blender installed on this computer.

The choices are checked here, then blender_character.py builds the character inside
Blender, started in the background. Nothing is downloaded and nothing goes online:
Blender only reads the choices and writes three files into the output folder.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import developer_tools
import network_gate

SCRIPT = Path(__file__).resolve().with_name("blender_character.py")
OUTPUTS = ("portrait.png", "character.blend")
OPTIONAL_OUTPUTS = ("character.glb",)  # glTF export needs numpy, which some Linux Blender builds lack
TIMEOUT_SECONDS = 300

HEIGHTS = ("short", "medium", "tall")
BUILDS = ("slim", "average", "broad")
HAIR_STYLES = ("none", "short", "long", "bun", "curly", "spiky")
ACCESSORIES = ("none", "glasses", "hat", "headphones")
SKIN_TONES = {"porcelain": "#f6dcc9", "light": "#f0c9a8", "medium": "#d9a07a",
              "tan": "#b97c55", "brown": "#8a5636", "deep": "#5a3624"}
COLOR_FIELDS = ("skin", "hair_color", "eye_color", "top_color", "bottom_color", "shoe_color", "backdrop")
DEFAULTS = {"name": "", "height": "medium", "build": "average", "skin": SKIN_TONES["light"],
            "hair_style": "short", "hair_color": "#3b2a20", "eye_color": "#3d6b8f",
            "top_color": "#0e8c78", "bottom_color": "#2f3a4a", "shoe_color": "#f2f2f2",
            "accessory": "none", "backdrop": "#dde6ea"}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{0,40}")


class CharacterError(RuntimeError):
    """The character could not be made; the message says what to do."""


def validate(choices):
    """Merge ``choices`` over the defaults and refuse anything outside the known options."""
    if not isinstance(choices, dict):
        raise ValueError("Character choices must be an object.")
    unknown = set(choices) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown character choice: {sorted(unknown)[0]}.")
    spec = {**DEFAULTS, **choices}
    if not isinstance(spec["name"], str) or not _NAME.fullmatch(spec["name"].strip()):
        raise ValueError("The character's name can be up to 40 characters.")
    spec["name"] = spec["name"].strip()
    for field, options in (("height", HEIGHTS), ("build", BUILDS), ("hair_style", HAIR_STYLES),
                           ("accessory", ACCESSORIES)):
        if spec[field] not in options:
            raise ValueError(f"Choose a {field.replace('_', ' ')} from: {', '.join(options)}.")
    if spec["skin"] in SKIN_TONES:
        spec["skin"] = SKIN_TONES[spec["skin"]]
    for field in COLOR_FIELDS:
        if not isinstance(spec[field], str) or not _HEX.fullmatch(spec[field]):
            raise ValueError(f"The {field.replace('_', ' ')} must be a colour like #3b2a20.")
        spec[field] = spec[field].lower()
    return spec


DESCRIBE_PROMPT = (
    "Turn the person's description of themselves into character choices for a cute 3D avatar. "
    "Answer with one JSON object and nothing else, using only these keys: "
    f"height ({'/'.join(HEIGHTS)}), build ({'/'.join(BUILDS)}), skin ({'/'.join(SKIN_TONES)}), "
    f"hair_style ({'/'.join(HAIR_STYLES)}), hair_color, eye_color, top_color, bottom_color, shoe_color "
    f"(colours as #rrggbb), accessory ({'/'.join(ACCESSORIES)}). Leave out what the description doesn't say."
)


def choices_from_model_reply(reply):
    """Keep only the valid choices a model suggested from a description; ignore everything else."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    try:
        suggested = json.loads(match.group(0)) if match else {}
    except ValueError:
        suggested = {}
    choices = {}
    for key, value in (suggested.items() if isinstance(suggested, dict) else ()):
        if key in DEFAULTS and key not in ("name", "backdrop"):
            try:
                validate({key: value.lower() if isinstance(value, str) else value})
            except ValueError:
                continue
            choices[key] = value.lower() if isinstance(value, str) else value
    return choices


def build(choices, output_folder, blender=None, timeout=TIMEOUT_SECONDS):
    """Make the character in Blender and return the paths of its three files."""
    spec = validate(choices)
    blender = blender or developer_tools.find_blender()
    if not blender:
        raise CharacterError("Blender is not installed on this computer. Install it from blender.org, "
                             "then create your character again. You can skip this step for now.")
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="ai-assist-character-") as work:
        # Blender writes into a fresh folder; the finished files then replace the old character at once.
        staging = Path(work) / "out"
        staging.mkdir()
        spec_path = Path(work) / "character.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        command = [blender, "--background", "--factory-startup", "--python", str(SCRIPT), "--",
                   str(spec_path), str(staging)]
        network_gate.note_outside("Blender", "Build your 3D character", "Create the character you designed, on this computer.")
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, **options)
        except subprocess.TimeoutExpired as error:
            raise CharacterError(f"Blender did not finish within {timeout // 60} minutes.") from error
        except OSError as error:
            raise CharacterError(f"Blender could not be started: {error}") from error
        missing = [name for name in OUTPUTS if not (staging / name).is_file()]
        if result.returncode != 0 or "AI_ASSIST_CHARACTER_DONE" not in result.stdout or missing:
            detail = (result.stderr or result.stdout).strip().splitlines()[-3:]
            raise CharacterError("Blender could not finish the character. " + " ".join(detail)[:400])
        files = {"portrait": "portrait.png", "blend": "character.blend", "model": "character.glb"}
        made = {}
        for key, name in files.items():
            if (staging / name).is_file():
                os.replace(staging / name, output / name)
                made[key] = str(output / name)
            elif key == "model":
                (output / name).unlink(missing_ok=True)  # never keep an old model beside a new character
    (output / "character.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return made
