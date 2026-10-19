"""The articulated tools without Blender (tools/blender/articulated_parts.py): the order's
part list, boxes resolved against an object's points (the narrowest section), pivots, axes in
glTF, the review's sweeps and the squaring of a turned footprint."""

import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import articulated_parts as ap  # noqa: E402

TANK = [{"id": "hull"},
        {"id": "turret", "parent": "hull", "find": {"box": [[-0.45, -0.4, [0.5, 0.75]], [0.45, 0.45, None]]},
         "joint": {"kind": "turn", "axis": "up", "pivot": "cut", "range": [-180, 180]}},
        {"id": "gun", "parent": "turret", "find": {"box": [[-0.08, 0.2, 0.6], [0.08, None, 0.95]]},
         "joint": {"kind": "hinge", "axis": "-side", "pivot": "back", "range": [-8, 20]}}]


def box_points(lo, hi, n=8):
    """Points on the surface of a box (model coordinates)."""
    pts = []
    for a in range(3):
        for v in (lo[a], hi[a]):
            g = np.meshgrid(*[np.linspace(lo[b], hi[b], n) for b in range(3) if b != a], indexing="ij")
            m = np.zeros((g[0].size, 3))
            others = [b for b in range(3) if b != a]
            m[:, others[0]], m[:, others[1]], m[:, a] = g[0].ravel(), g[1].ravel(), v
            pts.append(m)
    return np.vstack(pts)


BOXES = (((-0.6, -1.0, 0.0), (0.6, 1.0, 0.5)), ((-0.25, -0.25, 0.5), (0.25, 0.25, 0.56)),
         ((-0.45, -0.5, 0.56), (0.45, 0.5, 0.9)), ((-0.05, -1.4, 0.7), (0.05, -0.5, 0.78)))


def tank_points():
    """A hull 1.2 wide, 2.0 long and 0.5 high, a narrow ring up to 0.56, a turret to 0.9 and a
    barrel to the front (-Y)."""
    return np.vstack([box_points(lo, hi) for lo, hi in BOXES])


