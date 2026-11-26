"""The person's own 3D character: checked choices, a background Blender run, and its files."""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import character_builder as cb


def glb(nodes, binary=b"\x01\x02\x03\x04"):
    """A small glTF binary with the given node names, like Blender's export."""
    text = json.dumps({"asset": {"version": "2.0"}, "nodes": [{"name": name} for name in nodes]}).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def read_glb(data):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    size, kind = struct.unpack_from("<I4s", data, 12)
    binary_size, binary_kind = struct.unpack_from("<I4s", data, 20 + size)
    return (magic, version, length, kind, size, json.loads(data[20:20 + size]), binary_kind,
            data[28 + size:28 + size + binary_size])


class VrmTests(unittest.TestCase):
    def test_the_glb_becomes_a_vrm_avatar_with_every_humanoid_bone(self):
        names = ["Avatar"] + list(cb.VRM_BONES) + ["Luca"]
        avatar = cb.vrm_from_glb(glb(names), "Luca")
        magic, version, length, kind, size, document, binary_kind, binary = read_glb(avatar)
        self.assertEqual((magic, version, length, kind, binary_kind), (b"glTF", 2, len(avatar), b"JSON", b"BIN\x00"))
        self.assertEqual(size % 4, 0)
        self.assertEqual(binary, b"\x01\x02\x03\x04")  # the mesh data is untouched
        self.assertIn("VRMC_vrm", document["extensionsUsed"])
        vrm = document["extensions"]["VRMC_vrm"]
        self.assertEqual(vrm["specVersion"], "1.0")
        self.assertEqual(vrm["meta"]["name"], "Luca")
        self.assertEqual(vrm["meta"]["licenseUrl"], "https://vrm.dev/licenses/1.0/")
        self.assertEqual(vrm["humanoid"]["humanBones"]["hips"], {"node": 1})
        self.assertEqual(vrm["humanoid"]["humanBones"]["rightFoot"], {"node": names.index("rightFoot")})
        self.assertEqual(len(vrm["humanoid"]["humanBones"]), 17)
        self.assertEqual(cb.vrm_from_glb(glb(names))[:4], b"glTF")  # an unnamed character still gets a name

    def test_files_without_a_skeleton_or_not_gltf_are_refused(self):
        with self.assertRaisesRegex(ValueError, "leftFoot"):
            cb.vrm_from_glb(glb([bone for bone in cb.VRM_BONES if bone != "leftFoot"]))
        for data in (b"", b"glTF" + b"\0" * 8, glb(cb.VRM_BONES)[:-1]):
            with self.assertRaises((ValueError, struct.error)):
                cb.vrm_from_glb(data)


class ChoiceTests(unittest.TestCase):
    def test_choices_merge_over_defaults_and_skin_tones_become_colours(self):
        spec = cb.validate({"name": "  Luca ", "skin": "tan", "hair_color": "#ABCDEF", "accessory": "glasses"})
        self.assertEqual(spec["name"], "Luca")
        self.assertEqual(spec["skin"], cb.SKIN_TONES["tan"])
        self.assertEqual(spec["hair_color"], "#abcdef")
        self.assertEqual(spec["height"], "medium")
        self.assertEqual(set(spec), set(cb.DEFAULTS))

    def test_anything_outside_the_known_options_is_refused(self):
        for choices in ({"height": "giant"}, {"hair_style": "mohawk"}, {"accessory": "sword"}, {"skin": "blue"},
                        {"top_color": "red"}, {"eye_color": "#12345"}, {"name": "x" * 41}, {"name": "tab\there"},
                        {"script": "import os"}, []):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                cb.validate(choices)

    def test_a_model_description_only_fills_valid_choices(self):
        reply = ('Sure! {"height": "tall", "hair_style": "Curly", "hair_color": "#1A1412", "skin": "deep", '
                 '"accessory": "laser", "name": "Evil", "script": "rm -rf /", "eye_color": "brown"} Hope that helps.')
        self.assertEqual(cb.choices_from_model_reply(reply),
                         {"height": "tall", "hair_style": "curly", "hair_color": "#1a1412", "skin": "deep"})
        for junk in ("", "no json here", "{broken", "[1, 2]", None):
            self.assertEqual(cb.choices_from_model_reply(junk), {})


class BuildTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name).resolve() / "character"

    def fake_blender(self, files=("portrait.png", "character.blend", "character.glb"), code=0, done=True):
        calls = []

        def run(command, **options):
            calls.append(command)
            staging = Path(command[-1])
            spec = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
            for name in files:
                (staging / name).write_bytes(name.encode() + spec["top_color"].encode())
            return subprocess.CompletedProcess(command, code, "AI_ASSIST_CHARACTER_DONE\n" if done else "",
                                               "" if code == 0 else "Error: something broke\n")
        return calls, run

    def test_blender_runs_in_the_background_and_its_files_replace_the_old_character(self):
        calls, run = self.fake_blender()
        with patch("character_builder.subprocess.run", side_effect=run):
            made = cb.build({"top_color": "#112233"}, self.output, blender="/opt/blender/blender")
        command = calls[0]
        self.assertEqual(command[:5], ["/opt/blender/blender", "--background", "--factory-startup", "--python", str(cb.SCRIPT)])
        self.assertEqual(sorted(made), ["blend", "model", "portrait"])
        self.assertEqual(Path(made["portrait"]).read_bytes(), b"portrait.png#112233")
        self.assertEqual(json.loads((self.output / "character.json").read_text())["top_color"], "#112233")
        # A later build without a 3D model removes the old one, so files never mix two characters.
        calls, run = self.fake_blender(files=("portrait.png", "character.blend"))
        with patch("character_builder.subprocess.run", side_effect=run):
            made = cb.build({"top_color": "#445566"}, self.output, blender="/opt/blender/blender")
        self.assertNotIn("model", made)
        self.assertFalse((self.output / "character.glb").exists())

    def test_a_skeleton_glb_also_saves_a_vrm_and_a_later_build_without_one_removes_it(self):
        def run(command, **options):
            staging = Path(command[-1])
            for name in ("portrait.png", "character.blend"):
                (staging / name).write_bytes(b"x")
            (staging / "character.glb").write_bytes(glb(cb.VRM_BONES))
            return subprocess.CompletedProcess(command, 0, "AI_ASSIST_CHARACTER_DONE\n", "")
        with patch("character_builder.subprocess.run", side_effect=run):
            made = cb.build({"name": "Luca"}, self.output, blender="/opt/blender/blender")
        self.assertEqual(sorted(made), ["avatar", "blend", "model", "portrait"])
        self.assertEqual(read_glb(Path(made["avatar"]).read_bytes())[5]["extensions"]["VRMC_vrm"]["meta"]["name"], "Luca")
        calls, run = self.fake_blender(files=("portrait.png", "character.blend"))
        with patch("character_builder.subprocess.run", side_effect=run):
            made = cb.build({}, self.output, blender="/opt/blender/blender")
        self.assertNotIn("avatar", made)
        self.assertFalse((self.output / "character.vrm").exists())

    def test_failures_explain_what_to_do_and_keep_the_previous_character(self):
        calls, run = self.fake_blender()
        with patch("character_builder.subprocess.run", side_effect=run):
            cb.build({}, self.output, blender="/opt/blender/blender")
        before = (self.output / "portrait.png").read_bytes()
        with patch("character_builder.developer_tools.find_blender", return_value=None):
            with self.assertRaisesRegex(cb.CharacterError, "blender.org"):
                cb.build({}, self.output)
        for kwargs in ({"code": 1}, {"done": False}, {"files": ("character.blend",)}):
            calls, run = self.fake_blender(**kwargs)
            with self.subTest(**{key: str(value) for key, value in kwargs.items()}), \
                    patch("character_builder.subprocess.run", side_effect=run):
                with self.assertRaisesRegex(cb.CharacterError, "could not finish"):
                    cb.build({"top_color": "#000000"}, self.output, blender="/opt/blender/blender")
        with patch("character_builder.subprocess.run", side_effect=subprocess.TimeoutExpired("blender", 300)):
            with self.assertRaisesRegex(cb.CharacterError, "did not finish within 5 minutes"):
                cb.build({}, self.output, blender="/opt/blender/blender")
        self.assertEqual((self.output / "portrait.png").read_bytes(), before)

    def test_bad_choices_never_start_blender(self):
        with patch("character_builder.subprocess.run") as run:
            with self.assertRaises(ValueError):
                cb.build({"hair_style": "mohawk"}, self.output, blender="/opt/blender/blender")
        run.assert_not_called()


@unittest.skipUnless(shutil.which("blender"), "Blender is not installed here")
class RealBlenderTests(unittest.TestCase):
    def test_blender_builds_a_character_with_glasses_and_long_hair(self):
        with tempfile.TemporaryDirectory() as folder:
            made = cb.build({"name": "Test", "hair_style": "long", "accessory": "glasses"}, Path(folder) / "character")
            portrait = Path(made["portrait"]).read_bytes()
            self.assertEqual(portrait[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(len(portrait), 20000)
            self.assertGreater(Path(made["blend"]).stat().st_size, 100000)
            if "model" in made:  # Blender builds without numpy cannot export glTF
                document = read_glb(Path(made["model"]).read_bytes())[5]
                joints = {document["nodes"][index]["name"] for index in document["skins"][0]["joints"]}
                self.assertEqual(joints, set(cb.VRM_BONES))
                bones = read_glb(Path(made["avatar"]).read_bytes())[5]["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]
                self.assertEqual(set(bones), set(cb.VRM_BONES))


if __name__ == "__main__":
    unittest.main()
