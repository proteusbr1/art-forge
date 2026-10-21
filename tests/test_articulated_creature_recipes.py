"""The articulated and creature recipes: how a piece expands into jobs, what the order check
refuses, the sample orders (valid, and verified against a fake /object_info), and a night with
the fake server and stand-ins for the local tools (a failed variant, the review sheet, the
final decision, a manual frame or bone map redoing only its own jobs)."""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import acceptance, local_gpu, stages  # noqa: E402
from forge.config import TOOL_WEIGHTS, load_config  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for, sha_obj  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.readiness import VERIFIED, check_environment  # noqa: E402
from forge.recipes import expand, refs_of  # noqa: E402
from test_readiness import object_info  # noqa: E402
import creature_skeletons as sk  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
STYLE = {"global": "Plain test style.", "view_articulated": "The whole machine, plain grey background.",
         "view_creature": "The whole creature, plain grey background.", "view_figure": "One subject, grey."}
PARTS = [{"id": "hull"},
         {"id": "turret", "parent": "hull", "find": {"box": [[-0.4, -0.4, [0.45, 0.7]], [0.4, None, None]]},
          "joint": {"kind": "turn", "axis": "up", "range": [-180, 180]}}]
MOTIONS = [{"name": "idle_none", "source": "procedural", "kind": "breathe", "frames": 20, "fps": 20},
           {"name": "walk_none", "source": "procedural", "kind": "undulate", "frames": 24}]
ROLES = ("rgb", "mask", "rgba", "game_mesh", "raw_mesh", "painted_mesh", "metal_roughness", "rig", "glb", "model",
         "mesh_report", "motion_report", "frame", "clips", "bone_map")


