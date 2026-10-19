"""A humanoid that wears an approved body (inputs.body): the body file's check and the
comparison of skeletons and motions read from GLB files (forge/body.py), how a piece expands
(the export becomes body_export, the body's hash in its specification, the conform limits in
its decision; a piece without a body keeps its jobs), what the order check refuses, the sample
order (valid, verified against a fake /object_info, also from the command line), and a night
with the fake server and stand-ins for the local tools (a conform flag rejects its variant, the
conform measures reach the review sheet's labels, a manual bone map in conform/ redoes only its
export)."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import glb_synth  # noqa: E402
from fake_comfy import FakeComfy  # noqa: E402
from forge import acceptance, body, local_gpu  # noqa: E402
from forge.config import TOOL_WEIGHTS, load_config  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.readiness import VERIFIED, check_environment  # noqa: E402
from forge.recipes import expand  # noqa: E402
from test_humanoid_recipe import Base, FakeTools, dependants, piece_order  # noqa: E402
from test_readiness import object_info  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
SAMPLE = os.path.join(FIXTURES, "order_humanoid_body.json")


class BodyFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_body_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_the_same_skeleton_and_a_moved_joint(self):
        a = body.skeleton(glb_synth.skeleton_glb(self.path("a.glb")))
        b = body.skeleton(glb_synth.skeleton_glb(self.path("b.glb")))
        same = body.compare_skeletons(a, b)
        self.assertTrue(same["identical"], same)
        self.assertEqual(same["joints"], len(glb_synth.BONES))
        self.assertIn("item_ref", a["attached"])
        moved = body.compare_skeletons(body.skeleton(glb_synth.skeleton_glb(
            self.path("c.glb"), moved={"LeftLowerArm": (0.0, 0.002, 0.0)})), b)
        self.assertFalse(moved["identical"])
        self.assertAlmostEqual(moved["max_offset_m"], 0.002, places=6)
        self.assertTrue(any("rest position" in p for p in moved["problems"]))
        self.assertTrue(any("inverse bind" in p for p in moved["problems"]))
        # Tiny float noise is the same skeleton.
        noisy = body.compare_skeletons(body.skeleton(glb_synth.skeleton_glb(
            self.path("d.glb"), moved={"Head": (3e-7, 0.0, 0.0)})), b)
        self.assertTrue(noisy["identical"], noisy)
        renamed = [("Pelvis" if n == "Hips" else n, "Pelvis" if p == "Hips" else p, t) for n, p, t in glb_synth.BONES]
        other = body.compare_skeletons(body.skeleton(glb_synth.skeleton_glb(self.path("e.glb"), bones=renamed,
                                                                            attached=False)), b)
        self.assertIn("the body's joint Hips is missing", other["problems"])
        self.assertIn("joint Pelvis is not in the body's skeleton", other["problems"])

    def test_animations_compared_up_to_the_quaternions_sign(self):
        anim = {"name": "idle", "node": "Head", "times": [0.0, 0.5], "rotations": [(0, 0, 0, 1), (0, 0.1, 0, 0.995)]}
        a = body.animations(glb_synth.skeleton_glb(self.path("a.glb"), animation=anim))
        flipped = dict(anim, rotations=[(0, 0, 0, -1), (0, -0.1, 0, -0.995)])
        b = body.animations(glb_synth.skeleton_glb(self.path("b.glb"), animation=flipped))
        self.assertEqual(body.compare_animations(a, b)["max_difference"], 0.0)
        c = body.animations(glb_synth.skeleton_glb(self.path("c.glb"), animation=dict(
            anim, rotations=[(0, 0, 0, 1), (0, 0.12, 0, 0.993)])))
        diff = body.compare_animations(a, c)
        self.assertAlmostEqual(diff["max_difference"], 0.02, places=5)
        self.assertEqual(diff["worst"], ["idle", "Head", "rotation"])

    def test_check_body(self):
        good = glb_synth.stub_body(os.path.join(self.tmp, "good"))
        self.assertEqual(body.check_body(good, "f/p"), [])
        self.assertIn("must be a GLB", body.check_body(self.path("x.fbx"), "f/p")[0])
        lone = glb_synth.skeleton_glb(os.path.join(self.tmp, "lone", "x.glb"))
        self.assertIn("its model.json cannot be read", body.check_body(lone, "f/p")[0])
        with open(os.path.join(self.tmp, "lone", "model.json"), "w") as f:
            json.dump({"format": "forge-creature"}, f)
        self.assertIn("is not a humanoid exporter's model.json", body.check_body(lone, "f/p")[0])
        roles = dict(glb_synth.ROLE_NAMES)
        del roles["left_hand"]
        roles["neck"] = "Collar"
        partial = glb_synth.stub_body(os.path.join(self.tmp, "partial"), roles=roles)
        problems = body.check_body(partial, "f/p")
        self.assertTrue(any("names no bone for left_hand" in p for p in problems), problems)
        self.assertTrue(any("lacks the bones its model.json names: Collar" in p for p in problems), problems)
        flat = os.path.join(self.tmp, "flat")
        os.makedirs(flat)
        glb_synth.write_glb(os.path.join(flat, "flat.glb"), {"asset": {"version": "2.0"}, "nodes": []})
        with open(os.path.join(flat, "model.json"), "w") as f:
            json.dump({"format": "forge-humanoid", "height": 1.8, "bones": glb_synth.ROLE_NAMES}, f)
        self.assertTrue(any("cannot be read as a skinned GLB" in p for p in body.check_body(
            os.path.join(flat, "flat.glb"), "f/p")))

    def test_a_turned_body_is_refused(self):
        straight = glb_synth.stub_body(os.path.join(self.tmp, "straight"))
        sk = body.skeleton(straight)
        self.assertAlmostEqual(body.body_turn(sk, glb_synth.ROLE_NAMES), 0.0, places=6)
        self.assertAlmostEqual(body.joint_positions(sk)["LeftHand"][0], 0.33, places=5)
        # The upper arms 0.36 apart across and 0.2 apart front to back: turned about 29 degrees.
        turned = glb_synth.skeleton_glb(os.path.join(self.tmp, "turned", "turned.glb"),
                                        moved={"LeftUpperArm": (0.0, 0.0, 0.1), "RightUpperArm": (0.0, 0.0, -0.1)})
        with open(os.path.join(self.tmp, "turned", "model.json"), "w", encoding="utf-8") as f:
            json.dump({"format": "forge-humanoid", "height": 1.8, "bones": glb_synth.ROLE_NAMES}, f)
        self.assertAlmostEqual(body.body_turn(body.skeleton(turned), glb_synth.ROLE_NAMES), 29.05, delta=0.1)
        self.assertTrue(any("stands turned 29 degrees" in p for p in body.check_body(turned, "f/p")))

    def test_wears_own_output(self):
        self.assertTrue(body.wears_own_output(os.path.join("D:", os.sep, "night", "crew", "plain", "final", "plain.glb"),
                                              "crew", "plain"))
        self.assertFalse(body.wears_own_output(os.path.join("D:", os.sep, "night", "crew", "plain", "final", "plain.glb"),
                                               "crew", "technician"))


class BodyBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.body_a = glb_synth.stub_body(os.path.join(self.inputs, "body_a"), name="body_a")
        self.body_b = glb_synth.stub_body(os.path.join(self.inputs, "body_b"), name="body_b", height=1.7)

    def worn(self, piece=None, family=None):
        fam = {"inputs": {"clips": "inputs/library.json", "items": "inputs/items", "body": "inputs/body_a/body_a.glb"}}
        fam.update(family or {})
        return piece, fam


class Expansion(BodyBase):
    def test_the_export_wears_the_body(self):
        params = {"params": {"tris": 20000, "image_format": "compact"}}
        jobs = self.jobs(*self.worn(dict(params)))
        seeds = seeds_for("crew/tech", 2)
        ex = jobs["crew/tech/v%d/export" % seeds[0]]
        self.assertEqual((ex["tool"], ex["stage"], ex["kind"]), ("body_export", "export", "local_gpu"))
        self.assertEqual(ex["inputs"]["body"], {"file": self.body_a})
        self.assertEqual(ex["inputs"]["body_model"], {"file": os.path.join(os.path.dirname(self.body_a), "model.json")})
        self.assertEqual(ex["args"], {"name": "tech", "tris": 20000, "normal_map": 2048, "image_format": "compact"})
        self.assertEqual(ex["inputs"]["rig"]["ref"], "crew/tech/v%d/rig" % seeds[0])
        # The decision has the conform limits; a piece without a body keeps its limits.
        self.assertEqual(jobs["crew/tech/final"]["accept"], acceptance.limits(None, "humanoid_body"))
        self.assertEqual(jobs["crew/tech/final"]["accept"]["conform_flags"], 0)
        plain = self.jobs(dict(params), family={"inputs": {"clips": "inputs/library.json", "items": "inputs/items"}})
        self.assertEqual(plain["crew/tech/final"]["accept"], acceptance.limits(None))
        self.assertNotIn("conform_flags", plain["crew/tech/final"]["accept"])
        self.assertEqual(plain["crew/tech/v%d/export" % seeds[0]]["tool"], "export")
        # The same chain before the export.
        self.assertEqual(sorted(jobs), sorted(plain))
        for jid in jobs:
            if not jid.endswith(("/export", "/review", "/final")):
                self.assertEqual(jobs[jid], plain[jid], jid)

    def test_conform_settings_reach_the_export(self):
        piece = {"stages": {"export": {"option": "humanoid_export",
                                       "settings": {"conform_scale": 1.8, "conform_floor_cm": 6.0}}}}
        jobs = self.jobs(*self.worn(piece))
        ex = jobs["crew/tech/v%d/export" % seeds_for("crew/tech", 2)[0]]
        self.assertEqual((ex["args"]["conform_scale"], ex["args"]["conform_floor_cm"]), (1.8, 6.0))

    def test_a_piece_override_and_a_null(self):
        order = piece_order({"inputs": {"body": "inputs/body_b/body_b.glb"}}, family=self.worn()[1])
        order["families"][0]["assets"].append({"id": "plain", "brief": "Plain.", "inputs": {"body": None}})
        jobs = {j["id"]: j for j in expand(self.load(order))}
        s_tech, s_plain = seeds_for("crew/tech", 2)[0], seeds_for("crew/plain", 2)[0]
        self.assertEqual(jobs["crew/tech/v%d/export" % s_tech]["inputs"]["body"], {"file": self.body_b})
        self.assertEqual(jobs["crew/plain/v%d/export" % s_plain]["tool"], "export")
        self.assertNotIn("body", jobs["crew/plain/v%d/export" % s_plain]["inputs"])
        self.assertEqual(check_order(self.load(order), self.config), [])

    def test_a_changed_body_redoes_the_exports_and_decisions_only(self):
        order = piece_order(*self.worn())
        before = self.specs(order)
        with open(os.path.join(os.path.dirname(self.body_a), "model.json"), encoding="utf-8") as f:
            model = json.load(f)
        model["height"] = 1.75
        with open(os.path.join(os.path.dirname(self.body_a), "model.json"), "w", encoding="utf-8") as f:
            json.dump(model, f)
        after = self.specs(order)
        changed = sorted(j for j in before if before[j] != after[j])
        jobs = list(expand(self.load(order)))
        exports = [j["id"] for j in jobs if j["id"].endswith("/export")]
        expected = set()
        for e in exports:
            expected |= dependants(jobs, e)
        self.assertEqual(set(changed), expected)
        self.assertEqual(sorted(x.rsplit("/", 1)[-1] for x in changed),
                         ["export", "export", "final", "review"])


class Checks(BodyBase):
    def test_valid(self):
        self.assertEqual(self.problems(*self.worn()), [])

    def test_the_body_sets_the_proportions(self):
        problems = self.problems(*self.worn({"params": {"height": 1.9, "head": 1.1}}))
        self.assertProblem(problems, "params.height: the approved body (inputs.body) sets the height")
        self.assertProblem(problems, "params.head: the approved body")

    def test_the_body_needs_the_export(self):
        problems = self.problems(*self.worn({"skip": ["rig", "export"]}))
        self.assertProblem(problems, "input 'body' is not used by recipe humanoid with these stages")

    def test_a_missing_or_wrong_body(self):
        self.assertProblem(self.problems(*self.worn(family={"inputs": {"body": "inputs/none.glb"}})),
                           "input body not found")
        with open(os.path.join(self.inputs, "body.obj"), "w") as f:
            f.write("")
        self.assertProblem(self.problems(*self.worn(family={"inputs": {"body": "inputs/body.obj"}})),
                           "inputs.body must be a GLB the humanoid exporter wrote")
        os.remove(os.path.join(self.inputs, "body_a", "model.json"))
        self.assertProblem(self.problems(*self.worn()), "its model.json cannot be read")

    def test_a_piece_cannot_wear_its_own_output(self):
        own = glb_synth.stub_body(os.path.join(self.tmp, "night", "crew", "tech", "final"), name="tech")
        self.assertProblem(self.problems(*self.worn(family={"inputs": {"body": own}})),
                           "inputs.body is this piece's own output")

    def test_conform_settings_and_limits_need_a_body(self):
        piece = {"stages": {"export": {"option": "humanoid_export", "settings": {"conform_scale": 1.8}}},
                 "accept": {"conform_flags": 1, "body_outside_cm": 2.0}}
        problems = self.problems(piece)
        self.assertProblem(problems, "export settings conform_scale are the conform's: they need inputs.body")
        self.assertProblem(problems, "unknown accept key 'conform_flags'")
        self.assertEqual(self.problems(*self.worn(piece)), [])
        self.assertProblem(self.problems(*self.worn({"accept": {"conform_flags": -1}})),
                           "accept conform_flags must be a whole number")


class SampleOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_body_sample_")
        self.config = load_config(None)
        for k in ("blender_python", "godot", "skintokens_python"):
            self.config[k] = sys.executable
        self.config["skintokens_dir"] = self.tmp
        with open(os.path.join(self.tmp, "demo.py"), "w") as f:
            f.write("")
        for rel in TOOL_WEIGHTS["skintokens_dir"]:  # stand-in weight files (the environment check wants them)
            os.makedirs(os.path.dirname(os.path.join(self.tmp, rel)), exist_ok=True)
            open(os.path.join(self.tmp, rel), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sample(self):
        """The sample order, its bodies replaced by stubs when the body smoke test has not made
        them yet (a copy in the temporary folder)."""
        with open(SAMPLE, encoding="utf-8") as f:
            doc = json.load(f)
        for fam in doc["families"]:
            for holder in [fam] + fam["assets"]:
                ins = holder.get("inputs") or {}
                for k in ("clips", "items"):
                    if k in ins:
                        ins[k] = os.path.join(FIXTURES, ins[k])
                if ins.get("body"):
                    real = os.path.normpath(os.path.join(FIXTURES, ins["body"]))
                    name = os.path.splitext(os.path.basename(real))[0]
                    ins["body"] = real if os.path.isfile(real) else glb_synth.stub_body(
                        os.path.join(self.tmp, name), name=name)
        path = os.path.join(self.tmp, "order_humanoid_body.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def test_sample_order(self):
        order = load_order(self.sample())
        self.assertEqual(check_order(order, self.config), [])
        env = check_environment(order, self.config, object_info())
        self.assertEqual((env.status, env.problems), (VERIFIED, []))
        jobs = {j["id"]: j for j in expand(order)}
        tools = {jid.split("/")[1]: j["tool"] for jid, j in jobs.items() if jid.endswith("/export")}
        self.assertEqual(tools, {"plain": "export", "technician": "body_export", "hazard": "body_export",
                                 "pilot": "body_export"})
        pilot = [j for jid, j in jobs.items() if jid.startswith("crew/pilot/") and jid.endswith("/export")][0]
        self.assertTrue(pilot["inputs"]["body"]["file"].endswith("body_b.glb"))
        hazard = [j for jid, j in jobs.items() if jid.startswith("crew/hazard/") and jid.endswith("/export")][0]
        self.assertEqual(hazard["args"]["conform_floor_cm"], 6.0)
        self.assertEqual(jobs["crew/technician/final"]["accept"]["conform_stretch_p95"], 0.3)

    def test_check_and_estimate_from_the_command_line(self):
        fake = FakeComfy()
        try:
            fake.object_info = object_info()
            config = os.path.join(self.tmp, "forge.local.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({k: self.config[k] for k in ("blender_python", "godot", "skintokens_python",
                                                        "skintokens_dir")}, f)
            base = [sys.executable, "-m", "forge", "--order", self.sample(), "--out", os.path.join(self.tmp, "out"),
                    "--config", config, "--server", fake.url]
            res = subprocess.run(base + ["--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("order: valid", res.stdout)
            self.assertIn("environment: verified", res.stdout)
            res = subprocess.run(base + ["--estimate"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("total 92 jobs", res.stdout)
            self.assertFalse(any(r[1] == "POST" for r in fake.requests))
        finally:
            fake.close()


class BodyTools(FakeTools):
    """FakeTools with run_body_export: the export's files plus a conform report, with flags for
    the variants in conform_flagged."""

    def __init__(self, conform_flagged=(), **kw):
        FakeTools.__init__(self, **kw)
        self.conform_flagged = set(conform_flagged)

    def body_export(self, rig, out, config, body_glb, body_model=None, **kw):
        v = self.variant(out)
        kw = dict(kw, body=body_glb, body_model=body_model)
        files = FakeTools.export(self, rig, out, config, **kw)
        self.calls[-1] = ("body_export", v, kw)
        os.makedirs(os.path.join(out, "conform"), exist_ok=True)
        flags = ["outside: the body's left_lower_arm joint lies 3.0 cm outside the outfit (limit 1.5)"] \
            if v in self.conform_flagged else []
        files["conform_report"] = os.path.join(out, "conform_report.json")
        with open(files["conform_report"], "w", encoding="utf-8") as f:
            json.dump({"format": "forge-conform-report", "valid": True, "joint_error_cm": 0.4, "stretch_p95": 0.08,
                       "body_outside": {"p99_cm": 1.5}, "flags": flags, "variant": v}, f)
        return files


class NightTest(BodyBase):
    def setUp(self):
        BodyBase.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")
        self.v = ["v%d" % s for s in seeds_for("crew/tech", 3)]

    def tearDown(self):
        self.fake.close()
        BodyBase.tearDown(self)

    def run_night(self, tools):
        piece, fam = self.worn()
        fam["variants"] = 3
        self.load(piece_order(piece, family=fam))
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_body_export",
                                                                         tools.body_export), \
                patch.object(local_gpu, "run_export", tools.export), \
                patch.object(local_gpu, "run_review", tools.review), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_conform_flag_rejects_its_variant(self):
        tools = BodyTools(conform_flagged=[self.v[0]])
        m = self.run_night(tools)
        exports = [c for c in tools.calls if c[0] in ("export", "body_export")]
        self.assertEqual(sorted(c[1] for c in exports), sorted(self.v))
        self.assertTrue(all(c[0] == "body_export" for c in exports))
        kw = exports[0][2]
        self.assertEqual((kw["body"], kw["body_model"]),
                         (self.body_a, os.path.join(os.path.dirname(self.body_a), "model.json")))
        self.assertNotIn("height", kw["params"])
        self.assertIn("conform_report", m["crew/tech/%s/export" % self.v[0]]["files"])
        with open(os.path.join(self.out, "crew", "tech", "pick_final.json"), encoding="utf-8") as fh:
            pick = json.load(fh)
        first = pick["candidates"][0]
        self.assertEqual(first["class"], "rejected")
        self.assertEqual(first["responsible"], ["rig"])
        self.assertTrue(first["reasons"][0].startswith("conform: outside: the body's left_lower_arm joint"))
        self.assertEqual(pick["chosen"], "crew/tech/%s/export" % self.v[1])
        self.assertIn("conform_report.json", os.listdir(os.path.join(self.out, "crew", "tech", "final")))
        # The sheet's labels carry the conform measures.
        [review] = [c for c in tools.calls if c[0] == "review"]
        self.assertTrue(review)
        # A manual bone map in the variant's conform folder redoes only its export.
        folder = os.path.join(self.out, "crew", "tech", "%s_export" % self.v[2], "conform")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "bone_map.json"), "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "roles": {"hips": "bone_0"}}, fh)
        tools.calls.clear()
        m2 = self.run_night(tools)
        redone = sorted(c[1] for c in tools.calls if c[0] == "body_export")
        self.assertEqual(redone, [self.v[2]])
        self.assertNotEqual(m2["crew/tech/%s/export" % self.v[2]]["spec"], m["crew/tech/%s/export" % self.v[2]]["spec"])

    def test_the_review_labels_show_the_conform(self):
        tools = BodyTools()
        seen = {}
        inner = tools.review

        def review(glbs, out, config, camera=None, labels=None, verdicts=None, banner=None):
            seen["verdicts"] = verdicts
            return inner(glbs, out, config, camera=camera, labels=labels, verdicts=verdicts, banner=banner)
        tools.review = review
        self.run_night(tools)
        self.assertEqual(len(seen["verdicts"]), 3)
        for v in seen["verdicts"]:
            self.assertEqual(v["class"], "acceptable")
            self.assertIn("conform: joints 0.4 cm, stretch p95 0.08, body out 1.5 cm, 0 flags", v["reasons"])


if __name__ == "__main__":
    unittest.main()
