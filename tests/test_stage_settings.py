"""Per-stage settings: the string and object forms of a `stages` entry, where each setting
lands (the TRELLIS.2 samplers each get their own), what the order check refuses, and that a
setting changes only its own job's hash and what depends on it."""

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
from fake_comfy import FakeComfy, picture  # noqa: E402
from forge import stages  # noqa: E402
from forge.comfy_jobs import ComfyJobs  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_core import Factory, load_workflow  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.recipes import expand, refs_of  # noqa: E402

STYLE = {"global": "Plain test style.", "view_object": "Single object, plain light background."}


def order_of(piece, family=None):
    fam = {"id": "things", "recipe": "object_3d", "variants": 2,
           "assets": [dict({"id": "crate", "brief": "A wooden crate."}, **piece)]}
    fam.update(family or {})
    return {"version": 1, "style": dict(STYLE), "families": [fam]}


def build(job):
    """The prompt ComfyJobs builds for a job (no server: plain values only)."""
    f = ComfyJobs()
    f.workflow = load_workflow
    return f.build(dict({"family": "things", "asset": "statue"}, **job))[0]


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_settings_")
        self.config = load_config(None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        path = os.path.join(self.tmp, "order.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(path)

    def problems(self, piece, family=None):
        return check_order(self.load(order_of(piece, family)), self.config)

    def jobs(self, piece, family=None):
        return {j["id"]: j for j in expand(self.load(order_of(piece, family)))}

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    # --- forms
    def test_string_and_object_forms(self):
        plain = self.jobs({})
        self.assertEqual(self.problems({"stages": {"mesh": "hunyuan21"}}), [])
        self.assertEqual(self.jobs({"stages": {"mesh": "hunyuan21"}}), plain)
        # The object form without settings changes nothing either (no hash change).
        self.assertEqual(self.jobs({"stages": {"mesh": {"option": "hunyuan21"}}}), plain)
        piece = {"stages": {"mesh": {"option": "hunyuan21", "settings": {"shape_steps": 40, "paint_guidance": 2.5}},
                            "concept": {"settings": {"steps": 6}}}}
        self.assertEqual(self.problems(piece), [])
        jobs = self.jobs(piece)
        mesh = jobs["things/crate/mesh"]
        self.assertEqual(mesh["set"]["3.steps"], 40)
        self.assertEqual(mesh["set"]["9.guidance_scale"], 2.5)
        self.assertEqual(mesh["set"]["9.texture_size"], 2048)  # the recipe's own set is kept
        concepts = [j for j in jobs.values() if j.get("workflow") == "klein_t2i.json"]
        self.assertEqual(len(concepts), 2)
        for c in concepts:
            self.assertEqual(c["set"], {"10.steps": 6})
        built = build(dict(concepts[0], values=dict(concepts[0]["values"])))
        self.assertEqual(built["10"]["inputs"]["steps"], 6)
        # A family default stage entry reaches its pieces; the piece's entry wins whole.
        jobs = self.jobs({}, {"stages": {"mesh": {"settings": {"shape_steps": 20}}}})
        self.assertEqual(jobs["things/crate/mesh"]["set"]["3.steps"], 20)
        jobs = self.jobs({"stages": {"mesh": {"settings": {"paint_steps": 12}}}},
                         {"stages": {"mesh": {"settings": {"shape_steps": 20}}}})
        self.assertNotIn("3.steps", jobs["things/crate/mesh"]["set"])
        self.assertEqual(jobs["things/crate/mesh"]["set"]["9.steps"], 12)

    def test_trellis2_per_sampler_settings_reach_their_nodes(self):
        settings = {"structure_steps": 13, "shape_steps": 21, "upsample_steps": 17, "texture_steps": 9,
                    "structure_seed": 1, "shape_seed": 2, "upsample_seed": 3, "texture_seed": 4,
                    "shape_guidance": 6.0, "upsample_scheduler": "normal", "sampler": "dpmpp_2m",
                    "resolution": 1536}
        opt = stages.OPTIONS["trellis2"]
        self.assertEqual(stages.settings_problems(opt, settings), [])
        sets, args = stages.setting_targets(opt, settings)
        self.assertEqual(args, {})
        job = {"id": "things/statue/mesh", "workflow": "trellis2_mesh.json", "stage": "mesh",
               "values": {"$IMAGE$": "statue.png", "$SEED$": 77, "$TRIS$": 25000}}
        fake = {"mesh": {"default": "trellis2", "skippable": False, "options": {"trellis2": opt}}}
        with patch.dict(stages.CATALOGUE, {"object_3d": fake}):
            piece = {"stages": {"mesh": {"option": "trellis2", "settings": settings}}}
            [job] = stages.apply_settings("object_3d", piece, [job])
        self.assertEqual(job["set"], sets)
        wf = build(job)
        for node, steps, seed in (("3", 13, 1), ("18", 21, 2), ("23", 17, 3), ("12", 9, 4)):
            self.assertEqual(wf[node]["class_type"], "KSampler")
            self.assertEqual((wf[node]["inputs"]["steps"], wf[node]["inputs"]["seed"]), (steps, seed), node)
            self.assertEqual(wf[node]["inputs"]["sampler_name"], "dpmpp_2m")
        self.assertEqual(wf["18"]["inputs"]["cfg"], 6.0)
        self.assertEqual(wf["3"]["inputs"]["cfg"], 7.5)  # untouched
        self.assertEqual(wf["23"]["inputs"]["scheduler"], "normal")
        self.assertEqual(wf["94"]["inputs"]["target_resolution"], 1536)
        # The structure, shape and upsample samplers feed the stages they belong to.
        self.assertEqual(wf["119"]["inputs"]["samples"], ["3", 0])
        self.assertEqual(wf["94"]["inputs"]["shape_latent"], ["18", 0])
        self.assertEqual(wf["98"]["inputs"]["shape_latent"], ["23", 0])
        self.assertEqual(wf["93"]["inputs"]["samples"], ["12", 0])
        # Without settings the workflow's own values stay (and the variant's seed).
        wf = build(dict(job, set={}))
        self.assertEqual([wf[n]["inputs"]["steps"] for n in ("3", "18", "23", "12")], [12, 20, 12, 12])
        self.assertEqual(set(wf[n]["inputs"]["seed"] for n in ("3", "18", "23", "12")), {77})
        # A texture setting on a job that drops the texture bake is refused.
        with self.assertRaisesRegex(ValueError, "lands on node 12"):
            build(dict(job, drop=list(stages.TRELLIS2_TEXTURE_NODES)))

    def test_every_declared_setting_lands_on_its_workflow(self):
        for name, opt in stages.OPTIONS.items():
            self.assertIn(opt["kind"], ("comfy", "local", "local_gpu"), name)
            wf = load_workflow(opt["workflow"])[0] if opt.get("workflow") else None
            for sname, decl in opt.get("settings", {}).items():
                self.assertIn(decl["type"], ("int", "number", "seed", "bool", "combo"), (name, sname))
                self.assertTrue(decl.get("meaning"), (name, sname))
                self.assertTrue(decl.get("to") or decl.get("arg"), (name, sname))
                for key in decl.get("to", ()):
                    nid, inp = key.split(".", 1)
                    self.assertIn(nid, wf, (name, sname, key))
                    self.assertIn(inp, wf[nid]["inputs"], (name, sname, key))
                    v = wf[nid]["inputs"][inp]
                    self.assertFalse(isinstance(v, list), (name, sname, key, "a link"))
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and decl["type"] in ("int", "number"):
                        self.assertIsNone(stages.setting_problem(decl, v), (name, sname, "workflow value out of range"))
        for recipe, cat in stages.CATALOGUE.items():
            for stage, st in cat.items():
                self.assertIn(st["default"], st["options"], (recipe, stage))
        # Inputs marked as overridden exist in their workflows.
        for wfname, keys in stages.OVERRIDDEN.items():
            wf = load_workflow(wfname)[0]
            for key in keys:
                nid, inp = key.split(".", 1)
                self.assertIn(inp, wf[nid]["inputs"], (wfname, key))

    # --- refused
    def test_unknown_setting_and_out_of_range_values(self):
        def p(settings, stage="mesh"):
            return self.problems({"stages": {stage: {"option": "hunyuan21" if stage == "mesh" else "klein",
                                                     "settings": settings}}})
        self.assertProblem(p({"colour": 3}), "setting mesh.colour of option hunyuan21: unknown setting")
        self.assertProblem(p({"shape_steps": 500}), "setting mesh.shape_steps of option hunyuan21: must be a whole number from 1 to 100, not 500")
        self.assertProblem(p({"shape_steps": 0}), "must be a whole number from 1 to 100")
        self.assertProblem(p({"shape_steps": 20.5}), "must be a whole number")
        self.assertProblem(p({"shape_steps": True}), "must be a whole number")
        self.assertProblem(p({"octree_resolution": 260}), "a multiple of 8")
        self.assertProblem(p({"paint_guidance": 11}), "must be a number from 1.0 to 10.0")
        self.assertProblem(p({"shape_seed": -1}), "must be a whole number from 0 to")
        self.assertProblem(p({"shape_seed": 2 ** 64}), "must be a whole number from 0 to")
        self.assertProblem(p({"sampler": ""}, "concept"), "setting concept.sampler of option klein: must be a name")
        self.assertProblem(self.problems({"stages": {"mesh": {"option": "hunyuan21", "settings": [1]}}}),
                           "settings must be an object")
        self.assertProblem(self.problems({"stages": {"mesh": {"option": "hunyuan21", "setting": {}}}}),
                           "unknown keys ['setting']")
        self.assertProblem(self.problems({"stages": {"mesh": {"option": "nope"}}}), "has no option 'nope'")
        self.assertEqual(p({"shape_steps": 100, "octree_resolution": 256, "shape_seed": 0}), [])
        # Concept settings of a piece that goes straight from its reference to the mesh.
        with open(os.path.join(self.tmp, "ref.png"), "wb") as f:
            f.write(b"x")
        self.assertProblem(self.problems({"inputs": {"reference": os.path.join(self.tmp, "ref.png")},
                                          "stages": {"concept": {"settings": {"steps": 3}}}}),
                           "things/crate: the settings of stage concept land on no job of this piece")
        # Settings of a skipped stage would do nothing.
        fake = {"paint": {"default": "a", "skippable": True, "options": {"a": {
            "kind": "comfy", "settings": {"steps": {"type": "int", "min": 1, "max": 9, "to": ["1.steps"],
                                                    "meaning": "x"}}}}}}
        with patch.dict(stages.CATALOGUE, {"concept": fake}):
            self.assertProblem(check_order(self.load({"version": 1, "style": STYLE, "families": [
                {"id": "f", "recipe": "concept", "assets": [
                    {"id": "p", "brief": "B.", "skip": ["paint"], "stages": {"paint": {"settings": {"steps": 2}}}}]}]}),
                self.config), "stage paint is skipped")

    # --- the material recipe's texture stage
    def test_material_texture_option(self):
        style = dict(STYLE, material_top="A seamless top-down texture of", material_look="Flat light.",
                     material_negative="text, perspective")

        def order(piece):
            return {"version": 1, "style": style, "families": [{"id": "ground", "recipe": "material", "variants": 2,
                    "assets": [dict({"id": "slabs", "brief": "worn grey flagstones"}, **piece)]}]}
        plain = {j["id"]: j for j in expand(self.load(order({})))}
        raws = [j for j in plain.values() if "/raw_s" in j["id"]]
        self.assertEqual(len(raws), 2)
        for j in raws:
            self.assertEqual((j["workflow"], j["group"], j["roles"]), ("qwen_image_21_turbo_tile.json", "qwen_t2i", {"12": "rgb"}))
            self.assertNotIn("stage", j)
        # The default named explicitly changes nothing (no hash change).
        self.assertEqual({j["id"]: j for j in expand(self.load(order({"stages": {"texture": "qwen21_turbo"}})))}, plain)
        piece = {"stages": {"texture": {"option": "klein4b", "settings": {"steps": 6}}}}
        self.assertEqual(check_order(self.load(order(piece)), self.config), [])
        klein = {j["id"]: j for j in expand(self.load(order(piece)))}
        self.assertEqual(sorted(klein), sorted(plain))
        for jid, j in klein.items():
            if "/raw_s" not in jid:
                self.assertEqual(j, plain[jid])  # the pick, 2K repaint and seam stay as they were
                continue
            self.assertEqual((j["workflow"], j["group"], j["roles"]), ("klein4b_t2i.json", "klein4b", {"13": "rgb"}))
            self.assertEqual(j["drop"], list(stages.KLEIN_RGBA_NODES))
            self.assertNotIn("$NEGATIVE$", j["values"])
            self.assertEqual(j["values"]["$PROMPT$"], plain[jid]["values"]["$PROMPT$"])
            self.assertEqual(j["set"], {"10.steps": 6})
            wf = build(dict(j, family="ground", asset="slabs"))
            self.assertEqual(wf["10"]["inputs"]["steps"], 6)
            self.assertNotIn("18", wf)
        self.assertProblem(check_order(self.load(order({"stages": {"texture": "nope"}})), self.config),
                           "has no option 'nope'")


class SettingsHashTest(unittest.TestCase):
    """A setting enters the job specification: changing it redoes that job and what depends
    on it, nothing else."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_settings_hash_")
        os.makedirs(os.path.join(self.tmp, "refs"))
        picture(5, "RGBA").save(os.path.join(self.tmp, "refs", "stone.png"))
        self.order = os.path.join(self.tmp, "order.json")
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, crate_stages, stone_stages=None):
        order = {"version": 1, "style": dict(STYLE), "families": [
            {"id": "things", "recipe": "object_3d", "variants": 2, "assets": [
                {"id": "crate", "brief": "A wooden crate.", "stages": crate_stages},
                {"id": "stone", "brief": "A grey stone.", "inputs": {"reference": "refs/stone.png"},
                 "stages": stone_stages or {}}]}]}
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump(order, f)

    def specs(self):
        a = SimpleNamespace(order=self.order, order_base="", out=self.out, only="", retry_failed=False)
        f = Factory(a, "http://127.0.0.1:1")
        f.load()
        return {j["id"]: f.spec(j) for j in f.jobs if not refs_of(j)}

    def test_changing_one_setting_changes_only_its_jobs(self):
        self.write({})
        before = self.specs()
        self.write({}, {"mesh": {"settings": {"shape_steps": 25}}})
        after = self.specs()
        self.assertEqual(sorted(j for j in before if before[j] != after[j]), ["things/stone/mesh"])
        self.write({"concept": {"settings": {"steps": 6}}}, {"mesh": {"settings": {"shape_steps": 25}}})
        again = self.specs()
        self.assertEqual(sorted(j for j in after if after[j] != again[j]),
                         sorted(j for j in again if j.startswith("things/crate/concept_s")))

    def test_rerun_redoes_the_job_and_its_dependants(self):
        fake = FakeComfy()
        try:
            env = dict(os.environ, COMFY_SERVER=fake.url, FORGE_PASS_SLEEP="0.02", FORGE_ERROR_SLEEP="0.1",
                       PYTHONDONTWRITEBYTECODE="1")
            cmd = [sys.executable, "-m", "forge", "--order", self.order, "--out", self.out]

            def run():
                fake.reset_log()
                res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, cwd=ROOT)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                return sorted(s[0] for s in fake.submitted)
            self.write({})
            self.assertEqual(len(run()), 4)  # two concepts and two meshes
            # The crate's mesh setting: only its mesh.
            self.write({"mesh": {"settings": {"paint_steps": 12}}})
            self.assertEqual(run(), ["things/crate/mesh"])
            with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            self.assertEqual(m["things/crate/mesh"]["provenance"]["settings"]["9.steps"], 12)
            # Its concept setting: the concepts, and through the pick, its mesh.
            self.write({"mesh": {"settings": {"paint_steps": 12}}, "concept": {"settings": {"steps": 6}}})
            redone = run()
            self.assertEqual(redone, sorted([j for j in redone if j.startswith("things/crate/concept_s")]
                                            + ["things/crate/mesh"]))
            self.assertEqual(len(redone), 3)
            self.assertEqual(run(), [])
        finally:
            fake.close()


class EffectiveParametersTest(unittest.TestCase):
    """After a ComfyUI job the factory reads the executed prompt back from /history: each
    input keeps what was requested and what the server ran (null with a reason when it
    cannot be known); the provenance export carries them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_effective_")
        os.makedirs(os.path.join(self.tmp, "refs"))
        picture(5, "RGBA").save(os.path.join(self.tmp, "refs", "stone.png"))
        self.order = os.path.join(self.tmp, "order.json")
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "style": dict(STYLE), "families": [
                {"id": "things", "recipe": "object_3d", "assets": [
                    {"id": "stone", "brief": "A grey stone.", "inputs": {"reference": "refs/stone.png"},
                     "stages": {"mesh": {"settings": {"shape_steps": 25}}}}]},
                {"id": "sketches", "recipe": "concept", "variants": 1, "assets": [
                    {"id": "lamp", "brief": "A lamp."}]}]}, f)
        self.out = os.path.join(self.tmp, "out")
        self.fake = FakeComfy()

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_factory(self, *args):
        env = dict(os.environ, COMFY_SERVER=self.fake.url, FORGE_PASS_SLEEP="0.02", FORGE_ERROR_SLEEP="0.1",
                   PYTHONDONTWRITEBYTECODE="1")
        cmd = [sys.executable, "-m", "forge", "--order", self.order, "--out", self.out] + list(args)
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, cwd=ROOT)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_requested_and_effective_are_recorded_and_exported(self):
        self.fake.history_changes = {"10.steps": 8}  # the server "ran" 8 steps where 4 were asked
        m = self.run_factory()
        concept = [j for j in m if j.startswith("sketches/lamp/concept_s")][0]
        params = m[concept]["provenance"]["parameters"]
        self.assertEqual(params["10"]["class"], "Flux2Scheduler")
        self.assertEqual(params["10"]["inputs"]["steps"], {"requested": 4, "effective": 8, "changed": True})
        self.assertEqual(params["8"]["inputs"]["cfg"], {"requested": 1.0, "effective": 1.0})
        self.assertNotIn("model", params["8"]["inputs"])  # links between nodes are not parameters
        seed = int(concept.rsplit("_s", 1)[1])
        self.assertEqual(params["7"]["inputs"]["noise_seed"], {"requested": seed, "effective": seed})
        mesh = m["things/stone/mesh"]["provenance"]["parameters"]
        self.assertEqual(mesh["3"]["inputs"]["steps"], {"requested": 25, "effective": 25})
        paint_seed = mesh["9"]["inputs"]["seed"]
        self.assertEqual(paint_seed["requested"], mesh["3"]["inputs"]["seed"]["requested"])
        self.assertIsNone(paint_seed["effective"])
        self.assertIn("fixed at seed 0", paint_seed["reason"])
        dest = os.path.join(self.tmp, "provenance.json")
        self.run_factory("--provenance", dest)
        with open(dest, encoding="utf-8") as f:
            records = json.load(f)["files"]
        by = {(r["job"], r["role"]): r for r in records}
        self.assertEqual(by[(concept, "rgba")]["parameters"]["10"]["inputs"]["steps"]["effective"], 8)
        self.assertIsNone(by[("things/stone/mesh", "albedo")]["parameters"]["9"]["inputs"]["seed"]["effective"])

    def test_history_without_the_prompt(self):
        self.fake.history_prompt = False
        m = self.run_factory()
        steps = m["things/stone/mesh"]["provenance"]["parameters"]["3"]["inputs"]["steps"]
        self.assertEqual(steps["requested"], 25)
        self.assertIsNone(steps["effective"])
        self.assertIn("no executed prompt", steps["reason"])

    def test_tool_arguments(self):
        from forge.provenance import tool_parameters
        got = tool_parameters({"seed": 1, "beams": 10, "postprocess": True}, {"seed": 1, "beams": 8},
                              {"postprocess": "the checkout ignores it"})
        self.assertEqual(got["seed"], {"requested": 1, "effective": 1})
        self.assertEqual(got["beams"], {"requested": 10, "effective": 8, "changed": True})
        self.assertIsNone(got["postprocess"]["effective"])
        self.assertIn("ignores it", got["postprocess"]["reason"])
        got = tool_parameters({"seed": 1}, {})
        self.assertIn("did not report", got["seed"]["reason"])


if __name__ == "__main__":
    unittest.main()
