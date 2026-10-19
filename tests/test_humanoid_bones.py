"""The humanoid roles found from a skeleton's tree (tools/blender/humanoid_bones.py, the
part without bpy) and the bone map's validation."""

import json
import math
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))

import humanoid_bones as hb  # noqa: E402


class TreeBuilder:
    """A standing humanoid in the rig tools' style (bone_N, depth first), facing -y."""

    def __init__(self):
        self.tree = {}
        self.n = 0

    def bone(self, parent, head, tail, name=None):
        if name is None:
            name = "bone_%d" % self.n
            self.n += 1
        self.tree[name] = {"parent": parent, "head": tuple(head), "tail": tuple(tail)}
        return name

    def chain(self, parent, points):
        out = []
        for a, b in zip(points, points[1:]):
            parent = self.bone(parent, a, b)
            out.append(parent)
        return out


def humanoid(fingers=0, extras=False, extra_root=False, one_arm=False, long_left_leg=False):
    t = TreeBuilder()
    root = t.bone(None, (0, 0, 0.0), (0, 0, 0.05), name="root") if extra_root else None
    hips = t.bone(root, (0, 0, 0.50), (0, 0, 0.56))
    spine = t.chain(hips, [(0, 0, 0.56), (0, 0, 0.64), (0, 0, 0.72), (0, 0, 0.80)])
    top = spine[-1]
    t.chain(top, [(0, 0, 0.82), (0, 0, 0.88), (0, 0, 1.0)])
    hands = []
    for sx in (1, -1):
        if one_arm and sx == -1:
            t.n += 4  # keep the numbering of the other bones
            continue
        arm = t.chain(top, [(0.03 * sx, 0, 0.80), (0.10 * sx, 0, 0.80), (0.14 * sx, 0, 0.62),
                            (0.18 * sx, 0, 0.45), (0.19 * sx, 0, 0.39)])
        hands.append((arm[-1], sx))
    for sx in (1, -1):
        knee = 0.20 if (long_left_leg and sx == 1) else 0.28
        t.chain(hips, [(0.06 * sx, 0, 0.50), (0.07 * sx, 0, knee), (0.075 * sx, 0, 0.05),
                       (0.075 * sx, -0.06, 0.01), (0.075 * sx, -0.10, 0.01)])
    for hand, sx in hands:  # the fingers after the body (bone_22 on)
        for k in range(fingers):
            y = -0.02 + 0.01 * k
            t.chain(hand, [(0.19 * sx, y, 0.39), (0.195 * sx, y, 0.37), (0.20 * sx, y, 0.35), (0.205 * sx, y, 0.33)])
    if extras:
        t.bone(top, (0, 0.05, 0.75), (0, 0.10, 0.70), name="pack")          # on the back
        t.bone("bone_5", (0, 0, 1.0), (0, 0.03, 1.05), name="hair")          # on the head
        t.bone(hips, (0, 0.05, 0.50), (0, 0.15, 0.45), name="tail")          # on the hips
        t.bone(spine[1], (0.05, 0, 0.66), (0.08, 0, 0.66), name="pouch")    # on the spine
    return t.tree


def mirror(tree, axis):
    """Every point mirrored across one axis (0: x, 1: y)."""
    def m(p):
        p = list(p)
        p[axis] = -p[axis]
        return tuple(p)
    return {n: {"parent": b["parent"], "head": m(b["head"]), "tail": m(b["tail"])} for n, b in tree.items()}


EXPECTED = {
    "hips": "bone_0", "spine": "bone_1", "chest": "bone_2", "upper_chest": "bone_3",
    "neck": "bone_4", "head": "bone_5",
    "left_shoulder": "bone_6", "left_upper_arm": "bone_7", "left_lower_arm": "bone_8", "left_hand": "bone_9",
    "right_shoulder": "bone_10", "right_upper_arm": "bone_11", "right_lower_arm": "bone_12",
    "right_hand": "bone_13",
    "left_upper_leg": "bone_14", "left_lower_leg": "bone_15", "left_foot": "bone_16", "left_toes": "bone_17",
    "right_upper_leg": "bone_18", "right_lower_leg": "bone_19", "right_foot": "bone_20", "right_toes": "bone_21",
}


def swap_sides(roles):
    out = {}
    for r, b in roles.items():
        if r.startswith("left_"):
            out["right_" + r[5:]] = b
        elif r.startswith("right_"):
            out["left_" + r[6:]] = b
        else:
            out[r] = b
    return out


