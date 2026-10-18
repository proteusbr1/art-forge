"""The humanoid recipe: how a piece expands into jobs (variants, stage options, skips,
params and inputs, specification hashes), what the order check refuses, and, with the fake
server and stand-ins for the local tools, a night where one variant fails: the others go
on, the review sheet marks the failure and the provisional final pick prefers a variant
without flags, pending review."""

import contextlib
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
from fake_comfy import FakeComfy, picture  # noqa: E402
from forge import local_gpu, stages  # noqa: E402
from forge.comfy_jobs import ComfyJobs  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_core import Factory, load_workflow  # noqa: E402
from forge.factory_util import seeds_for, sha_file, sha_obj  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.recipes import expand, refs_of  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
SAMPLE = os.path.join(FIXTURES, "order_humanoid.json")
STYLE = {"global": "Plain test style.",
         "view_humanoid": "Full body figure in an A-pose, open empty hands held away from the body, "
                          "plain grey background, even light."}
STAGES = ("reference", "pose", "cutout", "mesh", "paint", "rig", "export")
ROLES = ("rgb", "mask", "rgba", "game_mesh", "raw_mesh", "painted_mesh", "rig", "glb", "model", "mesh_report",
         "motion_report")
# The local tools' configuration keys of the default stages, pointed at existing paths (the
# order check wants them present; nothing runs them here).
TOOLS = ("blender_python", "godot", "skintokens_python")


