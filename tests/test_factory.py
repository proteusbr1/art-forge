"""Tests of the factory (python -m forge) against a fake ComfyUI server (no GPU, no window).

  python -m unittest discover -s tests
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fake_comfy import FakeComfy  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
JOBS = os.path.join(HERE, "fixtures", "jobs_test.json")
sys.path.insert(0, ROOT)
from forge.recipes import expand  # noqa: E402


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ArtFactoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One full run shared by the tests that start from a finished output folder.
        cls.done_dir = tempfile.mkdtemp(prefix="art_factory_done_")
        first = cls("run_to_end")
        first.setUp()
        first.run_to_end()
        cls.first_submitted = list(first.fake.submitted)
        cls.first_frees = first.fake.frees
        shutil.copytree(first.out, os.path.join(cls.done_dir, "out"))
        first.tearDown()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.done_dir, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="art_factory_test_")
        self.out = os.path.join(self.tmp, "out")
        self.jobs_path = os.path.join(self.tmp, "jobs.json")
        shutil.copy(JOBS, self.jobs_path)
        self.fake = FakeComfy()

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_finished(self):
        """The output folder of the shared full run, copied for this test."""
        shutil.copytree(os.path.join(self.done_dir, "out"), self.out)
        return self.manifest()

    # --- helpers
    def factory(self, *args, timeout=120):
        env = dict(os.environ, COMFY_SERVER=self.fake.url, FORGE_PASS_SLEEP="0.02",
                   FORGE_ERROR_SLEEP="0.1", PYTHONDONTWRITEBYTECODE="1")
        cmd = [sys.executable, "-m", "forge", "--list", self.jobs_path, "--out", self.out] + list(args)
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def manifest(self):
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def job_list(self):
        with open(self.jobs_path, encoding="utf-8") as f:
            return json.load(f)

    def all_ids(self):
        return [j["id"] for j in expand(self.job_list())]

    def submitted(self):
        return [s[0] for s in self.fake.submitted]

    def run_to_end(self):
        self.factory()
        m = self.manifest()
        undone = [j for j in self.all_ids() if m.get(j, {}).get("status") != "done"]
        self.assertEqual(undone, [])
        return m

    # --- tests
    def test_full_run_records_hashes_and_is_idempotent(self):
        m = self.start_finished()
        self.assertEqual(sorted(m), sorted(self.all_ids()))
        self.assertTrue(all(e["status"] == "done" for e in m.values()))
        for jid, e in m.items():
            for role, (rel, digest) in e["files"].items():
                self.assertEqual(sha(os.path.join(self.out, rel)), digest, jid + " " + role)
            self.assertTrue(e["spec"])
        self.assertIn("painted_mesh", m["people/pilot/mesh"]["files"])
        self.assertIn("preview", m["surfaces/wall/seamless"]["files"])
        self.assertNotIn("things/poster/mesh", m)
        # Heavy jobs went alone, each after a /free.
        meshes = [s for s in self.first_submitted if s[0].endswith("/mesh")]
        self.assertEqual(len(meshes), 4)
        for job, wf, others in meshes:
            self.assertEqual(others, 0, job)
        self.assertGreaterEqual(self.first_frees, 4)
        for name in ("state.json", "timings.csv", "factory.log"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)
        with open(os.path.join(self.out, "state.json"), encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["counts"], {"done": len(self.all_ids())})
        self.assertEqual(state["picks_to_review"], 9)
        with open(os.path.join(self.out, "people", "pilot", "pick_pick.json"), encoding="utf-8") as f:
            pick = json.load(f)
        self.assertEqual(pick["review"], "pending")
        self.assertEqual(len(pick["candidates"]), 2)
        self.fake.reset_log()
        self.factory()
        self.assertEqual(self.submitted(), [])

    def test_changed_brief_redoes_only_that_asset(self):
        self.start_finished()
        lst = self.job_list()
        lst["families"][2]["assets"][0]["brief"] = "A wooden crate."
        with open(self.jobs_path, "w", encoding="utf-8") as f:
            json.dump(lst, f)
        self.fake.reset_log()
        self.run_to_end()
        redone = self.submitted()
        self.assertEqual(sorted(set(j.rsplit("/", 1)[0] for j in redone)), ["things/crate"])
        self.assertEqual(len(redone), 3)  # two concepts and the mesh

    def test_corrupt_output_redoes_only_its_producer(self):
        m = self.start_finished()
        jid = "things/crate/mesh"
        rel, expected = m[jid]["files"]["painted_mesh"]
        path = os.path.join(self.out, rel)
        with open(path, "wb") as f:
            f.write(b"truncated mesh")
        self.run_to_end()
        self.assertEqual(self.submitted(), [jid])
        self.assertEqual(sha(path), expected)

    def test_manual_pick_redoes_only_dependants(self):
        self.start_finished()
        path = os.path.join(self.out, "people", "pilot", "pick_pick.json")
        with open(path, encoding="utf-8") as f:
            pick = json.load(f)
        other = [c["job"] for c in pick["candidates"] if c["job"] != pick["chosen"]][0]
        pick.update({"chosen": other, "manual": True})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pick, f)
        self.fake.reset_log()
        m = self.run_to_end()
        self.assertEqual(m["people/pilot/pick"]["chosen"], other)
        self.assertEqual(sorted(self.submitted()), sorted(
            ["people/pilot/view_90", "people/pilot/view_180", "people/pilot/view_270",
             "people/pilot/emissive", "people/pilot/mesh"]))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["review"], "manual")

    def test_stop_file_stops_after_queued_prompts(self):
        self.fake.delay = 0.3
        self.factory("--one-pass", "--limit", "3")
        self.assertEqual(len(self.submitted()), 3)
        queued = [j for j, e in self.manifest().items() if e["status"] == "queued"]
        self.assertEqual(len(queued), 3)
        open(os.path.join(self.out, "STOP"), "w").close()
        self.fake.reset_log()
        out = self.factory()
        self.assertIn("stopped on request", out)
        self.assertEqual(self.submitted(), [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "STOP")))
        m = self.manifest()
        for j in queued:
            self.assertEqual(m[j]["status"], "done", j)
        self.assertLess(sum(1 for e in m.values() if e["status"] == "done"), len(self.all_ids()))
        self.fake.delay = 0.0
        self.run_to_end()  # resumes

    def test_provenance_export(self):
        m = self.start_finished()
        dest = os.path.join(self.tmp, "provenance.json")
        self.factory("--provenance", dest)
        with open(dest, encoding="utf-8") as f:
            prov = json.load(f)
        records = prov["files"]
        expected = sum(len(e["files"]) for e in m.values())
        self.assertEqual(len(records), expected)
        for r in records:
            self.assertTrue(r["provenance_recorded"], r["job"])
            self.assertEqual(sha(os.path.join(self.out, r["file"])), r["sha256"])
            self.assertTrue(r["finished"])
        by = {(r["job"], r["role"]): r for r in records}
        jobs = {j["id"]: j for j in expand(self.job_list())}
        concept = [j for j in jobs if j.startswith("people/pilot/concept_s")][0]
        c = by[(concept, "rgba")]
        self.assertEqual((c["family"], c["asset"], c["step"]), ("people", "pilot", concept.split("/")[-1]))
        self.assertEqual(c["workflow"], "klein_t2i.json")
        wf_path = os.path.join(ROOT, "forge", "workflows", "klein_t2i.json")
        self.assertEqual(c["workflow_sha256"], sha(wf_path))
        self.assertIn({"node": "1", "class": "UNETLoader", "input": "unet_name",
                       "file": "flux-2-klein-9b-fp8.safetensors"}, c["models"])
        self.assertIn("birefnet.safetensors", [x["file"] for x in c["models"]])
        self.assertEqual(c["prompt"], jobs[concept]["values"]["$PROMPT$"])
        self.assertEqual(c["seeds"], [jobs[concept]["values"]["$SEED$"]])
        self.assertEqual(c["inputs"], [])
        mesh = by[("people/pilot/mesh", "painted_mesh")]
        self.assertIn("hunyuan3d-dit-v2-1-fp16.ckpt", [x["file"] for x in mesh["models"]])
        chosen = m["people/pilot/pick"]["chosen"]
        self.assertEqual(len(mesh["inputs"]), 1)
        inp = mesh["inputs"][0]
        self.assertEqual((inp["job"], inp["pick"], inp["role"], inp["factory_output"]),
                         (chosen, "people/pilot/pick", "rgba", True))
        self.assertEqual(inp["sha256"], m[chosen]["files"]["rgba"][1])
        self.assertEqual(mesh["settings"]["9.texture_size"], 2048)
        mat = by[("surfaces/gravel/seam", "rgb")]
        self.assertEqual(mat["negative"], "perspective, horizon")
        self.assertEqual(sorted(i["role"] for i in mat["inputs"]), ["image", "mask"])
        pick = by[("people/pilot/pick", "pick")]
        self.assertEqual(pick["kind"], "pick")
        self.assertEqual(len(pick["inputs"]), 2)
        edit = by[("extra/backdrop/edit", "rgb")]
        self.assertIn({"server_file": "external_input.png", "factory_output": False, "sha256": None}, edit["inputs"])


if __name__ == "__main__":
    unittest.main()