def tank_mesh():
    """The same tank as boxes of 8 vertices and 12 triangles each: (vertices, faces)."""
    verts, faces = [], []
    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    for lo, hi in BOXES:
        base = len(verts)
        verts += [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        for a, b, c, d in quads:
            faces += [(base + a, base + b, base + c), (base + a, base + c, base + d)]
    return np.array(verts, dtype=np.float64), np.array(faces)


class PartList(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(ap.check_parts(TANK), [])
        rotor = [{"id": "wing"},
                 {"id": "nacelle_l", "parent": "wing", "find": {"box": [[0.4, None, None], [None, None, None]]},
                  "joint": {"kind": "hinge", "axis": "side", "pivot": "cut", "range": [0, 90]}},
                 {"id": "rotor_l", "parent": "nacelle_l", "find": {"boxes": [[[0.4, 0.3, None], [None, None, None]]]},
                  "joint": {"kind": "spin", "axis": "forward"}},
                 {"id": "mount", "parent": "wing", "find": {"box": [[-0.1, -0.1, 0.0], [0.1, 0.1, 0.2]]}},
                 {"id": "hatch", "parent": "mount", "find": {"box": [[-0.05, -0.05, 0.15], [0.05, 0.05, 0.2]]},
                  "joint": {"kind": "slide", "axis": "forward", "range": [0, 0.3]}}]
        self.assertEqual(ap.check_parts(rotor), [])
        self.assertEqual(ap.joint_of(rotor[2])["range"], [-180.0, 180.0])
        self.assertEqual(ap.joint_of(rotor[3])["kind"], "fixed")

    def test_problems(self):
        def problems(parts):
            return " | ".join(ap.check_parts(parts))
        self.assertIn("exactly one part has no parent", problems([{"id": "a"}, {"id": "b"}]))
        self.assertIn("must be a part listed before it", problems([{"id": "a"}, {"id": "b", "parent": "c",
                                                                               "find": TANK[1]["find"]}]))
        self.assertIn("the root (no parent)", problems([{"id": "a", "joint": TANK[1]["joint"]}]))
        bad = dict(TANK[1], find={"box": [[0, 0, [0.7, 0.5]], [1, 1, 1]]})
        self.assertIn("low < high", problems([TANK[0], bad]))
        bad = dict(TANK[1], joint=dict(TANK[1]["joint"], range=[10, 20]))
        self.assertIn("low <= 0 <= high", problems([TANK[0], bad]))
        bad = dict(TANK[1], joint=dict(TANK[1]["joint"], axis="diagonal"))
        self.assertIn("joint.axis must be", problems([TANK[0], bad]))
        bad = dict(TANK[1], joint={"kind": "fixed", "axis": "up"})
        self.assertIn("no axis or range", problems([TANK[0], bad]))
        self.assertIn("appears twice", problems([TANK[0], TANK[1], TANK[1]]))
        self.assertIn("find must be", problems([TANK[0], dict(TANK[1], find={"box": TANK[1]["find"]["box"],
                                                                            "boxes": []})]))


class Regions(unittest.TestCase):
    def test_the_turret_is_cut_at_its_ring(self):
        pts = tank_points()
        dims = ap.dims_of(pts)
        self.assertEqual(tuple(round(x, 3) for x in dims), (1.2, 2.4, 0.9))
        verts, faces = tank_mesh()
        lo, hi, rec = ap.resolve_box(TANK[1]["find"]["box"], verts, dims, faces)
        # The narrowest section between 0.5 and 0.75 of the height is the ring (0.5 to 0.56 m).
        self.assertTrue(0.5 < lo[2] < 0.56, lo)
        self.assertTrue(math.isinf(hi[2]))
        # Forward is -Y: the box's back edge (-0.4) is +Y.
        self.assertAlmostEqual(hi[1], 0.4 * 2.4)
        self.assertAlmostEqual(lo[1], -0.45 * 2.4)
        self.assertIn("up_low", rec)
        inside = ap.inside(pts, lo, hi)
        self.assertTrue(inside[(pts[:, 2] > 0.8) & (np.abs(pts[:, 1]) < 0.4)].all())
        self.assertFalse(inside[pts[:, 2] < 0.45].any())

    def test_a_gap_is_the_best_cut(self):
        pts = np.vstack([box_points((-1, -1, 0), (1, 1, 1)), box_points((-1, -1, 1.5), (1, 1, 2))])
        level = ap.narrowest(pts, 2, 0.5, 1.8, np.full(3, -np.inf), np.full(3, np.inf))
        self.assertTrue(1.0 < level < 1.5, level)

    def test_pivots_and_axes(self):
        part = box_points((-0.45, -0.5, 0.56), (0.45, 0.5, 0.9))
        cap = np.array([[0.2, 0.2, 0.56], [-0.2, -0.2, 0.56]])
        dims = (1.2, 2.4, 0.9)
        self.assertTrue(np.allclose(ap.pivot_point("cut", part, cap, dims), [0, 0, 0.56]))
        self.assertTrue(np.allclose(ap.pivot_point("back", part, cap, dims), [0, 0.5, 0.73]))
        self.assertTrue(np.allclose(ap.pivot_point("front", part, cap, dims), [0, -0.5, 0.73]))
        self.assertTrue(np.allclose(ap.pivot_point([0.5, 0.5, 1.0], part, cap, dims), [0.6, -1.2, 0.9]))
        # glTF: y up, front +Z, left +X.
        self.assertEqual(ap.to_gltf(ap.axis_vector("up")), [0.0, 1.0, 0.0])
        self.assertEqual(ap.to_gltf(ap.axis_vector("forward")), [0.0, 0.0, 1.0])
        self.assertEqual(ap.to_gltf(ap.axis_vector("-side")), [-1.0, 0.0, 0.0])
        rec = ap.joint_record(TANK[2], ap.joint_of(TANK[2]), np.array([0, -0.5, 0.74]), np.array([0, 0, 0.56]))
        self.assertEqual(rec["axis"], [-1.0, 0.0, 0.0])
        self.assertEqual(rec["offset_m"], [0.0, 0.18, 0.5])
        self.assertEqual((rec["unit"], rec["range"]), ("degrees", [-8.0, 20.0]))
        fixed = ap.joint_record({"id": "m", "parent": "hull"}, ap.joint_of({"id": "m"}), np.zeros(3), np.zeros(3))
        self.assertIsNone(fixed["axis"])

    def test_sweeps_and_squaring(self):
        self.assertEqual(ap.sweep_values([-8, 20], 5), [-8.0, -1.0, 6.0, 13.0, 20.0])
        self.assertEqual(ap.sweep_values([0, 1], 1), [1.0])
        t = math.radians(12)
        xy = np.array([[x, y] for x in np.linspace(-1, 1, 9) for y in (-0.4, 0.4)] +
                      [[x, y] for x in (-1, 1) for y in np.linspace(-0.4, 0.4, 5)])
        turned = xy @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])
        self.assertAlmostEqual(ap.snap_yaw(turned), -12.0, delta=1.0)
        self.assertEqual(ap.snap_yaw(xy), 0.0)


if __name__ == "__main__":
    unittest.main()
