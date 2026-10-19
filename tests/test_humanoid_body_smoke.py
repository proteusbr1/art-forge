"""A humanoid that wears an approved body, end to end in Blender on the CPU (no normal-map
bake: the meshes are not reduced and no bake source is given; no Godot). Skipped when
forge.local.json has no blender_python.

Two synthetic bodies (tests/fixtures/humanoid/make_body_fixtures.py) are exported by the
humanoid exporter as approved bodies under local/fixtures/humanoid_body/ (the sample order
order_humanoid_body.json names them). Two variants of other proportions, bulk and facing (one
with longer arms and shorter legs, bulky and facing +x, its soles lower; one with shorter arms
facing -x) are conformed to body_a and exported (forge/local_gpu.py run_body_export, outputs in
local/smoke/humanoid_body/): both get the body's skeleton exactly, the same retargeted
motions as each other and as the body, the body's height, and a conform report."""

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
from forge import body, local_gpu  # noqa: E402
from forge.config import load_config  # noqa: E402

CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
BLENDER = CONFIG.get("blender_python")
FIXTURES = os.path.join(HERE, "fixtures", "humanoid")
BODIES = os.path.join(ROOT, "local", "fixtures", "humanoid_body")
OUT = os.path.join(ROOT, "local", "smoke", "humanoid_body")
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
LIBRARY = os.path.join(FIXTURES, "library.json")
ITEMS = os.path.join(FIXTURES, "items")
BODY_SHAPES = {"body_a": [], "body_b": ["--arms", "1.1", "--legs", "1.05", "--yaw", "90"]}
VARIANTS = {"bulky": ["--height", "0.6", "--arms", "1.2", "--legs", "0.9", "--bulk", "1.6", "--yaw", "90",
                      "--sole", "0.01"],
            "slim": ["--height", "1.3", "--arms", "0.9", "--yaw", "-90"]}


def run(cmd, timeout=900):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **FLAGS)
    if res.returncode != 0:
        raise AssertionError("%s failed (%d):\n%s\n%s" % (cmd[1], res.returncode, res.stdout[-3000:], res.stderr[-3000:]))
    return res


def make_bodies(force=False):
    """The approved bodies of the sample order (made once)."""
    out = {}
    for name, args in BODY_SHAPES.items():
        folder = os.path.join(BODIES, name)
        glb = os.path.join(folder, name + ".glb")
        if force or not os.path.isfile(glb) or not os.path.isfile(os.path.join(folder, "model.json")):
            shutil.rmtree(folder, ignore_errors=True)
            rig = os.path.join(BODIES, "rigs", name + ".glb")
            run([BLENDER, os.path.join(FIXTURES, "make_body_fixtures.py"), "--out", rig] + args)
            run([BLENDER, os.path.join(ROOT, "tools", "blender", "humanoid_export.py"), "--rig", rig, "--out", folder,
                 "--name", name, "--device", "CPU", "--tris", "100000000", "--tex", "256", "--height", "1.8",
                 "--clips", LIBRARY, "--items", ITEMS])
        out[name] = glb
    return out


@unittest.skipUnless(BLENDER and os.path.exists(BLENDER), "blender_python is not configured")
class BodySmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(OUT, ignore_errors=True)
        os.makedirs(OUT)
        cls.bodies = make_bodies(force=True)
        cls.files = {}
        for name, args in VARIANTS.items():
            rig = os.path.join(OUT, "rigs", name + ".glb")
            run([BLENDER, os.path.join(FIXTURES, "make_body_fixtures.py"), "--out", rig] + args)
            cls.files[name] = local_gpu.run_body_export(
                rig, os.path.join(OUT, name), CONFIG, cls.bodies["body_a"], params={"tris": 10 ** 8, "normal_map": 256},
                clips=LIBRARY, items=ITEMS, name=name, device="CPU")

    def read(self, name, role):
        with open(self.files[name][role], encoding="utf-8") as f:
            return json.load(f)

    def test_every_variant_wears_the_bodys_skeleton(self):
        skel = body.skeleton(self.bodies["body_a"])
        for name in VARIANTS:
            model = self.read(name, "model")
            self.assertTrue(model["body"]["skeleton"]["identical"], (name, model["body"]["skeleton"]))
            self.assertEqual(model["body"]["sha256"], local_gpu.sha_file(self.bodies["body_a"]))
            check = body.compare_skeletons(body.skeleton(self.files[name]["glb"]), skel)
            self.assertTrue(check["identical"], (name, check))
            self.assertLess(check["max_offset_m"], 1e-5)
            with open(os.path.join(os.path.dirname(self.bodies["body_a"]), "model.json"), encoding="utf-8") as f:
                self.assertAlmostEqual(model["height"], json.load(f)["height"], places=6)
            self.assertEqual(self.read(name, "mesh_report")["flags"], [], name)
        # The other body is another skeleton.
        self.assertFalse(body.compare_skeletons(body.skeleton(self.bodies["body_b"]), skel)["identical"])

    def test_the_motions_are_the_same_on_every_variant(self):
        a = body.animations(self.files["bulky"]["glb"])
        b = body.animations(self.files["slim"]["glb"])
        own = body.animations(self.bodies["body_a"])
        self.assertTrue(a)
        for other in (b, own):
            diff = body.compare_animations(a, other)
            self.assertTrue(diff["same_channels"], diff)
            self.assertLess(diff["max_difference"], 1e-4, diff)

    def test_the_conform_report(self):
        for name in VARIANTS:
            rep = self.read(name, "conform_report")
            self.assertTrue(rep["valid"])
            self.assertEqual(rep["missing_roles"], [])
            self.assertLess(rep["joint_error_cm"], 0.5, name)
            self.assertEqual(rep["joint_outside_max_cm"], 0.0, name)
            self.assertIn("bone_%d" % 30, rep["bones_folded"])  # a finger, counted with its hand
        bulky, slim = self.read("bulky", "conform_report"), self.read("slim", "conform_report")
        self.assertAlmostEqual(bulky["prefit"]["turn_deg"] % 360, 270.0, delta=1.0)
        self.assertAlmostEqual(slim["prefit"]["turn_deg"] % 360, 90.0, delta=1.0)
        self.assertGreater(bulky["joints"]["left_hand"]["before_cm"], 2.0)
        self.assertLess(bulky["joints"]["left_lower_arm"]["scale"], 0.9)
        self.assertGreater(bulky["floor_offset_cm"]["left"], slim["floor_offset_cm"]["left"] + 1.0)
        self.assertEqual(bulky["body_outside"]["p99_cm"], 0.0)


if __name__ == "__main__":
    unittest.main()