def order_of(recipe, piece=None, family=None):
    base = {"id": "p", "brief": "A test subject."}
    if recipe == "articulated":
        base["parts"] = copy.deepcopy(PARTS)
    else:
        base.update(body=copy.deepcopy(sk.SERPENT_PLAN), motions=copy.deepcopy(MOTIONS))
    fam = {"id": "f", "recipe": recipe, "variants": 2, "assets": [dict(base, **(piece or {}))]}
    fam.update(family or {})
    return {"version": 1, "style": dict(STYLE), "families": [fam]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_ac_")
        self.config = load_config(None)
        tools = os.path.join(self.tmp, "tools", "skintokens")
        os.makedirs(tools)
        open(os.path.join(tools, "demo.py"), "w").close()
        for rel in TOOL_WEIGHTS["skintokens_dir"]:  # stand-in weight files (the environment check wants them)
            os.makedirs(os.path.dirname(os.path.join(tools, rel)), exist_ok=True)
            open(os.path.join(tools, rel), "w").close()
        for k in ("blender_python", "godot", "skintokens_python"):
            self.config[k] = sys.executable
        self.config["skintokens_dir"] = tools
        self.order_path = os.path.join(self.tmp, "order.json")
        shutil.copy(os.path.join(FIXTURES, "creature", "clips.json"), os.path.join(self.tmp, "clips.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        with open(self.order_path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(self.order_path)

    def problems(self, *args, **kw):
        return check_order(self.load(order_of(*args, **kw)), self.config)

    def jobs(self, *args, **kw):
        return {j["id"]: j for j in expand(self.load(order_of(*args, **kw)))}

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    def specs(self, order):
        self.load(order)
        a = SimpleNamespace(order=self.order_path, order_base="", out=os.path.join(self.tmp, "out"), only="",
                            retry_failed=False)
        f = Factory(a, "http://127.0.0.1:1")
        f.config = self.config
        f.load()
        out = {}
        while len(out) < len(f.jobs):
            for j in f.jobs:
                if j["id"] in out or any(r not in out for r in refs_of(j)):
                    continue
                sp = f.spec(j)
                out[j["id"]] = sp
                f.manifest[j["id"]] = {"status": "done", "spec": sp,
                                       "files": {r: ["x/%s.bin" % r, sha_obj([sp, r])] for r in ROLES}}
        return out


class Expansion(Base):
    def test_articulated_chain(self):
        jobs = self.jobs("articulated")
        seeds = seeds_for("f/p", 2)
        expected = ["f/p/v%d/%s" % (s, st) for s in seeds for st in ("reference", "cutout", "mesh", "paint", "parts")]
        self.assertEqual(list(jobs), expected + ["f/p/review", "f/p/final"])
        v = "f/p/v%d/" % seeds[0]
        self.assertEqual((jobs[v + "reference"]["values"]["$WIDTH$"], jobs[v + "reference"]["values"]["$HEIGHT$"]),
                         (1536, 1024))
        self.assertIn("The whole machine", jobs[v + "reference"]["values"]["$PROMPT$"])
        parts = jobs[v + "parts"]
        self.assertEqual((parts["kind"], parts["tool"], parts["group"]), ("local_gpu", "parts", "local_gpu"))
        self.assertEqual(parts["inputs"], {"mesh": {"ref": v + "paint", "role": "painted_mesh"},
                                           "bake_source": {"ref": v + "mesh", "role": "raw_mesh"},
                                           "metal_roughness": {"ref": v + "paint", "role": "metal_roughness"}})
        self.assertEqual(parts["args"], {"name": "p", "parts": PARTS})
        review = jobs["f/p/review"]
        self.assertEqual((review["tool"], review["args"]["pitch"]), ("sheet", 60))
        self.assertEqual(review["accept"], {"mesh_flags": 0})
        self.assertEqual([x["role"] for x in review["variants"]], ["glb", "glb"])
        self.assertIn("parts", local_gpu.RUNNERS)

    def test_articulated_params_settings_and_skip(self):
        jobs = self.jobs("articulated", {"params": {"length": 6.0, "front": "-x", "image_format": "compact"},
                                         "stages": {"parts": {"settings": {"min_share": 0.01, "snap": False}}},
                                         "skip": ["paint"]})
        v = "f/p/v%d/" % seeds_for("f/p", 1)[0]
        self.assertNotIn(v + "paint", jobs)
        self.assertEqual(jobs[v + "parts"]["inputs"], {"mesh": {"ref": v + "mesh", "role": "painted_mesh"},
                                                       "bake_source": {"ref": v + "mesh", "role": "raw_mesh"}})
        self.assertEqual(jobs[v + "parts"]["args"]["front"], "-x")
        self.assertEqual((jobs[v + "parts"]["args"]["min_share"], jobs[v + "parts"]["args"]["snap"]), (0.01, False))
        self.assertIn("322", jobs[v + "mesh"]["roles"])  # TRELLIS.2's own texture is kept

    def test_creature_chain(self):
        jobs = self.jobs("creature", {"inputs": {"clips": "clips.json"}},
                         family={"reserve": 1})
        seeds = seeds_for("f/p", 3)
        stages_ = ("reference", "pose", "cutout", "mesh", "paint", "rig", "export")
        expected = ["f/p/v%d/%s" % (s, st) for s in seeds for st in stages_]
        self.assertEqual(list(jobs), expected + ["f/p/review", "f/p/final", "f/p/review_reserve", "f/p/final_reserve"])
        v = "f/p/v%d/" % seeds[0]
        self.assertEqual((jobs[v + "reference"]["values"]["$WIDTH$"], jobs[v + "reference"]["values"]["$HEIGHT$"]),
                         (1024, 1536))
        self.assertEqual(jobs[v + "rig"]["args"], {"method": "skintokens", "seed": 9001, "beams": 10,
                                                   "postprocess": False})
        export = jobs[v + "export"]
        self.assertEqual((export["tool"], export["args"]["body"], export["args"]["motions"]),
                         ("creature_export", sk.SERPENT_PLAN, MOTIONS))
        self.assertEqual(export["inputs"]["clips"], {"file": os.path.join(self.tmp, "clips.json")})
        self.assertEqual(jobs["f/p/review"]["accept"],
                         {"mesh_flags": 0, "missing_clips": 0, "foot_slide_cm": None, "reach_shortfall_cm": None,
                          "stretch_p95": None, "below_ground_cm": None})
        self.assertTrue(jobs["f/p/v%d/export" % seeds[2]]["hold"])

    def test_family_motions_and_library_files(self):
        lib = os.path.join(self.tmp, "libs", "other.json")
        os.makedirs(os.path.dirname(lib))
        shutil.copy(os.path.join(self.tmp, "clips.json"), lib)
        order = order_of("beast" and "creature", {"body": sk.BEAST_PLAN})
        order["families"][0]["motions"] = [{"name": "idle_none", "source": "library",
                                            "library": {"file": "libs/other.json", "clip": "idle"}}]
        del order["families"][0]["assets"][0]["motions"]
        self.assertEqual(check_order(self.load(order), self.config), [])
        jobs = {j["id"]: j for j in expand(self.load(order))}
        export = [j for j in jobs.values() if j.get("tool") == "creature_export"][0]
        self.assertEqual(export["inputs"]["libraries"], [{"file": lib}])
        self.assertEqual(export["args"]["motions"][0]["library"]["file"], lib)

    def test_a_changed_motion_redoes_only_the_exports(self):
        a = self.specs(order_of("creature"))
        motions = copy.deepcopy(MOTIONS)
        motions[1]["settings"] = {"waves": 2.0}
        b = self.specs(order_of("creature", {"motions": motions}))
        changed = sorted(j for j in a if a[j] != b[j])
        seeds = seeds_for("f/p", 2)
        self.assertEqual(changed, sorted(["f/p/v%d/export" % s for s in seeds] + ["f/p/review", "f/p/final"]))
        c = self.specs(order_of("articulated"))
        parts = copy.deepcopy(PARTS)
        parts[1]["joint"]["range"] = [-90, 90]
        d = self.specs(order_of("articulated", {"parts": parts}))
        self.assertEqual(sorted(j for j in c if c[j] != d[j]),
                         sorted(["f/p/v%d/parts" % s for s in seeds] + ["f/p/review", "f/p/final"]))


class OrderCheck(Base):
    def test_valid(self):
        self.assertEqual(self.problems("articulated"), [])
        self.assertEqual(self.problems("creature"), [])

    def test_problems(self):
        self.assertProblem(self.problems("articulated", {"parts": [{"id": "a"}, {"id": "b"}]}),
                           "exactly one part has no parent")
        piece = order_of("articulated")["families"][0]["assets"][0]
        del piece["parts"]
        self.assertProblem(check_order(self.load({"version": 1, "style": STYLE, "families": [
            {"id": "f", "recipe": "articulated", "assets": [piece]}]}), self.config), "parts is required")
        self.assertProblem(self.problems("articulated", {"body": sk.SERPENT_PLAN}), "does not use body")
        self.assertProblem(self.problems("creature", {"parts": PARTS}), "does not use parts")
        self.assertProblem(self.problems("creature", {"body": {"chains": []}}), "at least one chain")
        self.assertProblem(self.problems("creature", {"motions": [{"name": "x", "source": "text", "prompt": "a"}]}),
                           "source text is not available yet")
        self.assertProblem(self.problems("creature", {"motions": [{"name": "x", "source": "library"}]}),
                           "needs inputs.clips")
        self.assertProblem(self.problems("creature", {"inputs": {"clips": "clips.json"}, "motions": [
            {"name": "x", "source": "library", "library": {"clip": "run"}}]}), "has no clip run")
        self.assertProblem(self.problems("creature", {"params": {"height": 2, "length": 3}}), "not both")
        self.assertProblem(self.problems("creature", {"params": {"front": "+y"}}), "front must be one of")
        self.assertProblem(self.problems("creature", {"accept": {"penetration_cm": 2}}), "unknown accept key")
        self.assertEqual(self.problems("creature", {"accept": {"below_ground_cm": 4, "foot_slide_cm": 3}}), [])
        self.assertProblem(self.problems("articulated", {"accept": {"stretch_p95": 1}}), "unknown accept key")
        self.assertProblem(self.problems("articulated", {"stages": {"parts": {"settings": {"min_share": 2}}}}),
                           "must be a number from 0.0 to 0.5")
        self.assertProblem(self.problems("creature", {"skip": ["rig"]}), "cannot be skipped")
        order = order_of("creature")
        del order["style"]["view_creature"]
        self.assertProblem(check_order(self.load(order), self.config), "style.view_creature is required")
        config = dict(self.config, godot=None)
        self.assertProblem(check_order(self.load(order_of("articulated")), config), "godot")

    def test_humanoid_keeps_its_accept_and_refuses_new_keys(self):
        self.assertEqual(acceptance.limits(), {k: v[1] for k, v in acceptance.ACCEPT.items()})
        order = {"version": 1, "style": {"view_humanoid": "A figure."}, "families": [
            {"id": "f", "recipe": "humanoid", "assets": [{"id": "p", "brief": "x", "motions": MOTIONS,
                                                          "accept": {"below_ground_cm": 3}}]}]}
        problems = check_order(self.load(order), self.config)
        # The humanoid reads motions too (since the motions list): a procedural kind is the creature's.
        self.assertProblem(problems, "procedural motion kinds are the creature's")
        self.assertProblem(problems, "unknown accept key 'below_ground_cm'")

    def test_sample_orders(self):
        for name in ("order_articulated.json", "order_creature.json"):
            order = load_order(os.path.join(FIXTURES, name))
            self.assertEqual(check_order(order, self.config), [], name)
            env = check_environment(order, self.config, object_info())
            self.assertEqual((env.status, env.problems), (VERIFIED, []), name)
            text = json.dumps(order["style"])
            self.assertIsNone(re.search(r"\b(no|not|without|never|avoid)\b", text, re.I), text)
        jobs = expand(load_order(os.path.join(FIXTURES, "order_creature.json")))
        figures = [j for j in jobs if j["family"] == "blobs"]
        self.assertEqual(sorted(set(j["id"].rsplit("/", 1)[1] for j in figures if "concept" not in j["id"])),
                         ["mesh", "normals", "pick", "sheet", "view_180", "view_270", "view_90"])
        mesh = [j for j in figures if j["id"].endswith("/mesh")][0]
        self.assertEqual((mesh["values"]["$FACES$"], mesh["mesh_role"]), (15000, "painted_mesh"))


class CommandLineCheck(Base):
    def test_check_and_estimate_against_the_fake_server(self):
        import subprocess
        fake = FakeComfy()
        try:
            fake.object_info = object_info()
            config = os.path.join(self.tmp, "forge.local.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({k: self.config[k] for k in ("blender_python", "godot", "skintokens_python",
                                                       "skintokens_dir")}, f)
            for name, jobs in (("order_articulated.json", 51), ("order_creature.json", 118)):
                base = [sys.executable, "-m", "forge", "--order", os.path.join(FIXTURES, name), "--out",
                        os.path.join(self.tmp, "out_" + name), "--config", config, "--server", fake.url]
                res = subprocess.run(base + ["--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn("order: valid", res.stdout)
                self.assertIn("environment: verified", res.stdout)
                res = subprocess.run(base + ["--estimate"], cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn("total %d jobs" % jobs, res.stdout)
            self.assertFalse(any(r[1] == "POST" for r in fake.requests))
        finally:
            fake.close()


# --- a night with the fake server and stand-ins for the local tools

class FakeTools:
    def __init__(self, fail=(), flagged=()):
        self.fail, self.flagged = set(fail), set(flagged)
        self.calls = []

    def variant(self, out):
        return os.path.basename(out).split("_")[0]

    def rig(self, mesh, out, config, **kw):
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "rigged.glb")
        with open(path, "wb") as f:
            f.write(b"glTF rig")
        return {"rig": path}

    def _files(self, out, v, names, model):
        files = {}
        for role, name, data in names:
            files[role] = os.path.join(out, name)
            if role in ("bone_map", "frame") and os.path.exists(files[role]):
                with open(files[role], encoding="utf-8") as f:
                    if json.load(f).get("manual"):
                        continue
            with open(files[role], "w", encoding="utf-8") as f:
                json.dump(dict(data, variant=v), f)
        return files

    def parts(self, mesh, out, config, parts, **kw):
        v = self.variant(out)
        self.calls.append(("parts", v, parts, kw))
        os.makedirs(out, exist_ok=True)
        if v in self.fail:
            raise local_gpu.ToolError("parts failed (exit 1), see x")
        return self._files(out, v, (("glb", "p.glb", {}), ("model", "model.json", {"format": "forge-articulated"}),
                                    ("mesh_report", "mesh_report.json",
                                     {"flags": ["part turret is empty: its region holds no geometry"]
                                      if v in self.flagged else []}),
                                    ("frame", "frame.json", {"manual": False})), None)

    def export(self, rig, out, config, body, **kw):
        v = self.variant(out)
        self.calls.append(("export", v, body, kw))
        os.makedirs(out, exist_ok=True)
        if v in self.fail:
            with open(os.path.join(out, "bone_map.json"), "w") as f:
                json.dump({"valid": False, "problems": ["role tail is missing"]}, f)
            raise local_gpu.ToolError("export stopped: invalid bone map (see x)", retry=False)
        return self._files(out, v, (("glb", "p.glb", {}), ("model", "model.json", {"format": "forge-creature"}),
                                    ("mesh_report", "mesh_report.json", {"flags": []}),
                                    ("motion_report", "motion_report.json",
                                     {"flags": [{"clip": "walk_none", "measure": "below_ground_cm", "value": 5.0,
                                                 "threshold": 3.0}] if v in self.flagged else []}),
                                    ("bone_map", "bone_map.json", {"valid": True}),
                                    ("clips", "clips.json", {"format": "forge-creature-clips"})), None)

    def sheet(self, glbs, out, config, camera=None, labels=None, verdicts=None, banner=None):
        self.calls.append(("sheet", list(glbs), list(labels), dict(camera), verdicts, banner))
        os.makedirs(out, exist_ok=True)
        files = {"sheet": os.path.join(out, "sheet.png"), "layout": os.path.join(out, "sheet.png.json")}
        Image.new("RGBA", (8, 8)).save(files["sheet"])
        with open(files["layout"], "w") as f:
            json.dump({"variants": labels}, f)
        return files


class Night(Base):
    def setUp(self):
        Base.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        self.fake.close()
        Base.tearDown(self)

    def run_night(self, tools, order):
        self.load(order)
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.config = self.config
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_parts", tools.parts), \
                patch.object(local_gpu, "run_creature_export", tools.export), \
                patch.object(local_gpu, "run_sheet", tools.sheet), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def pick(self):
        with open(os.path.join(self.out, "f", "p", "pick_final.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_articulated_night(self):
        v = ["v%d" % s for s in seeds_for("f/p", 3)]
        tools = FakeTools(fail=[v[0]], flagged=[v[1]])
        m = self.run_night(tools, order_of("articulated", family={"variants": 3}))
        self.assertEqual(m["f/p/%s/parts" % v[0]]["status"], "failed")
        self.assertEqual(m["f/p/%s/parts" % v[0]]["tries"], 2)  # an ordinary failure is tried once more
        self.assertEqual(m["f/p/%s/parts" % v[2]]["status"], "done")
        call = [c for c in tools.calls if c[:2] == ("parts", v[2])][0]
        self.assertEqual(call[2], PARTS)
        self.assertTrue(call[3]["metal_roughness"].endswith("_metal_roughness.png"))
        [sheet] = [c for c in tools.calls if c[0] == "sheet"]
        self.assertTrue(sheet[1][0].startswith("!parts failed"))
        self.assertEqual([x["class"] for x in sheet[4]], ["rejected", "rejected", "acceptable"])
        pick = self.pick()
        self.assertEqual((pick["outcome"], pick["variant"]), ("picked", v[2]))
        self.assertEqual(pick["candidates"][1]["responsible"], ["unknown"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "f", "p", "final"))),
                         ["frame.json", "mesh_report.json", "model.json", "p.glb"])
        # A frame corrected by hand redoes only that variant's parts, the sheet and the decision.
        frame = os.path.join(self.out, "f", "p", "%s_parts" % v[2], "frame.json")
        with open(frame, "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "front": "-x"}, fh)
        self.fake.reset_log()
        tools.calls.clear()
        self.run_night(tools, order_of("articulated", family={"variants": 3}))
        self.assertEqual(self.fake.submitted, [])
        self.assertEqual(sorted((c[0], c[1]) for c in tools.calls if c[0] != "sheet"), [("parts", v[2])])

    def test_creature_night(self):
        v = ["v%d" % s for s in seeds_for("f/p", 3)]
        tools = FakeTools(fail=[v[0]], flagged=[v[1]])
        m = self.run_night(tools, order_of("creature", family={"variants": 3},
                                           piece={"accept": {"below_ground_cm": 3.0}}))
        failed = m["f/p/%s/export" % v[0]]
        self.assertEqual((failed["status"], failed["tries"]), ("failed", 1))  # an invalid bone map is not retried
        call = [c for c in tools.calls if c[:2] == ("export", v[2])][0]
        self.assertEqual((call[2], call[3]["motions"]), (sk.SERPENT_PLAN, MOTIONS))
        pick = self.pick()
        self.assertEqual(pick["variant"], v[2])
        self.assertEqual(pick["candidates"][0]["responsible"], ["rig"])
        self.assertEqual(pick["candidates"][1]["class"], "rejected")
        self.assertIn("below_ground_cm", pick["candidates"][1]["reasons"][0])
        self.assertEqual(pick["motion"], "gated on below_ground_cm")
        # A bone map corrected by hand redoes only that export.
        bm = os.path.join(self.out, "f", "p", "%s_export" % v[2], "bone_map.json")
        with open(bm, "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "source": "manual", "roles": {}}, fh)
        tools.calls.clear()
        self.run_night(tools, order_of("creature", family={"variants": 3},
                                       piece={"accept": {"below_ground_cm": 3.0}}))
        self.assertEqual(sorted((c[0], c[1]) for c in tools.calls if c[0] != "sheet"), [("export", v[2])])


if __name__ == "__main__":
    unittest.main()
