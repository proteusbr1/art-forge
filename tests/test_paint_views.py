"""The paint option hunyuan_concept: its view step (forge/paint_views.py) on synthetic views, how
it expands (held_item, humanoid, the one-mesh recipes), where its settings land, the order
check, and a run of the factory against the fake server (views, the view step, the bake, the
normals step)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import paint_views, stages  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.recipes import expand, refs_of  # noqa: E402
from test_held_item_recipe import order_of, stub_glb, LIBRARY  # noqa: E402

N = 256
AXES = paint_views.VIEW_AXES


def l_shape(size, x0, y0, s):
    """An L: a long bar with a foot, at (x0, y0), scale s (pixels per unit)."""
    m = np.zeros((size, size), bool)
    yy, xx = np.mgrid[0:size, 0:size]
    bar = (xx >= x0) & (xx < x0 + 6 * s) & (yy >= y0) & (yy < y0 + s)
    foot = (xx >= x0) & (xx < x0 + s) & (yy >= y0) & (yy < y0 + 3 * s)
    return bar | foot


def scene(cutout_mask):
    """Six views of an object whose side view 3 is an L (view 1 its mirror), with position and
    normal maps as Hunyuan3D Paint saves them, dull views, and a striped cutout."""
    views, mrs, pos, nrm = [], [], [], []
    for k in range(6):
        if k == 3:
            m = l_shape(N, 40, 80, 24)
        elif k == 1:
            m = l_shape(N, 40, 80, 24)[:, ::-1]
        else:
            m = np.zeros((N, N), bool)
            m[80:152, 110:146] = True
        v = np.full((N, N, 3), 0.5)
        v[m] = (0.25, 0.27, 0.22)
        views.append(v)
        mrs.append(np.full((N, N, 3), 0.5))
        p = np.ones((N, N, 3))
        yy, xx = np.mgrid[0:N, 0:N] / N
        p[m] = np.stack([xx, yy, np.full_like(xx, 0.5)], -1)[m] * 0.8 + 0.1
        pos.append(p)
        n = np.ones((N, N, 3))
        n[m] = (np.array(AXES[k]) + 1) / 2
        nrm.append(n)
    h, w = cutout_mask.shape
    cut = np.zeros((h, w, 4))
    stripes = ((np.arange(w) // 6) % 2)[None, :].repeat(h, 0)
    cut[..., 0] = np.where(stripes, 0.9, 0.2)
    cut[..., 1] = 0.3
    cut[..., 2] = np.where(stripes, 0.2, 0.8)
    cut[..., 3] = cutout_mask
    return views, mrs, pos, nrm, cut


class ViewStepTest(unittest.TestCase):
    def test_cutout_laid_on_the_view_it_fits(self):
        views, mrs, pos, nrm, cut = scene(l_shape(400, 30, 50, 50))
        out, out_mr, weights, rec = paint_views.make_views(views, mrs, pos, nrm, cut, size=512)
        self.assertTrue(rec["applied"])
        self.assertEqual(rec["match_view"], 3)
        self.assertGreater(rec["fit"]["iou"], 0.95)
        self.assertEqual(weights, paint_views.CONCEPT_WEIGHTS)
        self.assertEqual(out[0].shape, (512, 512, 3))
        self.assertEqual(out_mr[0].shape, (512, 512, 3))
        m3 = paint_views.resize_mask(paint_views.silhouette(pos[3]), 512)
        inner = paint_views.erode(m3, 6)
        # the stripes (red and blue) are on the view; the dull colour is gone
        self.assertGreater(out[3][inner][:, 0].std(), 0.2)
        self.assertGreater(rec["cutout_share"], 0.8)
        # the other views keep their colours; with match_colour they move toward the cutout's
        m1 = paint_views.resize_mask(paint_views.silhouette(pos[1]), 512)
        inner1 = paint_views.erode(m1, 6)
        self.assertTrue(np.allclose(out[1][inner1], (0.25, 0.27, 0.22), atol=0.01))
        self.assertIsNone(rec["colour"])
        matched, _, _, rec2 = paint_views.make_views(views, mrs, pos, nrm, cut, size=512, match_colour=True)
        self.assertGreater(abs(matched[1][inner1].mean(0) - np.array([0.25, 0.27, 0.22])).sum(), 0.05)
        self.assertIsNotNone(rec2["colour"])
        # the background is left alone
        far = ~paint_views.resize_mask(paint_views.silhouette(pos[1]), 512)
        far = paint_views.erode(far, 4)
        self.assertTrue(np.allclose(out[1][far], 0.5, atol=0.02))
        json.dumps(rec)  # the record is plain JSON

    def test_a_cutout_that_fits_no_view_only_enlarges(self):
        yy, xx = np.mgrid[0:400, 0:400]
        disc = (xx - 200) ** 2 + (yy - 200) ** 2 < 150 ** 2
        views, mrs, pos, nrm, cut = scene(disc)
        out, _, weights, rec = paint_views.make_views(views, mrs, pos, nrm, cut, size=512)
        self.assertFalse(rec["applied"])
        self.assertIn("fits no view", rec["reason"])
        self.assertEqual(weights, paint_views.DEFAULT_WEIGHTS)
        for k in range(6):
            self.assertTrue(np.allclose(out[k], paint_views.resize(views[k], 512)))

    def test_surface_away_from_the_camera_keeps_the_view(self):
        """Where the surface turns away from the cutout's camera (here: half the L faces 70
        degrees away) the cutout is not laid."""
        views, mrs, pos, nrm, cut = scene(l_shape(400, 30, 50, 50))
        m = paint_views.silhouette(pos[3])
        tilted = (np.array([-np.cos(np.radians(70)), np.sin(np.radians(70)), 0]) + 1) / 2
        nrm[3][:, N // 2:][m[:, N // 2:]] = tilted
        out, _, _, rec = paint_views.make_views(views, mrs, pos, nrm, cut, size=256, min_fit=0.8)
        self.assertTrue(rec["applied"])
        right = paint_views.erode(m, 4).copy()
        right[:, :N // 2 + 8] = False
        left = paint_views.erode(m, 4).copy()
        left[:, N // 2 - 8:] = False
        self.assertGreater(out[3][left][:, 0].std(), 0.2)
        self.assertLess(out[3][right][:, 0].std(), 0.05)

    def test_run_writes_the_views_and_the_record(self):
        tmp = tempfile.mkdtemp(prefix="forge_pv_")
        try:
            views, mrs, pos, nrm, cut = scene(l_shape(400, 30, 50, 50))
            paths = {}
            for name, arrs in (("v", views), ("m", mrs), ("p", pos), ("n", nrm)):
                paths[name] = []
                for i, a in enumerate(arrs):
                    p = os.path.join(tmp, "%s%d.png" % (name, i))
                    paint_views.to_image(a).save(p)
                    paths[name].append(p)
            cp = os.path.join(tmp, "cut.png")
            Image.fromarray((cut * 255).astype(np.uint8), "RGBA").save(cp)
            out = paint_views.run(paths["v"], paths["m"], paths["p"], paths["n"], cp, os.path.join(tmp, "out"),
                                  size=512)
            self.assertEqual(len(out["albedo"]), 6)
            with Image.open(out["albedo"][3]) as im:
                self.assertEqual(im.size, (512, 512))
            with open(out["record"], encoding="utf-8") as f:
                self.assertTrue(json.load(f)["applied"])
            self.assertEqual(len(paint_views.code_fingerprint()), 64)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ExpansionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_pv_order_")
        stub_glb(os.path.join(self.tmp, "figure.glb"))
        shutil.copy(LIBRARY, os.path.join(self.tmp, "library.json"))
        Image.new("RGBA", (64, 64), (200, 100, 50, 255)).save(os.path.join(self.tmp, "ref.png"))
        self.config = load_config(None)
        for k in ("blender_python", "godot"):
            self.config[k] = sys.executable

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        path = os.path.join(self.tmp, "order.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(path)

    def test_held_item_jobs_and_settings(self):
        paint = {"option": "hunyuan_concept", "settings": {"steps": 20, "view_size": 1024, "min_fit": 0.9,
                                                           "bake_view_size": 1024, "match_colour": True}}
        order = self.load(order_of({"stages": {"paint": paint}}))
        self.assertEqual(check_order(order, self.config), [])
        jobs = {j["id"]: j for j in expand(order)}
        v = [k for k in jobs if k.endswith("/paint_views")][0].rsplit("/", 1)[0] + "/"
        views, concept, bake = jobs[v + "paint_views"], jobs[v + "paint_concept"], jobs[v + "paint"]
        self.assertEqual(views["workflow"], "hunyuan21_paint_views.json")
        self.assertTrue(views["heavy"])
        self.assertEqual(views["set"], {"9.steps": 20, "9.view_size": 1024})
        self.assertNotIn("args", views)
        self.assertEqual(views["values"]["$MESH$"], {"ref": v + "mesh", "role": "game_mesh", "transport": "local_path"})
        self.assertEqual(concept["kind"], "paint_views")
        self.assertEqual(concept["args"], {"min_fit": 0.9, "size": 1024, "match_colour": True})
        self.assertNotIn("set", concept)
        self.assertEqual(concept["inputs"]["view_5"], {"ref": v + "paint_views", "role": "views_5"})
        self.assertEqual(concept["inputs"]["normal_0"], {"ref": v + "paint_views", "role": "normals"})
        self.assertEqual(concept["inputs"]["cutout"], {"ref": v + "cutout", "role": "rgba"})
        self.assertEqual(bake["workflow"], "hunyuan21_bake_views.json")
        self.assertEqual(bake["mesh_role"], "painted_mesh")
        self.assertNotIn("set", bake)
        self.assertEqual(bake["values"]["$ALBEDO3$"], {"ref": v + "paint_concept", "role": "albedo_3"})
        self.assertEqual(bake["values"]["$TEXTURE$"], 2048)
        self.assertEqual(set(refs_of(bake)), {v + "mesh", v + "paint_concept"})
        item = jobs[v + "item"]
        self.assertEqual(item["inputs"]["mesh"], {"ref": v + "paint", "role": "painted_mesh"})
        self.assertEqual(item["inputs"]["metal_roughness"], {"ref": v + "paint", "role": "metal_roughness"})

    def test_sample_order_is_valid(self):
        order = load_order(os.path.join(HERE, "fixtures", "order_paint_concept.json"))
        self.assertEqual(check_order(order, self.config), [])
        ids = [j["id"] for j in expand(order)]
        for step in ("mesh", "paint_views", "paint_concept", "paint", "normals"):
            self.assertIn("objects/lantern/" + step, ids)
            self.assertIn("props/crate/" + step, ids)

    def test_settings_check(self):
        paint = {"option": "hunyuan_concept", "settings": {"min_fit": 1.5, "bake_view_size": 1000}}
        problems = check_order(self.load(order_of({"stages": {"paint": paint}})), self.config)
        self.assertTrue(any("min_fit" in p for p in problems), problems)
        self.assertTrue(any("bake_view_size" in p for p in problems), problems)

    def test_one_mesh_recipes(self):
        """object_3d, prop and vehicle: by default the paint stays in the mesh job (the jobs do
        not change); hunyuan_concept paints the mesh job's shape in three jobs."""
        for recipe in ("object_3d", "prop", "vehicle"):
            with self.subTest(recipe=recipe):
                style = {"global": "g", "view_object": "o", "view_prop": "p", "view_vehicle": "v", "emissive": "e"}
                piece = {"id": "box", "brief": "A box."}
                if recipe != "vehicle":
                    piece["inputs"] = {"reference": "ref.png"}
                base = {"version": 1, "style": style, "families": [{"id": "f", "recipe": recipe, "assets": [piece]}]}
                plain = {j["id"]: j for j in expand(self.load(base))}
                self.assertEqual(plain["f/box/mesh"]["mesh_role"], "painted_mesh")
                self.assertEqual(plain["f/box/normals"]["input"], {"ref": "f/box/mesh", "role": "painted_mesh"})
                piece["stages"] = {"paint": {"option": "hunyuan_concept", "settings": {"steps": 12}}}
                order = self.load(base)
                self.assertEqual(check_order(order, self.config), [])
                jobs = {j["id"]: j for j in expand(order)}
                mesh = jobs["f/box/mesh"]
                self.assertEqual(mesh["roles"], {"6": "shape"})
                self.assertNotIn("mesh_role", mesh)
                self.assertTrue(set(stages.HUNYUAN21_PAINT_NODES) <= set(mesh["drop"]))
                self.assertFalse(any(k.startswith("9.") for k in mesh.get("set", {})))
                self.assertEqual(jobs["f/box/paint_views"]["values"]["$MESH$"]["role"], "shape")
                self.assertEqual(jobs["f/box/paint_views"]["set"], {"9.steps": 12})
                self.assertEqual(jobs["f/box/paint"]["values"]["$TEXTURE$"], 4096 if recipe == "vehicle" else 2048)
                self.assertEqual(jobs["f/box/normals"]["input"], {"ref": "f/box/paint", "role": "painted_mesh"})


