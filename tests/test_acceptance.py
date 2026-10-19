"""Acceptance criteria, apart from the ranking: the order's "accept" limits and their
defaults, the classification of every variant as acceptable or rejected with the stage
held responsible (one test per row of the table), the final pick only among acceptable
variants, the outcome "no_variant_passed" with an empty final/, and reserve variants held
until that outcome, then released for a second decision (with the fake server and
stand-ins for the local tools)."""

import contextlib
import io
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from fake_comfy import FakeComfy  # noqa: E402
from test_humanoid_recipe import Base, FakeTools, piece_order  # noqa: E402
from forge import acceptance, local_gpu  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for  # noqa: E402
from forge.order import check_order  # noqa: E402
from forge.recipes import refs_of  # noqa: E402


def done(job, mesh_flags=(), motion=None, offset=None):
    """A complete variant's record for classify: its mesh flags and motion values."""
    clips = {c: dict(m, found=True) for c, m in (motion or {}).items()}
    return {"job": job, "variant": job.split("/")[2] if job.count("/") >= 3 else job, "complete": True,
            "failure": None, "mesh_report": {"flags": list(mesh_flags), "bake_source_offset_m": offset},
            "motion_report": {"clips": clips, "flags": []}}


def failed(job, stage, error):
    return {"job": job, "variant": job, "complete": False, "failure": {"stage": stage, "error": error},
            "mesh_report": None, "motion_report": None}


class LimitsTest(unittest.TestCase):
    def test_defaults_and_custom_limits(self):
        d = acceptance.limits()
        self.assertEqual(d, {"mesh_flags": 0, "missing_clips": 0, "foot_slide_cm": 2.0, "hand_socket_cm": 2.0,
                             "reach_shortfall_cm": 1.0, "stretch_p95": 0.5, "penetration_cm": 2.0,
                             "bake_source_offset_m": 0.05})
        # A motion measure follows the export stage's threshold unless accept gives it.
        self.assertEqual(acceptance.limits(None, {"stretch_p95": 0.7})["stretch_p95"], 0.7)
        self.assertEqual(acceptance.limits({"stretch_p95": 0.9}, {"stretch_p95": 0.7})["stretch_p95"], 0.9)
        lim = acceptance.limits({"mesh_flags": 1, "foot_slide_cm": 5.0})
        self.assertEqual((lim["mesh_flags"], lim["foot_slide_cm"], lim["penetration_cm"]), (1, 5.0, 2.0))

    def test_classification_with_default_and_custom_limits(self):
        records = [done("f/p/v1/export"),
                   done("f/p/v2/export", motion={"walk": {"foot_slide_cm": 3.0}}),
                   done("f/p/v3/export", mesh_flags=["3 vertices have no weight"]),
                   done("f/p/v4/export", offset=0.08)]
        verdicts, summary = acceptance.classify(records, acceptance.limits())
        self.assertEqual([v["class"] for v in verdicts], ["acceptable", "rejected", "rejected", "rejected"])
        self.assertEqual(summary["acceptable"], ["f/p/v1/export"])
        self.assertEqual(verdicts[1]["violations"][0]["measure"], "foot_slide_cm")
        self.assertEqual((verdicts[1]["violations"][0]["value"], verdicts[1]["violations"][0]["limit"]), (3.0, 2.0))
        self.assertIn("walk foot_slide_cm 3 over 2.0", verdicts[1]["reasons"])
        # Custom limits let the slide, one mesh flag and the offset pass.
        lim = acceptance.limits({"foot_slide_cm": 4.0, "mesh_flags": 1, "bake_source_offset_m": 0.1})
        verdicts, _ = acceptance.classify(records, lim)
        self.assertEqual([v["class"] for v in verdicts], ["acceptable"] * 4)


