"""The held item's geometry without Blender (tools/blender/held_item_geometry.py): the order's
item object, the frame of a generated mesh (its axes and the squaring to its principal
axes), the real size, the socket proposal of the class templates on synthetic meshes, the
socket checks (missing, off the mesh), a manual sockets file and the fit measures."""

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
        res = self.place(as_generated(self.verts, front="+y", top="-x"))  # generated standing up
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
