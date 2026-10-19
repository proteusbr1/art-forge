"""The humanoid exporter stands its figure straight whatever way the rig faces and whatever
orientation the rig tool gave its joints (a fix of 2026-10-19: rigs whose bones' tails do not
point to their child joints were exported crouching, every clip with deeply bent knees).

The synthetic figure (tests/fixtures/humanoid/make_fixtures.py) is made facing -y (the
glTF front), turned to face +x and -x, and with its bones' tails turned 60 degrees off their
children; each is exported with the synthetic library's idle and checked in Blender: the knee
stays about as straight as at rest, the ankles stay under the hips, and the exported model
faces glTF +z. Blender runs on the CPU (no normal-map bake: the mesh is not reduced and no bake
source is given), no Godot. Skipped when forge.local.json has no blender_python.

The facing (a fix of 2026-10-19: the exporter squared a figure only to the axis its feet point
to, and the first real night's figures stood turned 22 to 40 degrees): the figure is also
turned 30 degrees, with its feet turned 35 degrees while its hips and shoulders face front, with
its hips and shoulders turned 35 degrees while its feet face front, and twisted (its shoulders'
line 40 degrees off its hips'). Each exported GLB's hips' and shoulders' lines are read from
the file (forge/body.py) and must face glTF +z within 1 degree; the twisted figure must carry a
"facing:" mesh flag and the others none."""

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import fake_comfy  # noqa: E402,F401  (a GPU lock folder of the tests' own)
from forge.body import body_turn, skeleton  # noqa: E402
from forge.config import load_config  # noqa: E402

CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
BLENDER = CONFIG.get("blender_python")
FIXTURES = os.path.join(HERE, "fixtures", "humanoid")
OUT = os.path.join(ROOT, "local", "smoke", "pose")
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
# name: (yaw, skew, feet yaw, twist), degrees.
CASES = {"front": (0, False, 0, 0), "plus_x": (90, False, 0, 0), "minus_x": (-90, True, 0, 0),
         "skewed": (0, True, 0, 0), "turned_30": (30, False, 0, 0), "feet_askew": (0, False, 35, 0),
         "body_askew": (35, False, -35, 0), "twisted": (0, False, 0, 40)}


def run(cmd, timeout=900):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **FLAGS)
    if res.returncode != 0:
        raise AssertionError("%s failed (%d):\n%s\n%s" % (cmd[1], res.returncode, res.stdout[-3000:], res.stderr[-3000:]))
    return res


@unittest.skipUnless(BLENDER and os.path.exists(BLENDER), "blender_python is not configured")
class ExportPoseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(OUT, ignore_errors=True)
        os.makedirs(OUT)
        cls.results = {}
        for name, (yaw, skew, feet_yaw, twist) in CASES.items():
            folder = os.path.join(OUT, name)
            fig = os.path.join(folder, "figure.glb")
            run([BLENDER, os.path.join(FIXTURES, "make_fixtures.py"), "--figure", fig, "--yaw", str(yaw),
                 "--feet-yaw", str(feet_yaw), "--twist", str(twist)] + (["--skew"] if skew else []))
            run([BLENDER, os.path.join(ROOT, "tools", "blender", "humanoid_export.py"), "--rig", fig,
                 "--out", os.path.join(folder, "export"), "--name", "fig", "--device", "CPU", "--tris", "100000000",
                 "--tex", "256", "--height", "1.8", "--clips", os.path.join(FIXTURES, "library.json"),
                 "--only", "idle"])
            check = os.path.join(folder, "pose.json")
            run([BLENDER, os.path.join(FIXTURES, "check_pose.py"), os.path.join(folder, "export", "fig.glb"), "idle",
                 check])
            with open(check, encoding="utf-8") as f:
                pose = json.load(f)
            with open(os.path.join(folder, "export", "model.json"), encoding="utf-8") as f:
                model = json.load(f)
            with open(os.path.join(folder, "export", "mesh_report.json"), encoding="utf-8") as f:
                mesh = json.load(f)
            model["_turn_in_glb"] = body_turn(skeleton(os.path.join(folder, "export", "fig.glb")), model["bones"])
            model["_mesh_flags"] = mesh["flags"]
            cls.results[name] = (pose, model)

    def test_the_idle_stands_straight(self):
        for name, (pose, model) in self.results.items():
            for frame in pose["frames"]:
                for side, leg in frame.items():
                    rest = pose["rest"][side]
                    self.assertLess(abs(leg["knee_deg"] - rest["knee_deg"]), 8.0, (name, side, frame, rest))
                    self.assertLess(leg["knee_deg"], 12.0, (name, side, leg))
                    self.assertLess(leg["ankle_off_m"], 0.08, (name, side, leg))
                    self.assertGreater(leg["hip_z"] - leg["ankle_z"], 0.75, (name, side, leg))

    def test_every_figure_faces_gltf_plus_z(self):
        for name, (pose, model) in self.results.items():
            self.assertEqual([round(x, 6) for x in model["front"]], [0.0, 0.0, 1.0], name)
            self.assertTrue(model["facing"]["squared"], name)
            self.assertLess(abs(model["facing"]["after"]["turn_deg"]), 0.5, name)
            if name == "twisted":
                continue
            # The hips' and shoulders' lines of the GLB as written: across within 1 degree of +x.
            self.assertLess(model["_turn_in_glb"], 1.0, (name, model["facing"]))
            self.assertEqual([f for f in model["_mesh_flags"] if f.startswith("facing:")], [], name)
        self.assertAlmostEqual(self.results["turned_30"][1]["facing"]["before"]["turn_deg"], 30.0, places=2)
        self.assertAlmostEqual(self.results["body_askew"][1]["facing"]["before"]["turn_deg"], 35.0, places=2)
        self.assertAlmostEqual(self.results["feet_askew"][1]["facing"]["before"]["cues"]["feet"], 35.0, places=1)

    def test_a_twisted_figure_is_flagged(self):
        model = self.results["twisted"][1]
        flags = [f for f in model["_mesh_flags"] if f.startswith("facing:")]
        self.assertEqual(len(flags), 1, model["_mesh_flags"])
        self.assertIn("40 degrees apart", flags[0])


if __name__ == "__main__":
    unittest.main()