class ResponsibleStageTest(unittest.TestCase):
    # One row per entry of acceptance.MESH_FLAG_STAGES: a flag text and the stage it gives.
    MESH_ROWS = (
        ("material slot normal: written as jpeg, which is lossy", "export"),
        ("material slot metallic_roughness: its source map (x) is missing from the exported GLB", "paint"),
        ("material source: painted.glb cannot be imported: none of its maps could be carried", "paint"),
        ("the material has no base-colour texture", "paint"),
        ("3 vertices have no weight", "rig"),
        ("weights are not normalised (worst sum off by 0.0100)", "rig"),
        ("the bake source does not line up with the game mesh (90th percentile ...)", "mesh"),
        ("the mesh has no UV layer", "unknown"),
        ("the mesh has 2 materials, 1 is expected", "unknown"),
        ("something new", "unknown"),
    )

    def test_every_row_of_the_mesh_flag_table(self):
        self.assertEqual(len(self.MESH_ROWS), len(acceptance.MESH_FLAG_STAGES) + 1)  # + the fallback
        for flag, stage in self.MESH_ROWS:
            with self.subTest(flag=flag):
                self.assertEqual(acceptance.mesh_flag_stage(flag), stage)
                verdicts, summary = acceptance.classify([done("f/p/v1/export", mesh_flags=[flag])],
                                                        acceptance.limits())
                self.assertEqual(verdicts[0]["stages"], [stage])
                self.assertEqual(summary["responsible_stage"], stage)
        # With paint skipped, TRELLIS.2's own texture is the mesh stage's.
        self.assertEqual(acceptance.mesh_flag_stage("material slot opacity: x", paint_stage="mesh"), "mesh")

    def test_failures_bone_map_bake_offset_and_reports(self):
        rows = ((failed("f/p/v1/mesh", "mesh", "finished without the output(s) raw_mesh"), "mesh"),
                (failed("f/p/v1/export", "export", "export stopped: invalid bone map (see x)"), "rig"),
                (failed("f/p/v1/export", "export", "export failed (exit 1), see x"), "export"),
                (failed("f/p/v1/rig", "rig", "rig timed out"), "rig"),
                (failed("f/p/v1/x", None, "not finished"), "unknown"),
                (done("f/p/v1/export", offset=0.2), "mesh"),
                (dict(done("f/p/v1/export"), mesh_report="unreadable"), "export"),
                (dict(done("f/p/v1/export"), motion_report={"clips": {"walk": {"found": False}}, "flags": []}),
                 "export"))
        for rec, stage in rows:
            with self.subTest(rec=rec["failure"] or rec["mesh_report"]):
                verdicts, _ = acceptance.classify([rec], acceptance.limits())
                self.assertEqual(verdicts[0]["class"], "rejected")
                self.assertEqual(verdicts[0]["stages"], [stage])
        self.assertEqual(acceptance.RESPONSIBLE["bone_map"], "rig")

    def test_motion_is_the_rig_or_the_library_when_every_variant_fails_the_clip(self):
        walk = {"walk": {"foot_slide_cm": 3.0}}
        both = {"walk": {"foot_slide_cm": 3.0}, "idle": {"stretch_p95": 0.9}}
        verdicts, summary = acceptance.classify([done("f/p/v1/export", motion=both),
                                                 done("f/p/v2/export", motion=walk)], acceptance.limits())
        by = {(x["clip"], x["measure"]): x["stage"] for x in verdicts[0]["violations"]}
        self.assertEqual(by, {("walk", "foot_slide_cm"): "library", ("idle", "stretch_p95"): "rig"})
        self.assertEqual(verdicts[1]["stages"], ["library"])
        self.assertEqual(summary["responsible_stage"], "library")
        self.assertEqual(summary["responsible_counts"], {"library": 2, "rig": 1})
        # One variant alone cannot tell the clip from the rig.
        verdicts, _ = acceptance.classify([done("f/p/v1/export", motion=walk)], acceptance.limits())
        self.assertEqual(verdicts[0]["stages"], ["rig"])
        # A failed variant does not count as "every variant".
        verdicts, _ = acceptance.classify([done("f/p/v1/export", motion=walk), done("f/p/v2/export"),
                                           failed("f/p/v3/export", "export", "x")], acceptance.limits())
        self.assertEqual(verdicts[0]["stages"], ["rig"])


