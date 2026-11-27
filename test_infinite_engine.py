"""Infinite clips: patches are checked before Infinite sees them, and Infinite's own report drives the fixes.

A small stand-in program answers like Infinite's command line, so these tests run without
Infinite. RealInfiniteTests renders the prompt's examples when Infinite is installed here.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import infinite_engine as ie

FAKE_INFINITE = r'''
import json, os, sys, time
from pathlib import Path
config = json.loads(Path(os.environ["FAKE_INFINITE"]).read_text(encoding="utf-8"))
args = sys.argv[1:]
with open(config["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args[0] == "--version":
    print(config.get("version", "2.1.0"))
    sys.exit(0)
mode = args[0].lstrip("-")
counter = Path(config["log"] + "." + mode)
count = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count + 1))
answers = config.get(mode) or [{}]
answer = answers[min(count, len(answers) - 1)]
print("Infinite log line")
time.sleep(answer.get("sleep", 0))
result = {"ok": answer.get("code", 0) == 0, "errors": [], "warnings": [], **answer.get("result", {})}
if mode == "describe":
    Path(args[args.index("--json") + 1]).write_text(json.dumps(result), encoding="utf-8")
if mode == "render" and answer.get("write", True):
    Path(args[2]).write_bytes(answer.get("bytes", "\x00\x00\x00\x18ftypmp42").encode("latin-1") + b"\x00" * 64)
if answer.get("json", True):
    print(json.dumps(result))
sys.exit(answer.get("code", 0))
'''


# Type: (category, inputs as (name, kind), output kinds), as Infinite 2.1 describes them.
NODE_TYPES = {
    "Shape": ("Source", [], ["image"]),
    "LFO": ("Modulators", [("trig", "modulator")], ["modulator"]),
    "Output": ("Utility", [("in", "image"), ("audio", "audio")], ["image"]),
    "Audio Out": ("Utility", [("audio", "audio")], ["image"]),
    "Layer Stack": ("Compositing", [("input", "image"), ("input_2", "image")], ["image"]),
    "Analog": ("Synths", [("notes", "note")], ["audio"]),
    "Delay": ("AudioEffects", [("audio", "audio")], ["audio"]),
    "Torus Knot": ("3D", [("texture", "image")], ["geometry"]),
    "Camera": ("3D", [], ["camera"]),
    "Light": ("3D", [], ["light"]),
    "Render 3D": ("3D", [("geo_a", "geometry"), ("camera", "camera"), ("light_1", "light")], ["image"]),
    "OSC Send": ("Utility", [], []),
    "Video In": ("Source", [], ["image"]),
    **{f"effect {n}": ("Effects", [("input", "image")], ["image"]) for n in range(20)},
}


def described():
    """A ``--describe --json`` answer shaped like Infinite 2.1's, with 33 node types."""
    rows = [{"key": "shapeType", "tag": "i", "min": 0, "max": 2, "options": ["Circle", "Star"], "ui": True},
            {"key": "size", "tag": "f", "min": 0.01, "max": 1, "options": [], "ui": True},
            {"key": "fillColor", "tag": "c", "options": [], "ui": False},  # Infinite marks colours "not a slider"
            {"key": "width", "tag": "f", "min": 16, "max": 4096, "options": [], "ui": True},
            {"key": "wave1", "tag": "i", "min": 0, "max": 1, "options": ["saw", "sine"], "ui": True},
            {"key": "recordVideoPath", "tag": "s", "options": [], "ui": False},
            {"key": "text", "tag": "s", "options": [], "ui": False},
            {"key": "__outputPins", "tag": "s", "options": [], "ui": True}]
    return {"ok": True, "mode": "describe", "types": [
        {"type": name, "category": category, "hardware_driven": name == "Video In", "live_input": name == "Video In",
         "machine_dependent": False, "help": f"The {name} node. More detail.", "rows": rows,
         "modulatable": [{"key": "size"}],
         "inputs": [{"slot": slot, "kind": kind, "name": label} for slot, (label, kind) in enumerate(inputs)],
         "outputs": [{"output": number, "kind": kind, "label": "out"} for number, kind in enumerate(outputs)]}
        for name, (category, inputs, outputs) in NODE_TYPES.items()]}


GOOD = """infinite-patch 1
# a star that grows
node 1 Source Shape
  id shape
  i shapeType Star
  s text hello
end
node 2 Modulators LFO
  id lfo
end
node 3 Utility Output
  id out
end
cable out 0 shape
mod shape size lfo 0 0 1 0.5 0.2 0.4
expr shape size sin(t)
"""


