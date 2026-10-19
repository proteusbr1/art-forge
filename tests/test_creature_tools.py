"""The creature tools without Blender: the body plan's check, the roles found from synthetic
skeletons (tools/blender/creature_bones.py) and the procedural clips made on them
(tools/blender/creature_motion.py): loops, planted feet, reach, falls and waves."""

import math
import os
import random
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import creature_bones as cb  # noqa: E402
import creature_motion as cm  # noqa: E402
import creature_skeletons as sk  # noqa: E402


def mapped(builder, plan, shuffle=None):
    keys = [b[0] for b in builder.bones]
    order = list(keys)
    if shuffle is not None:
        # Parents still come first (the rig tools list them so), siblings in another order.
        random.Random(shuffle).shuffle(order)
        placed, out = set(), []
        parent = {b[0]: b[1] for b in builder.bones}
        while order:
            for k in list(order):
                if parent[k] is None or parent[k] in placed:
                    out.append(k)
                    placed.add(k)
                    order.remove(k)
        order = out
    tree, names = builder.tree(order)
    doc = cb.propose(tree, plan)
    return tree, names, doc


class PlanCheck(unittest.TestCase):
    def test_valid_plans(self):
        for plan in (sk.SERPENT_PLAN, sk.BEAST_PLAN, sk.INSECT_PLAN, sk.HOVER_PLAN):
            self.assertEqual(cb.check_plan(plan), [])

    def test_problems(self):
        def problems(plan):
            return " | ".join(cb.check_plan(plan))
        self.assertIn("at least one chain", problems({}))
        self.assertIn("listed before it", problems({"chains": [{"id": "arm", "from": "spine", "toward": "side",
                                                                "pairs": 1}]}))
        self.assertIn("toward must be", problems({"chains": [{"id": "spine", "toward": "sky"}]}))
        self.assertIn("needs pairs", problems({"chains": [{"id": "arm", "toward": "side"}]}))
        self.assertIn("appears twice", problems({"chains": [{"id": "a", "toward": "up"}, {"id": "a", "toward": "up"}]}))
        self.assertIn("no role of the plan", problems({"chains": [{"id": "spine", "toward": "up"}],
                                                       "holds": {"right": "arm_r0"}}))
        self.assertIn("its 1 pair", problems({"chains": [{"id": "arm", "toward": "side", "pairs": 1},
                                                         {"id": "hand", "from": "arm", "toward": "side", "pairs": 2}]}))

    def test_roles_of_a_plan(self):
        roles = [r for r, _ in cb.plan_roles(sk.INSECT_PLAN)]
        self.assertEqual(roles, ["spine", "neck", "arm_l0", "arm_r0", "arm_l1", "arm_r1", "leg_l0", "leg_r0"])
        plan = {"chains": [{"id": "arm", "toward": "side", "pairs": 1}, {"id": "hand", "from": "arm", "toward": "side"}]}
        slots = dict(cb.plan_roles(plan))
        self.assertEqual(slots["hand_r0"]["parent"], "arm_r0")
        self.assertEqual(cb.chain_roles(sk.INSECT_PLAN, "arm"), ["arm_l0", "arm_r0", "arm_l1", "arm_r1"])
        self.assertEqual(cb.chain_roles(sk.INSECT_PLAN, "leg_r0"), ["leg_r0"])