class OrderTest(Base):
    def test_check_order_refuses_unknown_accept_keys_and_negative_reserve(self):
        self.assertEqual(self.problems({"accept": {"mesh_flags": 1, "stretch_p95": 0.8}, "reserve": 2}), [])
        self.assertEqual(self.problems(family={"accept": {"foot_slide_cm": 3}, "reserve": 0}), [])
        self.assertProblem(self.problems({"accept": {"wobble": 1}}), "unknown accept key 'wobble'")
        self.assertProblem(self.problems({"accept": {"mesh_flags": -1}}), "accept mesh_flags must be a whole number")
        self.assertProblem(self.problems({"accept": {"stretch_p95": "low"}}), "accept stretch_p95 must be a number")
        self.assertProblem(self.problems({"accept": [1]}), "accept must be an object")
        self.assertProblem(self.problems({"reserve": -1}), "reserve must be a whole number of at least 0")
        self.assertProblem(self.problems({"reserve": 1.5}), "reserve must be a whole number of at least 0")
        # Only a recipe with a final decision has them.
        order = {"version": 1, "style": {"view_object": "x"}, "families": [
            {"id": "props", "recipe": "concept", "assets": [{"id": "crate", "brief": "A crate.",
                                                             "accept": {"mesh_flags": 0}, "reserve": 1}]}]}
        problems = check_order(self.load(order), self.config)
        self.assertProblem(problems, "props/crate: recipe concept has no acceptance criteria")
        self.assertProblem(problems, "props/crate: recipe concept has no reserve variants")
        # params.image_format: lossless or compact.
        self.assertEqual(self.problems(family={"params": {"image_format": "compact"}}), [])
        self.assertProblem(self.problems(family={"params": {"image_format": "jpeg"}}),
                           "param image_format must be one of lossless, compact")

    def test_reserve_variants_expand_with_their_own_seeds_and_stable_ids(self):
        jobs = self.jobs({"reserve": 2})
        self.assertEqual(jobs, self.jobs({"reserve": 2}))
        seeds = seeds_for("crew/tech", 4)
        main, extra = seeds[:2], seeds[2:]
        self.assertEqual(seeds_for("crew/tech", 2), main)
        ids = list(jobs)
        self.assertEqual(len(ids), 7 * 4 + 4)
        self.assertEqual(ids[-4:], ["crew/tech/review", "crew/tech/final", "crew/tech/review_reserve",
                                    "crew/tech/final_reserve"])
        hold = {"until": "crew/tech/final", "outcome": "no_variant_passed"}
        for s in main:
            self.assertFalse(any("hold" in j for jid, j in jobs.items() if jid.startswith("crew/tech/v%d/" % s)))
        for s in extra:
            chain = [j for jid, j in jobs.items() if jid.startswith("crew/tech/v%d/" % s)]
            self.assertEqual(len(chain), 7)
            self.assertTrue(all(j["hold"] == hold for j in chain))
        self.assertEqual([v["variant"] for v in jobs["crew/tech/final"]["variants"]], ["v%d" % s for s in main])
        self.assertEqual([v["variant"] for v in jobs["crew/tech/final_reserve"]["variants"]],
                         ["v%d" % s for s in seeds])
        self.assertEqual([v.get("reserve", False) for v in jobs["crew/tech/final_reserve"]["variants"]],
                         [False, False, True, True])
        for jid in ("crew/tech/review_reserve", "crew/tech/final_reserve"):
            self.assertEqual(jobs[jid]["hold"], hold)
            self.assertIn("crew/tech/final", refs_of(jobs[jid]))
        # The main variants' jobs and their hashes do not change with a reserve.
        base, more = self.specs(piece_order()), self.specs(piece_order({"reserve": 2}))
        self.assertEqual({j: base[j] for j in base if "/v" in j}, {j: more[j] for j in base if "/v" in j})
        # The limits enter the decisions (and only them).
        lim = acceptance.limits()
        self.assertEqual(jobs["crew/tech/final"]["accept"], lim)
        self.assertEqual(jobs["crew/tech/review"]["accept"], lim)
        changed = self.specs(piece_order({"accept": {"mesh_flags": 2}}))
        self.assertEqual(sorted(j for j in base if base[j] != changed[j]), ["crew/tech/final", "crew/tech/review"])

    def test_the_export_gets_the_painted_maps(self):
        jobs = self.jobs(family={"variants": 1})
        export = next(j for j in jobs.values() if j.get("stage") == "export")
        paint = next(j for j in jobs.values() if j.get("stage") == "paint")
        self.assertEqual(export["inputs"]["metal_roughness"], {"ref": paint["id"], "role": "metal_roughness"})
        self.assertEqual(export["inputs"]["material_source"], {"ref": paint["id"], "role": "painted_mesh"})
        self.assertNotIn("image_format", export["args"])
        # TRELLIS.2's own texture: the mesh job's painted mesh is the material source.
        jobs = self.jobs({"skip": ["paint"]}, family={"variants": 1, "params": {"texture": 1024,
                                                                                "image_format": "compact"}})
        export = next(j for j in jobs.values() if j.get("stage") == "export")
        mesh = next(j for j in jobs.values() if j.get("stage") == "mesh")
        self.assertEqual(export["inputs"]["material_source"], {"ref": mesh["id"], "role": "painted_mesh"})
        self.assertNotIn("metal_roughness", export["inputs"])
        self.assertEqual(export["args"]["image_format"], "compact")
        # The runner hands them to the exporter.
        factory = SimpleNamespace(config=self.config, resolve=lambda ref: ("/x/%s.bin" % ref["role"], "0" * 64))
        seen = {}
        with patch.object(local_gpu, "run_export", lambda rig, out, config, **kw: seen.update(kw) or {}):
            local_gpu.export_runner(factory, export, os.path.join(self.tmp, "export"))
        self.assertEqual((seen["material_source"], seen["metal_roughness"], seen["image_format"]),
                         ("/x/painted_mesh.bin", None, "compact"))


