"""The conform of a variant's mesh to an approved body (tools/blender/humanoid_fit.py), on
synthetic figures of different sizes, proportions, bulk and facing (tests/humanoid_synth.py):
the joints land on the body's, the bone lengths become the body's, the thickness stays the
variant's, and the flags name a joint outside the outfit, an overstretched segment, a tear and
soles off the floor. No bpy."""

import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import humanoid_fit as hf  # noqa: E402
import humanoid_synth as hs  # noqa: E402

BODY = hs.figure(height=1.8)
ROLE_MAP = {r: r for r in hs.ROLES}


def conform(variant, body=BODY, **kw):
    return hf.conform(variant["joints"], body["joints"], ROLE_MAP, ROLE_MAP, variant["front"], variant["points"],
                      variant["index"], variant["weight"], variant["roles"], variant["triangles"],
                      body_points=body["points"], body_floor=float(body["points"][:, 2].min()), body_sample=400,
                      **kw)


def segment_distance(p, a, b):
    """Distance of points p to the segment a-b."""
    d = b - a
    t = np.clip((p - a) @ d / (d @ d), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * d), axis=1)


def part(variant, role):
    return np.array([variant["roles"][i] == role for i in variant["index"][:, 0]])


class ConformTest(unittest.TestCase):
    def test_a_scaled_turned_figure_lands_on_the_body_exactly(self):
        # The body itself, half as tall, facing +x and standing elsewhere: one similarity.
        v = hs.figure(height=0.9, yaw=90, offset=(3.0, -2.0, 0.5))
        res = conform(v)
        rep = res["report"]
        self.assertAlmostEqual(rep["prefit"]["scale"], 2.0, places=6)
        self.assertAlmostEqual(rep["prefit"]["turn_deg"] % 360, 270.0, places=5)
        self.assertLess(rep["joint_error_cm"], 1e-6)
        self.assertLess(rep["stretch_max"], 1e-6)
        np.testing.assert_allclose(res["points"], BODY["points"], atol=1e-9)
        self.assertEqual(rep["flags"], [])

    def test_other_proportions_take_the_bodys_joints_and_keep_their_thickness(self):
        v = hs.figure(height=1.0, arms=1.25, legs=0.85, bulk=1.6, yaw=-90)
        res = conform(v)
        rep = res["report"]
        # Every joint of the limbs lands on the body's; the torso's spine joints come near.
        for role, j in rep["joints"].items():
            if j["kind"] == "torso":
                self.assertLess(j["after_cm"], 3.0, role)
            else:
                self.assertLess(j["after_cm"], 1e-6, role)
            self.assertGreater(j["before_cm"], -1)
        self.assertGreater(rep["joints"]["left_hand"]["before_cm"], 3.0)
        self.assertLess(rep["joints"]["left_lower_arm"]["scale"], 0.85)
        self.assertGreater(rep["joints"]["left_lower_leg"]["scale"], 1.1)
        # The lower arm's capsule now lies along the body's lower arm, as thick as the
        # variant's (scaled by the uniform fit only).
        bj = BODY["joints"]
        sel = part(v, "left_lower_arm")
        d = segment_distance(res["points"][sel], bj["left_lower_arm"], bj["left_hand"])
        radius = 0.03 * 1.6 * rep["prefit"]["scale"]
        self.assertLess(abs(np.median(d) - radius), 0.15 * radius)
        mid = (bj["left_lower_arm"] + bj["left_hand"]) / 2
        self.assertLess(np.linalg.norm(res["points"][sel].mean(axis=0) - mid), 0.02)
        self.assertEqual([f for f in rep["flags"] if not f.startswith("floor")], [])

    def test_a_bulky_figure_stays_bulky(self):
        thin, bulky = conform(hs.figure(height=1.0)), conform(hs.figure(height=1.0, bulk=2.0))
        bj = BODY["joints"]
        v = hs.figure()
        sel = part(v, "right_upper_leg")
        dt = segment_distance(thin["points"][sel], bj["right_upper_leg"], bj["right_lower_leg"])
        db = segment_distance(bulky["points"][sel], bj["right_upper_leg"], bj["right_lower_leg"])
        self.assertAlmostEqual(np.median(db) / np.median(dt), 2.0, delta=0.1)
        self.assertLess(bulky["report"]["joint_error_cm"], 1e-6)
        # The body's surface lies inside the bulky outfit, partly outside the thin one.
        self.assertEqual(bulky["report"]["body_outside"]["p99_cm"], 0.0)

    def test_a_joint_outside_the_outfit_is_flagged(self):
        # The left arm's capsules lie 8 cm (body heights 0.08) in front of its joints.
        res = conform(hs.figure(height=1.0, arm_shift=0.08))
        rep = res["report"]
        self.assertGreater(rep["joint_outside_cm"]["left_lower_arm"], 1.5)
        self.assertEqual(rep["joint_outside_cm"]["right_lower_arm"], 0.0)
        self.assertTrue(any(f.startswith("outside: the body's left_lower_arm joint") for f in rep["flags"]),
                        rep["flags"])

    def test_an_overstretched_segment_and_a_tear_are_flagged(self):
        rep = conform(hs.figure(height=1.0, arms=0.4))["report"]
        self.assertTrue(any(f.startswith("scale: the left_upper_arm segment") for f in rep["flags"]), rep["flags"])
        rep = conform(hs.figure(height=1.0, arms=0.4), thresholds={"torn_share": 0.0, "scale": 10.0})["report"]
        self.assertGreater(rep["torn_share"], 0.0)
        self.assertTrue(any(f.startswith("tear:") for f in rep["flags"]), rep["flags"])
        self.assertFalse(any(f.startswith("scale:") for f in rep["flags"]))

    def test_soles_below_the_floor_are_measured_and_flagged(self):
        rep = conform(hs.figure(height=1.0, sole=0.03))["report"]
        # 0.03 body heights of the variant, times the fit's scale (1.8): 5.4 cm below.
        self.assertAlmostEqual(rep["floor_offset_cm"]["left"], 5.4, delta=0.3)
        self.assertTrue(any(f.startswith("floor: the") and "below" in f for f in rep["flags"]), rep["flags"])
        rep = conform(hs.figure(height=1.0))["report"]
        self.assertLess(abs(rep["floor_offset_cm"]["left"]), 0.5)

    def test_thresholds_are_reported(self):
        # A rig that predicted its chest joint 2 cm (body heights 0.02) too high: the torso is one
        # piece, so the body's chest joint ends 3.6 cm from the variant's.
        v = hs.figure(height=1.0)
        v["joints"]["chest"] = v["joints"]["chest"] + np.array([0.0, 0.0, 0.02])
        rep = conform(v)["report"]
        self.assertAlmostEqual(rep["joint_error_cm"], 3.6, delta=0.05)
        self.assertEqual(rep["joint_error_role"], "chest")
        self.assertFalse(any(f.startswith("joints:") for f in rep["flags"]), rep["flags"])
        rep = conform(v, thresholds={"joint_cm": 3.0})["report"]
        self.assertEqual(rep["thresholds"]["joint_cm"], 3.0)
        self.assertTrue(any(f.startswith("joints: the conformed chest joint") for f in rep["flags"]), rep["flags"])


