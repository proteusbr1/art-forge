"""Files an order supplies ("inputs" of a piece, {"file": path} in a raw task) enter the
specification hash by content: changing one redoes only the jobs that use it."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy, picture  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import sha_file  # noqa: E402
from forge.recipes import refs_of  # noqa: E402

ORDER = {
    "version": 1,
    "style": {"global": "Plain test style.", "view_object": "Single object, plain light background."},
    "families": [
        {"id": "things", "recipe": "object_3d", "variants": 2, "assets": [
            {"id": "stone", "brief": "A smooth grey stone.", "inputs": {"reference": "refs/stone.png"}},
            {"id": "crate", "brief": "A wooden crate.", "inputs": {"reference": "refs/crate.png"}},
            {"id": "lantern", "brief": "An iron lantern."}]}],
    "tasks": [
        {"id": "extra/backdrop/edit", "workflow": "qwen_edit_2511.json",
         "values": {"$IMAGE1$": {"file": "refs/stone.png"}, "$PROMPT$": "Make it blue.", "$NEGATIVE$": "",
                    "$SEED$": 7, "$STEPS$": 4, "$CFG$": 1.0, "$LORA$": 1.0},
         "roles": {"16": "rgb"}}]}


class InputsHashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_inputs_")
        os.makedirs(os.path.join(self.tmp, "refs"))
        for i, name in enumerate(("stone", "crate")):
            picture(100 + i, "RGBA").save(os.path.join(self.tmp, "refs", name + ".png"))
        self.order = os.path.join(self.tmp, "order.json")
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump(ORDER, f)
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def specs(self):
        a = SimpleNamespace(order=self.order, order_base="", out=self.out, only="", retry_failed=False)
        f = Factory(a, "http://127.0.0.1:1")
        f.load()
        # Jobs that wait for no other job (the others need finished inputs to be hashed).
        return {j["id"]: f.spec(j) for j in f.jobs if j["kind"] == "comfy" and not refs_of(j)}

    def test_changed_input_changes_only_the_jobs_that_use_it(self):
        before = self.specs()
        self.assertIn("things/stone/mesh", before)
        self.assertNotIn("things/stone/pick", before)  # a reference replaces the concepts
        # Moving the order and its inputs elsewhere changes nothing.
        moved = os.path.join(self.tmp, "moved")
        os.makedirs(moved)
        shutil.copytree(os.path.join(self.tmp, "refs"), os.path.join(moved, "refs"))
        shutil.copy(self.order, os.path.join(moved, "order.json"))
        self.order = os.path.join(moved, "order.json")
        self.assertEqual(self.specs(), before)
        picture(999, "RGBA").save(os.path.join(moved, "refs", "stone.png"))
        after = self.specs()
        changed = sorted(j for j in before if before[j] != after[j])
        self.assertEqual(changed, ["extra/backdrop/edit", "things/stone/mesh"])

    def test_reference_runs_and_is_recorded(self):
        fake = FakeComfy()
        try:
            env = dict(os.environ, COMFY_SERVER=fake.url, FORGE_PASS_SLEEP="0.02", FORGE_ERROR_SLEEP="0.1",
                       PYTHONDONTWRITEBYTECODE="1")
            cmd = [sys.executable, "-m", "forge", "--order", self.order, "--out", self.out]
            res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, cwd=ROOT)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            self.assertTrue(all(e["status"] == "done" for e in m.values()), m)
            ref = os.path.join(self.tmp, "refs", "stone.png")
            inputs = m["things/stone/mesh"]["provenance"]["inputs"]
            self.assertEqual(len(inputs), 1)
            self.assertEqual((inputs[0]["sha256"], inputs[0]["factory_output"], inputs[0]["order_input"]),
                             (sha_file(ref), False, True))
            uploaded = [k for k in fake.files if k[0] == "input"]
            self.assertTrue(uploaded)
            # A second run sends nothing; changing the crate's reference redoes only its mesh.
            fake.reset_log()
            picture(555, "RGBA").save(os.path.join(self.tmp, "refs", "crate.png"))
            res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, cwd=ROOT)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual([s[0] for s in fake.submitted], ["things/crate/mesh"])
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