class FakeNightTest(unittest.TestCase):
    """The three jobs run against the fake server: six views come back, the view step lays the
    cutout (or not) and the bake's painted mesh feeds the normals step."""

    def test_object_3d_night(self):
        tmp = tempfile.mkdtemp(prefix="forge_pv_night_")
        fake = FakeComfy()
        try:
            ref = os.path.join(tmp, "ref.png")
            im = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            im.paste((200, 60, 40, 255), (20, 40, 108, 88))
            im.save(ref)
            order = {"version": 1, "style": {"global": "g", "view_object": "o"},
                     "families": [{"id": "f", "recipe": "object_3d", "assets": [
                         {"id": "box", "brief": "A box.", "inputs": {"reference": ref},
                          "stages": {"paint": "hunyuan_concept"}}]}]}
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(order, f)
            out = os.path.join(tmp, "out")
            env = dict(os.environ, COMFY_SERVER=fake.url, FORGE_PASS_SLEEP="0.02", FORGE_ERROR_SLEEP="0.1",
                       PYTHONDONTWRITEBYTECODE="1")
            res = subprocess.run([sys.executable, "-m", "forge", "--order", path, "--out", out], env=env,
                                 capture_output=True, text=True, timeout=300, cwd=ROOT)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            for jid in ("f/box/mesh", "f/box/paint_views", "f/box/paint_concept", "f/box/paint", "f/box/normals"):
                self.assertEqual(m[jid]["status"], "done", jid)
            self.assertEqual(sorted(r for r in m["f/box/paint_views"]["files"] if r.startswith("views")),
                             ["views"] + ["views_%d" % i for i in range(1, 6)])
            files = m["f/box/paint_concept"]["files"]
            self.assertEqual(len([r for r in files if r.startswith("albedo_")]), 6)
            with open(os.path.join(out, files["record"][0]), encoding="utf-8") as f:
                self.assertIn("applied", json.load(f))
            self.assertIn("painted_mesh", m["f/box/paint"]["files"])
            bakes = [s[1] for s in fake.submitted if s[1].get("9", {}).get("class_type") == "Hy3D21UseMultiViews"]
            self.assertEqual(len(bakes), 1)
            self.assertTrue(all(not bakes[0][str(n)]["inputs"]["image"].startswith("$")
                                for n in list(range(100, 106)) + list(range(200, 206))))
            # a second run does nothing
            fake.reset_log()
            res = subprocess.run([sys.executable, "-m", "forge", "--order", path, "--out", out], env=env,
                                 capture_output=True, text=True, timeout=300, cwd=ROOT)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(fake.submitted, [])
        finally:
            fake.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
