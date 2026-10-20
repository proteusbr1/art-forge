"""The held item's geometry without Blender (tools/blender/held_item_geometry.py): the order's
item object, the frame of a generated mesh (found from the reference picture's silhouette and
a gun's grip and barrel, or given; the squaring), the real size, the socket proposal of the
class templates on synthetic meshes, the socket checks (missing, off the mesh), a manual
sockets file and the fit measures. The found frame's cases are the first real night's: a
side view that came out with its length along glTF z, reversed, upside down, turned, round,
or broken."""

import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import held_item_geometry as hg  # noqa: E402

# A carbine in item space (metres; +x front, +z top, +y left), 0.9 m long: boxes (lo, hi).
CARBINE = {
    "stock": ((0.0, -0.02, 0.05), (0.22, 0.02, 0.14)),
    "receiver": ((0.22, -0.025, 0.10), (0.55, 0.025, 0.19)),
    "barrel": ((0.55, -0.012, 0.14), (0.90, 0.012, 0.165)),
    "grip": ((0.26, -0.015, 0.0), (0.31, 0.015, 0.10)),
    "magazine": ((0.36, -0.015, 0.02), (0.42, 0.015, 0.10)),
    "handguard": ((0.55, -0.022, 0.11), (0.72, 0.022, 0.18)),
    "sight": ((0.35, -0.01, 0.19), (0.45, 0.01, 0.22)),
}
# A pistol: a slide and a grip leaning back.
PISTOL = {
    "slide": ((0.0, -0.014, 0.09), (0.20, 0.014, 0.135)),
    "grip": ((0.01, -0.016, 0.0), (0.06, 0.016, 0.09)),
}


def box_mesh(boxes, n=4):
    """(vertices, triangles) of boxes, each face a grid of n x n quads (two triangles each)."""
    verts, tris = [], []
    for lo, hi in boxes:
        lo, hi = np.array(lo, float), np.array(hi, float)
        for axis in range(3):
            o = [b for b in range(3) if b != axis]
            for side in (0, 1):
                base = len(verts)
                for i in range(n + 1):
                    for j in range(n + 1):
                        p = np.zeros(3)
                        p[axis] = hi[axis] if side else lo[axis]
                        p[o[0]] = lo[o[0]] + (hi[o[0]] - lo[o[0]]) * i / n
                        p[o[1]] = lo[o[1]] + (hi[o[1]] - lo[o[1]]) * j / n
                        verts.append(p)
                for i in range(n):
                    for j in range(n):
                        a = base + i * (n + 1) + j
                        tris += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    return np.array(verts), np.array(tris)


def as_generated(verts_item, tilt_deg=0.0, scale=0.37, offset=(0.1, -0.2, 0.05), front="+x", top="+y"):
    """The item as a mesh generator might write it: glTF axes with its front along `front`
    and its top along `top`, turned by tilt_deg in the side view, at the generator's scale."""
    r0 = hg.base_rotation(front, top)
    a = math.radians(tilt_deg)
    tilt = np.array([[math.cos(a), 0.0, -math.sin(a)], [0.0, 1.0, 0.0], [math.sin(a), 0.0, math.cos(a)]])
    item = (verts_item - verts_item.mean(0)) @ tilt.T  # the nose goes up by tilt_deg (about item y)
    return item @ r0 * scale + np.array(offset)


def side_picture(boxes, ppm=500.0, pad=12):
    """The alpha of a strict side view of boxes given in item space (x to the right, z up), as
    the cutout of the reference stage: 2-D, row 0 the top."""
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    w = int(round((hi[0] - lo[0]) * ppm)) + 2 * pad
    h = int(round((hi[2] - lo[2]) * ppm)) + 2 * pad
    alpha = np.zeros((h, w), bool)
    for b0, b1 in boxes:
        x0, x1 = (int(round((v - lo[0]) * ppm)) + pad for v in (b0[0], b1[0]))
        z0, z1 = (int(round((v - lo[2]) * ppm)) for v in (b0[2], b1[2]))
        alpha[h - pad - z1:h - pad - z0, x0:x1] = True
    return alpha


