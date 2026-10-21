"""Smoke tests of the articulated and creature tools (no GPU inference): the parts exporter
and the creature exporter in Blender and the layout sheet in Godot, on synthetic fixtures
(tests/fixtures/articulated/make_tank.py, tests/fixtures/creature/make_rig.py), made once
under local/fixtures/; outputs go to local/smoke/ (ignored by git).

They need forge.local.json at the repository root with blender_python and godot; without
them they are skipped. As the humanoid smoke tests, they hold this user's machine-wide GPU
lock while they run and are skipped when a factory run holds it.
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
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import fake_comfy  # noqa: E402  (a GPU lock folder of the tests' own: FORGE_LOCK_DIR)

import creature_skeletons as sk  # noqa: E402
import glb_materials  # noqa: E402
from forge import gpu_lock, local_gpu  # noqa: E402
from forge.config import load_config  # noqa: E402

CACHE = os.path.join(ROOT, "local", "fixtures", "new_tools")
SMOKE = os.path.join(ROOT, "local", "smoke")
TANK = os.path.join(ROOT, "tests", "fixtures", "articulated", "make_tank.py")
RIG = os.path.join(ROOT, "tests", "fixtures", "creature", "make_rig.py")
LIBRARY = os.path.join(ROOT, "tests", "fixtures", "creature", "clips.json")
CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
HAVE_BLENDER = not local_gpu.check_tools(CONFIG, "parts")
HAVE_GODOT = not local_gpu.check_tools(CONFIG, "sheet")
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
MACHINE_LOCK = fake_comfy.MACHINE_LOCK_DIR or gpu_lock.default_lock_dir()
PARTS = [{"id": "hull"},
         {"id": "turret", "parent": "hull", "find": {"box": [[-0.45, -0.4, [0.5, 0.75]], [0.45, None, None]]},
          "joint": {"kind": "turn", "axis": "up", "pivot": "cut", "range": [-180, 180]}},
         {"id": "barrel", "parent": "turret", "find": {"box": [[-0.08, 0.2, 0.6], [0.08, None, 0.95]]},
          "joint": {"kind": "hinge", "axis": "-side", "pivot": "back", "range": [-8, 20]}}]
PARTS_WAIST = [{"id": "hull"},
               {"id": "turret", "parent": "hull", "find": {"box": [[-0.45, None, [0.42, 0.78]], [0.45, None, None]]},
                "joint": {"kind": "turn", "axis": "up", "pivot": "cut", "range": [-180, 180]}},
               {"id": "barrel", "parent": "turret", "find": {"box": [[-0.16, [-0.1, 0.42], 0.45], [0.16, None, None]]},
                "joint": {"kind": "hinge", "axis": "-side", "pivot": "cut", "range": [-8, 20]}}]
P = "procedural"
SERPENT_MOTIONS = [{"name": "idle_none", "source": P, "kind": "breathe", "frames": 20, "fps": 20},
                   {"name": "walk_none", "source": P, "kind": "undulate", "frames": 24, "settings": {"travel": 0.3}},
                   {"name": "aim_rifle", "source": P, "kind": "aim", "frames": 10, "holds": "rifle"},
                   {"name": "collapse", "source": P, "kind": "collapse", "frames": 30, "settings": {"fall": "left"}}]
BEAST_MOTIONS = [{"name": "idle_none", "source": "library", "library": {"clip": "idle"}, "frames": 12},
                 {"name": "walk_none", "source": P, "kind": "gait", "frames": 24},
                 {"name": "attack", "source": P, "kind": "strike", "frames": 20, "settings": {"chains": ["neck"]}}]
_lock = []


def setUpModule():
    if not (HAVE_BLENDER or HAVE_GODOT):
        return
    try:
        _lock.append(gpu_lock.acquire("art-forge smoke tests", MACHINE_LOCK))
    except gpu_lock.GpuBusy as ex:
        raise unittest.SkipTest("the card is in use: %s" % ex)


def tearDownModule():
    while _lock:
        gpu_lock.release(path=_lock.pop())


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def blender(args, timeout=600):
    r = subprocess.run([CONFIG["blender_python"]] + args, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, **FLAGS)
    if r.returncode != 0:
        raise AssertionError("blender failed: %s\n%s" % (args, r.stderr[-3000:]))
    return r.stdout


def fixtures():
    """The fixture GLBs, made again when a generator changed."""
    h = hashlib.sha256()
    for p in (TANK, RIG, os.path.join(ROOT, "tests", "creature_skeletons.py")):
        with open(p, "rb") as f:
            h.update(f.read())
    stamp = h.hexdigest()
    mark = os.path.join(CACHE, "stamp.txt")
    names = ("painted_tank.glb", "painted_tank_open.glb", "serpent.glb", "beast.glb", "predicted.glb",
             "predicted_turned.glb")
    old = ""
    if os.path.exists(mark):
        with open(mark) as f:
            old = f.read()
    if old == stamp and all(os.path.exists(os.path.join(CACHE, n)) for n in names):
        return CACHE
    os.makedirs(CACHE, exist_ok=True)
    blender([TANK, "--out", CACHE])
    blender([TANK, "--out", CACHE, "--open"])
    for kind in ("serpent", "beast", "predicted", "predicted_turned"):
        blender([RIG, "--kind", kind, "--out", CACHE])
    with open(mark, "w") as f:
        f.write(stamp)
    return CACHE


def fresh(*parts):
    path = os.path.join(SMOKE, *parts)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


def glb_doc(path):
    return glb_materials.read_glb(path)[0]


@unittest.skipUnless(HAVE_BLENDER, "blender_python is not configured in forge.local.json")
class Parts(unittest.TestCase):
    def test_tank_smooth_with_a_baked_normal_map_and_the_refresh(self):
        # The painted tank stands in for its own raw mesh (same frame): the map is baked.
        out = fresh("new_tools", "parts_normals")
        tank = os.path.join(fixtures(), "painted_tank.glb")
        files = local_gpu.run_parts(tank, out, CONFIG, PARTS, name="tank", params={"length": 6.0},
                                    bake_source=tank, device="CPU")
        report = load(files["mesh_report"])
        self.assertEqual(report["flags"], [])
        self.assertTrue(report["normals"]["normal_baked"])
        self.assertEqual(report["normals"]["bake_source_fit"]["how"], "as_is")
        self.assertTrue(report["materials"]["slots"]["normal"]["lossless"])
        self.assertTrue(all(p["normals"] == "present" for p in report["normals"]["primitives"]))
        # The refresh command finds the raw mesh on the exported parts (turned and scaled).
        from forge import normals
        again = fresh("new_tools", "parts_refresh")
        rep = normals.refresh(files["glb"], again, raw=tank, normals="always", config=CONFIG,
                              emit=lambda *_: None)
        self.assertEqual(rep["flags"], [])
        self.assertTrue(rep["bake"]["fit"]["used"], rep["bake"]["fit"])
        self.assertTrue(rep["map"]["materials"]["0"]["baked"])

    def test_tank_parts_nodes_and_frame(self):
        out = fresh("new_tools", "parts")
        files = local_gpu.run_parts(os.path.join(fixtures(), "painted_tank.glb"), out, CONFIG, PARTS, name="tank",
                                    params={"length": 6.0})
        report, model = load(files["mesh_report"]), load(files["model"])
        self.assertEqual(report["flags"], [])
        self.assertEqual(sorted(model["parts"]), ["barrel", "hull", "turret"])
        self.assertTrue(all(p["open_edges"] == 0 and p["triangles"] > 0 for p in model["parts"].values()))
        self.assertAlmostEqual(model["size_m"][2], 6.0, places=3)
        turret = next(j for j in model["joints"] if j["part"] == "turret")
        self.assertEqual(turret["axis"], [0.0, 1.0, 0.0])
        # The cut is the ring (0.5 to 0.56 of 0.9 high, scaled by 6 / 2.4).
        self.assertTrue(0.5 * 2.5 < turret["pivot_m"][1] < 0.56 * 2.5, turret)
        self.assertAlmostEqual(turret["pivot_m"][0], 0.0, places=3)
        doc = glb_doc(files["glb"])
        nodes = {n["name"]: n for n in doc["nodes"]}
        self.assertEqual([doc["nodes"][c]["name"] for c in nodes["hull"].get("children", [])], ["turret"])
        self.assertEqual([doc["nodes"][c]["name"] for c in nodes["turret"].get("children", [])], ["barrel"])
        self.assertTrue(np.allclose(nodes["turret"]["translation"], turret["offset_m"], atol=1e-3))
        self.assertNotIn("animations", doc)
        self.assertEqual(load(files["frame"])["front"], "+z")
        self.assertTrue(load(os.path.join(out, "parts.provenance.json"))["outputs"])
        # A frame corrected by hand turns the object as it says.
        with open(files["frame"], "w", encoding="utf-8") as f:
            json.dump({"manual": True, "front": "-z"}, f)
        files = local_gpu.run_parts(os.path.join(fixtures(), "painted_tank.glb"), out, CONFIG, PARTS, name="tank",
                                    params={"length": 6.0})
        model = load(files["model"])
        self.assertEqual((model["frame"]["source"], model["frame"]["front_given"]), ("manual", "-z"))
        self.assertTrue(load(files["frame"])["manual"])

    def test_an_open_mesh_and_a_barrel_cut_at_the_turret_face(self):
        # A generated mesh is not watertight: its own holes are not the cut's, and a cut that
        # crosses one is still closed (its open chain bridged).
        out = fresh("new_tools", "parts_open")
        files = local_gpu.run_parts(os.path.join(fixtures(), "painted_tank_open.glb"), out, CONFIG, PARTS_WAIST,
                                    name="tank", params={"length": 6.0})
        report, model = load(files["mesh_report"]), load(files["model"])
        self.assertEqual(report["flags"], [])
        parts = model["parts"]
        self.assertTrue(all(p["open_edges"] == 0 for p in parts.values()), parts)
        self.assertGreater(parts["hull"]["source_open_edges"], 0)
        self.assertGreater(parts["turret"]["bridged_edges"] + parts["hull"]["bridged_edges"], 0)
        # The barrel is cut where it leaves the turret's front face (0.3 m ahead of the footprint's
        # centre, scaled by 2.5), within a step of the search, not along its length, and its
        # hinge is there.
        barrel = next(j for j in model["joints"] if j["part"] == "barrel")
        self.assertTrue(0.3 * 2.5 < barrel["pivot_m"][2] < 0.3 * 2.5 + 0.12, barrel)
        self.assertGreater(parts["barrel"]["share"], 0.01)
        # The width check: this hull is half as wide as long.
        out = fresh("new_tools", "parts_wide")
        files = local_gpu.run_parts(os.path.join(fixtures(), "painted_tank.glb"), out, CONFIG, PARTS, name="tank",
                                    params={"length": 6.0}, max_width=0.4)
        flags = load(files["mesh_report"])["flags"]
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0].startswith("frame: the object is 3.00 m wide and 6.00 m long"), flags)

@unittest.skipUnless(HAVE_BLENDER, "blender_python is not configured in forge.local.json")
class Creature(unittest.TestCase):
    def test_serpent(self):
        out = fresh("new_tools", "serpent")
        files = local_gpu.run_creature_export(os.path.join(fixtures(), "serpent.glb"), out, CONFIG, sk.SERPENT_PLAN,
                                              SERPENT_MOTIONS, name="serpent", params={"height": 1.9})
        model, motion = load(files["model"]), load(files["motion_report"])
        self.assertEqual([a["name"] for a in model["animations"]], [m["name"] for m in SERPENT_MOTIONS])
        self.assertAlmostEqual(model["height"], 1.9, places=3)
        self.assertEqual(sorted(model["roles"]), ["arm_l0", "arm_r0", "neck", "spine", "tail"])
        self.assertEqual(model["item_bones"], {"right": "item", "reference": "item_ref"})
        self.assertEqual(load(files["mesh_report"])["flags"], [])
        self.assertEqual(motion["flags"], [])
        self.assertTrue(all(c["found"] for c in motion["clips"].values()))
        doc = glb_doc(files["glb"])
        self.assertEqual(sorted(a["name"] for a in doc["animations"]), sorted(m["name"] for m in SERPENT_MOTIONS))
        self.assertIn("item", [n.get("name") for n in doc["nodes"]])
        clips = load(files["clips"])
        self.assertEqual((clips["format"], sorted(clips["anims"])), ("forge-creature-clips",
                                                                      sorted(m["name"] for m in SERPENT_MOTIONS)))
        self.assertEqual(load(files["bone_map"])["source"], "proposed")

    def test_beast_library_and_gait(self):
        out = fresh("new_tools", "beast")
        files = local_gpu.run_creature_export(os.path.join(fixtures(), "beast.glb"), out, CONFIG, sk.BEAST_PLAN,
                                              BEAST_MOTIONS, name="beast", params={"length": 2.2}, clips=LIBRARY)
        model, motion = load(files["model"]), load(files["motion_report"])
        idle = model["animations"][0]
        self.assertEqual((idle["source"], idle["frames"]), ("library", 12))
        walk = motion["clips"]["walk_none"]
        self.assertLess(walk["foot_slide_cm"], 2.0)
        self.assertLess(walk["reach_shortfall_cm"], 1.0)
        self.assertEqual(motion["flags"], [])

    def test_skeletons_a_rig_tool_predicted(self):
        # The first real night's rigs: a humanoid template on a serpent (its tail grown as two
        # legs) and on a beast whose rig lay a quarter turn off.
        out = fresh("new_tools", "predicted")
        files = local_gpu.run_creature_export(os.path.join(fixtures(), "predicted.glb"), out, CONFIG, sk.SERPENT_PLAN,
                                              SERPENT_MOTIONS[:2], name="serpent", params={"height": 2.0})
        model = load(files["model"])
        self.assertIn("tail", model["roles"])
        self.assertEqual(len(model["twins"]["tail"]), 4)
        self.assertEqual(model["facing"]["turn_deg"], 0)
        self.assertTrue(all(c["found"] for c in load(files["motion_report"])["clips"].values()))
        _, names = sk.predicted_biped().tree()
        out = fresh("new_tools", "predicted_turned")
        motions = [{"name": "idle_none", "source": P, "kind": "breathe", "frames": 20, "fps": 20},
                   {"name": "walk_none", "source": P, "kind": "gait", "frames": 16}]
        files = local_gpu.run_creature_export(os.path.join(fixtures(), "predicted_turned.glb"), out, CONFIG,
                                              sk.BIPED_BEAST_PLAN, motions, name="beast", params={"height": 2.2})
        model, motion = load(files["model"]), load(files["motion_report"])
        self.assertEqual(model["facing"]["turn_deg"], -90)
        self.assertEqual(model["roles"]["leg_l0"][0], names["legl0"])
        self.assertEqual(model["roles"]["leg_r0"][0], names["legr0"])
        self.assertLess(motion["clips"]["walk_none"]["foot_slide_cm"], 2.0)

    def test_an_invalid_bone_map_stops_the_export(self):
        out = fresh("new_tools", "invalid")
        plan = {"chains": [{"id": "spine", "toward": "up"}, {"id": "wing", "toward": "side", "pairs": 2}]}
        with self.assertRaises(local_gpu.ToolError) as ctx:
            local_gpu.run_creature_export(os.path.join(fixtures(), "serpent.glb"), out, CONFIG, plan, [],
                                          name="serpent")
        self.assertFalse(ctx.exception.retry)
        self.assertFalse(load(os.path.join(out, "bone_map.json"))["valid"])


@unittest.skipUnless(HAVE_BLENDER and HAVE_GODOT, "blender_python and godot are not configured")
class Sheet(unittest.TestCase):
    def test_layout_sheets_render(self):
        parts = os.path.join(SMOKE, "new_tools", "parts", "tank.glb")
        serpent = os.path.join(SMOKE, "new_tools", "serpent", "serpent.glb")
        if not (os.path.exists(parts) and os.path.exists(serpent)):
            Parts("test_tank_parts_nodes_and_frame").test_tank_parts_nodes_and_frame()
            Creature("test_serpent").test_serpent()
        for name, glbs in (("articulated", [parts, "!parts failed: example"]), ("creature", [serpent])):
            out = fresh("new_tools", "sheet_" + name)
            files = local_gpu.run_sheet(glbs, out, CONFIG, camera={"pitch": 30, "directions": 4, "strip_frames": 4},
                                        labels=["v%d" % i for i in range(len(glbs))],
                                        verdicts=[{"class": "acceptable", "reasons": []}] * len(glbs))
            layout = load(files["layout"])
            with Image.open(files["sheet"]) as im:
                self.assertEqual(list(im.size), layout["size"])
                a = np.asarray(im.convert("RGBA"))
            self.assertEqual(layout["load_failures"], [])
            for cell in layout["cells"]:
                x, y, w, h = cell["rect"]
                self.assertGreater(int(a[y:y + h, x:x + w, 3].max()), 0, cell)
            strips = [c for c in layout["cells"] if c["kind"] == "strip"]
            self.assertTrue(strips)


if __name__ == "__main__":
    unittest.main()