def piece_order(piece=None, family=None, style=None, base=None):
    fam = {"id": "crew", "recipe": "humanoid", "variants": 2,
           "assets": [dict({"id": "tech", "brief": "A technician in overalls."}, **(piece or {}))]}
    fam.update(family or {})
    return {"version": 1, "style": dict(STYLE if style is None else style), "families": [fam]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_humanoid_")
        self.config = load_config(None)
        tool_dir = os.path.join(self.tmp, "tools")
        os.makedirs(os.path.join(tool_dir, "skintokens"))
        with open(os.path.join(tool_dir, "skintokens", "demo.py"), "w") as f:
            f.write("")
        for k in TOOLS:
            self.config[k] = sys.executable
        self.config["skintokens_dir"] = os.path.join(tool_dir, "skintokens")
        unirig = os.path.join(tool_dir, "unirig")
        for rel in ("run.py", os.path.join("venv", "Scripts", "python.exe"), os.path.join("venv", "bin", "python")):
            os.makedirs(os.path.dirname(os.path.join(unirig, rel)), exist_ok=True)
            with open(os.path.join(unirig, rel), "w") as f:
                f.write("")
        self.config.update(unirig_dir=unirig, unirig_venv=os.path.join(unirig, "venv"), unirig_hf=unirig,
                           unirig_shim=unirig)
        # The order's files: a copy of the synthetic motion library and held items.
        self.inputs = os.path.join(self.tmp, "inputs")
        shutil.copytree(os.path.join(FIXTURES, "humanoid", "items"), os.path.join(self.inputs, "items"))
        shutil.copy(os.path.join(FIXTURES, "humanoid", "library.json"), os.path.join(self.inputs, "library.json"))
        with open(os.path.join(self.inputs, "bone_map.json"), "w", encoding="utf-8") as f:
            json.dump({"roles": {"hips": "bone_0"}}, f)
        self.order_path = os.path.join(self.tmp, "order.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        with open(self.order_path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(self.order_path)

    def problems(self, *args, **kw):
        return check_order(self.load(piece_order(*args, **kw)), self.config)

    def jobs(self, *args, **kw):
        return {j["id"]: j for j in expand(self.load(piece_order(*args, **kw)))}

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    def specs(self, order):
        """Every job's specification hash, with each job "finished" in dependency order and
        its outputs given hashes derived from its own specification (a changed job changes
        what depends on it, as in a real run)."""
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


def dependants(jobs, jid):
    """jid and every job that depends on it, directly or not."""
    out, grew = {jid}, True
    while grew:
        grew = False
        for j in jobs:
            if j["id"] not in out and any(r in out for r in refs_of(j)):
                out.add(j["id"])
                grew = True
    return out


class ExpansionTest(Base):
    def test_jobs_per_variant_and_stable_ids(self):
        for n in (1, 2, 4):
            jobs = list(self.jobs(family={"variants": n}))
            self.assertEqual(len(jobs), 7 * n + 2)
        jobs = self.jobs(family={"variants": 4})
        self.assertEqual(jobs, self.jobs(family={"variants": 4}))
        seeds = seeds_for("crew/tech", 4)
        expected = ["crew/tech/v%d/%s" % (s, stage) for s in seeds for stage in STAGES]
        self.assertEqual(list(jobs), expected + ["crew/tech/review", "crew/tech/final"])
        for jid, j in jobs.items():
            if "/v" in jid:
                self.assertEqual(j["stage"], jid.rsplit("/", 1)[1])
                self.assertRegex(j["variant"], r"^v\d+$")
        # The default chain.
        v = "crew/tech/v%d/" % seeds[0]
        self.assertEqual(jobs[v + "reference"]["workflow"], "qwen_image_21_base_t2i.json")
        self.assertEqual((jobs[v + "reference"]["values"]["$WIDTH$"], jobs[v + "reference"]["values"]["$HEIGHT$"]),
                         (1024, 1536))
        self.assertEqual(jobs[v + "pose"]["workflow"], "qwen_edit_2511.json")
        self.assertEqual(jobs[v + "pose"]["values"]["$IMAGE1$"], {"ref": v + "reference", "role": "rgb"})
        self.assertEqual(jobs[v + "cutout"]["kind"], "cutout")
        self.assertEqual(jobs[v + "cutout"]["inputs"], {"image": {"ref": v + "pose", "role": "rgb"},
                                                         "mask": {"ref": v + "pose", "role": "mask"}})
        self.assertEqual(jobs[v + "mesh"]["workflow"], "trellis2_mesh.json")
        self.assertEqual(jobs[v + "mesh"]["drop"], list(stages.TRELLIS2_TEXTURE_NODES))
        self.assertEqual(jobs[v + "paint"]["workflow"], "hunyuan21_paint_mesh.json")
        self.assertTrue(jobs[v + "paint"]["heavy"])
        self.assertEqual(jobs[v + "paint"]["values"]["$MESH$"],
                         {"ref": v + "mesh", "role": "game_mesh", "transport": "local_path"})
        self.assertEqual(jobs[v + "rig"]["inputs"], {"mesh": {"ref": v + "paint", "role": "painted_mesh"}})
        # The rig: the validated SkinTokens setting (seed 9001, 10 beams), not the variant's seed.
        self.assertEqual(jobs[v + "rig"]["args"], {"method": "skintokens", "seed": 9001, "beams": 10,
                                                   "postprocess": False})
        self.assertEqual(set(j["args"]["seed"] for j in jobs.values() if j.get("stage") == "rig"), {9001})
        for stage in ("rig", "export"):
            self.assertEqual((jobs[v + stage]["kind"], jobs[v + stage]["tool"]), ("local_gpu", stage))
            self.assertIn(stage, local_gpu.RUNNERS)

    def test_reference_prompt(self):
        jobs = self.jobs({"tier": "basic"}, family={"style": "Station crew."},
                         style=dict(STYLE, tier_basic="Worn materials."))
        ref = next(j for j in jobs.values() if j.get("stage") == "reference")
        self.assertEqual(ref["values"]["$PROMPT$"], "A technician in overalls. Worn materials. Station crew. "
                                                    "Plain test style. " + STYLE["view_humanoid"])
        pose = next(j for j in jobs.values() if j.get("stage") == "pose")
        self.assertEqual(pose["values"]["$PROMPT$"], STYLE["view_humanoid"])
        # The pose text may be given separately.
        jobs = self.jobs(style=dict(STYLE, pose_humanoid="Same figure, A-pose, open hands apart."))
        pose = next(j for j in jobs.values() if j.get("stage") == "pose")
        self.assertEqual(pose["values"]["$PROMPT$"], "Same figure, A-pose, open hands apart.")

    def test_stage_option_change_swaps_the_workflow_and_only_its_hashes(self):
        base = self.specs(piece_order())
        jobs = list(self.jobs().values())
        for stage, option, workflow in (("cutout", "qwen_edit_character", "qwen_edit_character.json"),
                                        ("mesh", "hunyuan21", "hunyuan21_shape_paint.json"),
                                        ("reference", "klein", "klein_t2i.json"),
                                        ("reference", "qwen21_turbo", "qwen_image_21_turbo_t2i.json"),
                                        ("rig", "unirig", None)):
            with self.subTest(stage=stage, option=option):
                order = piece_order({"stages": {stage: option}})
                self.assertEqual(check_order(self.load(order), self.config), [])
                changed = self.specs(order)
                new_jobs = {j["id"]: j for j in expand(self.load(order))}
                swapped = [jid for jid, j in new_jobs.items() if j.get("stage") == stage and "/v" in jid]
                self.assertEqual(len(swapped), 2)
                expect = set()
                for jid in swapped:
                    if workflow:
                        self.assertEqual(new_jobs[jid]["workflow"], workflow)
                    else:
                        self.assertEqual(new_jobs[jid]["args"]["method"], option)
                    expect |= dependants(jobs, jid)
                self.assertEqual(set(j for j in base if base[j] != changed[j]), expect)
        # Hunyuan3D shape only: its Paint nodes dropped, game and raw mesh roles.
        mesh = next(j for j in self.jobs({"stages": {"mesh": "hunyuan21"}}).values() if j.get("stage") == "mesh")
        self.assertEqual(mesh["drop"], list(stages.HUNYUAN21_PAINT_NODES))
        self.assertEqual(mesh["roles"], {"6": "game_mesh", "16": "raw_mesh"})
        self.assertTrue(mesh["heavy"])

    def test_skip_pose_and_paint(self):
        jobs = self.jobs({"skip": ["pose"]})
        self.assertFalse(any(j.get("stage") == "pose" for j in jobs.values()))
        cut = next(j for j in jobs.values() if j.get("stage") == "cutout")
        ref = cut["inputs"]["image"]["ref"]
        self.assertEqual(cut["inputs"], {"image": {"ref": ref, "role": "rgb"}, "mask": {"ref": ref, "role": "mask"}})
        self.assertEqual(jobs[ref]["stage"], "reference")
        # Klein's reference already has its alpha.
        jobs = self.jobs({"skip": ["pose"], "stages": {"reference": "klein"}})
        cut = next(j for j in jobs.values() if j.get("stage") == "cutout")
        self.assertEqual(list(cut["inputs"]), ["image"])
        self.assertEqual(cut["inputs"]["image"]["role"], "rgba")
        # Paint skipped: TRELLIS.2 keeps its own texture bake; the rig takes that mesh.
        for piece in ({"skip": ["paint"]}, {"stages": {"paint": "trellis2_texture"}}):
            with self.subTest(piece=piece):
                self.assertEqual(self.problems(piece, family={"params": {"texture": 1024}}), [])
                jobs = self.jobs(piece, family={"params": {"texture": 1024}})
                self.assertFalse(any(j.get("stage") == "paint" for j in jobs.values()))
                mesh = next(j for j in jobs.values() if j.get("stage") == "mesh")
                self.assertNotIn("drop", mesh)
                self.assertEqual(mesh["roles"]["322"], "painted_mesh")
                self.assertEqual(mesh["set"], {"147.texture_size": 1024, "196.resolution": 1024})
                rig = next(j for j in jobs.values() if j.get("stage") == "rig")
                self.assertEqual(rig["inputs"]["mesh"], {"ref": mesh["id"], "role": "painted_mesh"})
        # A statue: no rig, no export; the review and the final copy take the painted mesh.
        jobs = self.jobs({"skip": ["rig", "export"]})
        review = jobs["crew/tech/review"]
        self.assertEqual([v["role"] for v in review["variants"]], ["painted_mesh"] * 2)
        self.assertTrue(all(jobs[v["job"]]["stage"] == "paint" for v in review["variants"]))

    def built(self, job):
        """The prompt a comfy job sends (references resolved to a dummy picture)."""
        dummy = os.path.join(self.tmp, "dummy.png")
        picture(3, "RGBA").save(dummy)
        f = ComfyJobs()
        f.workflow = load_workflow
        f.resolve = lambda ref: (dummy, sha_file(dummy)) if "ref" in ref else (ref["file"], sha_file(ref["file"]))
        f.input_record = lambda ref: {}
        f.server = SimpleNamespace(url="http://127.0.0.1:1", upload=lambda path, sha: "factory_dummy.png")
        return f.build(job)[0]

    def test_params_reach_the_workflows(self):
        params = {"tris": 18000, "texture": 1024, "height": 1.75, "head": 1.1}
        jobs = self.jobs(family={"params": params, "variants": 1})
        by_stage = {j.get("stage"): j for j in jobs.values()}
        mesh = self.built(by_stage["mesh"])
        self.assertEqual(mesh["186"]["inputs"]["target_face_count"], 18000)
        self.assertNotIn("147", mesh)  # the bake is dropped: Hunyuan3D Paint paints the mesh
        paint = self.built(by_stage["paint"])
        self.assertEqual(paint["9"]["inputs"]["texture_size"], 1024)
        self.assertTrue(os.path.isabs(paint["2"]["inputs"]["glb_path"]))  # the game mesh by local path
        self.assertEqual(by_stage["export"]["args"], {"name": "tech", "tris": 18000, "normal_map": 1024,
                                                      "height": 1.75, "head": 1.1})
        # The defaults: 25k triangles, 2048 texture.
        by_stage = {j.get("stage"): j for j in self.jobs(family={"variants": 1}).values()}
        self.assertEqual(self.built(by_stage["mesh"])["186"]["inputs"]["target_face_count"], 25000)
        self.assertEqual(self.built(by_stage["paint"])["9"]["inputs"]["texture_size"], 2048)
        # TRELLIS.2's own bake and the Hunyuan3D shape budget.
        by_stage = {j.get("stage"): j for j in self.jobs({"skip": ["paint"]}, family={"params": params}).values()}
        mesh = self.built(by_stage["mesh"])
        self.assertEqual((mesh["147"]["inputs"]["texture_size"], mesh["196"]["inputs"]["resolution"]), (1024, 1024))
        by_stage = {j.get("stage"): j for j in self.jobs({"stages": {"mesh": "hunyuan21"}},
                                                          family={"params": params}).values()}
        mesh = self.built(by_stage["mesh"])
        self.assertEqual(mesh["5"]["inputs"]["max_facenum"], 18000)
        self.assertNotIn("9", mesh)

    def test_settings_land_on_their_stage(self):
        piece = {"stages": {"mesh": {"settings": {"shape_steps": 24, "structure_steps": 10}},
                            "pose": {"settings": {"steps": 8, "lora_strength": 0.8}},
                            "paint": {"settings": {"steps": 12, "view_size": 512}},
                            "rig": {"settings": {"beams": 4, "seed": 7}},
                            "export": {"settings": {"normal_map": 1024, "foot_slide_cm": 3.0}},
                            "review": {"settings": {"strip_frames": 6}}}}
        self.assertEqual(self.problems(piece), [])
        jobs = self.jobs(piece)
        by = {}
        for j in jobs.values():
            by.setdefault(j.get("stage"), j)
        self.assertEqual(by["mesh"]["set"], {"18.steps": 24, "3.steps": 10})
        self.assertEqual(by["pose"]["set"], {"14.steps": 8, "4.strength_model": 0.8})
        self.assertEqual(by["paint"]["set"], {"9.steps": 12, "9.view_size": 512})
        self.assertEqual((by["rig"]["args"]["beams"], by["rig"]["args"]["seed"]), (4, 7))
        self.assertEqual((by["export"]["args"]["normal_map"], by["export"]["args"]["foot_slide_cm"]), (1024, 3.0))
        self.assertEqual(jobs["crew/tech/review"]["args"]["strip_frames"], 6)
        # The texture sampler of TRELLIS.2 is dropped while Hunyuan3D Paint paints.
        self.assertProblem(self.problems({"stages": {"mesh": {"settings": {"texture_steps": 8}}}}),
                           "12.steps lands on node 12, which this job drops")
        # The setting of all four samplers lands on the three the job keeps.
        piece = {"stages": {"mesh": {"settings": {"sampler": "euler"}}}}
        self.assertEqual(self.problems(piece), [])
        mesh = next(j for j in self.jobs(piece).values() if j.get("stage") == "mesh")
        self.assertEqual(mesh["set"], {"3.sampler_name": "euler", "18.sampler_name": "euler",
                                       "23.sampler_name": "euler"})
        mesh = next(j for j in self.jobs(dict(piece, skip=["paint"])).values() if j.get("stage") == "mesh")
        self.assertEqual(mesh["set"]["12.sampler_name"], "euler")
        self.assertEqual(self.problems({"skip": ["paint"], "stages": {"mesh": {"settings": {"texture_steps": 8}}}}),
                         [])

    def test_raw_mesh_and_inputs_reach_the_export(self):
        inputs = {"clips": "inputs/library.json", "items": "inputs/items", "bone_map": "inputs/bone_map.json"}
        self.assertEqual(self.problems(family={"inputs": inputs}), [])
        jobs = self.jobs(family={"inputs": inputs, "variants": 1})
        export = next(j for j in jobs.values() if j.get("stage") == "export")
        mesh = next(j for j in jobs.values() if j.get("stage") == "mesh")
        self.assertEqual(export["inputs"]["bake_source"], {"ref": mesh["id"], "role": "raw_mesh"})
        self.assertEqual(export["inputs"]["clips"], {"file": os.path.join(self.inputs, "library.json")})
        self.assertEqual(export["inputs"]["items"], {"folder": os.path.join(self.inputs, "items")})
        self.assertEqual(export["inputs"]["bone_map"], {"file": os.path.join(self.inputs, "bone_map.json")})
        # The export runner hands them to the exporter.
        paths = {"rig": os.path.join(self.tmp, "rigged.glb"), "raw": os.path.join(self.tmp, "raw.glb")}
        factory = SimpleNamespace(config=self.config, resolve=lambda ref: (
            paths["rig"] if ref["role"] == "rig" else paths["raw"], "0" * 64))
        seen = {}

        def fake_export(rig, out, config, **kw):
            seen.update(kw, rig=rig)
            return {"glb": os.path.join(out, "tech.glb")}
        with patch.object(local_gpu, "run_export", fake_export):
            res = local_gpu.export_runner(factory, export, os.path.join(self.tmp, "export"))
        self.assertEqual(seen["rig"], paths["rig"])
        self.assertEqual(seen["bake_source"], paths["raw"])
        self.assertEqual(seen["clips"], os.path.join(self.inputs, "library.json"))
        self.assertEqual(seen["items"], os.path.join(self.inputs, "items"))
        self.assertEqual(seen["bone_map"], os.path.join(self.inputs, "bone_map.json"))
        self.assertEqual(seen["params"], {"tris": 25000, "normal_map": 2048})
        self.assertEqual(res["files"], {"glb": os.path.join(self.tmp, "export", "tech.glb")})

    def test_a_changed_file_in_the_items_folder_changes_the_export(self):
        order = piece_order(family={"inputs": {"clips": "inputs/library.json", "items": "inputs/items"}})
        before = self.specs(order)
        self.assertEqual(self.specs(order), before)
        with open(os.path.join(self.inputs, "items", "box.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        after = self.specs(order)
        changed = sorted(j for j in before if before[j] != after[j])
        exports = sorted(j for j in before if j.endswith("/export"))
        self.assertEqual(changed, sorted(exports + ["crew/tech/review", "crew/tech/final"]))
        # A new file in the folder too.
        shutil.copy(os.path.join(self.inputs, "items", "box.json"), os.path.join(self.inputs, "items", "crate.json"))
        again = self.specs(order)
        self.assertEqual(sorted(j for j in after if after[j] != again[j]), changed)

    def test_a_changed_tool_redoes_its_jobs(self):
        order = piece_order()
        before = self.specs(order)
        jobs = list(self.jobs().values())
        stage_of = {j["id"]: j.get("stage") for j in jobs}

        def changed_with(name, path):
            with patch.object(local_gpu, name, path):
                after = self.specs(order)
            return sorted(j for j in before if before[j] != after[j])
        tmp_tools = os.path.join(self.tmp, "tool_copies")
        os.makedirs(tmp_tools)
        for name, src in (("EXPORT_SCRIPT", local_gpu.EXPORT_SCRIPT), ("BONES_SCRIPT", local_gpu.BONES_SCRIPT),
                          ("SKINTOKENS_ENTRY", local_gpu.SKINTOKENS_ENTRY)):
            copy = os.path.join(tmp_tools, os.path.basename(src))
            shutil.copy(src, copy)
            with open(copy, "a", encoding="utf-8") as f:
                f.write("\n# a fix\n")
            with self.subTest(script=name):
                stage = "rig" if name == "SKINTOKENS_ENTRY" else "export"
                expect = set()
                for jid in (j for j in stage_of if stage_of[j] == stage):
                    expect |= dependants(jobs, jid)
                self.assertEqual(changed_with(name, copy), sorted(expect))
        review = os.path.join(tmp_tools, "review_godot")
        shutil.copytree(local_gpu.REVIEW_PROJECT, review, ignore=shutil.ignore_patterns(".godot"))
        with open(os.path.join(review, "main.gd"), "a", encoding="utf-8") as f:
            f.write("\n# a fix\n")
        self.assertEqual(changed_with("REVIEW_PROJECT", review), ["crew/tech/review"])
        # The rig checkout moved to another revision: the rigs and what depends on them.
        git = os.path.join(self.config["skintokens_dir"], ".git")
        os.makedirs(git)
        with open(os.path.join(git, "HEAD"), "w") as f:
            f.write("0123456789abcdef0123456789abcdef01234567\n")
        after = self.specs(order)
        rigs = [j for j in stage_of if stage_of[j] == "rig"]
        expect = set()
        for jid in rigs:
            expect |= dependants(jobs, jid)
        self.assertEqual(sorted(j for j in before if before[j] != after[j]), sorted(expect))

    def test_a_bone_map_per_variant(self):
        seeds = seeds_for("crew/tech", 2)
        other = os.path.join(self.inputs, "bone_map_b.json")
        shutil.copy(os.path.join(self.inputs, "bone_map.json"), other)
        inputs = {"bone_map": {"v%d" % seeds[0]: "inputs/bone_map.json", str(seeds[1]): "inputs/bone_map_b.json"}}
        self.assertEqual(self.problems(family={"inputs": inputs}), [])
        jobs = self.jobs(family={"inputs": inputs})
        exports = [j for j in jobs.values() if j.get("stage") == "export"]
        self.assertEqual([e["inputs"]["bone_map"]["file"] for e in exports],
                         [os.path.join(self.inputs, "bone_map.json"), other])
        # A variant the object does not name gets the proposal.
        jobs = self.jobs(family={"inputs": {"bone_map": {"v%d" % seeds[1]: "inputs/bone_map_b.json"}}})
        exports = [j for j in jobs.values() if j.get("stage") == "export"]
        self.assertNotIn("bone_map", exports[0]["inputs"])
        self.assertEqual(exports[1]["inputs"]["bone_map"], {"file": other})
        # Changing one variant's map redoes only that variant's export (and review, final).
        order = piece_order(family={"inputs": inputs})
        before = self.specs(order)
        with open(other, "w", encoding="utf-8") as f:
            json.dump({"roles": {"hips": "bone_2"}}, f)
        after = self.specs(order)
        self.assertEqual(sorted(j for j in before if before[j] != after[j]),
                         ["crew/tech/final", "crew/tech/review", "crew/tech/v%d/export" % seeds[1]])
        # The check names a key that is no variant and a missing file.
        self.assertProblem(self.problems(family={"inputs": {"bone_map": {"v1": "inputs/bone_map.json"}}}),
                           "inputs.bone_map names 'v1', which is no variant of this piece")
        self.assertProblem(self.problems(family={"inputs": {"bone_map": {"v%d" % seeds[0]: "inputs/none.json"}}}),
                           "input bone_map.v%d not found" % seeds[0])

    def test_known_ignored_arguments_reach_the_job(self):
        rig = dict(stages.HUMANOID_OPTIONS["rig"]["skintokens"], ignored={"postprocess": "the tool ignores it"})
        with patch.dict(stages.HUMANOID_OPTIONS["rig"], {"skintokens": rig}):
            jobs = self.jobs()
        rigs = [j for j in jobs.values() if j.get("stage") == "rig"]
        self.assertEqual([j["ignored"] for j in rigs], [{"postprocess": "the tool ignores it"}] * 2)
        self.assertFalse(any("ignored" in j for j in self.jobs().values()))

    def test_review_receives_every_variant(self):
        jobs = self.jobs(family={"variants": 4})
        review, final = jobs["crew/tech/review"], jobs["crew/tech/final"]
        exports = [jid for jid in jobs if jid.endswith("/export")]
        self.assertEqual(review["from"], exports)
        self.assertEqual([v["job"] for v in review["variants"]], exports)
        self.assertEqual([v["variant"] for v in review["variants"]], ["v%d" % s for s in seeds_for("crew/tech", 4)])
        self.assertEqual(review["args"], {"pitch": 60, "directions": 8, "figure_height": 96, "front": True})
        self.assertTrue(review["partial"] and final["partial"])
        self.assertEqual(final["from"], exports)
        self.assertEqual(review["variants"][0]["chain"], [jid for jid in jobs if jid.startswith(
            "crew/tech/%s/" % review["variants"][0]["variant"])])
        jobs = self.jobs(family={"review": {"pitch": 45, "directions": 4}})
        self.assertEqual(jobs["crew/tech/review"]["args"], {"pitch": 45, "directions": 4, "figure_height": 96,
                                                            "front": True})

    def test_order_check(self):
        self.assertEqual(self.problems(), [])
        self.assertProblem(self.problems(style={"global": "x"}), "style.view_humanoid is required")
        self.assertProblem(self.problems({"stages": {"mesh": "hunyuan21"}, "skip": ["paint"]}),
                           "skipping paint keeps TRELLIS.2's own texture, which needs mesh trellis2")
        self.assertProblem(self.problems({"stages": {"mesh": "hunyuan21", "paint": "trellis2_texture"}}),
                           "paint trellis2_texture keeps TRELLIS.2's own texture")
        self.assertProblem(self.problems({"skip": ["rig"]}), "export needs the rig")
        self.assertEqual(self.problems({"skip": ["rig", "export", "pose"]}), [])
        self.assertProblem(self.problems({"skip": ["cutout"]}), "stage 'cutout' cannot be skipped")
        self.assertProblem(self.problems({"stages": {"reference": "input_image"}}), "needs inputs.reference")
        self.assertProblem(self.problems({"inputs": {"reference": "inputs/library.json"}}),
                           "input 'reference' is not used")
        self.assertProblem(self.problems({"inputs": {"items": "inputs/library.json"}}), "must be a folder")
        self.assertProblem(self.problems({"inputs": {"clips": "inputs/items"}}), "must be a file")
        self.assertProblem(self.problems(family={"params": {"texture": 1000}}), "Hunyuan3D Paint takes 512 to 4096")
        self.assertProblem(self.problems({"review": {"pitch": "high"}}), "review pitch must be")
        self.assertProblem(self.problems({"stages": {"rig": {"settings": {"seed": 2 ** 40}}}}), "setting rig.seed")
        cfg = dict(self.config, skintokens_dir=None)
        problems = check_order(self.load(piece_order()), cfg)
        self.assertProblem(problems, "skintokens_dir is not set")
        # A reference picture with pose skipped and the BiRefNet cutout must be transparent.
        opaque, clear = os.path.join(self.tmp, "opaque.png"), os.path.join(self.tmp, "clear.png")
        Image.new("RGB", (8, 8), (90, 90, 90)).save(opaque)
        picture(4, "RGBA").save(clear)
        piece = {"stages": {"reference": "input_image"}, "skip": ["pose"]}
        self.assertProblem(self.problems(dict(piece, inputs={"reference": opaque})), "has no alpha channel")
        self.assertEqual(self.problems(dict(piece, inputs={"reference": clear})), [])
        self.assertEqual(self.problems({"stages": {"reference": "input_image"}, "inputs": {"reference": opaque}}), [])
        jobs = self.jobs(dict(piece, inputs={"reference": clear}))
        cut = next(j for j in jobs.values() if j.get("stage") == "cutout")
        self.assertEqual(cut["inputs"], {"image": {"file": clear}})
        self.assertFalse(any(j.get("stage") == "reference" for j in jobs.values()))

    def test_sample_order_is_valid(self):
        order = load_order(SAMPLE)
        self.assertEqual(check_order(order, self.config), [])
        jobs = expand(order)
        self.assertEqual(len(jobs), 2 * (7 * 4 + 2))
        guard_mesh = [j for j in jobs if j["id"].startswith("crew/guard/") and j.get("stage") == "mesh"]
        self.assertEqual(guard_mesh[0]["set"], {"3.steps": 12, "18.steps": 24, "23.steps": 12})
        text = json.dumps(order["style"])
        self.assertIsNone(re.search(r"\b(no|not|without|never|avoid)\b", text, re.I), text)


# --- a night with the fake server and stand-ins for the local tools

class FakeTools:
    """run_rig, run_export and run_review stand-ins: files as the real ones name them; the
    export of the variants in fail_export stops on an invalid bone map, those in flagged get a
    motion flag (flagged may also be {variant: [(measure, value, threshold), ...]}), those in
    mesh_flagged a mesh flag."""

    def __init__(self, fail_export=(), flagged=(), mesh_flagged=()):
        self.fail_export, self.mesh_flagged = set(fail_export), set(mesh_flagged)
        if not isinstance(flagged, dict):
            flagged = {v: [("foot_slide_cm", 4.0, 2.0)] for v in flagged}
        self.flagged = flagged
        self.calls = []

    def variant(self, out):
        return os.path.basename(out).split("_")[0]

    def rig(self, mesh, out, config, **kw):
        self.calls.append(("rig", self.variant(out), kw))
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "rigged.glb")
        with open(path, "wb") as f:
            f.write(b"glTF rig " + self.variant(out).encode())
        return {"rig": path}

    def export(self, rig, out, config, **kw):
        v = self.variant(out)
        self.calls.append(("export", v, kw))
        os.makedirs(out, exist_ok=True)
        if v in self.fail_export:
            with open(os.path.join(out, "bone_map.json"), "w") as f:
                json.dump({"valid": False, "problems": ["no hips"]}, f)
            raise local_gpu.ToolError("export stopped: invalid bone map (see %s)" % os.path.join(out, "bone_map.json"),
                                      retry=False)
        files = {}
        for role, name, data in (("glb", "tech.glb", {}), ("model", "model.json", {"status": "pending review"}),
                                 ("mesh_report", "mesh_report.json",
                                  {"flags": ["3 vertices have no weight"] if v in self.mesh_flagged else []}),
                                 ("motion_report", "motion_report.json",
                                  {"flags": [{"clip": "walk", "measure": m, "value": val, "threshold": t}
                                             for m, val, t in self.flagged.get(v, [])]}),
                                 ("bone_map", "bone_map.json", {"valid": True}), ("regions_mask", "regions_mask.png", {})):
            files[role] = os.path.join(out, name)
            if role == "bone_map" and os.path.exists(files[role]):
                with open(files[role], encoding="utf-8") as f:
                    if json.load(f).get("manual"):
                        continue  # as the exporter: a manual map stays as written
            with open(files[role], "w", encoding="utf-8") as f:
                json.dump(dict(data, variant=v), f)
        return files

    def review(self, glbs, out, config, camera=None, labels=None):
        self.calls.append(("review", list(glbs), list(labels), dict(camera)))
        os.makedirs(out, exist_ok=True)
        files = {"sheet": os.path.join(out, "sheet.png"), "layout": os.path.join(out, "sheet.png.json")}
        Image.new("RGBA", (8, 8)).save(files["sheet"])
        with open(files["layout"], "w") as f:
            json.dump({"variants": labels}, f)
        return files


class NightTest(Base):
    def setUp(self):
        Base.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")
        self.seeds = seeds_for("crew/tech", 3)
        self.v = ["v%d" % s for s in self.seeds]

    def tearDown(self):
        self.fake.close()
        Base.tearDown(self)

    def run_night(self, tools, piece=None):
        self.load(piece_order(piece, family={"variants": 3,
                                             "inputs": {"clips": "inputs/library.json", "items": "inputs/items"}}))
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_export", tools.export), \
                patch.object(local_gpu, "run_review", tools.review), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def pick(self):
        with open(os.path.join(self.out, "crew", "tech", "pick_final.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_failed_variant_lets_the_others_go_on(self):
        tools = FakeTools(fail_export=[self.v[0]], flagged=[self.v[1]])
        m = self.run_night(tools)
        failed = "crew/tech/%s/export" % self.v[0]
        self.assertEqual((m[failed]["status"], m[failed]["tries"]), ("failed", 1))  # not retried
        self.assertIn("invalid bone map", m[failed]["error"])
        for v in self.v[1:]:
            self.assertEqual(m["crew/tech/%s/export" % v]["status"], "done")
        for v in self.v:
            for stage in STAGES[:-1]:
                self.assertEqual(m["crew/tech/%s/%s" % (v, stage)]["status"], "done", (v, stage))
        # Every stage's outputs.
        e = m["crew/tech/%s/mesh" % self.v[1]]
        self.assertEqual(sorted(e["files"]), ["game_mesh", "raw_mesh"])
        self.assertEqual(sorted(m["crew/tech/%s/paint" % self.v[1]]["files"]),
                         ["albedo", "metal_roughness", "paint_views", "painted_mesh"])
        self.assertEqual(sorted(m["crew/tech/%s/cutout" % self.v[1]]["files"]), ["rgba"])
        with Image.open(os.path.join(self.out, m["crew/tech/%s/cutout" % self.v[1]]["files"]["rgba"][0])) as im:
            self.assertEqual(im.mode, "RGBA")
            self.assertEqual(im.getchannel("A").getextrema(), (0, 255))
        # The paint job got the game mesh by its local path; the export its raw mesh.
        paint = [s for s in self.fake.submitted if s[0] == "crew/tech/%s_paint" % self.v[1]][0][1]
        game = os.path.join(self.out, e["files"]["game_mesh"][0])
        self.assertEqual(os.path.normcase(paint["2"]["inputs"]["glb_path"]), os.path.normcase(os.path.abspath(game)))
        export_kw = [c[2] for c in tools.calls if c[:2] == ("export", self.v[1])][0]
        self.assertEqual(os.path.normcase(export_kw["bake_source"]),
                         os.path.normcase(os.path.join(self.out, e["files"]["raw_mesh"][0])))
        self.assertEqual(export_kw["items"], os.path.join(self.inputs, "items"))
        # The review sheet: every variant, the failure marked in its place.
        [review] = [c for c in tools.calls if c[0] == "review"]
        glbs, labels = review[1], review[2]
        self.assertEqual(labels, self.v)
        self.assertTrue(glbs[0].startswith("!export failed: export stopped: invalid bone map"), glbs[0])
        self.assertTrue(glbs[1].endswith("tech.glb") and glbs[2].endswith("tech.glb"))
        self.assertEqual(review[3], {"pitch": 60, "directions": 8, "figure_height": 96, "front": True})
        self.assertEqual(m["crew/tech/review"]["status"], "done")
        # The provisional final pick: the first variant without flags, pending review.
        pick = self.pick()
        self.assertEqual(pick["chosen"], "crew/tech/%s/export" % self.v[2])
        self.assertEqual((pick["manual"], pick["review"]), (False, "pending"))
        self.assertEqual([c["complete"] for c in pick["candidates"]], [False, True, True])
        self.assertEqual(len(pick["candidates"][1]["flags"]), 1)
        final = os.path.join(self.out, "crew", "tech", "final")
        self.assertEqual(sorted(os.listdir(final)), ["bone_map.json", "mesh_report.json", "model.json",
                                                     "motion_report.json", "regions_mask.png", "tech.glb"])
        with open(os.path.join(final, "model.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["variant"], self.v[2])
        with open(os.path.join(self.out, "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["failures"], [failed])
        self.assertEqual(state["picks_to_review"], 1)
        # A manual pick redoes only the copy.
        pick.update(chosen="crew/tech/%s/export" % self.v[1], manual=True)
        with open(os.path.join(self.out, "crew", "tech", "pick_final.json"), "w", encoding="utf-8") as fh:
            json.dump(pick, fh)
        self.fake.reset_log()
        tools.calls.clear()
        m2 = self.run_night(tools)
        self.assertEqual(self.fake.submitted, [])
        self.assertEqual(tools.calls, [])
        self.assertEqual(m2["crew/tech/final"]["chosen"], "crew/tech/%s/export" % self.v[1])
        self.assertEqual(self.pick()["review"], "manual")
        with open(os.path.join(final, "model.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["variant"], self.v[1])
        self.assertEqual(m2["crew/tech/review"]["spec"], m["crew/tech/review"]["spec"])

    def test_every_variant_flagged_picks_the_fewest_and_smallest_flags(self):
        # v0: two motion flags; v1: one large; v2: one small. The rank: mesh flags, then the
        # number of motion flags, then how far they are over their thresholds.
        tools = FakeTools(flagged={self.v[0]: [("foot_slide_cm", 2.2, 2.0), ("stretch_p95", 0.55, 0.5)],
                                   self.v[1]: [("stretch_p95", 0.9, 0.5)],
                                   self.v[2]: [("stretch_p95", 0.6, 0.5)]})
        self.run_night(tools)
        pick = self.pick()
        self.assertEqual(pick["chosen"], "crew/tech/%s/export" % self.v[2])
        self.assertEqual(pick["review"], "pending")
        self.assertIn("every one has flags", pick["metric"])
        self.assertEqual([c["rank"]["motion_flags"] for c in pick["candidates"]], [2, 1, 1])
        self.assertAlmostEqual(pick["candidates"][1]["rank"]["excess"], 0.8, places=3)
        # A mesh flag weighs more than any number of motion flags.
        shutil.rmtree(self.out)
        self.run_night(FakeTools(flagged={self.v[0]: [("stretch_p95", 0.6, 0.5)] * 3},
                                 mesh_flagged=[self.v[1], self.v[2]]))
        self.assertEqual(self.pick()["chosen"], "crew/tech/%s/export" % self.v[0])

    def test_a_missing_raw_mesh_fails_only_its_variant(self):
        # The raw-mesh save (node 405) of the first variant leaves no file in the history.
        self.fake.withhold["crew/tech/%s_mesh" % self.v[0]] = ("405",)
        tools = FakeTools()
        m = self.run_night(tools)
        mesh = m["crew/tech/%s/mesh" % self.v[0]]
        self.assertEqual((mesh["status"], mesh["tries"]), ("failed", 2))  # tried once more
        self.assertIn("finished without the output(s) raw_mesh", mesh["error"])
        self.assertEqual(len([s for s in self.fake.submitted if s[0] == "crew/tech/%s_mesh" % self.v[0]]), 2)
        for stage in ("paint", "rig", "export"):
            self.assertNotIn("crew/tech/%s/%s" % (self.v[0], stage), m)
        for v in self.v[1:]:
            self.assertEqual(m["crew/tech/%s/export" % v]["status"], "done")
        [review] = [c for c in tools.calls if c[0] == "review"]
        self.assertTrue(review[1][0].startswith("!mesh failed: finished without the output(s) raw_mesh"),
                        review[1][0])
        self.assertEqual(self.pick()["chosen"], "crew/tech/%s/export" % self.v[1])
        with open(os.path.join(self.out, "state.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["failures"], ["crew/tech/%s/mesh" % self.v[0]])

    def test_an_unworkable_specification_fails_only_its_job(self):
        # A finished mesh job whose record lost a role a later job reads (an older manifest):
        # the export of that variant cannot work out its specification.
        tools = FakeTools()
        self.run_night(tools)
        path = os.path.join(self.out, "manifest.json")
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        del m["crew/tech/%s/mesh" % self.v[0]]["files"]["raw_mesh"]
        del m["crew/tech/%s/export" % self.v[0]]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        tools.calls.clear()
        m2 = self.run_night(tools)  # finishes (exit 0) instead of stopping the factory
        export = "crew/tech/%s/export" % self.v[0]
        self.assertNotIn(export, m2)
        self.assertFalse([c for c in tools.calls if c[0] == "export"])
        with open(os.path.join(self.out, "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertIn(export, state["failures"])
        self.assertIn("has no output with role raw_mesh", state["specification_errors"][export])
        [review] = [c for c in tools.calls if c[0] == "review"]
        self.assertTrue(review[1][0].startswith("!export failed: its specification cannot be worked out"),
                        review[1][0])
        with open(os.path.join(self.out, "factory.log"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("FAILED %s: its specification" % export), 1)  # logged once

    def test_a_changed_glb_redoes_only_its_export(self):
        tools = FakeTools()
        manifest = self.run_night(tools)
        jid = "crew/tech/%s/export" % self.v[1]
        rel, _ = manifest[jid]["files"]["glb"]
        with open(os.path.join(self.out, rel), "wb") as stream:
            stream.write(b"truncated mesh")
        tools.calls.clear()
        self.fake.reset_log()
        self.run_night(tools)
        self.assertEqual([(c[0], c[1]) for c in tools.calls], [("export", self.v[1])])
        self.assertEqual(self.fake.submitted, [])

    def test_a_manual_bone_map_redoes_its_export(self):
        tools = FakeTools()
        self.run_night(tools)
        folder = os.path.join(self.out, "crew", "tech", "%s_export" % self.v[1])
        # A proposal (not manual) next to the export changes nothing.
        with open(os.path.join(folder, "bone_map.json"), "w", encoding="utf-8") as fh:
            json.dump({"source": "proposed", "roles": {"hips": "bone_0"}}, fh)
        tools.calls.clear()
        self.run_night(tools)
        self.assertEqual(tools.calls, [])
        # The copy the exporter writes from the order's map is manual too, but not a correction.
        with open(os.path.join(folder, "bone_map.json"), "w", encoding="utf-8") as fh:
            json.dump({"source": "order", "manual": True, "roles": {"hips": "bone_0"}}, fh)
        self.run_night(tools)
        self.assertEqual(tools.calls, [])
        # Marked manual: that variant's export, the review and the final copy are redone.
        with open(os.path.join(folder, "bone_map.json"), "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "roles": {"hips": "bone_1"}}, fh)
        self.fake.reset_log()
        self.run_night(tools)
        self.assertEqual([(c[0], c[1]) for c in tools.calls if c[0] != "review"], [("export", self.v[1])])
        self.assertEqual(len([c for c in tools.calls if c[0] == "review"]), 1)
        self.assertEqual(self.fake.submitted, [])

    def test_a_statue_reviews_the_painted_mesh(self):
        tools = FakeTools()
        m = self.run_night(tools, {"skip": ["pose", "rig", "export"]})
        self.assertEqual([c[0] for c in tools.calls], ["review"])
        glbs = tools.calls[0][1]
        self.assertTrue(all(g.endswith("_paint_painted_mesh.glb") for g in glbs), glbs)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "crew", "tech", "final"))),
                         sorted("%s_paint_%s" % (self.v[0], name) for name in (
                             "albedo.png", "metal_roughness.png", "painted_mesh.glb", "paint_views.png")))
        self.assertEqual(m["crew/tech/final"]["chosen"], "crew/tech/%s/paint" % self.v[0])


if __name__ == "__main__":
    unittest.main()