class BoneRoles(unittest.TestCase):
    def test_godot_names(self):
        self.assertEqual(hb.godot_name("hips"), "Hips")
        self.assertEqual(hb.godot_name("upper_chest"), "UpperChest")
        self.assertEqual(hb.godot_name("left_upper_arm"), "LeftUpperArm")
        self.assertEqual(hb.godot_name("right_toes"), "RightToes")
        with self.assertRaises(KeyError):
            hb.godot_name("tail")

    def test_standing_tree(self):
        doc = hb.propose_bone_map(humanoid())
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"], EXPECTED)
        self.assertEqual(doc["front"], [0.0, -1.0, 0.0])
        self.assertEqual(doc["left"][0], 1.0)
        roles = hb.bone_roles(humanoid())
        self.assertEqual(roles["bone_9"], "left_hand")
        self.assertEqual(hb.renames(doc["roles"])["bone_13"], "RightHand")

    def test_mirrored_across_x(self):
        doc = hb.propose_bone_map(mirror(humanoid(), 0))
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"], swap_sides(EXPECTED))

    def test_facing_the_other_way(self):
        # Toes towards +y: the figure faces +y, its left is -x.
        doc = hb.propose_bone_map(mirror(humanoid(), 1))
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["front"], [0.0, 1.0, 0.0])
        self.assertEqual(doc["roles"], swap_sides(EXPECTED))

    def test_extra_bones_keep_their_names(self):
        tree = humanoid(extras=True)
        doc = hb.propose_bone_map(tree)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"], EXPECTED)
        for extra in ("pack", "hair", "tail", "pouch"):
            self.assertIn(extra, doc["unmapped"])
            self.assertNotIn(extra, hb.renames(doc["roles"]))

    def test_finger_chains_under_each_hand(self):
        tree = humanoid(fingers=5)
        doc = hb.propose_bone_map(tree)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"], EXPECTED)
        right_hand = doc["roles"]["right_hand"]
        self.assertEqual(tree[right_hand]["head"], (-0.18, 0, 0.45))
        fingers = [n for n, b in tree.items() if b["parent"] in (doc["roles"]["left_hand"], right_hand)]
        self.assertEqual(len(fingers), 10)
        for f in fingers:
            self.assertIn(f, doc["unmapped"])

    def test_hips_under_an_extra_root(self):
        tree = humanoid(extra_root=True)
        doc = hb.propose_bone_map(tree)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"]["hips"], "bone_0")
        self.assertIn("root", doc["unmapped"])
        self.assertEqual(hb.chains(tree, doc["roles"])["spine"], ["bone_1", "bone_2", "bone_3"])

    def test_one_arm_missing_gives_a_report_and_no_map(self):
        doc = hb.propose_bone_map(humanoid(one_arm=True))
        self.assertFalse(doc["valid"])
        self.assertIsNone(doc["roles"])
        self.assertTrue(any("arm" in p for p in doc["problems"]), doc["problems"])
        with self.assertRaises(ValueError):
            hb.bone_roles(humanoid(one_arm=True))

    def test_uneven_sides_are_refused(self):
        doc = hb.propose_bone_map(humanoid(long_left_leg=True))
        self.assertFalse(doc["valid"])
        self.assertTrue(any("upper leg" in p for p in doc["problems"]), doc["problems"])

    def test_validation_of_a_manual_map(self):
        tree = humanoid()
        self.assertEqual(hb.validate_bone_map(tree, EXPECTED), [])
        bad = dict(EXPECTED, left_hand="bone_13", right_hand="bone_9")
        problems = hb.validate_bone_map(tree, bad)
        self.assertTrue(any("child of" in p for p in problems), problems)
        hands_only = dict(EXPECTED)
        for part in ("shoulder", "upper_arm", "lower_arm", "hand"):
            hands_only["left_" + part], hands_only["right_" + part] = EXPECTED["right_" + part], EXPECTED["left_" + part]
        problems = hb.validate_bone_map(tree, hands_only)
        self.assertTrue(any("different sides" in p for p in problems), problems)
        missing = {k: v for k, v in EXPECTED.items() if k != "left_foot"}
        self.assertIn("role left_foot is missing", hb.validate_bone_map(tree, missing))

    def test_a_manual_map_wins(self):
        tree = humanoid()
        # A manual map that leaves the upper chest to the chest (a different, valid choice).
        manual = dict(EXPECTED)
        del manual["upper_chest"]
        manual["chest"] = "bone_3"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bone_map.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"manual": True, "roles": manual}, f)
            doc = hb.choose_bone_map(tree, existing=path)
            self.assertEqual(doc["source"], "manual")
            self.assertTrue(doc["valid"], doc["problems"])
            self.assertEqual(doc["roles"]["chest"], "bone_3")
            self.assertEqual(hb.chains(tree, doc["roles"])["spine"], ["bone_1", "bone_2", "bone_3"])
            # Not marked manual: the proposal wins.
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"manual": False, "roles": manual}, f)
            self.assertEqual(hb.choose_bone_map(tree, existing=path)["source"], "proposed")
            # The order's map wins over everything.
            doc = hb.choose_bone_map(tree, given=dict(EXPECTED), existing=path)
            self.assertEqual(doc["source"], "order")
            self.assertEqual(doc["roles"], EXPECTED)
            # An invalid manual map is reported, never replaced by a guess.
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"manual": True, "roles": dict(EXPECTED, head="nothing")}, f)
            doc = hb.choose_bone_map(tree, existing=path)
            self.assertFalse(doc["valid"])
            self.assertIsNone(doc["roles"])

    def test_a_short_neck_under_clavicles_that_point_up(self):
        # The topology of rigs of bulky armour (tests/fixtures/humanoid/bone_trees.json): the
        # clavicles' tails end higher than the head, so the highest child of the spine's top is
        # an arm; the neck is the child between the arms that goes up and has no fingers.
        with open(os.path.join(ROOT, "tests", "fixtures", "humanoid", "bone_trees.json"), encoding="utf-8") as f:
            trees = json.load(f)
        for name, right in (("helmet_five_fingers", "bone_25"), ("helmet_two_fingers", "bone_16")):
            for tree in (trees[name], mirror(trees[name], 0)):
                self.assertGreater(max(tree[b]["tail"][2] for b in ("bone_6", right)), tree["bone_5"]["tail"][2])
                doc = hb.propose_bone_map(tree)
                self.assertTrue(doc["valid"], (name, doc["problems"]))
                roles = doc["roles"]
                self.assertEqual((roles["neck"], roles["head"]), ("bone_4", "bone_5"), name)
                arms = {roles["left_shoulder"], roles["right_shoulder"]}
                self.assertEqual(arms, {"bone_6", right}, name)
                self.assertEqual(roles["left_hand"] if roles["left_shoulder"] == "bone_6" else roles["right_hand"],
                                 "bone_9", name)

    def test_chains(self):
        tree = humanoid(fingers=5)
        ch = hb.chains(tree, hb.propose_bone_map(tree)["roles"])
        self.assertEqual(ch["head"], ["bone_4", "bone_5"])
        self.assertEqual(ch["arms"]["left"], ["bone_6", "bone_7", "bone_8", "bone_9"])
        self.assertEqual(ch["legs"]["right"], ["bone_18", "bone_19", "bone_20", "bone_21"])


