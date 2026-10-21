"""Smoke tests of the held-item tools (no GPU inference): the item tool and the hold (the
reference figure made ready, then the humanoid exporter) in Blender, and the item sheet in
Godot, on synthetic fixtures (tests/fixtures/held_item/make_carbine.py and the synthetic
humanoid of tests/fixtures/humanoid), made once under local/fixtures/; outputs go to
local/smoke/held_item/ (ignored by git).

They need forge.local.json at the repository root with blender_python and godot; without
them they are skipped. As the other smoke tests, they hold this user's machine-wide GPU lock
while they run and are skipped when a factory run holds it.
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

import glb_materials  # noqa: E402
import test_local_tools_smoke as humanoid_smoke  # noqa: E402  (the synthetic humanoid fixture)
from forge import gpu_lock, local_gpu  # noqa: E402
from forge.config import load_config  # noqa: E402

CACHE = os.path.join(ROOT, "local", "fixtures", "held_item")
SMOKE = os.path.join(ROOT, "local", "smoke", "held_item")
CARBINE = os.path.join(ROOT, "tests", "fixtures", "held_item", "make_carbine.py")
LIBRARY = os.path.join(ROOT, "tests", "fixtures", "held_item", "library.json")
CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
HAVE_BLENDER = not local_gpu.check_tools(CONFIG, "item")
HAVE_GODOT = not local_gpu.check_tools(CONFIG, "item_sheet")
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
MACHINE_LOCK = fake_comfy.MACHINE_LOCK_DIR or gpu_lock.default_lock_dir()
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


def carbine():
    """The painted carbine GLB, made again when its generator changed."""
    with open(CARBINE, "rb") as f:
        stamp = hashlib.sha256(f.read()).hexdigest()
    mark = os.path.join(CACHE, "stamp.txt")
    path = os.path.join(CACHE, "painted_carbine.glb")
    old = ""
    if os.path.exists(mark):
        with open(mark) as f:
            old = f.read()
    if old != stamp or not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run([CONFIG["blender_python"], CARBINE, "--out", CACHE], capture_output=True, text=True,
                           timeout=600, stdin=subprocess.DEVNULL, **FLAGS)
        if r.returncode != 0:
            raise AssertionError("blender failed: %s" % r.stderr[-3000:])
        with open(mark, "w") as f:
            f.write(stamp)
    return path


def carbine_picture():
    """The carbine's reference picture: a strict side view, straight, the front to the right,
    on a transparent background (the fixture's mesh came out tilted 15 degrees from it)."""
    import test_held_item_geometry as geometry
    path = os.path.join(CACHE, "carbine_cutout.png")
    alpha = geometry.side_picture(list(geometry.CARBINE.values()))
    rgba = np.zeros(alpha.shape + (4,), np.uint8)
    rgba[..., :3] = 60
    rgba[..., 3] = np.where(alpha, 255, 0)
    os.makedirs(CACHE, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def fresh(*parts):
    path = os.path.join(SMOKE, *parts)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


def item_files():
    out = fresh("item")
    return out, local_gpu.run_item(carbine(), out, CONFIG, {"class": "carbine"}, name="carbine",
                                   params={"length": 0.9}, reference=carbine_picture())


@unittest.skipUnless(HAVE_BLENDER, "blender_python is not configured in forge.local.json")
class Item(unittest.TestCase):
    def test_carbine_smooth_with_a_baked_normal_map(self):
        # The painted carbine stands in for its own raw mesh (same frame): the map is baked.
        out = fresh("item_normals")
        files = local_gpu.run_item(carbine(), out, CONFIG, {"class": "carbine"}, name="carbine",
                                   params={"length": 0.9}, reference=carbine_picture(), bake_source=carbine(),
                                   device="CPU")
        report = load(files["mesh_report"])
        self.assertEqual(report["flags"], [])
        self.assertTrue(report["normals"]["normal_baked"])
        self.assertTrue(report["materials"]["slots"]["normal"]["lossless"])
        self.assertLess(report["normals"]["primitives"][0]["flat_share"], 0.5)

    def test_carbine_in_item_space_with_its_sockets(self):
        out, files = item_files()
        report, model, sockets = load(files["mesh_report"]), load(files["model"]), load(files["sockets"])
        self.assertEqual(report["flags"], [])
        self.assertEqual((sockets["class"], sockets["sockets"]["grip"]), ("carbine", [0.0, 0.0, 0.0]))
        self.assertEqual(sorted(sockets["sockets"]), ["butt", "grip", "guard", "muzzle", "top"])
        self.assertAlmostEqual(model["size_m"][0], 0.9, places=3)
        self.assertTrue(model["frame"]["square"]["squared"])  # the fixture is tilted 15 degrees
        self.assertAlmostEqual(abs(model["frame"]["square"]["tilt_deg"]), 15.0, delta=2.0)
        self.assertEqual((model["frame"]["source"], model["frame"]["found"]["method"]), ("found", "reference"))
        self.assertEqual(model["frame"]["found"]["front_axis"], "+x")
        # The GLB in item space: front glTF +x, top glTF +y, the grip at the origin.
        doc, binary = glb_materials.read_glb(files["glb"])
        acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
        lo, hi = acc["min"], acc["max"]
        box = sockets["box"]
        np.testing.assert_allclose(lo, [box[0][0], box[0][2], -box[1][1]], atol=1e-3)
        np.testing.assert_allclose(hi, [box[1][0], box[1][2], -box[0][1]], atol=1e-3)
        self.assertGreater(sockets["sockets"]["muzzle"][0], 0.5)
        self.assertLess(sockets["sockets"]["butt"][0], -0.2)
        self.assertTrue(load(os.path.join(out, "item.provenance.json"))["outputs"])
        # A manual sockets file is read as written; a manual frame turns the item as it says.
        with open(files["socket_map"], "w", encoding="utf-8") as f:
            json.dump({"manual": True, "sockets": dict(sockets["sockets"], guard=[0.3, 0.0, -0.3])}, f)
        files = local_gpu.run_item(carbine(), out, CONFIG, {"class": "carbine"}, name="carbine",
                                   params={"length": 0.9})
        self.assertTrue(any(f.startswith("socket guard is off the mesh") for f in load(files["mesh_report"])["flags"]))
        self.assertEqual(load(files["model"])["sockets_source"], "manual")
        os.remove(files["socket_map"])
        with open(files["frame"], "w", encoding="utf-8") as f:
            json.dump({"manual": True, "front": "+x", "top": "+y", "rotate_deg": [0, 0, 0]}, f)
        files = local_gpu.run_item(carbine(), out, CONFIG, {"class": "carbine"}, name="carbine",
                                   params={"length": 0.9})
        self.assertEqual(load(files["model"])["frame"]["source"], "manual")
        self.assertEqual(load(files["model"])["frame"]["square"]["turned_deg"], 0.0)


@unittest.skipUnless(HAVE_BLENDER, "blender_python is not configured in forge.local.json")
class Hold(unittest.TestCase):
    def test_the_reference_figure_holds_the_item(self):
        _, item = item_files()
        figure = os.path.join(humanoid_smoke.fixtures(), "humanoid.glb")
        out = fresh("hold")
        files = local_gpu.run_hold(item["glb"], item["sockets"], figure, LIBRARY, out, CONFIG, "carbine",
                                   name="carbine")
        model = load(files["model"])
        self.assertEqual([a["name"] for a in model["animations"]], ["idle_carbine", "aim_carbine"])
        self.assertEqual(model["classes"]["carbine"]["items"], ["carbine"])
        self.assertEqual(load(files["clips"])["anims"].keys(), {"idle_carbine", "aim_carbine"})
        self.assertFalse(load(files["mesh_report"])["reduced"])
        self.assertTrue(load(os.path.join(out, "hold.provenance.json"))["outputs"])
        # A figure the humanoid exporter already wrote (item bones, item_ref, clips) is made ready again.
        again = fresh("hold_exported")
        files = local_gpu.run_hold(item["glb"], item["sockets"], files["glb"], LIBRARY, again, CONFIG, "carbine",
                                   name="carbine")
        removed = load(files["figure"])["removed"]
        self.assertIn("item_ref", removed["meshes"])
        self.assertEqual(sorted(removed["bones"]), ["item", "item_l"])
        names = [n.get("name") for n in glb_materials.read_glb(files["glb"])[0]["nodes"]]
        self.assertEqual(names.count("item"), 1)


@unittest.skipUnless(HAVE_BLENDER and HAVE_GODOT, "blender_python and godot are not configured")
class Sheet(unittest.TestCase):
    def test_item_sheet_renders(self):
        _, item = item_files()
        figure = os.path.join(humanoid_smoke.fixtures(), "humanoid.glb")
        hold = local_gpu.run_hold(item["glb"], item["sockets"], figure, LIBRARY, fresh("sheet_hold"), CONFIG,
                                  "carbine", name="carbine")["glb"]
        glb = item["glb"]
        out = fresh("sheet")
        files = local_gpu.run_item_sheet([glb, "!item failed: example"], out, CONFIG,
                                         camera={"pitch": 30, "directions": 4, "figure_height": 96},
                                         labels=["v1", "v2"], holds=[hold, None],
                                         verdicts=[{"class": "acceptable", "reasons": []},
                                                   {"class": "rejected", "reasons": ["item failed"]}])
        layout = load(files["layout"])
        self.assertEqual(layout["load_failures"], [])
        with Image.open(files["sheet"]) as im:
            self.assertEqual(list(im.size), layout["size"])
            a = np.asarray(im.convert("RGBA"))
        for cell in layout["cells"]:
            x, y, w, h = cell["rect"]
            self.assertGreater(int(a[y:y + h, x:x + w, 3].max()), 0, cell)
        self.assertEqual(sorted(c["kind"] for c in layout["cells"] if c["variant"] == 0).count("hold"), 8)
        self.assertEqual(len(layout["marks"]), 5)


if __name__ == "__main__":
    unittest.main()