class Proposal(unittest.TestCase):
    def assertRoles(self, doc, names, expected):
        self.assertTrue(doc["valid"], doc["problems"])
        got = {r: [names_inv for names_inv in bones] for r, bones in doc["roles"].items()}
        want = {r: [names[k] for k in keys] for r, keys in expected.items()}
        self.assertEqual(got, want)

    def test_serpent(self):
        b = sk.serpent()
        for shuffle in (None, 1, 2, 3):
            tree, names, doc = mapped(b, sk.SERPENT_PLAN, shuffle)
            self.assertRoles(doc, names, {"spine": ["spine0", "spine1"], "neck": ["neck0", "neck1"],
                                          "tail": ["tail%d" % i for i in range(5)],
                                          "arm_l0": ["arml0", "arml1", "arml2"], "arm_r0": ["armr0", "armr1", "armr2"]})
            self.assertEqual(doc["root"], [names["hips0"]])

    def test_beast(self):
        tree, names, doc = mapped(sk.beast(), sk.BEAST_PLAN)
        self.assertRoles(doc, names, {"spine": ["spine0", "spine1"], "neck": ["neck0", "neck1"],
                                      "foreleg_l0": ["forel0", "forel1", "forel2"],
                                      "foreleg_r0": ["forer0", "forer1", "forer2"],
                                      "hindleg_l0": ["hindl0", "hindl1", "hindl2"],
                                      "hindleg_r0": ["hindr0", "hindr1", "hindr2"], "tail": ["tail0", "tail1"]})

    def test_four_arms_are_indexed_from_the_top(self):
        tree, names, doc = mapped(sk.insect(), sk.INSECT_PLAN, shuffle=5)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"]["arm_l0"][0], names["arml0_0"])
        self.assertEqual(doc["roles"]["arm_l1"][0], names["arml1_0"])
        self.assertEqual(doc["roles"]["arm_r1"][0], names["armr1_0"])
        self.assertEqual(doc["roles"]["leg_l0"], [names["legl%d" % i] for i in range(3)])

    def test_hover(self):
        tree, names, doc = mapped(sk.hover(), sk.HOVER_PLAN)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"]["base"], [names["base0"], names["base1"]])

    def test_a_neck_grown_into_the_spine_is_split_off(self):
        b = sk.Builder()
        hips = b.chain(None, [(0, 0, 0.9), (0, 0, 1.0)], "hips")
        b.chain(hips[-1], [(0, 0, 1.0), (0, 0, 1.2), (0, 0, 1.4), (0, 0, 1.6)], "spine")
        leg = [(0.1, 0, 0.9), (0.12, 0, 0.5), (0.12, 0, 0.05)]
        b.chain(hips[0], leg, "legl")
        b.chain(hips[0], sk.mirror(leg), "legr")
        plan = {"chains": [{"id": "spine", "toward": "up"}, {"id": "neck", "from": "spine", "toward": "up"},
                           {"id": "leg", "toward": "down", "pairs": 1}]}
        tree, names, doc = mapped(b, plan)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"]["neck"], [names["spine2"]])
        self.assertEqual(doc["roles"]["spine"], [names["spine0"], names["spine1"]])
        self.assertEqual(doc["split"], {"neck": "spine"})

    def test_legs_behind_a_connector(self):
        b = sk.Builder()
        root = b.chain(None, [(0, 0, 1.0), (0, 0, 1.05)], "root")
        b.chain(root[-1], [(0, 0, 1.05), (0, 0, 1.4), (0, 0, 1.75)], "spine")
        pelvis = b.chain(root[-1], [(0, 0, 1.0), (0, 0.02, 0.95)], "pelvis")
        leg = [(0.1, 0.02, 0.95), (0.12, 0, 0.5), (0.12, 0, 0.05)]
        b.chain(pelvis[-1], leg, "legl")
        b.chain(pelvis[-1], sk.mirror(leg), "legr")
        plan = {"chains": [{"id": "spine", "toward": "up"}, {"id": "leg", "toward": "down", "pairs": 1}]}
        tree, names, doc = mapped(b, plan)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual(doc["roles"]["leg_l0"], [names["legl0"], names["legl1"]])
        self.assertIn(names["pelvis0"], doc["unmapped"])

    def test_missing_chain_and_manual_map(self):
        tree, names, doc = mapped(sk.hover(), sk.SERPENT_PLAN)  # no tail on this skeleton
        self.assertFalse(doc["valid"])
        self.assertTrue(any("role tail is missing" in p for p in doc["problems"]), doc["problems"])
        self.assertIsNone(doc["roles"])
        roles = dict(doc["found"])
        roles["tail"] = [names["base0"], names["base1"]]
        self.assertEqual(cb.validate(tree, sk.SERPENT_PLAN, roles), [])
        bad = dict(roles, arm_l0=list(roles["arm_r0"]), arm_r0=list(roles["arm_l0"]))
        problems = cb.validate(tree, sk.SERPENT_PLAN, bad)
        self.assertTrue(any("not on the left" in p for p in problems), problems)
        broken = dict(roles, tail=[names["base1"], names["base0"]])
        self.assertTrue(any("must be a child of" in p for p in cb.validate(tree, sk.SERPENT_PLAN, broken)))
        twice = dict(roles, neck=list(roles["spine"]))
        self.assertTrue(any("has two roles" in p for p in cb.validate(tree, sk.SERPENT_PLAN, twice)))

    def test_choose_precedence(self):
        import json
        import tempfile
        tree, names, doc = mapped(sk.serpent(), sk.SERPENT_PLAN)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bone_map.json")
            manual = dict(doc, manual=True, source="manual")
            manual["roles"] = dict(doc["roles"], neck=[names["neck1"]])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manual, f)
            got = cb.choose(tree, sk.SERPENT_PLAN, existing=path)
            self.assertEqual((got["source"], got["roles"]["neck"]), ("manual", [names["neck1"]]))
            self.assertEqual(cb.choose(tree, sk.SERPENT_PLAN, given=path)["source"], "order")
            manual["source"] = "order"  # the exporter's copy of the order's map is no correction
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manual, f)
            self.assertEqual(cb.choose(tree, sk.SERPENT_PLAN, existing=path)["source"], "proposed")