def turned_about_y(verts, deg):
    """Vertices (glTF) turned deg degrees about glTF y (the generators' up)."""
    a = math.radians(deg)
    r = np.array([[math.cos(a), 0.0, math.sin(a)], [0.0, 1.0, 0.0], [-math.sin(a), 0.0, math.cos(a)]])
    return verts @ r.T


def inside(p, box, margin=0.004):
    lo, hi = box
    return all(lo[i] - margin <= p[i] <= hi[i] + margin for i in range(3))


class ItemObject(unittest.TestCase):
    def test_valid_and_resolved(self):
        self.assertEqual(hg.check_item({"class": "rifle"}), [])
        self.assertEqual(hg.check_item({"class": "hand", "template": "thrown", "clips": ["idle_thrown"]}), [])
        t = hg.resolve({"class": "rifle", "sockets": {"top": None, "sling": {"at": [0.1, 0.5, 0.3], "snap": "surface"}}})
        self.assertNotIn("top", t["sockets"])
        self.assertEqual(t["sockets"]["sling"]["snap"], "surface")
        self.assertEqual(t["sockets"]["grip"]["band"], [0.0, 0.45])
        self.assertTrue(t["square"])
        self.assertFalse(hg.resolve({"class": "pistol"})["square"])
        for name, tpl in hg.TEMPLATES.items():
            self.assertIn("grip", tpl["sockets"], name)
            for sock, spec in tpl["sockets"].items():
                self.assertEqual(hg.check_socket(sock, spec), [], (name, sock))

    def test_problems(self):
        def problems(item):
            return " | ".join(hg.check_item(item))
        self.assertIn("lower-case word", problems({"class": "two_hand"}))
        self.assertIn("give item.template", problems({"class": "hand"}))
        self.assertIn("not a class template", problems({"class": "rifle", "template": "bow"}))
        self.assertIn("cannot be removed", problems({"class": "rifle", "sockets": {"grip": None}}))
        self.assertIn("at must be", problems({"class": "rifle", "sockets": {"x": {"at": [2, 0, 0]}}}))
        self.assertIn("snap must be", problems({"class": "rifle", "sockets": {"x": {"at": [0, 0, 0], "snap": "y"}}}))
        self.assertIn("band must be", problems({"class": "rifle", "sockets": {"x": {"at": [0, 0, 0], "band": [1, 0]}}}))
        self.assertIn("unknown keys", problems({"class": "rifle", "colour": "red"}))
        self.assertIn("clips must be", problems({"class": "rifle", "clips": "idle"}))


class Frame(unittest.TestCase):
    def test_axes(self):
        r = hg.base_rotation("+x", "+y")
        np.testing.assert_allclose(r @ [1, 0, 0], [1, 0, 0])
        np.testing.assert_allclose(r @ [0, 1, 0], [0, 0, 1])
        np.testing.assert_allclose(r @ [0, 0, 1], [0, -1, 0])  # the camera side is the item's right
        self.assertAlmostEqual(np.linalg.det(hg.base_rotation("-z", "+x")), 1.0)
        with self.assertRaises(ValueError):
            hg.base_rotation("+x", "-x")

    def test_rotation_between(self):
        for a, b in (([1, 0, 0], [0, 1, 0]), ([1, 0, 0], [-1, 0, 0]), ([0.3, 0.2, 0.9], [1, 0, 0])):
            r = hg.rotation_between(a, b)
            np.testing.assert_allclose(r @ (np.array(a) / np.linalg.norm(a)), np.array(b) / np.linalg.norm(b),
                                       atol=1e-9)
            self.assertAlmostEqual(np.linalg.det(r), 1.0)


