"""The humanoid's motions list (forge/motion_clips.py, forge/motion_jobs.py,
forge/motion_text.py): the canonical steps on synthetic motion (body coordinates, the loop
window and closure, contacts, hip height, foot cleanup), the BVH and HY-Motion joint readers,
the starter library, the order check, the jobs (made once per family, path-free
specifications, orders without motions unchanged) and a night with the fake server and
stand-ins (HY-Motion's entry replaced by a script that writes synthetic joints)."""

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import local_gpu, motion_clips as mc, stages  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for  # noqa: E402
from forge.recipes import expand  # noqa: E402
from motion_synth import smpl_output, walk, write_bvh  # noqa: E402
from test_humanoid_recipe import ROLES, Base, FakeTools, piece_order  # noqa: E402

STANDIN = os.path.join(HERE, "fixtures", "motion", "hymotion_standin.py")
TEXT = {"name": "walk_text", "source": "text", "prompt": "A person walks forward at a steady pace.",
        "frames": 30, "fps": 30, "loop": True, "kind": "walk"}


def entry(**kw):
    return dict({"name": "walk_x", "frames": 30, "fps": 30, "loop": True}, **kw)


class CanonicalTest(unittest.TestCase):
    def test_a_generated_walk_becomes_a_closed_walk_in_place(self):
        pos = walk(4.0)
        clip, rep = mc.make_clip(pos, 30.0, 1, entry(), "synthetic")
        height = rep["scale"]["height_source_units"]
        self.assertAlmostEqual(clip["travel"][1], 1.2 / height, places=3)  # 1.2 m a one-second cycle
        self.assertAlmostEqual(clip["travel"][0], 0.0, places=3)
        self.assertEqual(clip["feet"], "walk")
        self.assertEqual(len(clip["frames"]), 30)
        self.assertTrue(rep["valid"], rep["flags"])
        for side in ("left", "right"):  # stance is 0.6 of the cycle: most of it is found
            self.assertGreaterEqual(len(clip["stance"][side]), 14)
        arr = mc.to_array(clip["frames"])
        self.assertAlmostEqual(float(arr[:, mc.IDX["pelvis"], :2].mean(0)[1]), 0.0, delta=0.02)  # in place
        names = [c["name"] for c in rep["corrections"]]
        self.assertEqual(names, ["cycle_closure", "hip_height", "foot_contacts", "below_ground"])
        self.assertTrue(all(c["passed"] for c in rep["corrections"]))

    def test_corrections_fix_floating_hips_and_sliding_feet(self):
        pos = walk(4.0)
        rng = np.random.default_rng(3)
        pos[:, :, 1] += rng.normal(0, 0.012, len(pos))[:, None]  # the whole body floats and sinks
        for s in ("l", "r"):  # planted feet slide sideways
            pos[:, mc.IDX[s + "_ankle"], 0] += 0.03 * np.sin(np.arange(len(pos)) / 3.0)
        clip, rep = mc.make_clip(pos, 30.0, 1, entry(), "noisy")
        rec = {c["name"]: c for c in rep["corrections"]}
        self.assertTrue(rec["hip_height"]["applied"])
        self.assertGreater(rec["hip_height"]["before"], mc.THRESHOLDS["ground"])
        self.assertLessEqual(rec["hip_height"]["after"], mc.THRESHOLDS["ground"])
        self.assertTrue(rec["foot_contacts"]["applied"])
        self.assertGreater(rec["foot_contacts"]["before"], mc.THRESHOLDS["foot_slide"])
        self.assertLessEqual(rec["foot_contacts"]["after"], mc.THRESHOLDS["foot_slide"])
        self.assertTrue(rep["valid"], rep["flags"])

    def test_given_contacts_win_and_an_idle_stands_still(self):
        clip, rep = mc.make_clip(walk(2.0, still=True), 30.0, 1, entry(name="idle_x", frames=20, fps=20), "idle")
        self.assertEqual(clip["stance"], {"left": "all", "right": "all"})
        self.assertEqual(clip["feet"], "clip")
        self.assertNotIn("travel", clip)
        clip, rep = mc.make_clip(walk(2.0, still=True), 30.0, 1,
                                 entry(name="idle_x", frames=20, fps=20, contacts={"left": [0, 1, 2]}), "idle")
        self.assertEqual(rep["contacts"]["how"], "given")
        self.assertEqual(clip["stance"], {"left": [0, 1, 2], "right": []})

    def test_a_one_shot_keeps_its_length_and_its_place(self):
        clip, rep = mc.make_clip(walk(2.0), 30.0, 1, entry(name="step", frames=48, fps=24, loop=False), "one shot")
        self.assertEqual(len(clip["frames"]), 48)
        self.assertFalse(clip["loop"])
        self.assertNotIn("travel", clip)
        self.assertNotIn("window", rep)
        arr = mc.to_array(clip["frames"])
        self.assertLess(float(np.abs(arr[0, mc.IDX["pelvis"], :2]).max()), 1e-6)  # starts over the origin
        self.assertGreater(float(arr[-1, mc.IDX["pelvis"], 1]), 0.8)  # and walks forward

    def test_hymotion_joints_and_bvh_captures_read_back(self):
        pos = walk(1.0)
        kp, transl = smpl_output(pos)
        back = mc.smpl_canon(kp, transl)
        keep = [i for i, j in enumerate(mc.CANON) if j != "head_top"]
        shift = back[0, 0] - pos[0, 0]
        self.assertLess(float(np.abs(back[:, keep] - shift - pos[:, keep]).max()), 1e-9)
        tmp = tempfile.mkdtemp(prefix="forge_bvh_")
        self.addCleanup(shutil.rmtree, tmp, True)
        for prefix in ("", "mixamorig:"):
            path = os.path.join(tmp, "walk%d.bvh" % len(prefix))
            write_bvh(path, pos, 30.0, prefix=prefix)
            got, fps, up, joints = mc.bvh_canon(path)
            self.assertAlmostEqual(fps, 30.0, places=2)
            self.assertEqual(up, 1)
            self.assertLess(float(np.abs(got / 100.0 - pos).max()), 1e-5)
            self.assertEqual(joints["chest"], prefix + "Spine1")
        got, _, _, _ = mc.bvh_canon(path, {"range": [5, 9], "up": "z"})
        self.assertEqual(len(got), 5)

    def test_bvh_rotations(self):
        tmp = tempfile.mkdtemp(prefix="forge_bvh_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "arm.bvh")
        with open(path, "w") as f:
            f.write("HIERARCHY\nROOT A\n{\n OFFSET 0 0 0\n CHANNELS 6 Xposition Yposition Zposition Zrotation "
                    "Xrotation Yrotation\n JOINT B\n {\n  OFFSET 1 0 0\n  CHANNELS 3 Zrotation Xrotation Yrotation\n"
                    "  End Site\n  {\n   OFFSET 1 0 0\n  }\n }\n}\nMOTION\nFrames: 1\nFrame Time: 0.5\n"
                    "0 0 0 90 0 0 90 0 0\n")
        pos, names, fps = mc.read_bvh(path)
        self.assertEqual(names, ["A", "B", "B/end"])
        self.assertEqual(fps, 2.0)
        np.testing.assert_allclose(pos[0, 1], [0, 1, 0], atol=1e-9)   # A turned 90 degrees about z
        np.testing.assert_allclose(pos[0, 2], [-1, 1, 0], atol=1e-9)  # B turned 90 more

    def test_the_starter_library(self):
        doc, problems = mc.read_library(mc.STARTER)
        self.assertEqual(problems, [])
        self.assertEqual(len(doc["anims"]), 45)
        text = json.dumps(doc).lower()
        for word in ("tile", "soldier", "unit "):
            self.assertNotIn(word, text)
        walk_clip = doc["anims"]["walk_rifle"]
        self.assertEqual(walk_clip["travel"], [0.0, 0.76308])
        clip, rep = mc.library_entry_clip(doc, {"name": "walk_rifle", "source": "library", "loop": True}, "starter")
        self.assertEqual(rep["contacts"]["how"], "given")
        fc = next(c for c in rep["corrections"] if c["name"] == "foot_contacts")
        self.assertTrue(fc["applied"])
        self.assertLessEqual(fc["after"], mc.THRESHOLDS["foot_slide"])
        clip, rep = mc.library_entry_clip(doc, {"name": "w", "library": {"clip": "walk_none"}, "frames": 12}, "starter")
        self.assertEqual(len(clip["frames"]), 12)
        self.assertEqual(len(clip["stance"]["left"]), 7)  # 13 of 24 frames, by time
        self.assertEqual(clip["holds"], None)

    def test_motions_check(self):
        lib = {"anims": {"idle": {"frames": []}}}

        def problems(m, default=lib):
            return mc.check_motions([m], default, {})
        self.assertEqual(problems({"name": "idle", "source": "library"}), [])
        self.assertTrue(any("no clip walk" in p for p in problems({"name": "walk", "source": "library"})))
        self.assertTrue(any("needs inputs.clips" in p for p in problems({"name": "idle", "source": "library"}, None)))
        self.assertTrue(any("creature's" in p for p in problems({"name": "a", "source": "procedural", "kind": "gait"})))
        self.assertTrue(any("frames is required" in p for p in problems({"name": "a", "source": "text", "prompt": "x"})))
        self.assertTrue(any("prompt must be" in p for p in problems({"name": "a", "source": "text", "frames": 10})))
        self.assertTrue(any("not found" in p for p in problems({"name": "a", "source": "bvh", "file": "x.bvh",
                                                                 "frames": 10})))
        self.assertTrue(any("belongs to source text" in p for p in problems({"name": "idle", "source": "library",
                                                                              "prompt": "x"})))
        self.assertTrue(any("no setting 'beams'" in p for p in problems(dict(TEXT, settings={"beams": 2}))))
        self.assertTrue(any("steps must be" in p for p in problems(dict(TEXT, settings={"steps": 0}))))
        self.assertTrue(any("contacts must be" in p for p in problems(dict(TEXT, contacts={"l_foot": "all"}))))
        self.assertTrue(any("unknown keys" in p for p in problems(dict(TEXT, speed=2))))
        self.assertEqual(problems(dict(TEXT, contacts={"left": "all"}, settings={"seed": 4, "duration": 4.0})), [])
        from forge import tool_specs
        self.assertEqual(mc.MOTION_KEYS, tool_specs._tool("creature_motion").MOTION_KEYS)


class MotionJobsTest(Base):
    def setUp(self):
        Base.setUp(self)
        self.capture = os.path.join(self.inputs, "walk.bvh")
        write_bvh(self.capture, walk(3.0), 30.0)
        hy = os.path.join(self.tmp, "hymotion")
        for rel in (os.path.join("hymotion", "pipeline", "motion_diffusion.py"), os.path.join("stats", "Mean.npy"),
                    os.path.join("stats", "Std.npy"), os.path.join("lite", "latest.ckpt"),
                    os.path.join("lite", "config.yml"), os.path.join("qwen", "config.json"),
                    os.path.join("qwen", "model.safetensors"), os.path.join("clip", "config.json"),
                    os.path.join("clip", "model.safetensors")):
            os.makedirs(os.path.dirname(os.path.join(hy, rel)), exist_ok=True)
            with open(os.path.join(hy, rel), "w") as f:
                f.write("x")
        self.config.update(hymotion_python=sys.executable, hymotion_dir=hy,
                           hymotion_model=os.path.join(hy, "lite", "latest.ckpt"),
                           hymotion_text_encoder=os.path.join(hy, "qwen"), hymotion_clip=os.path.join(hy, "clip"))

    def specs(self, order):
        import test_humanoid_recipe as thr
        with patch.object(thr, "ROLES", ROLES + ("clip", "library", "report")):
            return Base.specs(self, order)

    def motions(self):
        return [{"name": "walk_rifle", "source": "library", "library": {"file": "@starter"}, "holds": "rifle"},
                {"name": "idle_none", "source": "library", "library": {"file": "inputs/library.json", "clip": "idle"}},
                dict(TEXT),
                {"name": "walk_capture", "source": "bvh", "file": "inputs/walk.bvh", "frames": 30, "fps": 30,
                 "loop": True, "settings": {"range": [0, 89]}}]

    def two_pieces(self, other=None):
        order = piece_order(family={"motions": self.motions(), "inputs": {"clips": "inputs/library.json"}})
        order["families"][0]["assets"].append(dict({"id": "medic", "brief": "A medic."}, **(other or {})))
        return order

    def test_an_order_without_motions_expands_as_before(self):
        jobs = self.jobs(family={"inputs": {"clips": "inputs/library.json"}})
        self.assertFalse(any("motion" in j for j in jobs))
        export = jobs["crew/tech/v%d/export" % seeds_for("crew/tech", 1)[0]]
        self.assertEqual(export["inputs"]["clips"], {"file": os.path.join(self.inputs, "library.json")})

    def test_made_motions_are_one_job_per_family(self):
        jobs = {j["id"]: j for j in expand(self.load(self.two_pieces()))}
        self.assertIn("crew/_motions/walk_text", jobs)
        self.assertIn("crew/_motions/walk_capture", jobs)
        self.assertEqual(len([j for j in jobs if j.startswith("crew/_motions/")]), 2)
        text = jobs["crew/_motions/walk_text"]
        self.assertEqual((text["kind"], text["tool"]), ("local_gpu", "motion_text"))
        self.assertEqual(text["args"]["settings"], {"steps": 50, "seed": seeds_for("crew/_motions/walk_text", 1)[0],
                                                    "guidance": 5.0, "feet": "auto", "duration": 3.0})
        self.assertEqual(jobs["crew/_motions/walk_capture"]["kind"], "motion")
        for piece in ("tech", "medic"):
            lib = jobs["crew/%s/motions" % piece]
            self.assertEqual(lib["inputs"]["made"]["walk_text"], {"ref": "crew/_motions/walk_text", "role": "clip"})
            self.assertEqual(lib["inputs"]["libraries"]["lib0"], {"file": mc.STARTER_PATH})
            self.assertEqual([m["name"] for m in lib["args"]["motions"]],
                             ["walk_rifle", "idle_none", "walk_text", "walk_capture"])
            self.assertNotIn("file", json.dumps(lib["args"]).replace('"file": "lib', ""))
            for s in seeds_for("crew/" + piece, 2):
                self.assertEqual(jobs["crew/%s/v%d/export" % (piece, s)]["inputs"]["clips"],
                                 {"ref": "crew/%s/motions" % piece, "role": "library"})
        # The same name with another prompt: two jobs, each named with its specification's hash.
        other = self.two_pieces({"motions": self.motions()[:2] + [dict(TEXT, prompt="A person jogs.")]})
        ids = sorted(j["id"] for j in expand(self.load(other)) if j["id"].startswith("crew/_motions/walk_text"))
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(len(i.split("/")[-1]) == len("walk_text") + 9 for i in ids))

    def test_the_order_check(self):
        order = self.two_pieces()
        from forge.order import check_order
        self.assertEqual(check_order(self.load(order), self.config), [])
        bare = dict(self.config, hymotion_python=None)
        self.assertTrue(any("hymotion_python is not set" in p for p in check_order(self.load(order), bare)))
        statue = self.two_pieces({"skip": ["rig", "export"]})
        self.assertTrue(any("motions need the export stage" in p for p in check_order(self.load(statue), self.config)))
        named = self.two_pieces()
        named["families"][0]["assets"][1]["id"] = "_motions"
        self.assertTrue(any("cannot be named _motions" in p for p in check_order(self.load(named), self.config)))
        wrong = self.two_pieces({"motions": [{"name": "x", "source": "library", "library": {"clip": "nope"}}]})
        self.assertTrue(any("has no clip nope" in p for p in check_order(self.load(wrong), self.config)))
        self.assertIn("hymotion_python", stages.tools_needed("humanoid", self.load(order)["families"][0]["assets"][0]))

    def test_the_sample_order(self):
        from forge.order import check_order, load_order
        order = load_order(os.path.join(HERE, "fixtures", "order_humanoid_motions.json"))
        self.assertEqual(check_order(order, self.config), [])
        jobs = [j["id"] for j in expand(order)]
        self.assertEqual(len(jobs), 64)
        self.assertEqual(sorted(j for j in jobs if "/_motions/" in j), ["crew/_motions/walk_captured", "crew/_motions/wave"])

    def test_the_environment_check_imports_hymotion(self):
        from forge.readiness import check_environment
        env = check_environment(self.load(self.two_pieces()), self.config, {})
        self.assertTrue(any("cannot import HY-Motion's modules" in p for p in env.problems), env.problems)
        with patch("forge.motion_text.import_check", return_value=("torch 2.x cuda 12", None)):
            env = check_environment(self.load(self.two_pieces()), self.config, {})
        self.assertEqual(env.versions.get("hymotion_python"), "torch 2.x cuda 12")
        self.assertFalse(any("HY-Motion" in p for p in env.problems))

    def test_moving_the_order_redoes_nothing(self):
        first = self.specs(self.two_pieces())
        moved = os.path.join(self.tmp, "moved")
        shutil.copytree(self.inputs, os.path.join(moved, "inputs"))
        self.order_path, keep = os.path.join(moved, "order.json"), self.order_path
        try:
            second = self.specs(self.two_pieces())
        finally:
            self.order_path = keep
        self.assertEqual(first, second)
        # A changed capture redoes its clip and what depends on it, not the text clip.
        write_bvh(self.capture, walk(3.0, speed=1.0), 30.0)
        third = self.specs(self.two_pieces())
        self.assertNotEqual(first["crew/_motions/walk_capture"], third["crew/_motions/walk_capture"])
        self.assertEqual(first["crew/_motions/walk_text"], third["crew/_motions/walk_text"])
        self.assertNotEqual(first["crew/tech/motions"], third["crew/tech/motions"])