def rig_of(builder, plan, height=None):
    tree, names, doc = mapped(builder, plan)
    assert doc["valid"], doc["problems"]
    zs = [p[2] for b in tree.values() for p in (b["head"], b["tail"])]
    return cm.Rig(tree, doc["roles"], doc["root"], height or (max(zs) - min(zs))), doc


class Motions(unittest.TestCase):
    def test_check(self):
        plan = sk.BEAST_PLAN
        ok = [{"name": "walk_none", "source": "procedural", "kind": "gait", "frames": 24, "fps": 24, "loop": True,
               "holds": None, "settings": {"stride": 0.3}},
              {"name": "idle_none", "source": "library", "library": {"clip": "idle"}, "frames": 40,
               "contacts": {"foreleg_l0": "all"}}]
        self.assertEqual(cm.check_motions(ok, plan), [])
        self.assertIn("has no clip idle", " ".join(cm.check_motions(ok, plan, {"anims": {}})))
        P = "procedural"
        p = " | ".join(cm.check_motions([
            {"name": "walk", "source": P, "kind": "gait", "frames": 24, "settings": {"legs": ["wing"]}},
            {"name": "walk", "source": P, "kind": "fly", "frames": 3},
            {"name": "Bad name", "source": P, "kind": "gait", "frames": 3},
            {"name": "x", "source": P, "kind": "breathe", "frames": 8, "settings": {"wobble": 2}},
            {"name": "s", "source": P, "kind": "breathe", "frames": 1},
            {"name": "y", "source": P, "kind": "gait", "frames": 8, "settings": {"phases": [0, 0.5]}},
            {"name": "z", "source": "text", "prompt": "walks", "frames": 8},
            {"name": "w", "kind": "gait", "frames": 8},
            {"name": "v", "source": P, "kind": "gait", "frames": 8, "contacts": {"foreleg_l0": "all"}},
            {"name": "u", "source": P, "kind": "gait", "frames": 8, "library": {"clip": "a"}, "stride": 1},
            {"name": "t", "source": "library", "settings": {"x": 1}}], plan))
        for text in ("setting legs must be a list of chains", "appears twice", "kind must be one of", "name must be",
                     "frames must be", "unknown setting 'wobble'", "phases has 2 values for 4 legs",
                     "source text is not available yet", "source must be one of", "makes its own contacts",
                     "library belongs to source library", "unknown keys ['stride']", "a library clip has no settings"):
            self.assertIn(text, p)
        self.assertIn("no chain to move", " ".join(cm.check_motions(
            [{"name": "slither", "source": P, "kind": "undulate", "frames": 8}], sk.HOVER_PLAN)))

    def test_every_kind_runs_and_loops_close(self):
        rig, doc = rig_of(sk.serpent(), sk.SERPENT_PLAN)
        for kind in cm.KINDS:
            if kind == "gait":
                continue
            clip = cm.generate(rig, sk.SERPENT_PLAN, {"name": kind, "kind": kind, "frames": 20, "fps": 20})
            self.assertEqual(len(clip["rotations"]), 20)
            for frame in clip["rotations"]:
                for q in frame.values():
                    self.assertAlmostEqual(sum(x * x for x in q), 1.0, places=6)
            if clip["loop"]:
                # The frame after the last is the first again: the pose changes smoothly across it.
                first, _ = cm.posed_points(rig, clip, 0)
                last, _ = cm.posed_points(rig, clip, 19)
                step, _ = cm.posed_points(rig, clip, 1)
                self.assertLess(np.abs(first - last).max(), 3 * np.abs(step - first).max() + 1e-6, kind)

    def test_gait_feet_stay_planted_and_in_reach(self):
        for builder, plan in ((sk.beast(), sk.BEAST_PLAN), (sk.insect(), sk.INSECT_PLAN)):
            rig, doc = rig_of(builder, plan)
            clip = cm.generate(rig, plan, {"name": "walk_none", "kind": "gait", "frames": 24, "fps": 24})
            self.assertLess(clip["reach_shortfall_m"], 0.01)
            step = cm.FRONT * (clip["travel"][1] * rig.height / 24)
            for role, frames in clip["stance"].items():
                self.assertTrue(frames)
                foot = doc["roles"][role][-1]
                i = rig.index[foot]
                runs, cur = [], [frames[0]]
                for f in frames[1:]:
                    if f == cur[-1] + 1:
                        cur.append(f)
                    else:
                        runs.append(cur)
                        cur = [f]
                runs.append(cur)
                for run in runs:
                    pts = [cm.posed_points(rig, clip, f)[0][i] + step * f for f in run]
                    slide = max(np.linalg.norm(p - pts[0]) for p in pts)
                    self.assertLess(slide, 0.005, (role, run))
        # Default phases: a four-legged body trots (diagonal legs together).
        rig, doc = rig_of(sk.beast(), sk.BEAST_PLAN)
        auto = cm.generate(rig, sk.BEAST_PLAN, {"name": "w", "kind": "gait", "frames": 8})["params"]
        self.assertEqual(dict(zip(auto["roles"], auto["phases"])),
                         {"foreleg_l0": 0.0, "foreleg_r0": 0.5, "hindleg_l0": 0.5, "hindleg_r0": 0.0})

    def test_collapse_lies_down(self):
        # A serpent falls to its side (a fall back would swing its tail up); a hovering torso back.
        for builder, plan, fall in ((sk.serpent(), sk.SERPENT_PLAN, "left"), (sk.hover(), sk.HOVER_PLAN, "back")):
            rig, _ = rig_of(builder, plan)
            clip = cm.generate(rig, plan, {"name": "collapse", "kind": "collapse", "frames": 30,
                                            "settings": {"fall": fall}})
            heads, tails = cm.posed_points(rig, clip, 29)
            low = min(heads[:, 2].min(), tails[:, 2].min())
            self.assertAlmostEqual(low, 0.06 * rig.height, places=3)
            high = max(heads[:, 2].max(), tails[:, 2].max())
            self.assertLess(high, 0.7 * rig.height)  # lying (an arm may point up)
            self.assertFalse(clip["loop"])

    def test_undulation_travels_along_the_tail(self):
        rig, doc = rig_of(sk.serpent(), sk.SERPENT_PLAN)
        clip = cm.generate(rig, sk.SERPENT_PLAN, {"name": "walk_none", "kind": "undulate", "frames": 24,
                                                  "settings": {"travel": 0.4}})
        self.assertEqual(clip["params"]["roles"], ["tail"])
        self.assertEqual(clip["travel"], [0.0, 0.4])
        tip = rig.index[doc["roles"]["tail"][-1]]
        xs = [cm.posed_points(rig, clip, f)[1][tip][0] for f in range(24)]
        self.assertGreater(max(xs), 0.05)
        self.assertLess(min(xs), -0.05)
        self.assertLess(abs(sum(xs) / len(xs)), 0.05)

    def test_strike_and_aim_reach_forward(self):
        rig, doc = rig_of(sk.serpent(), sk.SERPENT_PLAN)
        hand = rig.index[doc["roles"]["arm_r0"][-1]]
        rest = rig.tail[hand]
        strike = cm.generate(rig, sk.SERPENT_PLAN, {"name": "throw", "kind": "strike", "frames": 21})
        peak = cm.posed_points(rig, strike, 9)[1][hand]
        self.assertLess(peak[1], rest[1] - 0.1)  # forward is -Y
        aim = cm.generate(rig, sk.SERPENT_PLAN, {"name": "aim_rifle", "kind": "aim", "frames": 10})
        self.assertEqual(aim["params"]["roles"], ["arm_r0"])  # the holding arm
        held = cm.posed_points(rig, aim, 0)[1][hand]
        self.assertLess(held[1], rest[1] - 0.2)
        self.assertGreater(held[2], rest[2] + 0.1)

    def test_role_space_round_trip_and_another_skeleton(self):
        rig, doc = rig_of(sk.serpent(), sk.SERPENT_PLAN)
        clips = [cm.generate(rig, sk.SERPENT_PLAN, {"name": n, "kind": k, "frames": 12})
                 for n, k in (("idle_none", "breathe"), ("walk_none", "undulate"), ("collapse", "collapse"))]
        lib = cm.library(rig, clips)
        self.assertEqual(lib["format"], "forge-creature-clips")
        self.assertEqual(cm.check_library(lib), [])
        self.assertIn("forge-creature-clips", " ".join(cm.check_library({"anims": {}})))
        # The library source: resampled to the entry's frames, contacts from the entry.
        entry = {"name": "walk_slow", "source": "library", "library": {"clip": "walk_none"}, "frames": 24,
                 "contacts": {"tail": "all"}}
        slow = cm.make_clip(rig, sk.SERPENT_PLAN, entry, {"lib.json": lib}, "lib.json")
        self.assertEqual((slow["frames"], slow["stance"], slow["params"]["clip"]), (24, {"tail": "all"}, "walk_none"))
        a, _ = cm.posed_points(rig, clips[1], 3)
        b, _ = cm.posed_points(rig, slow, 6)
        self.assertLess(np.abs(a - b).max(), 1e-6)
        for c in clips:
            back = cm.from_role_space(rig, c["name"], lib["anims"][c["name"]])
            for f in range(12):
                a, _ = cm.posed_points(rig, c, f)
                b, _ = cm.posed_points(rig, back, f)
                self.assertLess(np.abs(a - b).max(), 1e-6, c["name"])
        # A variant whose tail has twice the bones: the wave keeps its size.
        b2 = sk.Builder()
        hips = b2.chain(None, [(0, 0, 0.85), (0, 0, 0.95)], "hips")
        spine = b2.chain(hips[-1], [(0, 0, 0.95), (0, 0.01, 1.15), (0, 0.0, 1.38)], "spine")
        arm = [(0.06, 0, 1.36), (0.2, 0.0, 1.34), (0.42, -0.02, 1.16), (0.58, -0.04, 1.02)]
        b2.chain(spine[-1], arm, "arml")
        b2.chain(spine[-1], sk.mirror(arm), "armr")
        pts = [(0, 0, 0.85)] + [(0, 0.06 + 0.115 * i * 0.1, 0.5 - 0.035 * i) for i in range(1, 2)]
        pts = [(0, 0, 0.85), (0, 0.03, 0.67), (0, 0.06, 0.5), (0, 0.09, 0.33), (0, 0.12, 0.16), (0, 0.28, 0.1),
               (0, 0.45, 0.06), (0, 0.65, 0.055), (0, 0.85, 0.05), (0, 1.05, 0.045), (0, 1.25, 0.04)]
        b2.chain(hips[-1], pts, "tail")
        rig2, doc2 = rig_of(b2, sk.SERPENT_PLAN)
        self.assertEqual(len(doc2["roles"]["tail"]), 10)
        walk = cm.from_role_space(rig2, "walk_none", lib["anims"]["walk_none"])
        tip1 = rig.index[doc["roles"]["tail"][-1]]
        tip2 = rig2.index[doc2["roles"]["tail"][-1]]
        x1 = [cm.posed_points(rig, clips[1], f)[1][tip1][0] for f in range(12)]
        x2 = [cm.posed_points(rig2, walk, f)[1][tip2][0] for f in range(12)]
        self.assertLess(max(abs(a - b) for a, b in zip(x1, x2)), 0.05)

    def test_template_with_digitigrade_legs(self):
        # Three-segment legs: thigh forward, shin back, a long foot (the reverse ankle) forward.
        def leg(side):
            s = 1 if side == "l" else -1
            return [
                {"name": "thigh_" + side, "parent": "pelvis", "head": [0.08 * s, 0, 0.6], "tail": [0.09 * s, 0.08, 0.38],
                 "role": "leg_%s0" % side, "bend": "front"},
                {"name": "shin_" + side, "parent": "thigh_" + side, "head": [0.09 * s, 0.08, 0.38],
                 "tail": [0.09 * s, -0.06, 0.18], "role": "leg_%s0" % side, "bend": "back"},
                {"name": "foot_" + side, "parent": "shin_" + side, "head": [0.09 * s, -0.06, 0.18],
                 "tail": [0.09 * s, 0.0, 0.02], "role": "leg_%s0" % side, "bend": "front"},
                {"name": "toe_" + side, "parent": "foot_" + side, "head": [0.09 * s, 0.0, 0.02],
                 "tail": [0.09 * s, -0.08, 0.0], "role": "leg_%s0" % side}]
        template = {"bones": [{"name": "pelvis", "parent": None, "head": [0, 0, 0.6], "tail": [0, 0, 0.65],
                               "role": "root"},
                              {"name": "chest", "parent": "pelvis", "head": [0, 0, 0.65], "tail": [0, -0.1, 0.9],
                               "role": "spine"},
                              {"name": "head", "parent": "chest", "head": [0, -0.1, 0.9], "tail": [0, -0.25, 1.0],
                               "role": "neck"}] + leg("l") + leg("r")}
        self.assertEqual(cb.check_template(template), [])
        plan = cb.body_plan({"template": template})
        self.assertEqual(cb.check_body({"template": template}), [])
        legc = [c for c in plan["chains"] if c["id"] == "leg"][0]
        self.assertEqual((legc["toward"], legc["pairs"], legc["joints"], legc["bends"]),
                         ("down", 1, 4, ["front", "back", "front", "none"]))
        # The template itself, as a predicted skeleton of 1.8 m (body heights to metres, model axes).
        tree = {}
        for i, b in enumerate(template["bones"]):
            conv = [(p[0] * 1.8, -p[1] * 1.8, p[2] * 1.8) for p in (b["head"], b["tail"])]
            tree[b["name"]] = {"parent": b["parent"], "head": conv[0], "tail": conv[1]}
        doc = cb.propose(tree, plan)
        self.assertTrue(doc["valid"], doc["problems"])
        self.assertEqual([r["bend"] for r in doc["joints"]["leg_l0"]], ["front", "back", "front", "none"])
        rig = cm.Rig(tree, doc["roles"], doc["root"], 1.8, doc["joints"])
        clip = cm.generate(rig, plan, {"name": "walk_none", "kind": "gait", "frames": 16,
                                         "settings": {"legs": ["leg"]}})
        self.assertLess(clip["reach_shortfall_m"], 0.01)
        # The reverse ankle (head of the foot) stays behind the line from hip to the foot's end.
        for f in range(16):
            heads, tails = cm.posed_points(rig, clip, f)
            hip, knee, ankle = (heads[rig.index[n]] for n in ("thigh_l", "shin_l", "foot_l"))
            self.assertLess(knee[1], hip[1] + 0.05)  # the knee forward of the hip (forward is -Y)
            self.assertGreater(ankle[1], knee[1])     # the ankle behind the knee

    def test_rotation_helpers(self):
        for axis in ([1, 0, 0], [0, 1, 0], [0.3, -0.5, 0.8]):
            m = cm.rot(axis, 37.0)
            self.assertTrue(np.allclose(cm._matrix(cm.quaternion(m)), m, atol=1e-9))
        d = np.array([0.0, 0.0, 1.0])
        self.assertLess((cm.rot(cm.pitch_axis(d), 30) @ d)[1], 0)  # up turns toward the front (-Y)
        d = np.array([0.0, 0.0, -1.0])
        self.assertLess((cm.rot(cm.pitch_axis(d), 30) @ d)[1], 0)  # down too
        d = np.array([0.0, 1.0, 0.0])
        self.assertLess((cm.rot(cm.pitch_axis(d), 30) @ d)[2], 0)  # back turns toward the ground
        self.assertGreater((cm.rot(cm.lateral_axis(d), 30) @ d)[0], 0)  # and toward the left
        self.assertTrue(math.isclose(np.linalg.norm(cm.between(np.array([1.0, 0, 0]), np.array([0, 1.0, 0]))
                                                    @ [1, 0, 0] - np.array([0, 1, 0])), 0, abs_tol=1e-9))


if __name__ == "__main__":
    unittest.main()
