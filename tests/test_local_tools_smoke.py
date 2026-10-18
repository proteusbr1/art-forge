"""Smoke tests of the local tools (no GPU inference): the humanoid export in Blender and
the review sheet in Godot, on the synthetic fixtures (tests/fixtures/humanoid) and, when
FORGE_SMOKE_RIG and FORGE_SMOKE_CLIPS are set, on a real rigged GLB and motion library.

They need forge.local.json at the repository root with blender_python (export) and godot
(sheets); without them they are skipped. Normal maps are baked on the CPU here. Outputs go
to local/smoke/ (ignored by git); the fixture GLBs are made once under local/fixtures/.
Optional: FORGE_SMOKE_ITEMS (held-item folder of the real library), FORGE_SMOKE_OUT.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forge import local_gpu  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_util import write_json  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "humanoid")
GEN = os.path.join(FIX, "make_fixtures.py")
CHECK = os.path.join(FIX, "check_glb.py")
CACHE = os.path.join(ROOT, "local", "fixtures", "humanoid")
SMOKE = os.path.join(ROOT, "local", "smoke")
CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
HAVE_BLENDER = not local_gpu.check_tools(CONFIG, "export")
HAVE_GODOT = not local_gpu.check_tools(CONFIG, "review")
PROFILE = ["Hips", "Spine", "Chest", "UpperChest", "Neck", "Head", "LeftShoulder", "LeftUpperArm",
           "LeftLowerArm", "LeftHand", "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand",
           "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes", "RightUpperLeg", "RightLowerLeg",
           "RightFoot", "RightToes"]
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def blender(args, timeout=600):
    r = subprocess.run([CONFIG["blender_python"]] + args, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, **FLAGS)
    if r.returncode != 0:
        raise AssertionError("blender failed: %s\n%s" % (args, r.stderr[-3000:]))
    return r.stdout


def fixtures():
    """The fixture GLBs, made again when the generator changed."""
    stamp = sha(GEN)
    mark = os.path.join(CACHE, "stamp.txt")
    names = ("humanoid.glb", "humanoid_high.glb", "box.glb")
    old = ""
    if os.path.exists(mark):
        with open(mark) as f:
            old = f.read()
    if old == stamp and all(os.path.exists(os.path.join(CACHE, n)) for n in names):
        return CACHE
    os.makedirs(CACHE, exist_ok=True)
    blender([GEN, "--glbs", CACHE])
    with open(mark, "w") as f:
        f.write(stamp)
    return CACHE


def check_glb(glb, model=None):
    args = [CHECK, "--glb", glb] + (["--model", model] if model else [])
    out = blender(args)
    line = next(ln for ln in out.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def fresh(*parts):
    path = os.path.join(SMOKE, *parts)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


class FixtureData(unittest.TestCase):
    def test_committed_data_matches_the_generator(self):
        sys.path.insert(0, FIX)
        import make_fixtures
        self.assertEqual(load(os.path.join(FIX, "library.json")), json.loads(json.dumps(make_fixtures.library())))
        self.assertEqual(load(os.path.join(FIX, "items", "box.json")), make_fixtures.BOX)
        lib = make_fixtures.library()
        self.assertEqual(len(lib["anims"]["walk"]["frames"]), 8)
        self.assertEqual(sorted(lib["anims"]["idle"]["frames"][0]), sorted(make_fixtures.CANON))


class RunnerChecks(unittest.TestCase):
    def test_rig_tools_are_checked_per_method(self):
        empty = load_config(None)
        self.assertEqual(local_gpu.tools_for("rig"), ("skintokens_dir", "skintokens_python"))
        problems = local_gpu.check_tools(empty, "rig", "unirig")
        self.assertEqual(len(problems), 4)
        self.assertTrue(all("not set" in p for p in problems))
        with self.assertRaises(local_gpu.ToolError):
            local_gpu.run_rig("mesh.glb", os.path.join(SMOKE, "never"), empty)
        with self.assertRaises(ValueError):
            local_gpu.tools_for("rig", "other")

    def test_tool_folders_need_their_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(load_config(None), skintokens_dir=tmp, skintokens_python=os.path.join(tmp, "python.exe"))
            open(cfg["skintokens_python"], "w").close()
            problems = local_gpu.check_tools(cfg, "rig", "skintokens")
            self.assertEqual(len(problems), 1)
            self.assertIn("demo.py", problems[0])


@unittest.skipUnless(HAVE_BLENDER, "blender_python is not configured")
class SyntheticExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = fixtures()
        cls.rig = os.path.join(fx, "humanoid.glb")
        cls.high = os.path.join(fx, "humanoid_high.glb")
        cls.clips = os.path.join(FIX, "library.json")
        cls.items = os.path.join(FIX, "items")
        cls.params = {"tris": 8000, "height": 1.8, "normal_map": 256}
        cls.rest_dir = fresh("synthetic", "rest")
        cls.rest = local_gpu.run_export(cls.rig, cls.rest_dir, CONFIG, cls.params, device="CPU", name="figure")
        cls.lib_dir = fresh("synthetic", "library")
        cls.lib = local_gpu.run_export(cls.rig, cls.lib_dir, CONFIG, dict(cls.params, tris=25000), clips=cls.clips,
                                       items=cls.items, bake_source=cls.high, device="CPU", name="figure")

    def test_rest_pose_export(self):
        files = self.rest
        for role in ("glb", "model", "regions_mask", "bone_map", "mesh_report"):
            self.assertTrue(os.path.exists(files[role]), role)
        self.assertNotIn("motion_report", files)
        r = check_glb(files["glb"], files["model"])
        for name in PROFILE:
            self.assertIn(name, r["bones"])
        self.assertIn("bone_30", r["bones"])  # a finger keeps its name
        self.assertTrue(r["groups_match"])
        self.assertEqual(r["animations"], {})
        self.assertAlmostEqual(r["height"], 1.8, delta=0.01)
        self.assertAlmostEqual(r["floor"], 0.0, delta=0.01)
        self.assertEqual(r["base_color"], [256, 256])
        self.assertEqual(r["normal_map"], [256, 256])
        mesh = load(files["mesh_report"])
        self.assertTrue(mesh["valid"], mesh["problems"])
        self.assertTrue(mesh["reduced"])
        self.assertTrue(mesh["normal_baked"])
        self.assertEqual(mesh["bake_source"], "mesh before reduction")
        self.assertLessEqual(mesh["triangles"], 8000)
        self.assertEqual(mesh["unweighted_vertices"], 0)
        self.assertLessEqual(mesh["max_influences"], 4)
        self.assertEqual(mesh["status"], "pending review")
        bone_map = load(files["bone_map"])
        self.assertTrue(bone_map["valid"])
        self.assertEqual(bone_map["source"], "proposed")
        mask = np.asarray(Image.open(files["regions_mask"]).convert("RGB"))
        self.assertGreater((mask[..., 0] > 0).sum(), 0)  # skin
        self.assertGreater((mask[..., 1] > 0).sum(), 0)  # hair
        prov = local_gpu.provenance(self.rest_dir, "export")
        self.assertEqual(prov["inputs"][0]["role"], "rig")
        self.assertTrue(prov["tools"]["blender_python"]["version"].startswith("bpy "))

    def test_library_export(self):
        files = self.lib
        model = load(files["model"])
        r = check_glb(files["glb"], files["model"])
        for name in PROFILE:
            self.assertIn(name, r["bones"])
        lib = load(self.clips)["anims"]
        self.assertEqual(sorted(a["name"] for a in model["animations"]), sorted(lib))
        for name, clip in lib.items():
            found = [v for k, v in r["animations"].items() if k == name or k.startswith(name + "_")]
            self.assertEqual(len(found), 1, (name, r["animations"]))
            self.assertAlmostEqual(found[0]["seconds"], len(clip["frames"]) / clip["fps"], delta=1e-3)
        self.assertAlmostEqual(r["height"], 1.8, delta=0.01)
        self.assertTrue(r["normal_map"])
        # Hands on the box's sockets in the holding idle.
        self.assertIn("idle", r["hands"])
        for side in ("left", "right"):
            self.assertLess(r["hands"]["idle"][side], 0.02, r["hands"])
        # The game mesh was not reduced again; the normal map comes from the high mesh.
        mesh = load(files["mesh_report"])
        self.assertFalse(mesh["reduced"])
        self.assertTrue(mesh["normal_baked"])
        self.assertEqual(mesh["bake_source"], "humanoid_high.glb")
        self.assertEqual(mesh["triangles"], mesh["triangles_in"])
        self.assertEqual(r["triangles"], mesh["triangles"])
        self.assertLess(mesh["bake_source_offset_m"], 0.05)
        motion = load(files["motion_report"])
        self.assertEqual(motion["status"], "pending review")
        self.assertEqual(sorted(motion["clips"]), sorted(lib))
        self.assertEqual(motion["flags"], [])
        idle = motion["clips"]["idle"]
        self.assertLess(idle["hand_socket_cm"], 2.0)
        self.assertLess(idle["foot_slide_cm"], 2.0)
        self.assertIsNotNone(idle["penetration_cm"])
        self.assertIsNotNone(idle["stretch_p95"])
        self.assertLess(motion["clips"]["walk"]["foot_slide_cm"], 2.0)
        self.assertNotIn("approved", json.dumps(motion).lower())

    def test_feet_sliding_in_declared_stance_are_flagged(self):
        lib = load(self.clips)
        bad = json.loads(json.dumps(lib["anims"]["idle"]))
        bad["holds"] = None
        for i, f in enumerate(bad["frames"]):
            f["l_ankle"][1] += 0.006 * i  # the left foot creeps forward while it should stand
            f["l_toe"][1] += 0.006 * i
        lib["anims"] = {"slide": bad}
        out = fresh("synthetic", "slide")
        path = os.path.join(out, "slide_library.json")
        write_json(path, lib)
        files = local_gpu.run_export(self.rig, os.path.join(out, "export"), CONFIG, self.params, clips=path,
                                     device="CPU", name="figure")
        motion = load(files["motion_report"])
        flagged = [f for f in motion["flags"] if f["clip"] == "slide" and f["measure"] == "foot_slide_cm"]
        self.assertEqual(len(flagged), 1, motion["flags"])
        self.assertGreater(flagged[0]["value"], 5.0)

    def test_an_invalid_manual_map_stops_the_export(self):
        out = fresh("synthetic", "badmap")
        sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
        import humanoid_bones as hb
        roles = {r: "bone_%d" % (len(hb.ROLES) - i) for i, r in enumerate(hb.ROLES)}  # nonsense
        write_json(os.path.join(out, "bone_map.json"), {"manual": True, "roles": roles})
        with self.assertRaises(local_gpu.ToolError) as cm:
            local_gpu.run_export(self.rig, out, CONFIG, self.params, device="CPU")
        self.assertIn("bone map", str(cm.exception))
        self.assertTrue(load(os.path.join(out, "bone_map.json"))["manual"])  # kept as written
        report = load(os.path.join(out, "bone_map_report.json"))
        self.assertFalse(report["valid"])
        self.assertTrue(report["problems"])
        self.assertFalse(os.path.exists(os.path.join(out, "model.glb")))

    @unittest.skipUnless(HAVE_GODOT, "godot is not configured")
    def test_review_sheet_and_provenance(self):
        out = fresh("synthetic", "review")
        camera = {"pitch": 60, "directions": 8, "figure_height": 96, "front": True}
        files = local_gpu.run_review([self.rest["glb"], self.lib["glb"], "!rig failed"], out, CONFIG, camera)
        img = np.asarray(Image.open(files["sheet"]).convert("RGBA"))
        layout = load(files["layout"])
        self.assertEqual([img.shape[1], img.shape[0]], layout["size"])
        self.assertEqual(len(layout["variants"]), 3)
        self.assertEqual(layout["status"], "pending review")
        kinds = {}
        for v in layout["variants"]:
            for c in v["cells"]:
                x, y, w, h = c["rect"]
                cell = img[y:y + h, x:x + w]
                kinds.setdefault(c["kind"], 0)
                kinds[c["kind"]] += 1
                if c["kind"] == "failure":
                    red = (cell[..., 0] > 180) & (cell[..., 1] < 80) & (cell[..., 3] > 200)
                    self.assertGreater(red.sum(), 100, "no failure mark")
                else:
                    self.assertGreater((cell[..., 3] > 0).sum(), 30, (v["label"], c))
        self.assertEqual(kinds["small"], 16)
        self.assertEqual(kinds["front"], 2)
        self.assertEqual(kinds["failure"], 1)
        self.assertEqual(kinds["strip"], 8)  # the walk of the second variant
        self.assertTrue(layout["variants"][2]["failed"])
        self.assertEqual(layout["variants"][1]["pose"], "idle")
        self.assertEqual(layout["variants"][0]["pose"], "rest")
        self.assertIn("motion: no report", layout["variants"][0]["flags"])
        self.assertEqual(layout["variants"][1]["flags"], ["no flags"])
        # Provenance of the smoke run, exported with the factory's --provenance.
        manifest = {}
        for jid, d, step in (("smoke/figure/export", self.lib_dir, "export"), ("smoke/figure/review", out, "review")):
            rec = local_gpu.provenance(d, step)
            files_ = {role: [os.path.relpath(os.path.join(d, o["file"]), SMOKE), o["sha256"]]
                      for role, o in rec["outputs"].items()}
            rec.update({"family": "smoke", "asset": "figure"})
            manifest[jid] = {"status": "done", "files": files_, "finished": "now", "provenance": rec}
        write_json(os.path.join(SMOKE, "manifest.json"), manifest)
        dest = os.path.join(SMOKE, "provenance.json")
        r = subprocess.run([sys.executable, "-m", "forge", "--out", SMOKE, "--provenance", dest], cwd=ROOT,
                           capture_output=True, text=True, **FLAGS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = load(dest)
        recs = doc["files"]
        export = [x for x in recs if x["job"] == "smoke/figure/export" and x["role"] == "glb"][0]
        roles = {i["role"]: i for i in export["inputs"]}
        self.assertEqual(roles["rig"]["sha256"], sha(self.rig))
        self.assertIn("clips", roles)
        self.assertIn("items/box.json", roles)
        self.assertIn("bake_source", roles)
        self.assertIn("blender_python", export["tools"])
        self.assertIn("tools/blender/humanoid_export.py", export["tool_files"])
        review = [x for x in recs if x["job"] == "smoke/figure/review" and x["role"] == "sheet"][0]
        self.assertTrue(review["tools"]["godot"]["version"].startswith("v4"))
        self.assertTrue(all(x["present"] and len(x["sha256"]) == 64 for x in recs))


@unittest.skipUnless(HAVE_BLENDER and HAVE_GODOT and os.environ.get("FORGE_SMOKE_RIG")
                     and os.environ.get("FORGE_SMOKE_CLIPS"), "FORGE_SMOKE_RIG / FORGE_SMOKE_CLIPS not set")
class RealRig(unittest.TestCase):
    def test_real_rig_export_and_sheet(self):
        rig, clips = os.environ["FORGE_SMOKE_RIG"], os.environ["FORGE_SMOKE_CLIPS"]
        items = os.environ.get("FORGE_SMOKE_ITEMS") or None
        out = os.environ.get("FORGE_SMOKE_OUT") or os.path.join(SMOKE, "real")
        if os.path.exists(out):
            shutil.rmtree(out)
        params = {"tris": 25000, "height": 1.8, "normal_map": 1024}
        files = local_gpu.run_export(rig, os.path.join(out, "v1"), CONFIG, params, clips=clips, items=items,
                                     device="CPU", name="model")
        r = check_glb(files["glb"], files["model"])
        for name in PROFILE:
            self.assertIn(name, r["bones"])
        self.assertAlmostEqual(r["height"], 1.8, delta=0.01)
        self.assertTrue(r["normal_map"] and r["base_color"])
        lib = load(clips)["anims"]
        model = load(files["model"])
        self.assertEqual(len(model["animations"]), len(lib))
        motion = load(files["motion_report"])
        self.assertEqual(len(motion["clips"]), len(lib))
        sheet = local_gpu.run_review([files["glb"], "!second variant not made in the smoke test"], out, CONFIG,
                                     {"pitch": 60, "directions": 8, "figure_height": 96, "front": True})
        with Image.open(sheet["sheet"]) as img:
            size = list(img.size)
        layout = load(sheet["layout"])
        self.assertEqual(size, layout["size"])
        manifest = {}
        for jid, d, step in (("smoke/real/export", os.path.join(out, "v1"), "export"), ("smoke/real/review", out, "review")):
            rec = local_gpu.provenance(d, step)
            rec.update({"family": "smoke", "asset": "real"})
            manifest[jid] = {"status": "done", "finished": "now", "provenance": rec,
                             "files": {role: [os.path.relpath(os.path.join(d, o["file"]), out), o["sha256"]]
                                       for role, o in rec["outputs"].items()}}
        write_json(os.path.join(out, "manifest.json"), manifest)
        dest = os.path.join(out, "provenance.json")
        r = subprocess.run([sys.executable, "-m", "forge", "--out", out, "--provenance", dest], cwd=ROOT,
                           capture_output=True, text=True, **FLAGS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = load(dest)
        export = [x for x in doc["files"] if x["job"] == "smoke/real/export" and x["role"] == "glb"][0]
        roles = {i["role"] for i in export["inputs"]}
        self.assertTrue({"rig", "clips"} <= roles)
        self.assertIn("blender_python", export["tools"])
        print("\nREAL-RIG SHEET", sheet["sheet"])


if __name__ == "__main__":
    unittest.main()
