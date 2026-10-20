"""The glow: what glows on a painted texture (tools/blender/glow_regions.py), its emissive
map in a GLB written without Blender, the glow command (python -m forge.glow) and the export
jobs' glow (forge/recipes.py, forge/local_gpu.py job_glow). Synthetic textures, and small crops
of generated textures in tests/fixtures/glow/: skin_node (pale skin around a small teal node),
plate_seam (white plates and a cyan seam), plate_tab (white plates and an orange paint tab),
pastel_skin (lavender skin) and line_on_dark (pale green lines on dark armour). No Blender, no
GPU."""

import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import fake_comfy  # noqa: E402,F401  (FORGE_LOCK_DIR and FORGE_NVIDIA_SMI for the tests)
import glow_regions as gr  # noqa: E402
import glb_materials  # noqa: E402
from forge import glow as glow_cmd  # noqa: E402
from forge import local_gpu, stages  # noqa: E402

CROPS = os.path.join(HERE, "fixtures", "glow")


def crop(name):
    return Image.open(os.path.join(CROPS, name + ".png")).convert("RGB")


def canvas(size=256, colour=(200, 196, 190)):
    return np.full((size, size, 3), colour, np.uint8)


def dot(img, x, y, r, colour):
    yy, xx = np.mgrid[:img.shape[0], :img.shape[1]]
    img[(xx - x) ** 2 + (yy - y) ** 2 <= r * r] = colour


