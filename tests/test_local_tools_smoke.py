"""Smoke tests of the local tools (no GPU inference): the humanoid export in Blender and
the review sheet in Godot, on the synthetic fixtures (tests/fixtures/humanoid) and, when
FORGE_SMOKE_RIG and FORGE_SMOKE_CLIPS are set, on a real rigged GLB and motion library.

They need forge.local.json at the repository root with blender_python (export) and godot
(sheets); without them they are skipped. Normal maps are baked on the CPU here. Outputs go
to local/smoke/ (ignored by git); the fixture GLBs are made once under local/fixtures/.
Optional: FORGE_SMOKE_ITEMS (held-item folder of the real library), FORGE_SMOKE_OUT.

The sheets render on the card: these tests hold this user's machine-wide GPU lock (not the
tests' temporary one) while they run, and are skipped when a factory run holds it.
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
import fake_comfy  # noqa: E402  (a GPU lock folder of the tests' own: FORGE_LOCK_DIR)

from forge import gpu_lock, local_gpu  # noqa: E402
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
# This user's machine-wide lock folder (fake_comfy points FORGE_LOCK_DIR elsewhere).
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


def material(files):
    """The materials section of an export's mesh report."""
    return load(files["mesh_report"])["materials"]


def metal_roughness_png(path, size=128):
    """A metallic-roughness map in the glTF convention: roughness in green, metallic in blue."""
    g = np.tile(np.linspace(40, 220, size).astype(np.uint8), (size, 1))
    img = np.dstack([np.full_like(g, 255), g, np.full_like(g, 30)])
    Image.fromarray(img, "RGB").save(path)
    return path


def fresh(*parts):
    path = os.path.join(SMOKE, *parts)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


# A Blender script: the fixture rig's mesh with a metallic-roughness map and an alpha channel
# in its base colour (opacity), saved as a painted mesh GLB. Arguments: rig, out, map.
PAINTED_SOURCE = """import os, sys, bpy
import numpy as np
rig, out, mr = sys.argv[-3:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=rig)
for o in list(bpy.data.objects):
    if o.type != "MESH":
        bpy.data.objects.remove(o)
body = next(o for o in bpy.data.objects if o.type == "MESH")
body.modifiers.clear()
mat = body.data.materials[0]
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
base = bsdf.inputs["Base Color"].links[0].from_node.image
w, h = base.size
px = np.empty(w * h * 4, np.float32)
base.pixels.foreach_get(px)
px = px.reshape(h, w, 4)
px[: h // 2, :, 3] = 0.4
rgba = bpy.data.images.new("painted_rgba", w, h, alpha=True)
rgba.pixels.foreach_set(px.ravel())
rgba.filepath_raw = os.path.join(os.path.dirname(out), "painted_rgba.png")
rgba.file_format = "PNG"
rgba.save()
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = rgba
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
img = bpy.data.images.load(mr)
img.colorspace_settings.name = "Non-Color"
mtex = nt.nodes.new("ShaderNodeTexImage")
mtex.image = img
sep = nt.nodes.new("ShaderNodeSeparateColor")
nt.links.new(mtex.outputs["Color"], sep.inputs["Color"])
nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_skins=False,
                          export_animations=False, export_draco_mesh_compression_enable=False)
sys.stdout.flush()
os._exit(0)
"""


class FixtureData(unittest.TestCase):
    def test_committed_data_matches_the_generator(self):
        sys.path.insert(0, FIX)
        import make_fixtures
        self.assertEqual(load(os.path.join(FIX, "library.json")), json.loads(json.dumps(make_fixtures.library())))
        self.assertEqual(load(os.path.join(FIX, "items", "box.json")), make_fixtures.BOX)
        lib = make_fixtures.library()
        self.assertEqual(len(lib["anims"]["walk"]["frames"]), 8)
        self.assertEqual(sorted(lib["anims"]["idle"]["frames"][0]), sorted(make_fixtures.CANON))