class Tools(FakeTools):
    """FakeTools whose review also records the verdicts and banner the sheet shows."""

    def review(self, glbs, out, config, camera=None, labels=None, verdicts=None, banner=None):
        files = FakeTools.review(self, glbs, out, config, camera=camera, labels=labels)
        self.calls[-1] = self.calls[-1] + ({"verdicts": verdicts, "banner": banner},)
        return files


class NightTest(Base):
    def setUp(self):
        Base.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        self.fake.close()
        Base.tearDown(self)

    def factory(self, piece, variants=3):
        self.load(piece_order(piece, family={"variants": variants,
                                             "inputs": {"clips": "inputs/library.json", "items": "inputs/items"}}))
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        return f

    def run_night(self, tools, piece=None, variants=3):
        f = self.factory(piece, variants)
        with patch.object(local_gpu, "run_rig", tools.rig), patch.object(local_gpu, "run_export", tools.export), \
                patch.object(local_gpu, "run_review", tools.review), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def test_final_picks_only_among_acceptable_variants(self):
        v = ["v%d" % s for s in seeds_for("crew/tech", 3)]
        # v0: one mesh flag (rejected by default); v1: a stretch the order accepts but the
        # report flags; v2: a foot slide over the order's limit. Ranking alone would pick v1
        # or v0; acceptance leaves only v1.
        tools = Tools(mesh_flagged=[v[0]], flagged={v[1]: [("stretch_p95", 0.6, 0.5)],
                                                    v[2]: [("foot_slide_cm", 3.5, 2.0)]})
        self.run_night(tools, {"accept": {"stretch_p95": 0.8, "foot_slide_cm": 3.0}})
        pick = self.read("crew", "tech", "pick_final.json")
        self.assertEqual(pick["outcome"], "picked")
        self.assertEqual(pick["chosen"], "crew/tech/%s/export" % v[1])
        self.assertEqual([c["class"] for c in pick["candidates"]], ["rejected", "acceptable", "rejected"])
        self.assertEqual(pick["candidates"][0]["responsible"], ["rig"])
        self.assertIn("mesh: 3 vertices have no weight", pick["candidates"][0]["reasons"])
        self.assertEqual(pick["accept"]["stretch_p95"], 0.8)
        self.assertEqual(pick["review"], "pending")
        # The sheet labels each variant.
        [review] = [c for c in tools.calls if c[0] == "review"]
        verdicts = review[4]["verdicts"]
        self.assertEqual([x["class"] for x in verdicts], ["rejected", "acceptable", "rejected"])
        self.assertTrue(verdicts[2]["reasons"][0].startswith("walk foot_slide_cm 3.5 over 3"), verdicts[2])
        self.assertIsNone(review[4]["banner"])

    def test_all_rejected_is_no_variant_passed_with_an_empty_final(self):
        v = ["v%d" % s for s in seeds_for("crew/tech", 3)]
        tools = Tools(fail_export=[v[0]], flagged=[v[1], v[2]])
        m = self.run_night(tools)
        pick = self.read("crew", "tech", "pick_final.json")
        self.assertEqual(pick["outcome"], "no_variant_passed")
        self.assertIsNone(pick["chosen"])
        self.assertEqual([c["class"] for c in pick["candidates"]], ["rejected"] * 3)
        self.assertEqual(pick["candidates"][0]["responsible"], ["rig"])  # the invalid bone map
        self.assertEqual(pick["candidates"][0]["reasons"][0][:30], "export failed: export stopped:")
        # The same clip fails on both measured variants: the library's clip.
        self.assertEqual(pick["candidates"][1]["responsible"], ["library"])
        self.assertEqual(pick["responsible_stage"], "library")
        self.assertEqual(pick["responsible_counts"], {"library": 2, "rig": 1})
        self.assertFalse(os.path.exists(os.path.join(self.out, "crew", "tech", "final")))
        self.assertEqual(m["crew/tech/final"]["outcome"], "no_variant_passed")
        self.assertEqual(sorted(m["crew/tech/final"]["files"]), ["pick"])
        [review] = [c for c in tools.calls if c[0] == "review"]
        self.assertEqual(review[4]["banner"], "no variant passed: most often the library (2 of 3)")
        # A final copy left by an earlier pick goes when the outcome is no_variant_passed.
        final = os.path.join(self.out, "crew", "tech", "final")
        os.makedirs(final)
        with open(os.path.join(final, "old.glb"), "w") as fh:
            fh.write("old")
        del m["crew/tech/final"]
        with open(os.path.join(self.out, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        self.run_night(tools)
        self.assertFalse(os.path.exists(final))

    def test_every_variant_failed_still_decides(self):
        v = ["v%d" % s for s in seeds_for("crew/tech", 2)]
        tools = Tools(fail_export=v)
        self.run_night(tools, variants=2)
        pick = self.read("crew", "tech", "pick_final.json")
        self.assertEqual(pick["outcome"], "no_variant_passed")
        self.assertEqual(pick["responsible_stage"], "rig")
        [review] = [c for c in tools.calls if c[0] == "review"]
        self.assertTrue(all(g.startswith("!") for g in review[1]))

    def test_reserve_variants_are_held_until_no_variant_passed(self):
        seeds = seeds_for("crew/tech", 4)
        v = ["v%d" % s for s in seeds]
        # The main variants pass: the reserve is never run and does not keep the night going.
        tools = Tools()
        m = self.run_night(tools, {"reserve": 2}, variants=2)
        self.assertEqual(self.read("crew", "tech", "pick_final.json")["outcome"], "picked")
        for s in v[2:]:
            self.assertFalse(any(jid.startswith("crew/tech/%s/" % s) for jid in m), s)
        self.assertNotIn("crew/tech/final_reserve", m)
        self.assertFalse([s for s in self.fake.submitted if s[0].startswith(v[2]) or s[0].startswith(v[3])])
        state = self.read("state.json")
        self.assertEqual(state["counts"]["held"], 2 * 7 + 2)
        self.assertEqual(state["counts"].get("ready", 0) + state["counts"].get("waiting", 0), 0)
        pick = self.read("crew", "tech", "pick_final.json")
        self.assertEqual(pick["reserve"], {"variants": v[2:], "released": False, "decision": "crew/tech/final_reserve"})

    def test_no_variant_passed_releases_the_reserve_for_a_second_decision(self):
        seeds = seeds_for("crew/tech", 4)
        v = ["v%d" % s for s in seeds]
        # Both main variants have a mesh flag; the first reserve variant is clean.
        tools = Tools(mesh_flagged=[v[0], v[1], v[3]])
        m = self.run_night(tools, {"reserve": 2}, variants=2)
        first = self.read("crew", "tech", "pick_final.json")
        self.assertEqual(first["outcome"], "no_variant_passed")
        self.assertEqual(first["reserve"]["released"], True)
        self.assertEqual(first["responsible_stage"], "rig")
        for s in v[2:]:
            self.assertEqual(m["crew/tech/%s/export" % s]["status"], "done")
        second = self.read("crew", "tech", "pick_final_reserve.json")
        self.assertEqual(second["outcome"], "picked")
        self.assertEqual(second["chosen"], "crew/tech/%s/export" % v[2])
        self.assertEqual([c["variant"] for c in second["candidates"]], v)
        self.assertEqual([c["class"] for c in second["candidates"]], ["rejected", "rejected", "acceptable", "rejected"])
        with open(os.path.join(self.out, "crew", "tech", "final", "model.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["variant"], v[2])
        reviews = [c for c in tools.calls if c[0] == "review"]
        self.assertEqual([len(c[2]) for c in reviews], [2, 4])  # the first sheet, then all variants
        self.assertEqual(reviews[0][4]["banner"], "no variant passed: most often the rig (2 of 2)")
        self.assertIsNone(reviews[1][4]["banner"])
        self.assertEqual(self.read("state.json")["counts"].get("held", 0), 0)
        # Run again: nothing more to do.
        tools.calls.clear()
        self.fake.reset_log()
        self.run_night(tools, {"reserve": 2}, variants=2)
        self.assertEqual((tools.calls, self.fake.submitted), ([], []))

    def test_with_no_reserve_left_the_outcome_stays_no_variant_passed(self):
        v = ["v%d" % s for s in seeds_for("crew/tech", 3)]
        tools = Tools(mesh_flagged=v)
        self.run_night(tools, {"reserve": 1}, variants=2)
        second = self.read("crew", "tech", "pick_final_reserve.json")
        self.assertEqual(second["outcome"], "no_variant_passed")
        self.assertEqual(len(second["candidates"]), 3)
        self.assertEqual(second["reserve"], {"variants": [v[2]], "released": True, "left": 0})
        self.assertFalse(os.path.exists(os.path.join(self.out, "crew", "tech", "final")))

    def test_the_estimate_counts_the_reserve_apart(self):
        f = self.factory({"reserve": 2}, variants=2)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            f.estimate()
        text = buf.getvalue()
        self.assertIn("total 32 jobs", text)
        self.assertIn("if needed: 16 held reserve jobs", text)  # 2 x 7 + review_reserve + final_reserve
        self.assertRegex(text, r"local_gpu\s+10 jobs,\s+5 left, ~0\.\d\d GPU h, 5 held")


if __name__ == "__main__":
    unittest.main()