class Carbine(unittest.TestCase):
    def setUp(self):
        self.verts, self.tris = box_mesh(CARBINE.values())
        self.template = hg.resolve({"class": "carbine"})

    def place(self, verts, **kw):
        kw.setdefault("length", 0.9)
        return hg.place_item(verts, self.tris, self.template, **kw)

    def socket_in_carbine(self, res):
        """The sockets in the carbine's own space (its box's low corner is (0, -0.025, 0))."""
        return {k: np.array(v) - np.array(res["box"][0]) + np.array([0.0, -0.025, 0.0])
                for k, v in res["sockets"].items()}

    def test_a_straight_generated_carbine_is_scaled_and_socketed(self):
        res = self.place(as_generated(self.verts))
        self.assertEqual(res["flags"], [])
        self.assertEqual(res["frame"]["square"]["turned_deg"], 0.0)  # not tilted by its grip and stock
        self.assertAlmostEqual(res["size_m"][0], 0.9, places=3)
        s = self.socket_in_carbine(res)
        self.assertTrue(inside(s["grip"], CARBINE["grip"]), s["grip"])
        self.assertTrue(inside(s["guard"], CARBINE["handguard"]), s["guard"])
        self.assertTrue(inside(s["muzzle"], CARBINE["barrel"]) and s["muzzle"][0] > 0.89, s["muzzle"])
        self.assertTrue(inside(s["butt"], CARBINE["stock"]) and s["butt"][0] < 0.01, s["butt"])
        self.assertGreater(s["top"][2], 0.21)
        self.assertEqual(res["sockets"]["grip"], [0.0, 0.0, 0.0])
        self.assertLess(res["fit"]["socket_shift"], 0.15)
        self.assertEqual(res["required"], ["butt", "grip", "guard", "muzzle"])

    def test_a_tilted_carbine_is_squared(self):
        res = self.place(as_generated(self.verts, tilt_deg=15.0))
        sq = res["frame"]["square"]
        self.assertTrue(sq["squared"] and 12.0 < sq["turned_deg"] < 22.0, sq)  # the principal axis is ~5 deg off
        self.assertAlmostEqual(res["size_m"][0], 0.9, places=3)
        self.assertEqual(res["flags"], [])
        s = self.socket_in_carbine(res)
        self.assertGreater(s["muzzle"][0], 0.85)
        self.assertLess(s["butt"][0], 0.05)
        self.assertLess(res["sockets"]["muzzle"][0] - res["sockets"]["butt"][0], 0.91)

    def test_matrix_maps_the_generated_mesh_into_item_space(self):
        g = as_generated(self.verts, tilt_deg=5.0, front="-z", top="+y")
        res = self.place(g, front="-z", top="+y")
        item = g @ res["matrix"].T + res["translation"]
        np.testing.assert_allclose(item.min(0), res["box"][0], atol=1e-6)
        np.testing.assert_allclose(item.max(0), res["box"][1], atol=1e-6)
        self.assertAlmostEqual(np.ptp(item[:, 0]), 0.9, places=3)
        self.assertGreater(np.ptp(item[:, 2]), np.ptp(item[:, 1]))  # its top is up

    def test_no_squaring_and_a_turned_mesh_is_flagged(self):
        res = self.place(as_generated(self.verts, tilt_deg=15.0), snap=False)
        self.assertEqual(res["frame"]["square"]["turned_deg"], 0.0)
        # Generated standing up, with a frame given by hand that says otherwise.
        res = self.place(as_generated(self.verts, front="+y", top="-x"), front="+x", top="+y")
        found = [f for f in res["flags"] if f.startswith("frame: the longest axis is ")]
        self.assertTrue(found and int(found[0].split()[5]) > 60, res["flags"])

    def test_a_manual_rotation_replaces_the_squaring(self):
        res = self.place(as_generated(self.verts, tilt_deg=15.0), rotate_deg=[0.0, 0.0, 0.0])
        self.assertEqual(res["frame"]["square"]["turned_deg"], 0.0)
        self.assertTrue(res["frame"]["square"]["manual"])

    def test_height_scales_instead_of_length(self):
        res = hg.place_item(as_generated(self.verts), self.tris, self.template, height=0.44)
        self.assertAlmostEqual(res["size_m"][2], 0.44, places=3)
        self.assertAlmostEqual(res["size_m"][0], 1.8, places=2)

    def test_a_missing_grip_and_an_off_mesh_socket_are_flagged(self):
        template = hg.resolve({"class": "carbine", "sockets": {
            "sling": {"at": [0.8, 0.5, 0.1], "band": [0.0, 0.3], "required": True}}})  # nothing under the barrel
        res = hg.place_item(as_generated(self.verts), self.tris, template, length=0.9)
        self.assertEqual(res["flags"], ["socket sling is missing: no geometry at its station"])
        self.assertIsNone(res["sockets"]["sling"])
        good = self.place(as_generated(self.verts))
        manual = dict(good["sockets"], guard=[0.3, 0.0, -0.2])
        res = self.place(as_generated(self.verts), manual_sockets=manual)
        self.assertTrue(any(f.startswith("socket guard is off the mesh") for f in res["flags"]), res["flags"])
        self.assertEqual(len(res["flags"]), 1)
        res = self.place(as_generated(self.verts), manual_sockets={"grip": [0.0, 0.0, 0.0]})
        self.assertIn("socket muzzle is missing: not in the manual sockets file", res["flags"])

    def test_a_manual_grip_moves_the_origin(self):
        good = self.place(as_generated(self.verts))
        manual = dict(good["sockets"])
        manual["grip"] = [0.01, 0.0, 0.02]
        res = self.place(as_generated(self.verts), manual_sockets=manual)
        self.assertEqual(res["sockets"]["grip"], [0.0, 0.0, 0.0])
        np.testing.assert_allclose(res["sockets"]["muzzle"], np.array(good["sockets"]["muzzle"]) - [0.01, 0.0, 0.02],
                                   atol=1e-6)
        np.testing.assert_allclose(np.array(res["box"][0]), np.array(good["box"][0]) - [0.01, 0.0, 0.02], atol=1e-6)

    def test_loose_geometry_and_fit(self):
        body = ((0.0, 0.0, 0.0), (0.6, 0.1, 0.1))
        bit = ((1.0, 0.0, 0.0), (1.05, 0.05, 0.05))  # a floating bit: 0.015 m2 of 0.275 m2
        verts, tris = box_mesh([body, bit])
        self.assertAlmostEqual(hg.loose_share(verts, tris), 0.015 / 0.275, places=6)
        f = hg.fit({"a": {"found": True, "snap": "section", "shift_m": 0.09}}, 0.9, 7.5, 0.01)
        self.assertEqual((f["socket_shift"], f["turned_deg"], f["loose_share"]), (0.1, 7.5, 0.01))
        self.assertAlmostEqual(f["excess"], 0.1 / 0.15 + 0.5 + 0.2, places=3)