class RolesTest(unittest.TestCase):
    def test_bones_without_a_role_count_with_their_mapped_ancestor(self):
        parents = {"root": None, "b0": "root", "b1": "b0", "hand": "b1", "f1": "hand", "f2": "f1", "spine": "b0",
                   "chest": "spine", "head": "chest"}
        roles = {"hips": "b0", "left_hand": "hand", "left_lower_arm": "b1", "spine": "spine", "chest": "chest",
                 "head": "head"}
        common = hf.common_roles(roles, {r: r for r in roles})
        out = hf.bone_roles(parents, roles, common)
        self.assertEqual(out["f2"], "left_hand")
        self.assertEqual(out["root"], "hips")
        self.assertEqual(out["b1"], "left_lower_arm")

    def test_a_role_the_body_lacks_counts_with_its_nearest_shared_ancestor(self):
        variant = {r: r for r in hs.ROLES}
        body = {r: r for r in hs.ROLES if r not in ("upper_chest", "left_toes", "right_toes", "neck")}
        common = hf.common_roles(variant, body)
        self.assertEqual(hf.shared_role("upper_chest", common), "chest")
        self.assertEqual(hf.shared_role("left_toes", common), "left_foot")
        self.assertEqual(hf.shared_role("neck", common), "chest")
        b = hs.figure(height=1.8)
        res = hf.conform(hs.figure()["joints"], b["joints"], variant, body, (0, -1, 0), hs.figure()["points"],
                         hs.figure()["index"], hs.figure()["weight"], hs.figure()["roles"], hs.figure()["triangles"])
        self.assertEqual(res["report"]["missing_roles"], [])
        self.assertNotIn("neck", res["mats"])
        # The torso goes up to the head when the body has no neck.
        self.assertEqual(res["report"]["joints"]["hips"]["kind"], "torso")

    def test_a_role_the_variant_lacks_is_reported(self):
        variant = {r: r for r in hs.ROLES if r != "left_toes"}
        res = hf.conform(hs.figure()["joints"], BODY["joints"], variant, ROLE_MAP, (0, -1, 0), hs.figure()["points"],
                         hs.figure()["index"], hs.figure()["weight"],
                         [r if r != "left_toes" else "left_foot" for r in hs.figure()["roles"]],
                         hs.figure()["triangles"])
        self.assertEqual(res["report"]["missing_roles"], ["left_toes"])
        self.assertEqual(res["report"]["joints"]["left_foot"]["kind"], "end")