class GlbReader(unittest.TestCase):
    """The materials reader of tools/blender/glb_materials.py on hand-made GLBs (no Blender)."""

    def glb(self, images, material):
        import struct
        binary, views, imgs = b"", [], []
        for name, data in images:
            views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
            imgs.append({"name": name, "bufferView": len(views) - 1, "mimeType": "image/png"})
            binary += data + b"\0" * (-len(data) % 4)
        doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
               "images": imgs, "textures": [{"source": i} for i in range(len(imgs))], "materials": [material],
               "meshes": [{"primitives": [{"attributes": {}, "material": 0}]}], "nodes": [{"name": "body", "mesh": 0}]}
        js = json.dumps(doc).encode()
        js += b" " * (-len(js) % 4)
        body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
        path = os.path.join(SMOKE, "reader", "test.glb")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
        return path

    def encoded(self, mode, fmt, size=(6, 4)):
        import io
        buf = io.BytesIO()
        Image.new(mode, size).save(buf, fmt)
        return buf.getvalue()

    def test_slots_formats_sizes_and_opacity(self):
        sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
        import glb_materials as gm
        path = self.glb([("base", self.encoded("RGBA", "PNG")), ("mr", self.encoded("RGB", "JPEG", (8, 2)))],
                        {"name": "m", "alphaMode": "BLEND",
                         "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                  "metallicRoughnessTexture": {"index": 1}}})
        slots = gm.material_slots(path)
        self.assertEqual(slots["base_color"], {"present": True, "format": "png", "size": [6, 4], "alpha": True,
                                               "lossless": True, "image": "base"})
        self.assertEqual((slots["metallic_roughness"]["format"], slots["metallic_roughness"]["size"],
                          slots["metallic_roughness"]["lossless"]), ("jpeg", [8, 2], False))
        self.assertTrue(slots["opacity"]["present"])
        for slot in ("normal", "occlusion", "emissive"):
            self.assertFalse(slots[slot]["present"], slot)
        # A base colour without alpha has no opacity, whatever the alpha mode says.
        path = self.glb([("base", self.encoded("RGB", "JPEG"))],
                        {"alphaMode": "BLEND", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}})
        slots = gm.material_slots(path)
        self.assertFalse(slots["opacity"]["present"])
        self.assertEqual(slots["opacity"]["alpha_mode"], "BLEND")
        # The base colour above carried the opacity as JPEG: lossy even in compact.
        flags = gm.slot_flags(slots, {}, "compact")
        self.assertEqual(list(flags), ["base_color"])
        self.assertIn("carries the opacity", flags["base_color"])

    def test_a_lossy_technical_map_is_flagged_without_a_recorded_source(self):
        sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
        import glb_materials as gm
        path = self.glb([("base", self.encoded("RGB", "JPEG")), ("mr", self.encoded("RGB", "JPEG"))],
                        {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                  "metallicRoughnessTexture": {"index": 1}}})
        slots = gm.material_slots(path)
        # No source recorded for either (the exporter did not see them): the GLB decides.
        flags = gm.slot_flags(slots, {}, "compact")
        self.assertEqual(list(flags), ["metallic_roughness"])  # an opaque base colour may be JPEG
        self.assertIn("written as jpeg, which is lossy", flags["metallic_roughness"])
        self.assertEqual(sorted(gm.slot_flags(slots, {}, "lossless")), ["base_color", "metallic_roughness"])
        # A source that existed but is missing; a source map that could not be carried.
        flags = gm.slot_flags(slots, {"normal": "the exporter"}, "compact",
                              {"occlusion": "the map of the material source p.glb could not be carried: x"})
        self.assertIn("is missing from the exported GLB", flags["normal"])
        self.assertTrue(flags["occlusion"].endswith("could not be carried: x; the exported GLB has none"))


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
        os.makedirs(os.path.join(SMOKE, "synthetic"), exist_ok=True)
        cls.mr = metal_roughness_png(os.path.join(SMOKE, "synthetic", "metal_roughness.png"))
        cls.rest_dir = fresh("synthetic", "rest")
        cls.rest = local_gpu.run_export(cls.rig, cls.rest_dir, CONFIG, cls.params, device="CPU", name="figure")
        cls.lib_dir = fresh("synthetic", "library")
        cls.lib = local_gpu.run_export(cls.rig, cls.lib_dir, CONFIG, dict(cls.params, tris=25000), clips=cls.clips,
                                       items=cls.items, bake_source=cls.high, device="CPU", name="figure",
                                       metal_roughness=cls.mr)

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
        # Lossless by default: every map PNG, read back from the written GLB.
        mats = material(files)
        self.assertEqual(mats["image_format"], "lossless")
        slots = mats["slots"]
        for slot in ("base_color", "normal"):
            self.assertEqual((slots[slot]["present"], slots[slot]["format"], slots[slot]["source"]),
                             (True, "png", True), slot)
        self.assertEqual(slots["base_color"]["size"], [256, 256])
        self.assertEqual(slots["normal"]["size"], [256, 256])
        for slot in ("metallic_roughness", "occlusion", "emissive", "opacity"):
            self.assertEqual((slots[slot]["present"], slots[slot]["source"]), (False, False), slot)
        self.assertEqual(r["material"]["base_color"]["format"], "png")
        self.assertEqual(r["material"]["normal"]["format"], "png")
        prov = local_gpu.provenance(self.rest_dir, "export")
        self.assertEqual(prov["inputs"][0]["role"], "rig")
        self.assertEqual(prov["settings"]["image_format"], "lossless")
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
        # The metallic-roughness map given to the exporter is in the GLB, lossless.
        slots = mesh["materials"]["slots"]
        mr = slots["metallic_roughness"]
        self.assertEqual((mr["present"], mr["format"], mr["size"], mr["source"]), (True, "png", [128, 128], True))
        self.assertIn("metal_roughness.png", mr["source_from"])
        self.assertEqual(r["material"]["metallic_roughness"]["format"], "png")
        self.assertEqual(r["material"]["metallic_roughness"]["size"], [128, 128])
        self.assertTrue(all(x["format"] == "png" for x in slots.values() if x["present"]), slots)
        self.assertEqual(mesh["flags"], [])
        prov = local_gpu.provenance(self.lib_dir, "export")
        self.assertIn("metal_roughness", [i["role"] for i in prov["inputs"]])
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

    def test_compact_writes_jpeg_only_for_the_base_colour(self):
        out = fresh("synthetic", "compact")
        files = local_gpu.run_export(self.rig, out, CONFIG, self.params, device="CPU", name="figure",
                                     metal_roughness=self.mr, image_format="compact")
        mesh = load(files["mesh_report"])
        slots = mesh["materials"]["slots"]
        self.assertEqual(mesh["materials"]["image_format"], "compact")
        self.assertEqual(slots["base_color"]["format"], "jpeg")
        self.assertEqual(slots["normal"]["format"], "png")
        self.assertEqual(slots["metallic_roughness"]["format"], "png")
        self.assertEqual(mesh["flags"], [])
        r = check_glb(files["glb"], files["model"])
        self.assertEqual(r["material"]["base_color"]["format"], "jpeg")
        self.assertEqual(r["material"]["metallic_roughness"]["format"], "png")
        self.assertEqual(local_gpu.provenance(out, "export")["settings"]["image_format"], "compact")

    def test_a_source_map_that_cannot_be_carried_is_reported(self):
        # A metallic-roughness "map" that is no picture: reported and flagged, not dropped silently.
        out = fresh("synthetic", "broken_map")
        broken = os.path.join(out, "broken_metal_roughness.png")
        with open(broken, "wb") as f:
            f.write(b"not a picture")
        files = local_gpu.run_export(self.rig, os.path.join(out, "export"), CONFIG, self.params, device="CPU",
                                     name="figure", metal_roughness=broken)
        mesh = load(files["mesh_report"])
        mr = mesh["materials"]["slots"]["metallic_roughness"]
        self.assertEqual((mr["source"], mr["present"]), (True, False))
        self.assertTrue(any("broken_metal_roughness.png" in x for x in mesh["materials"]["not_carried"]),
                        mesh["materials"])
        self.assertTrue(any(f.startswith("material slot metallic_roughness:") for f in mesh["flags"]), mesh["flags"])
        self.assertFalse(mesh["valid"])

    def test_the_painted_mesh_maps_are_carried(self):
        # The painted mesh (the rig's input) has a metallic-roughness map and opacity the rig
        # lost: the exporter carries them from --material-source.
        source = os.path.join(SMOKE, "synthetic", "painted_source.glb")
        script = os.path.join(SMOKE, "synthetic", "painted_source.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(PAINTED_SOURCE)
        blender([script, self.rig, source, self.mr])
        out = fresh("synthetic", "carried")
        files = local_gpu.run_export(self.rig, out, CONFIG, self.params, device="CPU", name="figure",
                                     material_source=source)
        mesh = load(files["mesh_report"])
        slots = mesh["materials"]["slots"]
        for slot in ("metallic_roughness", "opacity"):
            self.assertEqual((slots[slot]["present"], slots[slot]["format"], slots[slot]["source"]),
                             (True, "png", True), slot)
            self.assertIn("painted_source.glb", slots[slot]["source_from"])
        self.assertEqual(mesh["flags"], [])
        # The material source's own base colour is dropped by design, and said so.
        self.assertTrue(any(x.startswith("base_color: the material source's map") and "is not taken" in x
                            for x in mesh["materials"]["not_taken"]), mesh["materials"])
        r = check_glb(files["glb"], files["model"])
        self.assertEqual(r["material"]["metallic_roughness"]["format"], "png")
        self.assertTrue(r["material"]["opacity"])

    def test_the_machine_lock_is_held(self):
        self.assertEqual(gpu_lock.holder(MACHINE_LOCK)["what"], "art-forge smoke tests")

    def test_the_draco_error_line_is_explained_in_the_export_log(self):
        # Blender's glTF exporter logs the missing Draco library as an error even with
        # compression off; the exporter says right after it that it is harmless.
        with open(os.path.join(self.lib_dir, "export.stdout.log"), encoding="utf-8", errors="replace") as f:
            text = f.read()
        if "Draco mesh compression is not available" in text:
            self.assertIn("NOTE the Draco line above is harmless", text)

    def moved_high(self, name, matrix):
        """A copy of the fixture's high mesh with every mesh object transformed (Blender axes)."""
        path = os.path.join(SMOKE, "synthetic", name + ".glb")
        code = ("import sys, bpy\nfrom mathutils import Matrix\n"
                "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                "bpy.ops.import_scene.gltf(filepath=sys.argv[1])\n"
                "m = Matrix(%r)\n"
                "for o in bpy.data.objects:\n"
                "    if o.type == 'MESH':\n"
                "        o.matrix_world = m @ o.matrix_world\n"
                "bpy.ops.export_scene.gltf(filepath=sys.argv[2], export_format='GLB', "
                "export_draco_mesh_compression_enable=False)\n"
                "import os\nsys.stdout.flush()\nos._exit(0)\n" % ([list(r) for r in matrix],))
        script = os.path.join(SMOKE, "synthetic", name + ".py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)
        blender([script, self.high, path])
        return path

    def test_a_shifted_bake_source_is_fitted(self):
        # Moved and scaled as a re-centring tool would: fitted back, then baked from.
        k = 1.06
        moved = self.moved_high("high_moved", [[k, 0, 0, 0.04], [0, k, 0, -0.03], [0, 0, k, 0.05], [0, 0, 0, 1]])
        out = fresh("synthetic", "bake_fitted")
        files = local_gpu.run_export(self.rig, out, CONFIG, dict(self.params, tris=25000), bake_source=moved,
                                     device="CPU", name="figure")
        mesh = load(files["mesh_report"])
        fit = mesh["bake_source_fit"]
        self.assertTrue(fit["corrected"] and fit["used"], fit)
        self.assertAlmostEqual(fit["scale"], 1 / k, delta=0.01)
        self.assertGreater(fit["distance_before_m"]["p90"], fit["tolerance_m"])
        self.assertLessEqual(fit["distance_after_m"]["p90"], fit["tolerance_m"])
        self.assertEqual(mesh["bake_source"], "high_moved.glb")
        self.assertTrue(mesh["normal_baked"])
        self.assertEqual(mesh["flags"], [])

    def test_a_bake_source_that_cannot_be_fitted_is_left_out(self):
        # Stretched on one axis only: no scale and translation lines it up.
        moved = self.moved_high("high_stretched", [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1.3, 0], [0, 0, 0, 1]])
        out = fresh("synthetic", "bake_rejected")
        files = local_gpu.run_export(self.rig, out, CONFIG, dict(self.params, tris=25000), bake_source=moved,
                                     device="CPU", name="figure")
        mesh = load(files["mesh_report"])
        fit = mesh["bake_source_fit"]
        self.assertTrue(fit["corrected"])
        self.assertFalse(fit["used"])
        self.assertIsNone(mesh["bake_source"])
        self.assertEqual(mesh["bake_source_rejected"], "high_stretched.glb")
        self.assertFalse(mesh["normal_baked"])  # not reduced: the texture relief only
        self.assertFalse(mesh["valid"])
        self.assertTrue(any("does not line up" in f for f in mesh["flags"]), mesh["flags"])
        r = check_glb(files["glb"], files["model"])
        self.assertTrue(r["normal_map"])

    @unittest.skipUnless(HAVE_GODOT, "godot is not configured")
    def test_the_sheet_poses_the_idle_where_both_feet_stand(self):
        lib = load(self.clips)
        idle = json.loads(json.dumps(lib["anims"]["idle"]))
        idle["holds"] = None
        for i, f in enumerate(idle["frames"]):
            if i < 4:  # the right foot lifted on the first frames (a weight shift)
                for joint in ("r_ankle", "r_toe"):
                    f[joint][2] += 0.12
                    f[joint][1] -= 0.08
        marked = json.loads(json.dumps(idle))
        marked.update(kind="other", pose_frame=2)
        lib["anims"] = {"idle": idle, "marked": marked}
        out = fresh("synthetic", "pose")
        path = os.path.join(out, "lifted_library.json")
        write_json(path, lib)
        files = local_gpu.run_export(self.rig, os.path.join(out, "export"), CONFIG, self.params, clips=path,
                                     device="CPU", name="figure")
        model = load(files["model"])
        anims = {x["name"]: x for x in model["animations"]}
        self.assertIsNone(anims["idle"]["pose_frame"])
        self.assertEqual(anims["marked"]["pose_frame"], 2)  # carried from the library
        sheet = local_gpu.run_review([files["glb"]], os.path.join(out, "review"), CONFIG,
                                     {"pitch": 60, "directions": 2, "figure_height": 64, "front": False})
        v = load(sheet["layout"])["variants"][0]
        self.assertEqual((v["pose"], v["pose_how"]), ("idle", "both ankles lowest"))
        self.assertGreaterEqual(v["pose_frame"], 4)
        self.assertTrue(all(c["time"] == v["pose_time"] for c in v["cells"] if c["kind"] == "small"))
        with open(os.path.join(out, "review", "review.stdout.log"), encoding="utf-8", errors="replace") as f:
            self.assertIn("REVIEW: pose v1 idle frame %d (both ankles lowest)" % v["pose_frame"], f.read())
        # A pose_frame in model.json wins.
        anims["idle"]["pose_frame"] = 1
        write_json(files["model"], model)
        sheet = local_gpu.run_review([files["glb"]], os.path.join(out, "review2"), CONFIG,
                                     {"pitch": 60, "directions": 2, "figure_height": 64, "front": False})
        v = load(sheet["layout"])["variants"][0]
        self.assertEqual((v["pose_frame"], v["pose_how"]), (1, "pose_frame of the clip"))

    @unittest.skipUnless(HAVE_GODOT, "godot is not configured")
    def test_the_sheet_says_when_no_variant_passed(self):
        out = fresh("synthetic", "review_none")
        files = local_gpu.run_review([self.rest["glb"], "!export failed"], out, CONFIG,
                                     {"pitch": 60, "directions": 2, "figure_height": 64, "front": False},
                                     verdicts=[{"class": "rejected", "reasons": ["mesh: 3 vertices have no weight"]},
                                               {"class": "rejected", "reasons": ["export failed"]}],
                                     banner="no variant passed: most often the rig (2 of 2)")
        layout = load(files["layout"])
        self.assertEqual(layout["banner"], "no variant passed: most often the rig (2 of 2)")
        self.assertEqual([v["verdict"] for v in layout["variants"]], ["rejected", "rejected"])
        img = np.asarray(Image.open(files["sheet"]).convert("RGBA"))
        band = img[:24]  # the banner line, in red over its dark band
        self.assertGreater(((band[..., 0] > 150) & (band[..., 1] < 90) & (band[..., 3] > 200)).sum(), 50)
        first = min(c["rect"][1] for c in layout["variants"][0]["cells"])
        self.assertGreater(first, 2 * 18)  # the figures start below the banner, header and verdict

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
        verdicts = [{"class": "acceptable", "reasons": []},
                    {"class": "rejected", "reasons": ["walk foot_slide_cm 3 over 2.0", "mesh: a | b"]},
                    {"class": "rejected", "reasons": ["rig failed"]}]
        files = local_gpu.run_review([self.rest["glb"], self.lib["glb"], "!rig failed"], out, CONFIG, camera,
                                     verdicts=verdicts)
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
        self.assertEqual(layout["variants"][1]["pose_how"], "both ankles lowest")
        self.assertGreaterEqual(layout["variants"][1]["pose_frame"], 0)
        self.assertEqual(layout["variants"][0]["pose"], "rest")
        self.assertIn("motion: no report", layout["variants"][0]["flags"])
        self.assertEqual(layout["variants"][1]["flags"], ["no flags"])
        # Each variant labelled acceptable or rejected with its first reasons; no banner.
        self.assertEqual([v["verdict"] for v in layout["variants"]], ["acceptable", "rejected", "rejected"])
        self.assertEqual(layout["variants"][1]["verdict_reasons"], ["walk foot_slide_cm 3 over 2.0", "mesh: a / b"])
        self.assertIsNone(layout["banner"])
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
        # Lossless technical maps after an independent re-import: the normal map always, the
        # metallic-roughness map when the rig's material has one.
        slots = load(files["mesh_report"])["materials"]["slots"]
        self.assertEqual((slots["normal"]["format"], r["material"]["normal"]["format"]), ("png", "png"))
        if slots["metallic_roughness"]["source"]:
            self.assertEqual(slots["metallic_roughness"]["format"], "png")
            self.assertEqual(r["material"]["metallic_roughness"]["format"], "png")
        print("\nREAL-RIG MATERIALS", json.dumps({k: (v["format"], v["size"]) for k, v in slots.items()
                                                  if v["present"]}))
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