class FoundFrame(unittest.TestCase):
    """The frame found without a hand-given front and top, on the night's failures made
    synthetic: the carbine (a gun) with and without its reference picture."""

    def setUp(self):
        self.verts, self.tris = box_mesh(CARBINE.values())
        self.template = hg.resolve({"class": "carbine"})
        self.picture = side_picture(list(CARBINE.values()))

    def place(self, verts, **kw):
        kw.setdefault("length", 0.9)
        return hg.place_item(verts, self.tris, self.template, **kw)

    def assertRightWayRound(self, res):
        """Front +x (the muzzle at the front end, the butt at the back), top +z (the grip below
        the barrel), the length at its real size, no flags."""
        self.assertEqual(res["flags"], [])
        self.assertAlmostEqual(res["size_m"][0], 0.9, places=2)
        s = res["sockets"]
        self.assertGreater(s["muzzle"][0] - s["butt"][0], 0.85)
        self.assertGreater(s["muzzle"][2], s["grip"][2] + 0.08)
        self.assertGreater(s["top"][2], 0.15)
        self.assertGreater(res["size_m"][2], 2.5 * res["size_m"][1])  # its side, not its top, faces the camera

    def test_every_way_the_generators_laid_it(self):
        for front in ("+x", "-x", "+z", "-z"):
            for picture in (None, self.picture):
                with self.subTest(front=front, picture=picture is not None):
                    res = self.place(as_generated(self.verts, front=front, top="+y"), reference=picture)
                    self.assertRightWayRound(res)
                    found = res["frame"]["orient"]
                    self.assertEqual((found["front_axis"], found["top_axis"]), (front, "+y"))
                    self.assertEqual(found["method"], "shape" if picture is None else "reference")
                    self.assertEqual(res["frame"]["front"], "auto")

    def test_upside_down(self):
        for picture in (None, self.picture):
            res = self.place(as_generated(self.verts, front="-z", top="-y"), reference=picture)
            self.assertRightWayRound(res)
            self.assertEqual(res["frame"]["orient"]["top_axis"], "-y")

    def test_turned_in_any_direction(self):
        g = turned_about_y(as_generated(self.verts, front="+x", top="+y"), 45.0)
        res = self.place(g, reference=self.picture)
        self.assertRightWayRound(res)
        front = np.array(res["frame"]["orient"]["front"])
        np.testing.assert_allclose(front, [math.sqrt(0.5), 0.0, -math.sqrt(0.5)], atol=0.1)
        # Without the picture its longest principal axis is its length: about 5 degrees off the
        # bore (the grip and the stock pull it), the right way round.
        res = self.place(g)
        self.assertEqual(res["flags"], [])
        s = res["sockets"]
        self.assertGreater(s["muzzle"][0] - s["butt"][0], 0.85)
        self.assertGreater(s["top"][2], 0.15)
        bore = turned_about_y(np.array([[1.0, 0.0, 0.0]]), 45.0)[0]
        self.assertLess(math.degrees(math.acos(float(res["rotation"][0] @ bore))), 8.0)

    def test_a_tilt_is_squared_to_the_picture_not_to_the_principal_axes(self):
        res = self.place(as_generated(self.verts, tilt_deg=15.0, front="+z"), reference=self.picture)
        sq = res["frame"]["square"]
        self.assertEqual(sq["length_by"], "reference")
        self.assertAlmostEqual(abs(sq["tilt_deg"]), 15.0, delta=2.0)
        self.assertRightWayRound(res)
        straight = self.place(as_generated(self.verts, front="+z"), reference=self.picture)
        self.assertEqual(straight["frame"]["square"]["turned_deg"], 0.0)  # the grip's pull is not a tilt

    def test_cues_that_disagree_are_flagged(self):
        mirrored = self.picture[:, ::-1]  # a picture with the muzzle to the left
        res = self.place(as_generated(self.verts), reference=mirrored)
        self.assertTrue(any(f.startswith("frame: the cues disagree") for f in res["flags"]), res["flags"])

    def test_a_mesh_that_matches_no_side_of_its_picture_is_flagged(self):
        ring = np.zeros((200, 200), bool)
        yy, xx = np.mgrid[0:200, 0:200]
        ring[(np.hypot(yy - 100, xx - 100) < 95) & (np.hypot(yy - 100, xx - 100) > 70)] = True
        res = self.place(as_generated(self.verts), reference=ring)
        self.assertTrue(any("matches its reference picture from no side" in f for f in res["flags"]), res["flags"])

    def test_two_crossed_bodies_are_flagged(self):
        a = as_generated(self.verts)
        a = a - a.mean(0)
        verts = np.vstack([a, turned_about_y(a, 90.0)])
        tris = np.vstack([self.tris, self.tris + len(a)])
        res = hg.place_item(verts, tris, self.template, length=0.9, reference=self.picture)
        self.assertTrue(any(f.startswith("frame: a long item with no one long axis") for f in res["flags"]),
                        res["flags"])

    def test_a_pistol_backwards_is_turned_round(self):
        verts, tris = box_mesh(PISTOL.values())
        for picture in (None, side_picture(list(PISTOL.values()))):
            res = hg.place_item(as_generated(verts, front="-x"), tris, hg.resolve({"class": "pistol"}), length=0.2,
                                reference=picture)
            self.assertEqual(res["flags"], [])
            self.assertEqual(res["frame"]["orient"]["front_axis"], "-x")
            self.assertGreater(res["sockets"]["muzzle"][0], 0.12)