def quiet(argv):
    """python -m forge.glow without its lines on the console."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return glow_cmd.main(argv)


# A crop's lights are a large share of its few texels: regions may be up to a quarter of it.
CROP_SETTINGS = {"max_region": 0.25}


class ColoursInText(unittest.TestCase):
    def test_colours_bound_to_glow_words(self):
        cases = {
            "a robot of white and cobalt blue plates, thin glowing cyan seams and a small glowing core": ["cyan"],
            "a lamp post with glowing emerald or magenta lights, curved arms": ["emerald", "magenta"],
            "a lantern base with a faint emerald glow underneath": ["emerald"],
            "a cracked stone with bright orange-red glowing seams showing through": ["red", "orange"],
            "a hull of grey steel and rust red with glowing orange exhaust vents": ["orange"],
            "a faint violet glow around the crystal": ["violet"],
            "Plain gear. No glowing parts, no neon.": [],
            "light blue trousers and a red coat": [],
            "a teal and purple raincoat": [],
            "": [],
        }
        for text, want in cases.items():
            self.assertEqual(gr.colours_in_text(text), want, text)

    def test_parse_hues(self):
        self.assertEqual(gr.parse_hues("cyan"), [gr.COLOURS["cyan"]])
        self.assertEqual(gr.parse_hues(["10-40", " violet "]), [(10.0, 40.0), gr.COLOURS["violet"]])
        self.assertEqual(gr.parse_hues("350-20"), [(350.0, 20.0)])
        self.assertEqual(gr.parse_hues(""), [])
        self.assertIsNotNone(gr.hue_problem("cyan,plaid"))
        self.assertIsNone(gr.hue_problem("cyan, 100-120"))

    def test_hues_wrap_around_red(self):
        h = np.array([355.0, 5.0, 30.0, 180.0])
        self.assertEqual(gr.in_hues(h, [(350.0, 12.0)]).tolist(), [True, True, False, False])


class Regions(unittest.TestCase):
    def brute(self, mask):
        h, w = mask.shape
        seen = np.zeros_like(mask, bool)
        sizes = []
        for y in range(h):
            for x in range(w):
                if mask[y, x] and not seen[y, x]:
                    stack, n = [(y, x)], 0
                    seen[y, x] = True
                    while stack:
                        cy, cx = stack.pop()
                        n += 1
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                ny, nx = cy + dy, cx + dx
                                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                                    seen[ny, nx] = True
                                    stack.append((ny, nx))
                    sizes.append(n)
        return sorted(sizes)

    def test_label_matches_a_flood_fill(self):
        rng = np.random.default_rng(7)
        for p in (0.2, 0.45, 0.6):
            mask = rng.random((40, 50)) < p
            labels, sizes = gr.label(mask)
            self.assertEqual(sorted(sizes[1:].tolist()), self.brute(mask))
            self.assertTrue(((labels > 0) == mask).all())

    def test_diagonal_texels_are_one_region(self):
        mask = np.eye(6, dtype=bool)
        labels, sizes = gr.label(mask)
        self.assertEqual(sizes[1:].tolist(), [6])

    def test_empty(self):
        labels, sizes = gr.label(np.zeros((5, 5), bool))
        self.assertEqual(sizes.tolist(), [0])


class SyntheticTextures(unittest.TestCase):
    def test_small_lights_glow_and_pale_skin_does_not(self):
        img = canvas(colour=(205, 200, 210))   # pale skin
        dot(img, 60, 60, 4, (20, 230, 200))      # teal nodes
        dot(img, 180, 120, 3, (30, 240, 210))
        w, rep = gr.detect(img, ["teal"])
        self.assertGreater(w[60, 60], 0.5)
        self.assertGreater(w[120, 180], 0.5)
        self.assertEqual(rep["regions"], 2)
        far = np.ones(w.shape, bool)
        far[45:76, 45:76] = False
        far[105:136, 165:196] = False
        self.assertEqual(float(w[far].max()), 0.0)

    def test_a_painted_panel_is_not_a_light(self):
        img = canvas(colour=(40, 40, 45))
        img[20:200, 20:200] = (30, 200, 230)    # a large cyan panel
        dot(img, 230, 230, 3, (30, 220, 240))    # one small light
        w, rep = gr.detect(img, ["cyan"])
        self.assertEqual(float(w[100, 100]), 0.0)
        self.assertGreater(w[230, 230], 0.5)
        self.assertEqual(rep["regions_too_large"], 1)

    def test_cobalt_plates_and_white_plates_stay_dark_around_a_cyan_seam(self):
        img = canvas(colour=(225, 222, 215))     # pearl white
        img[:, :128] = (25, 70, 170)             # cobalt blue plate
        img[:, 126:130] = (60, 230, 250)         # the cyan seam between them
        w, rep = gr.detect(img, ["cyan"], settings={"max_region": 0.05})
        self.assertGreater(w[100, 128], 0.5)
        self.assertEqual(float(w[:, :120].max()), 0.0)
        self.assertEqual(float(w[:, 136:].max()), 0.0)

    def test_colours_not_named_do_not_glow(self):
        img = canvas()
        dot(img, 100, 100, 4, (255, 120, 20))    # an orange tab
        w, rep = gr.detect(img, ["cyan"])
        self.assertEqual(rep["glow_pixels"], 0)
        w, rep = gr.detect(img, [])
        self.assertEqual(rep["glow_pixels"], 0)
        self.assertEqual(rep["note"], "no glow colour")
        w, rep = gr.detect(img, ["orange"])
        self.assertGreater(rep["glow_pixels"], 0)

    def test_a_pastel_patch_is_not_a_light(self):
        img = canvas(512, colour=(25, 20, 30))
        img[30:66, 30:66] = (120, 60, 220)       # a small lavender patch whose shaded rim is saturated
        img[31:65, 31:65] = (190, 160, 240)
        dot(img, 190, 190, 5, (150, 40, 255))    # a violet sigil
        w, rep = gr.detect(img, ["violet"])
        self.assertEqual(float(w[30:66, 30:66].max()), 0.0)
        self.assertGreater(w[190, 190], 0.5)
        self.assertEqual(rep["regions_pale"], 1)
        w, rep = gr.detect(img, ["violet"], settings={"region_saturation": 0.0})
        self.assertGreater(w[48, 48], 0.5)

    def test_excluded_texels_never_glow(self):
        img = canvas()
        dot(img, 100, 100, 4, (20, 230, 200))
        ex = np.zeros(img.shape[:2], bool)
        ex[80:120, 80:120] = True
        w, rep = gr.detect(img, ["teal"], exclude=ex)
        self.assertEqual(rep["glow_pixels"], 0)

    def test_a_dim_light_counts_when_brighter_than_around_it(self):
        img = canvas(colour=(45, 25, 60))        # dark violet harness
        dot(img, 100, 100, 5, (40, 120, 105))    # a dim teal node (value 0.47)
        w, rep = gr.detect(img, ["teal"])
        self.assertGreater(w[100, 100], 0.5)

    def test_the_reference_weighs_the_colours(self):
        img = canvas(colour=(40, 40, 45))
        dot(img, 60, 60, 4, (30, 220, 240))      # cyan light
        dot(img, 180, 180, 4, (40, 240, 200))    # green-teal light
        both = ["cyan", "teal"]
        plain, _ = gr.detect(img, both)
        ref = np.zeros((200, 200, 4), np.uint8)
        ref[..., 3] = 255
        ref[..., :3] = (60, 60, 64)
        dot(ref, 100, 100, 8, (40, 220, 250, 255))    # the picture lights only the cyan
        weighed, rep = gr.detect(img, both, reference=Image.fromarray(ref, "RGBA"))
        self.assertGreater(rep["reference"]["lit_pixels"], 0)
        self.assertGreaterEqual(weighed[60, 60], plain[60, 60])
        self.assertLess(weighed[180, 180], plain[180, 180])

    def test_white_hot_core_joins_its_light(self):
        img = canvas(colour=(30, 30, 35))
        dot(img, 100, 100, 6, (30, 200, 230))
        dot(img, 100, 100, 1, (250, 252, 255))   # white core
        w, _ = gr.detect(img, ["cyan"])
        self.assertGreater(w[100, 100], 0.5)

    def test_emissive_map(self):
        img = canvas(colour=(30, 30, 35))
        dot(img, 100, 100, 4, (30, 120, 140))
        w, _ = gr.detect(img, ["cyan"])
        em = gr.emissive_map(img, w)
        self.assertEqual(em[0, 0].tolist(), [0, 0, 0])
        self.assertEqual(int(em[100, 100].max()), int(round(255 * w[100, 100])))
        self.assertEqual(gr.mask_blue(w).dtype, np.uint8)


class RealCrops(unittest.TestCase):
    """Crops of generated textures: lights found, pale skin and white plates left dark."""

    def pale_glowing(self, img, w):
        rgb = gr.as_float_rgb(img)
        h, c, v = gr.hue_chroma_value(rgb)
        pale = c < 0.12
        lit = (w > 0) & ~pale
        near = gr.dilate(lit, gr.DEFAULTS["core_reach"])
        return int(((w > 0) & pale & ~near).sum()), int(((w > 0) & pale).sum()), int(pale.sum())

    def test_teal_node_on_pale_skin(self):
        img = crop("skin_node")
        w, rep = gr.detect(img, ["teal", "emerald", "magenta"], settings=CROP_SETTINGS)
        self.assertGreater(rep["glow_pixels"], 100)
        away, lit, pale = self.pale_glowing(img, w)
        self.assertEqual(away, 0)
        self.assertLess(lit, 0.01 * pale)
        self.assertEqual(float(w[:, :40].max()), 0.0)   # the skin at the left

    def test_cyan_seam_between_white_plates(self):
        img = crop("plate_seam")
        w, rep = gr.detect(img, ["cyan"], settings=CROP_SETTINGS)
        self.assertGreater(rep["glow_pixels"], 100)
        self.assertEqual(self.pale_glowing(img, w)[0], 0)
        self.assertEqual(float(w[100:, :].max()), 0.0)  # the white plates below

    def test_white_plates_and_orange_paint_without_orange(self):
        w, rep = gr.detect(crop("plate_tab"), ["cyan", "emerald", "violet", "magenta"], settings=CROP_SETTINGS)
        self.assertEqual(rep["glow_pixels"], 0)

    def test_white_plates_never_glow_whatever_the_colours(self):
        img = crop("plate_tab")
        w, rep = gr.detect(img, ["0-359"], settings=CROP_SETTINGS)
        self.assertEqual(self.pale_glowing(img, w)[0], 0)

    def test_lavender_skin(self):
        w, rep = gr.detect(crop("pastel_skin"), ["violet", "magenta", "purple"], settings=CROP_SETTINGS)
        self.assertEqual(rep["glow_pixels"], 0)

    def test_pale_lines_on_dark_armour(self):
        img = crop("line_on_dark")
        w, rep = gr.detect(img, ["emerald"], settings=CROP_SETTINGS)
        self.assertGreater(rep["glow_pixels"], 500)
        self.assertEqual(float(w[110:, :].max()), 0.0)  # the dark blue armour below


def textured_glb(path, base, emissive=None, data_uri=False):
    """A one-triangle GLB whose material has the base colour (and an emissive map) inside."""
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    uvs = struct.pack("<6f", 0, 0, 1, 0, 0, 1)
    binary = bytearray(pos + uvs)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 24}]
    images, textures = [], []

    def add_image(arr, name):
        buf = io.BytesIO()
        Image.fromarray(arr, "RGB").save(buf, format="PNG")
        data = buf.getvalue()
        if data_uri:
            import base64
            images.append({"uri": "data:image/png;base64," + base64.b64encode(data).decode(), "name": name})
        else:
            while len(binary) % 4:
                binary.append(0)
            views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
            binary.extend(data)
            images.append({"bufferView": len(views) - 1, "mimeType": "image/png", "name": name})
        textures.append({"source": len(images) - 1, "sampler": 0})
        return len(textures) - 1

    mat = {"name": "skin", "pbrMetallicRoughness": {"baseColorTexture": {"index": add_image(base, "base")}}}
    if emissive is not None:
        mat["emissiveTexture"] = {"index": add_image(emissive, "own_emissive")}
        mat["emissiveFactor"] = [1, 1, 1]
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                          "min": [0, 0, 0], "max": [1, 1, 0]},
                         {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"}],
           "images": images, "textures": textures, "samplers": [{}], "materials": [mat],
           "meshes": [{"name": "body", "primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                                      "material": 0}]}],
           "nodes": [{"name": "body", "mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0}
    gr._write_glb(path, doc, bytes(binary))


def lit_texture():
    img = canvas(colour=(205, 200, 210))
    dot(img, 60, 60, 4, (20, 230, 200))
    dot(img, 200, 40, 3, (30, 240, 210))
    return img


class GlbEmissive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_glow_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, path):
        doc, binary = gr._read_glb(path)
        for bv in doc["bufferViews"]:
            self.assertLessEqual(bv["byteOffset"] + bv["byteLength"], len(binary))
            self.assertEqual(bv["byteOffset"] % 4, 0)
        self.assertIn(len(binary) - doc["buffers"][0]["byteLength"], (0, 1, 2, 3))  # the chunk's padding
        return doc, binary

    def test_written_emissive_slot(self):
        src = os.path.join(self.tmp, "a.glb")
        textured_glb(src, lit_texture())
        before = gr._read_glb(src)[1][:60]
        rep = gr.glow_files(src, ["teal"], out_glb=os.path.join(self.tmp, "b.glb"), strength=3.0)
        self.assertEqual(rep["emissive"], {"0": "written"})
        doc, binary = self.read(os.path.join(self.tmp, "b.glb"))
        self.assertEqual(binary[:60], before)          # the geometry's bytes are where they were
        mat = doc["materials"][0]
        self.assertEqual(mat["emissiveFactor"], [1.0, 1.0, 1.0])
        self.assertEqual(mat["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"], 3.0)
        self.assertIn("KHR_materials_emissive_strength", doc["extensionsUsed"])
        slots = glb_materials.material_slots(os.path.join(self.tmp, "b.glb"))
        self.assertTrue(slots["emissive"]["present"])
        self.assertEqual(slots["emissive"]["format"], "png")
        em = np.asarray(Image.open(io.BytesIO(gr.glb_image_bytes(doc, binary, len(doc["images"]) - 1))))
        self.assertGreater(int(em[60, 60].max()), 100)
        self.assertEqual(int(em[150, 150].max()), 0)

    def test_applied_again_replaces_its_own_map_and_off_clears_it(self):
        src = os.path.join(self.tmp, "a.glb")
        textured_glb(src, lit_texture())
        gr.glow_files(src, ["teal"])
        doc1, _ = self.read(src)
        gr.glow_files(src, ["teal"], strength=1.0)
        doc2, _ = self.read(src)
        self.assertEqual(len(doc2["images"]), len(doc1["images"]))
        self.assertNotIn("extensions", doc2["materials"][0])
        self.assertNotIn("extensionsUsed", doc2)
        gr.glow_files(src, [])
        doc3, _ = self.read(src)
        self.assertNotIn("emissiveTexture", doc3["materials"][0])

    def test_an_own_emissive_map_is_kept(self):
        src = os.path.join(self.tmp, "a.glb")
        own = np.zeros((16, 16, 3), np.uint8)
        own[4:8, 4:8] = 255
        textured_glb(src, lit_texture(), emissive=own)
        rep = gr.glow_files(src, ["teal"], mask_out=os.path.join(self.tmp, "m.png"))
        self.assertTrue(rep["emissive"]["0"].startswith("kept"))
        doc, binary = self.read(src)
        tex = doc["materials"][0]["emissiveTexture"]["index"]
        self.assertEqual(doc["images"][doc["textures"][tex]["source"]]["name"], "own_emissive")
        self.assertGreater(int(np.asarray(Image.open(os.path.join(self.tmp, "m.png"))).max()), 127)

    def test_a_picture_inside_the_json(self):
        src = os.path.join(self.tmp, "a.glb")
        textured_glb(src, lit_texture(), data_uri=True)
        rep = gr.glow_files(src, ["teal"])
        self.assertGreater(rep["glow_pixels"], 0)
        doc, _ = self.read(src)
        self.assertIn("emissiveTexture", doc["materials"][0])


class GlowCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_glow_cmd_")
        self.src = os.path.join(self.tmp, "export")
        os.makedirs(self.src)
        tex = lit_texture()
        dot(tex, 150, 150, 4, (20, 230, 200))   # a light on the "skin" the mask marks
        textured_glb(os.path.join(self.src, "unit.glb"), tex)
        mask = np.zeros(tex.shape, np.uint8)
        mask[130:170, 130:170, 0] = 255
        Image.fromarray(mask, "RGB").save(os.path.join(self.src, "regions_mask.png"))
        with open(os.path.join(self.src, "model.json"), "w") as f:
            json.dump({"name": "unit", "regions": {"mask": "regions_mask.png", "skin_pixels": 1600,
                                                   "glow_pixels": None}}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_folder(self):
        out = os.path.join(self.tmp, "out")
        code = quiet([self.src, "--out", out, "--text", "a figure with small glowing teal nodes"])
        self.assertEqual(code, 0)
        for f in ("unit.glb", "regions_mask.png", "model.json", "glow.json", "glow_emissive.png"):
            self.assertTrue(os.path.isfile(os.path.join(out, f)), f)
        mask = np.asarray(Image.open(os.path.join(out, "regions_mask.png")))
        self.assertEqual(int(mask[150, 150, 0]), 255)        # the skin kept
        self.assertEqual(int(mask[150, 150, 2]), 0)          # and never glowing
        self.assertGreater(int(mask[60, 60, 2]), 127)
        with open(os.path.join(out, "model.json")) as f:
            regions = json.load(f)["regions"]
        self.assertEqual(regions["glow_colours"], ["teal"])
        self.assertGreater(regions["glow_pixels"], 0)
        self.assertEqual(regions["skin_pixels"], 1600)
        # Again from the written folder, its colours from model.json, in place: the same result.
        self.assertEqual(quiet([out, "--out", out]), 0)
        with open(os.path.join(out, "glow.json")) as f:
            self.assertEqual(json.load(f)["colours"], ["teal"])
        self.assertEqual(quiet([out, "--out", out, "--off"]), 0)
        self.assertEqual(int(np.asarray(Image.open(os.path.join(out, "regions_mask.png")))[..., 2].max()), 0)

    def test_glb_file_and_problems(self):
        out = os.path.join(self.tmp, "out2")
        glb = os.path.join(self.src, "unit.glb")
        self.assertEqual(quiet([glb, "--out", out, "--colours", "teal", "--set", "max_region=0.1"]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "glow_mask.png")))
        self.assertEqual(quiet([glb, "--out", out]), 2)                          # no colours
        self.assertEqual(quiet([glb, "--out", out, "--colours", "plaid"]), 2)    # unknown colour
        self.assertEqual(quiet([glb, "--out", out, "--colours", "teal", "--set", "nope=1"]), 2)

    def test_order_piece_text(self):
        order = os.path.join(self.tmp, "order.json")
        with open(order, "w") as f:
            json.dump({"version": 1, "style": {"global": "Clean studio art.", "view_humanoid": "Front view.",
                                               "tier_lit": "Gear with glowing emerald lines."},
                       "families": [{"id": "crew", "recipe": "humanoid", "assets": [
                           {"id": "pilot", "tier": "lit", "brief": "A pilot with a glowing violet visor."}]}]}, f)
        text = glow_cmd.piece_text(order, "crew/pilot")
        self.assertEqual(gr.colours_in_text(text), ["violet", "emerald"])


class ExportJobs(unittest.TestCase):
    def test_glow_of_a_job(self):
        class F:
            def resolve(self, ref):
                return ("/x/%s.png" % ref["ref"], None)
        ins = {"reference": {"ref": "cut", "role": "rgba"}}
        self.assertIsNone(local_gpu.job_glow(F(), {}, ins))
        self.assertIsNone(local_gpu.job_glow(F(), {"glow": False, "glow_colours": ["cyan"]}, ins))
        self.assertTrue(local_gpu.job_glow(F(), {"glow": True}, ins))
        g = local_gpu.job_glow(F(), {"glow_colours": "cyan, violet", "glow_strength": 3, "glow_value": 0.5}, ins)
        self.assertEqual(g, {"colours": ["cyan", "violet"], "reference": "/x/cut.png", "strength": 3,
                             "settings": {"value": 0.5}})
        cmd, settings, inputs = local_gpu.glow_args(dict(g, reference=None))
        self.assertEqual(cmd[:2], ["--glow-colours", "cyan,violet"])
        self.assertIn("--glow-strength", cmd)
        self.assertEqual(json.loads(cmd[cmd.index("--glow-settings") + 1]), {"value": 0.5})
        self.assertEqual(settings["glow_colours"], ["cyan", "violet"])
        self.assertEqual(local_gpu.glow_args(True)[0], ["--glow", "--glow-hues", "165,225"])

    def test_glow_settings_are_checked(self):
        decl = stages.GLOW_SETTINGS["glow_colours"]
        self.assertIsNone(stages.setting_problem(decl, "cyan,emerald"))
        self.assertIsNone(stages.setting_problem(decl, ["10-40", "violet"]))
        self.assertIsNotNone(stages.setting_problem(decl, "cyan,plaid"))
        self.assertIsNotNone(stages.setting_problem(decl, ""))
        self.assertIsNotNone(stages.setting_problem(decl, 3))
        for recipe, option in (("humanoid", "humanoid_export"), ("creature", "creature_export")):
            declared = stages.option(recipe, "export", option)["settings"]
            for name in ("glow", "glow_colours", "glow_strength", "glow_value", "glow_chroma", "glow_max_region"):
                self.assertIn(name, declared)

    def test_recipes_pass_the_texts_colours_and_the_cutout(self):
        from forge import recipes
        st = {"global": "Studio art.", "view_humanoid": "Front view.", "view_creature": "Front view.",
              "tier_lit": "Gear with glowing emerald lines."}
        fam = {"id": "crew", "recipe": "humanoid"}
        lit = recipes.recipe_humanoid(fam, {"id": "pilot", "tier": "lit", "brief": "A pilot."}, st)
        plain = recipes.recipe_humanoid(fam, {"id": "clerk", "brief": "A clerk in a grey suit."}, st)
        exports = [j for j in lit if j.get("stage") == "export"]
        self.assertTrue(exports)
        for j in exports:
            self.assertEqual(j["args"]["glow_colours"], ["emerald"])
            self.assertEqual(j["inputs"]["reference"], {"ref": j["id"].rsplit("/", 1)[0] + "/cutout", "role": "rgba"})
        for j in (j for j in plain if j.get("stage") == "export"):
            self.assertNotIn("glow_colours", j["args"])
            self.assertNotIn("reference", j["inputs"])
        cfam = {"id": "beasts", "recipe": "creature"}
        piece = {"id": "crawler", "brief": "A crawler with glowing violet eyes.", "body": {"plan": "biped"}}
        try:
            jobs = recipes.recipe_creature(cfam, piece, st)
        except (KeyError, ValueError):
            self.skipTest("the creature body plan of this sample is not known")
        for j in (j for j in jobs if j.get("stage") == "export"):
            self.assertEqual(j["args"]["glow_colours"], ["violet"])
            self.assertTrue(j["inputs"]["reference"]["ref"].endswith("/cutout"))


if __name__ == "__main__":
    unittest.main()