class GeometryTest(unittest.TestCase):
    def test_rotation_between(self):
        for a, b in (((1, 0, 0), (0, 1, 0)), ((0, 0, 1), (0, 0, -1)), ((1, 2, 3), (1, 2, 3)), ((1, 0, 0), (-1, 1e-13, 0))):
            r = hf.rotation_between(a, b)
            np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-9)
            self.assertAlmostEqual(np.linalg.det(r), 1.0, places=9)
            np.testing.assert_allclose(r @ (np.array(a) / np.linalg.norm(a)), np.array(b) / np.linalg.norm(b),
                                       atol=1e-9)

    def test_escape_inside_and_outside_a_closed_surface(self):
        pts, tris = hs.capsule((0, 0, 0), (0, 0, 1), 0.2)
        surface = hf.MeshSurface(pts, tris)
        depth = hf.outside_depth([(0, 0, 0.5), (0.5, 0, 0.5), (0, 0, 2.0)], surface)
        self.assertEqual(depth[0], 0.0)
        self.assertGreater(depth[1], 0.25)
        self.assertGreater(depth[2], 0.7)

    def test_skin_blends_and_keeps_unweighted_points(self):
        mats = np.array([hf.affine(np.eye(3), [1, 0, 0]), hf.affine(np.eye(3), [0, 2, 0])])
        p = hf.skin([[0, 0, 0], [0, 0, 0], [5, 5, 5]], np.array([[0, 1], [0, 1], [0, 1]]),
                    np.array([[1, 1], [3, 1], [0, 0]]), mats)
        np.testing.assert_allclose(p, [[0.5, 1, 0], [0.75, 0.5, 0], [5, 5, 5]])

    def test_ray_directions_cover_the_sphere(self):
        d = hf.ray_directions()
        np.testing.assert_allclose(np.linalg.norm(d, axis=1), 1.0)
        self.assertLess(np.linalg.norm(d.mean(axis=0)), 0.05)
        self.assertTrue(all(math.isfinite(x) for x in d.ravel()))


if __name__ == "__main__":
    unittest.main()