class MotionNightTest(MotionJobsTest):
    def setUp(self):
        MotionJobsTest.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")
        self.env = patch.dict(os.environ, {"FORGE_HYMOTION_ENTRY": STANDIN})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.fake.close()
        MotionJobsTest.tearDown(self)

    def test_a_night_with_every_source(self):
        self.load(self.two_pieces())
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.config = self.config
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        f.gpu_memory = lambda server: None
        tools = FakeTools()
        seen = {}

        def export(rig, out, config, **kw):
            with open(kw["clips"], encoding="utf-8") as fh:
                seen[out] = json.load(fh)
            return tools.export(rig, out, config, **kw)
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_export", export), \
                patch.object(local_gpu, "run_review", tools.review), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        for jid in ("crew/_motions/walk_text", "crew/_motions/walk_capture", "crew/tech/motions", "crew/medic/motions"):
            self.assertEqual(m[jid]["status"], "done", m[jid].get("error"))
        # The text clip ran once for both pieces, with its provenance.
        text_dir = os.path.join(self.out, "crew", "_motions", "walk_text")
        with open(os.path.join(text_dir, "motion_text.provenance.json"), encoding="utf-8") as fh:
            prov = json.load(fh)
        self.assertEqual(prov["prompt"], TEXT["prompt"])
        self.assertEqual(prov["seeds"], [seeds_for("crew/_motions/walk_text", 1)[0]])
        self.assertEqual(prov["settings"]["steps"], 50)
        self.assertEqual(m["crew/_motions/walk_text"]["provenance"]["parameters"]["prompt"]["requested"],
                         TEXT["prompt"])
        with open(os.path.join(text_dir, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertTrue(report["valid"], report["flags"])
        self.assertEqual(report["generation"]["seed"], prov["seeds"][0])
        # Every export got the piece's library: the four motions, named, holding and timed
        # as the entries say.
        self.assertEqual(len(seen), 4)
        lib = next(iter(seen.values()))
        self.assertEqual(list(lib["anims"]), ["walk_rifle", "idle_none", "walk_text", "walk_capture"])
        self.assertEqual(lib["anims"]["walk_rifle"]["holds"], "rifle")
        self.assertEqual(lib["anims"]["walk_text"]["kind"], "walk")
        self.assertEqual(len(lib["anims"]["walk_text"]["frames"]), 30)
        self.assertEqual(lib["anims"]["walk_capture"]["feet"], "walk")
        self.assertIn("box", lib["holds"])  # the default library's hold placements came along
        with open(os.path.join(self.out, "crew", "tech", "motions", "report.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["valid"])
        # A second run redoes nothing.
        before = copy.deepcopy(m)
        f2 = Factory(a, self.fake.url)
        f2.load()
        f2.config = self.config
        f2.pass_sleep = 0.02
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_export", export), \
                patch.object(local_gpu, "run_review", tools.review), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f2.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertEqual({k: v.get("spec") for k, v in before.items()}, {k: v.get("spec") for k, v in after.items()})
        self.assertEqual(after["crew/_motions/walk_text"]["finished"], before["crew/_motions/walk_text"]["finished"])


if __name__ == "__main__":
    unittest.main()