class FoundFrameCompact(unittest.TestCase):
    def test_a_round_item_takes_the_up_of_its_picture_and_has_no_frame_flag(self):
        # A grenade lying on its side in its picture (the night's side views drew them so),
        # generated with that length along glTF z: a body, a fuse at one end, a lever on top.
        boxes = [((0.0, -0.03, 0.0), (0.09, 0.03, 0.06)), ((0.09, -0.012, 0.018), (0.11, 0.012, 0.042)),
                 ((0.01, -0.006, 0.06), (0.08, 0.006, 0.066))]
        verts, tris = box_mesh(boxes)
        template = hg.resolve({"class": "hand", "template": "thrown"})
        for front in ("+x", "-z", "+z"):
            res = hg.place_item(as_generated(verts, front=front), tris, template, length=0.11,
                                reference=side_picture(boxes))
            self.assertEqual(res["flags"], [], front)
            self.assertEqual(res["frame"]["orient"]["top_axis"], "+y")
            self.assertAlmostEqual(res["size_m"][0], 0.11, places=3)
            self.assertAlmostEqual(res["size_m"][2], 0.066, delta=0.004)

    def test_without_a_picture_a_compact_item_keeps_the_default_frame(self):
        verts, tris = box_mesh([((0.0, -0.03, 0.0), (0.06, 0.03, 0.09))])
        res = hg.place_item(as_generated(verts, front="+z"), tris, hg.resolve({"class": "hand", "template": "thrown"}),
                            length=0.06)
        self.assertEqual(res["flags"], [])
        self.assertEqual(res["frame"]["orient"]["method"], "default")