class FakeInfinite:
    """Writes the stand-in program and its answers into a temporary folder."""

    def __init__(self, test, **answers):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        script = self.root / "fake_infinite.py"
        script.write_text(FAKE_INFINITE, encoding="utf-8")
        self.program = [sys.executable, str(script)]
        self.log = self.root / "calls.log"
        self.config = self.root / "config.json"
        self.answers = {"log": str(self.log), "describe": [{"result": described()}], **answers}
        self.save()
        environment = patch.dict(os.environ, {"FAKE_INFINITE": str(self.config)})
        environment.start()
        test.addCleanup(environment.stop)
        self.work = self.root / "work"
        self.work.mkdir()

    def save(self):
        self.config.write_text(json.dumps(self.answers), encoding="utf-8")

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


class FindTests(unittest.TestCase):
    def test_a_chosen_copy_and_a_mac_app_folder_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            program = Path(folder) / "Infinite.app" / "Contents" / "MacOS" / "Infinite"
            program.parent.mkdir(parents=True)
            program.write_text("")
            program.chmod(0o755)
            for chosen in (str(program), str(Path(folder) / "Infinite.app")):
                with patch.dict(os.environ, {"AI_ASSIST_INFINITE": chosen}):
                    self.assertEqual(ie.find_infinite(), os.path.abspath(program))

    def test_nothing_installed_finds_nothing(self):
        clean = {key: value for key, value in os.environ.items()
                 if key not in ("AI_ASSIST_INFINITE", "INFINITE_BIN", "INFINITE_APP")}
        with tempfile.TemporaryDirectory() as folder:
            clean.update(ProgramFiles=folder, LOCALAPPDATA=folder)
            with patch.dict(os.environ, clean, clear=True), patch.object(ie.shutil, "which", return_value=None), \
                    patch.object(ie.sys, "platform", "linux"):
                self.assertIsNone(ie.find_infinite())


class CheckPatchTests(unittest.TestCase):
    def setUp(self):
        self.types = ie._compact(described())

    def codes(self, text):
        return [problem["code"] for problem in ie.check_patch(text, self.types)]

    def test_a_plain_patch_passes(self):
        self.assertEqual(ie.check_patch(GOOD, self.types), [])

    def test_nodes_that_reach_outside_the_patch_are_refused(self):
        self.assertTrue(self.types["OSC Send"]["blocked"])
        self.assertTrue(self.types["Video In"]["blocked"])  # Infinite marks it hardware-driven
        for name in ("OSC Send", "Video In", "osc send"):
            problems = ie.check_patch(GOOD.replace("Modulators LFO", f"Utility {name}"), self.types)
            self.assertEqual([problem["code"] for problem in problems], ["E_NODE_TYPE"])
            self.assertIn("reaches outside", problems[0]["message"])

    def test_file_paths_and_addresses_are_refused(self):
        for line in ("s recordVideoPath /Users/me/Desktop/x.mov", "s exportImagePath ../x.png", "s host 10.0.0.2",
                     "s filePath C:\\x.wav"):
            self.assertEqual(self.codes(GOOD.replace("  s text hello", "  " + line)), ["E_LOCATION"], line)

    def test_unknown_records_types_and_shapes_are_reported_plainly(self):
        self.assertEqual(self.codes(GOOD.replace("infinite-patch 1", "infinite-patch 2")), ["E_HEADER"])
        self.assertEqual(self.codes(GOOD + "include other.inf\n"), ["E_RECORD"])
        self.assertEqual(self.codes(GOOD.replace("  id lfo", "  data abc")), ["E_RECORD"])
        self.assertEqual(self.codes(GOOD.replace("  id shape", "  f width 8000")), ["E_TOO_BIG"])
        self.assertEqual(self.codes(GOOD.replace("  id shape", "  f width 1280")), [])
        self.assertEqual(self.codes(GOOD.rsplit("end", 1)[0]), ["E_RECORD"])  # the last node has no end
        problems = ie.check_patch(GOOD.replace("Source Shape", "Source Shap"), self.types)
        self.assertIn("did you mean Shape", problems[0]["message"])
        many = "infinite-patch 1\n" + "".join(f"node {n} Source Shape\nend\n" for n in range(ie.MAX_NODES + 1))
        self.assertEqual(self.codes(many), ["E_TOO_LONG"])
        self.assertEqual(self.codes(""), ["E_NO_PATCH"])

    def test_the_patch_is_found_in_a_reply(self):
        for reply in (f"Here it is:\n```inf\n{GOOD}```\nEnjoy!", f"<think>first a plan</think>\n```\n{GOOD}```",
                      GOOD, f"Sure.\n{GOOD}"):
            self.assertEqual(ie.extract_patch(reply), GOOD)
        self.assertEqual(ie.extract_patch("I cannot help with that."), "")
        self.assertEqual(ie.extract_patch(None), "")

    def test_the_prompt_lists_safe_types_and_controls_only(self):
        prompt = ie.system_prompt(self.types)
        self.assertIn("Source Shape: i shapeType (Circle|Star), i wave1 (saw|sine), c fillColor, f size 0.01..1 ~", prompt)
        self.assertIn("Utility Output", prompt)
        self.assertNotIn("OSC Send", prompt)
        self.assertNotIn("recordVideoPath", prompt)
        self.assertNotIn("__outputPins", prompt)
        self.assertNotIn("f width", prompt)  # the short list leaves picture sizes out
        self.assertIn("f width 16..4096", ie.describe_type("Shape", self.types["Shape"]))  # fixes show everything


