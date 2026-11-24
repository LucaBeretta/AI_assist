"""The person's own 3D character: choices, Blender availability and the finished portrait."""

import base64
import json

import character_builder
import developer_tools
from webui.api_utils import json_method

_PORTRAIT_LIMIT = 8 * 1024 * 1024


def _portrait_url(folder):
    path = folder / "portrait.png"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _PORTRAIT_LIMIT:
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _saved_choices(folder):
    try:
        choices = character_builder.validate(json.loads((folder / "character.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    # Saved skin is a colour; show the tone it came from when it is one of the offered tones.
    tones = {colour: name for name, colour in character_builder.SKIN_TONES.items()}
    choices["skin"] = tones.get(choices["skin"], choices["skin"])
    return choices


class CharacterApi:
    """Mixed into the desktop API. Blender runs in the background, on this computer only."""

    def _character_folder(self):
        return self._store.directory / "character"

    @json_method
    def character_state(self):
        folder = self._character_folder()
        return {"ok": True, "blender": bool(self._find_blender()), "choices": _saved_choices(folder),
                "portrait": _portrait_url(folder), "building": self._jobs.busy("character"),
                "options": {"height": character_builder.HEIGHTS, "build": character_builder.BUILDS,
                            "hair_style": character_builder.HAIR_STYLES,
                            "accessory": character_builder.ACCESSORIES,
                            "skin": list(character_builder.SKIN_TONES)}}

    @json_method
    def build_character(self, choices):
        character_builder.validate(choices)  # refuse bad choices now, not after Blender starts
        blender = self._find_blender()
        if not blender:
            raise ValueError("Blender is not installed on this computer. Install it from blender.org, "
                             "or skip this step for now.")
        folder = self._character_folder()

        def work(progress, cancel):
            progress(status="Blender is building your character…", cancellable=False)
            made = self._build_character(choices, folder, blender=blender)
            return {"portrait": _portrait_url(folder), "files": made, "folder": str(folder)}

        return self._jobs.start("character", work, key="character")

    def _find_blender(self):
        return developer_tools.find_blender()

    def _build_character(self, choices, folder, blender):
        return character_builder.build(choices, folder, blender=blender)