class Sockets(unittest.TestCase):
    def test_a_launcher_grip_behind_its_station_and_a_section_in_the_air(self):
        # A tube with a pistol grip at 0.17 to 0.22 of its length and a magazine at 0.55: the
        # grip's band at the class's station holds the tube's bottom and nothing under it.
        boxes = [((0.0, -0.05, 0.12), (1.0, 0.05, 0.22)), ((0.17, -0.015, 0.0), (0.22, 0.015, 0.12)),
                 ((0.55, -0.015, 0.03), (0.6, 0.015, 0.12))]
        verts, tris = box_mesh(boxes)
        res = hg.place_item(as_generated(verts, front="+z"), tris, hg.resolve({"class": "launcher"}), length=1.0,
                            reference=side_picture(boxes))
        self.assertEqual(res["flags"], [])
        grip = np.array(res["proposal"]["grip"]["at"]) - np.array(res["box"][0])
        self.assertTrue(0.18 <= grip[0] <= 0.21 and grip[2] < 0.12, grip)  # in the grip, not on its face
        self.assertLess(res["proposal"]["grip"]["station"], 0.22)

    def test_a_grip_with_no_geometry_within_reach_is_still_missing(self):
        boxes = [((0.0, -0.05, 0.12), (1.0, 0.05, 0.22)), ((0.62, -0.015, 0.0), (0.67, 0.015, 0.12))]
        verts, tris = box_mesh(boxes)
        res = hg.place_item(as_generated(verts), tris, hg.resolve({"class": "launcher"}), length=1.0,
                            reference=side_picture(boxes))
        self.assertIn("socket grip is missing: no geometry at its station", res["flags"])


class OtherTemplates(unittest.TestCase):
    def test_a_pistol_is_not_squared_by_its_grip(self):
        verts, tris = box_mesh(PISTOL.values())
        res = hg.place_item(as_generated(verts), tris, hg.resolve({"class": "pistol"}), length=0.2)
        self.assertEqual(res["frame"]["square"]["turned_deg"], 0.0)
        self.assertEqual(res["flags"], [])
        s = {k: np.array(v) - np.array(res["box"][0]) + np.array([0.0, -0.016, 0.0]) for k, v in res["sockets"].items()}
        self.assertTrue(inside(s["grip"], PISTOL["grip"], 0.01), s["grip"])
        self.assertTrue(s["muzzle"][0] > 0.19 and s["muzzle"][2] > 0.09, s["muzzle"])

    def test_a_thrown_object_holds_at_its_centre(self):
        verts, tris = box_mesh([((0.0, -0.03, 0.0), (0.06, 0.03, 0.09))])
        res = hg.place_item(as_generated(verts), tris, hg.resolve({"class": "hand", "template": "thrown"}),
                            length=0.06)
        self.assertEqual(res["flags"], [])
        np.testing.assert_allclose(res["box"][0], [-0.03, -0.03, -0.045], atol=1e-3)
        self.assertAlmostEqual(res["sockets"]["top"][2], 0.045, places=3)


if __name__ == "__main__":
    unittest.main()