class TidyTests(unittest.TestCase):
    def setUp(self):
        self.types = ie._compact(described())

    def test_slips_with_one_right_answer_are_fixed_before_infinite_sees_them(self):
        patch_text = """infinite-patch 1
node 1 Synths Analog
  id synth
  f wave1 saw
end
node 2 AudioEffects Delay
  id delay
end
node 3 3D Torus Knot
  id knot
end
node 4 Utility Output
  id out
end
cable delay 0 synth
cable out 0 delay
aud out audio delay
aud speakers 0 delay
geo view 0 knot
geo view 0 cam
cable out 0 view
"""
        fixed, notes = ie.tidy(patch_text, self.types)
        lines = fixed.splitlines()
        self.assertIn("  i wave1 saw", lines)
        self.assertEqual(lines[lines.index("  id speakers") - 1], "node 5 Utility Audio Out")
        self.assertIn("node 6 3D Render 3D", lines)
        self.assertIn("node 7 3D Camera", lines)
        self.assertLess(lines.index("node 7 3D Camera"), lines.index("aud delay 0 synth"))  # nodes come first
        self.assertIn("aud delay 0 synth", lines)
        self.assertEqual(lines.count("aud out audio delay"), 1)  # corrected, and the repeat left out
        self.assertIn("geo view camera cam", lines)  # a camera goes to the camera input
        self.assertIn("geo view 0 knot", lines)
        self.assertNotIn("sun", fixed)  # only names the patch uses are added
        self.assertTrue(any("repeated wire" in note for note in notes))
        self.assertEqual(ie.check_patch(fixed, self.types), [])

    def test_unknown_names_and_blocked_types_are_left_for_the_checks(self):
        patch_text = GOOD + "cable out 0 nowhere\n"
        self.assertEqual(ie.tidy(patch_text, self.types), (patch_text, []))
        types = {**self.types, "Audio Out": {**self.types["Audio Out"], "blocked": True}}
        fixed, notes = ie.tidy(GOOD + "aud speakers 0 shape\n", types)
        self.assertNotIn("Audio Out", fixed)
        self.assertEqual(notes, [])

    def test_only_pictures_shapes_and_sounds_that_reach_nothing_are_loose_ends(self):
        result = {"warnings": [{"code": "W_UNUSED_NODE", "node": 1, "line": 2, "message": "Shape does not reach an Output"},
                               {"code": "W_UNUSED_NODE", "node": 2, "line": 8, "message": "LFO does not reach an Output"},
                               {"code": "W_CLIPPING", "message": "too loud"}]}
        problems = ie._loose_ends(result, GOOD, self.types)
        self.assertEqual([problem["message"] for problem in problems], ["Shape does not reach an Output"])
        self.assertEqual(problems[0]["hint"], "wire it into the picture, for example into a Layer Stack: cable mix 1 shape; or leave it out")