def turned(tree, deg, bones=None, about=None):
    """The tree with the points of `bones` (every bone when None) turned by deg about the
    vertical through `about` (x, y; the origin when None). Positive turns -y towards +x."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    ox, oy = about or (0.0, 0.0)

    def t(p):
        x, y = p[0] - ox, p[1] - oy
        return (ox + c * x - s * y, oy + s * x + c * y, p[2])
    return {n: ({"parent": b["parent"], "head": t(b["head"]), "tail": t(b["tail"])}
                if bones is None or n in bones else dict(b)) for n, b in tree.items()}


def feet_turned(tree, deg, roles):
    """Each foot and its toes turned by deg about the vertical through its ankle."""
    for s in hb.SIDES:
        ankle = tree[roles[s + "_foot"]]["head"]
        tree = turned(tree, deg, {roles[s + "_foot"], roles[s + "_toes"]}, (ankle[0], ankle[1]))
    return tree


# The bones above the top of the spine in humanoid(fingers=5): the chest's last bone, neck, head,
# arms and fingers (bone_22 on).
UPPER = {"bone_%d" % k for k in list(range(3, 14)) + list(range(22, 52))}


class BodyFacing(unittest.TestCase):
    """The front of a standing figure from its hips' and shoulders' lines, the feet a tie-breaker
    (humanoid_bones.body_facing): a generated figure may stand turned between two axes."""

    def facing(self, tree, roles=None):
        roles = roles or hb.propose_bone_map(tree)["roles"]
        return hb.body_facing(tree, roles)

    def test_a_figure_facing_front(self):
        f = self.facing(humanoid(fingers=5))
        self.assertAlmostEqual(f["turn_deg"], 0.0, places=6)
        self.assertEqual(f["source"], "hips and shoulders")
        self.assertEqual(f["problems"], [])
        self.assertAlmostEqual(f["front"][1], -1.0, places=9)

    def test_a_figure_turned_between_two_axes(self):
        # The bone map's front snaps to an axis; the body's does not (the first real night's
        # figures stood turned 22 to 40 degrees after the export).
        for deg in (30.0, -40.0, 130.0, -160.0, 12.5):
            tree = turned(humanoid(fingers=5), deg)
            doc = hb.propose_bone_map(tree)
            self.assertTrue(doc["valid"], (deg, doc["problems"]))
            f = hb.body_facing(tree, doc["roles"])
            self.assertAlmostEqual(f["turn_deg"], deg, places=6, msg=deg)
            self.assertEqual(f["problems"], [], deg)
            for cue in ("hips", "shoulders", "feet"):
                self.assertAlmostEqual(f["cues"][cue], deg, places=6, msg=(deg, cue))

    def test_feet_askew_while_hips_and_shoulders_face_front(self):
        base = humanoid(fingers=5)
        roles = hb.propose_bone_map(base)["roles"]
        for deg in (35.0, -35.0, 55.0):
            f = self.facing(feet_turned(base, deg, roles), roles)
            self.assertAlmostEqual(f["turn_deg"], 0.0, places=6, msg=deg)
            self.assertAlmostEqual(f["cues"]["feet"], deg, places=6, msg=deg)
            self.assertAlmostEqual(f["feet_apart_deg"], abs(deg), places=6)
            self.assertEqual(f["problems"], [], deg)
        # Feet pointing further away than the limit: the way the figure faces is not known.
        f = self.facing(feet_turned(base, 70.0, roles), roles)
        self.assertAlmostEqual(f["turn_deg"], 0.0, places=6)
        self.assertEqual(len(f["problems"]), 1)
        self.assertTrue(f["problems"][0].startswith("facing: the feet point 70 degrees"), f["problems"])

    def test_hips_and_shoulders_askew_while_the_feet_face_front(self):
        base = humanoid(fingers=5)
        roles = hb.propose_bone_map(base)["roles"]
        for deg in (35.0, -30.0):
            tree = feet_turned(turned(base, deg), -deg, roles)
            doc = hb.propose_bone_map(tree)
            self.assertTrue(doc["valid"], doc["problems"])
            self.assertEqual(doc["front"], [0.0, -1.0, 0.0])  # what the exporter turned by before
            f = hb.body_facing(tree, doc["roles"])
            self.assertAlmostEqual(f["turn_deg"], deg, places=6, msg=deg)
            self.assertAlmostEqual(f["cues"]["feet"], 0.0, places=6, msg=deg)
            self.assertEqual(f["problems"], [], deg)

    def test_the_two_lines_are_weighted_by_their_length(self):
        base = humanoid(fingers=5)
        roles = hb.propose_bone_map(base)["roles"]
        f = self.facing(turned(base, 10.0, UPPER), roles)
        self.assertAlmostEqual(f["lines_apart_deg"], 10.0, places=6)
        # Shoulders 0.20 across, hips 0.12: the front leans to the shoulders' line.
        self.assertGreater(f["turn_deg"], 5.0)
        self.assertLess(f["turn_deg"], 10.0)
        self.assertEqual(f["problems"], [])

    def test_a_twisted_figure_is_a_problem_and_the_feet_break_the_tie(self):
        base = humanoid(fingers=5)
        roles = hb.propose_bone_map(base)["roles"]
        f = self.facing(turned(base, 40.0, UPPER), roles)  # shoulders 40 degrees off the hips
        self.assertAlmostEqual(f["turn_deg"], 0.0, places=6)
        self.assertEqual(f["source"], "hips (nearer the feet)")
        self.assertEqual(len(f["problems"]), 1)
        self.assertIn("40 degrees apart", f["problems"][0])
        # The feet follow the shoulders: the shoulders' line is used.
        f = self.facing(feet_turned(turned(base, 40.0, UPPER), 40.0, roles), roles)
        self.assertAlmostEqual(f["turn_deg"], 40.0, places=6)
        self.assertEqual(f["source"], "shoulders (nearer the feet)")
        self.assertEqual(len(f["problems"]), 1)

    def test_no_line_across_the_body(self):
        tree = humanoid(fingers=5)
        roles = hb.propose_bone_map(tree)["roles"]
        for r in ("left_upper_leg", "right_upper_leg", "left_upper_arm", "right_upper_arm"):
            h = tree[roles[r]]["head"]
            tree[roles[r]] = dict(tree[roles[r]], head=(0.0, 0.0, h[2]))
        f = hb.body_facing(tree, roles)
        self.assertEqual(f["source"], "feet")
        self.assertAlmostEqual(f["turn_deg"], 0.0, places=6)
        self.assertEqual(len(f["problems"]), 1)

    def test_the_real_topology_fixtures(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "humanoid", "bone_trees.json"), encoding="utf-8") as f:
            trees = json.load(f)
        for name in ("helmet_five_fingers", "helmet_two_fingers"):
            for deg in (0.0, 28.0, -37.0, 95.0):
                for tree in (turned(trees[name], deg), turned(mirror(trees[name], 0), deg)):
                    doc = hb.propose_bone_map(tree)
                    self.assertTrue(doc["valid"], (name, deg, doc["problems"]))
                    f = hb.body_facing(tree, doc["roles"])
                    self.assertAlmostEqual(f["turn_deg"], deg, places=6, msg=(name, deg))
                    self.assertEqual(f["problems"], [], (name, deg))


if __name__ == "__main__":
    unittest.main()