class RunTests(unittest.TestCase):
    def test_the_last_json_line_is_the_result_and_the_log_is_removed(self):
        fake = FakeInfinite(self, validate=[{"code": 3, "result": {"errors": [{"line": 3, "message": "bad"}]}}])
        code, result = ie.run(fake.program, ["--validate", "patch.inf"], fake.work, 10)
        self.assertEqual(code, 3)
        self.assertEqual(result["errors"][0]["message"], "bad")
        self.assertEqual(list(fake.work.iterdir()), [])

    def test_a_refused_command_line_or_a_missing_result_is_an_error(self):
        fake = FakeInfinite(self, validate=[{"code": 2, "result": {"errors": [{"message": "unknown option --x"}]}},
                                            {"code": 5, "json": False}])
        with self.assertRaisesRegex(ie.InfiniteError, "did not accept.*unknown option --x"):
            ie.run(fake.program, ["--validate", "patch.inf"], fake.work, 10)
        with self.assertRaisesRegex(ie.InfiniteError, "without a result \\(exit code 5\\)"):
            ie.run(fake.program, ["--validate", "patch.inf"], fake.work, 10)

    def test_stop_and_the_last_resort_deadline_end_the_program(self):
        fake = FakeInfinite(self, validate=[{"sleep": 30}])
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        with self.assertRaises(InterruptedError):
            ie.run(fake.program, ["--validate", "patch.inf"], fake.work, 60, cancel)
        with patch.object(ie, "_GRACE_SECONDS", 0), \
                self.assertRaisesRegex(ie.InfiniteError, "did not finish within 1 seconds"):
            ie.run(fake.program, ["--validate", "patch.inf"], fake.work, 1)

    def test_the_node_list_is_read_once_per_version(self):
        fake = FakeInfinite(self)
        types = ie.catalogue(fake.program, fake.work)
        self.assertEqual(len(types), len(NODE_TYPES))
        self.assertEqual(ie.catalogue(fake.program, fake.work), types)
        self.assertEqual([call[0] for call in fake.calls()], ["--version", "--describe", "--version"])
        self.assertTrue((fake.work / "catalogue-2.1.0-2.json").is_file())
        self.assertFalse((fake.work / "describe.json").exists())
        fake.answers["version"] = "2.2.0"
        fake.save()
        ie.catalogue(fake.program, fake.work)
        self.assertEqual(fake.calls()[-1][0], "--describe")

    def test_a_short_node_list_is_an_error(self):
        fake = FakeInfinite(self, describe=[{"result": {"types": []}}])
        with self.assertRaisesRegex(ie.InfiniteError, "did not list its node types"):
            ie.catalogue(fake.program, fake.work)


class MakeClipTests(unittest.TestCase):
    def setUp(self):
        self.types = ie._compact(described())
        self.asked = []
        self.stages = []

    def ask_with(self, *replies):
        replies = list(replies)

        def ask(messages):
            self.asked.append([dict(message) for message in messages])
            return replies.pop(0)
        return ask

    def make(self, fake, ask, **options):
        return ie.make_clip("a star that grows", ask, fake.program, fake.work / "clip", types=self.types,
                            progress=lambda stage, **values: self.stages.append((stage, values)), **options)

    def test_problems_go_back_to_the_model_until_infinite_accepts_the_patch(self):
        fake = FakeInfinite(self, validate=[
            {"code": 3, "result": {"errors": [{"line": 4, "message": "'Star' is not a number", "hint": "write the number"}]}},
            {"code": 0}], render=[{"result": {"audio": {"frames": 48000}}}])
        blocked = GOOD.replace("Modulators LFO", "Utility OSC Send")
        made = self.make(fake, self.ask_with(f"```inf\n{blocked}```", f"<think>hmm</think>{GOOD}", GOOD), seconds=8)
        self.assertEqual(made["replies"], 3)
        self.assertTrue(made["sound"])
        self.assertEqual(made["credit"], ie.CREDIT)
        self.assertEqual(Path(made["video"]).read_bytes()[4:8], b"ftyp")
        self.assertEqual((fake.work / "clip" / "patch.inf").read_text(encoding="utf-8"), GOOD)
        first_fix, second_fix = self.asked[1][-1]["content"], self.asked[2][-1]["content"]
        self.assertIn("reaches outside the patch", first_fix)
        self.assertIn("'Star' is not a number (write the number)", second_fix)
        self.assertIn("Source Shape: i shapeType (Circle|Star)", second_fix)
        self.assertNotIn("<think>", self.asked[2][-2]["content"])  # only the patch goes back
        modes = [call[0] for call in fake.calls()]
        self.assertEqual(modes, ["--validate", "--validate", "--render"])  # the blocked patch never ran
        render = fake.calls()[-1]
        self.assertEqual(render[render.index("--duration") + 1], "8")
        self.assertEqual(render[render.index("--timeout") + 1], str(ie.RENDER_TIMEOUT))
        self.assertNotIn("--size", render)
        self.assertEqual([stage for stage, _ in self.stages],
                         ["writing", "fixing", "checking", "fixing", "checking", "rendering"])
        self.assertEqual(self.stages[1][1], {"attempt": 1, "rounds": ie.FIX_ROUNDS})

    def test_a_patch_that_never_passes_is_an_error_with_the_last_problem(self):
        fake = FakeInfinite(self, validate=[{"code": 3, "result": {"errors": [{"line": 2, "message": "no Output"}]}}])
        with self.assertRaisesRegex(ie.InfiniteError, "could not write a patch.*line 2: no Output"):
            self.make(fake, self.ask_with(*[GOOD] * (ie.FIX_ROUNDS + 1)))
        self.assertEqual(len(self.asked), ie.FIX_ROUNDS + 1)
        self.assertNotIn("--render", [call[0] for call in fake.calls()])

    def test_bad_requests_and_bad_renders_are_refused(self):
        fake = FakeInfinite(self, render=[{"code": 5, "result": {"errors": [{"message": "GPU lost"}]}},
                                          {"bytes": "<html>not a video"}])
        for options in ({"seconds": 5}, {"seconds": "4"}):
            with self.assertRaisesRegex(ie.InfiniteError, "4, 8 or 12"):
                self.make(fake, self.ask_with(GOOD), **options)
        with self.assertRaisesRegex(ie.InfiniteError, "1 to 1,500"):
            ie.make_clip(" ", self.ask_with(GOOD), fake.program, fake.work, types=self.types)
        self.assertEqual(fake.calls(), [])
        with self.assertRaisesRegex(ie.InfiniteError, "could not render the clip: GPU lost"):
            self.make(fake, self.ask_with(GOOD))
        with self.assertRaisesRegex(ie.InfiniteError, "not an MP4"):
            self.make(fake, self.ask_with(GOOD))

    def test_a_picture_that_reaches_nothing_goes_back_to_the_model_but_the_last_round_renders(self):
        loose = {"warnings": [{"code": "W_UNUSED_NODE", "node": 1, "line": 3, "message": "Shape does not reach an Output"}]}
        fake = FakeInfinite(self, validate=[{"result": loose}])
        made = self.make(fake, self.ask_with(*[GOOD] * (ie.FIX_ROUNDS + 1)))
        self.assertEqual(made["replies"], ie.FIX_ROUNDS + 1)
        self.assertIn("line 3: Shape does not reach an Output (wire it into the picture, for example into a Layer Stack: cable mix 1 shape", self.asked[1][-1]["content"])
        self.assertEqual([call[0] for call in fake.calls()][-1], "--render")

    def test_stop_is_honoured_between_replies(self):
        fake = FakeInfinite(self)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            self.make(fake, self.ask_with(GOOD), cancel=cancel)
        self.assertEqual(self.asked, [])


@unittest.skipUnless(ie.find_infinite(), "Infinite is not installed here")
class RealInfiniteTests(unittest.TestCase):
    def test_the_prompt_examples_validate_and_render_with_sound_where_wired(self):
        program = ie.find_infinite()
        patches = [ie.extract_patch(block) for block in ie.EXAMPLES.split("Example ")[1:]]
        self.assertEqual(len(patches), 3)
        with tempfile.TemporaryDirectory() as folder:
            for number, text in enumerate(patches, 1):
                (Path(folder) / "patch.inf").write_text(text, encoding="utf-8")
                code, result = ie.run(program, ["--validate", "patch.inf", "--for-render"], folder, ie.CHECK_TIMEOUT)
                self.assertEqual((code, result["errors"]), (0, []), f"example {number}")
                code, result = ie.run(program, ["--render", "patch.inf", f"{number}.mp4", "--duration", 1,
                                                "--timeout", 60], folder, 60)
                self.assertEqual(code, 0, result)
                self.assertEqual((Path(folder) / f"{number}.mp4").read_bytes()[4:8], b"ftyp")
                self.assertEqual(bool(result["audio"]["frames"]), number == 2, f"example {number}")


if __name__ == "__main__":
    unittest.main()
