"""Bounded retries and missing results, using only a fake server and fake local runner."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from fake_comfy import FakeComfy  # noqa: E402
from forge import local_gpu  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import sha_file  # noqa: E402

PLAIN = {"id": "extra/backdrop/plain", "workflow": "qwen_image_21_turbo_t2i.json",
         "values": {"$PROMPT$": "A plain backdrop.", "$NEGATIVE$": "", "$SEED$": 42},
         "roles": {"12": "rgb", "16": "mask"}}


class ResumeFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge_resume_")
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeComfy()
        self.addCleanup(self.fake.close)

    def factory(self, task=PLAIN, retry=False):
        path = os.path.join(self.tmp.name, "order.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "families": [], "tasks": [task]}, stream)
        args = SimpleNamespace(order=path, order_base="", out=os.path.join(self.tmp.name, "out"),
                               only="", retry_failed=retry, limit=2, one_pass=False, config="")
        factory = Factory(args, self.fake.url)
        factory.load()
        factory.pass_sleep = 0
        factory.gpu_processes = lambda: []
        factory.gpu_memory = lambda server: None
        return factory

    def run_bounded(self, factory):
        # An implementation bug must not hang the test or launch unlimited jobs.
        original = factory.pass_once
        passes = []

        def limited_pass():
            passes.append(None)
            return "finished" if len(passes) >= 12 else original()

        with patch.object(factory, "pass_once", limited_pass), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(factory.run(), 0)
        self.assertLess(len(passes), 12, "the factory kept retrying instead of finishing")

    def test_empty_result_is_failed_even_without_dependants(self):
        factory = self.factory()
        self.fake.withhold[PLAIN["id"]] = ["12", "16"]
        self.run_bounded(factory)
        entry = factory.entry(PLAIN["id"])
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["tries"], 2)
        self.assertIn("without", entry["error"])
        self.assertEqual(len(self.fake.submitted), 2)

    def test_output_integrity_is_rechecked_within_the_same_process(self):
        factory = self.factory()
        path = os.path.join(factory.out, "mesh.glb")
        with open(path, "wb") as stream:
            stream.write(b"original mesh")
        entry = {"files": {"mesh": ["mesh.glb", sha_file(path)]}}
        self.assertTrue(factory.files_present(entry))
        with open(path, "wb") as stream:
            stream.write(b"truncated")
        self.assertFalse(factory.files_present(entry))

    def test_retry_flag_does_not_repeat_new_failures_forever(self):
        factory = self.factory(retry=True)
        factory.needed[PLAIN["id"]] = {"rgb"}
        self.fake.withhold[PLAIN["id"]] = ["12", "16"]
        self.run_bounded(factory)
        self.assertEqual(len(self.fake.submitted), 2)
        self.assertEqual(factory.entry(PLAIN["id"])["status"], "failed")

    def test_retry_flag_gives_existing_failure_one_new_bounded_attempt_cycle(self):
        factory = self.factory(retry=True)
        jid = PLAIN["id"]
        factory.manifest[jid] = {"status": "failed", "spec": factory.spec(factory.by_id[jid]),
                                 "tries": 2, "error": "earlier failure"}
        untouched = {"status": "failed", "tries": 2, "error": "outside this order"}
        factory.manifest["other/stone/mesh"] = dict(untouched)
        factory.save_manifest()
        factory.needed[jid] = {"rgb"}
        self.fake.withhold[jid] = ["12", "16"]
        self.run_bounded(factory)
        self.assertEqual(len(self.fake.submitted), 2)
        self.assertEqual(factory.entry(jid)["tries"], 2)
        self.assertEqual(factory.entry("other/stone/mesh"), untouched)
        # Starting normally afterwards still leaves the failure alone.
        self.fake.reset_log()
        factory = self.factory()
        self.run_bounded(factory)
        self.assertEqual(self.fake.submitted, [])

    def test_retry_flag_is_bounded_for_local_tools_too(self):
        task = {"id": "people/technician/rig", "kind": "local_gpu", "tool": "fake_rig"}
        factory = self.factory(task, retry=True)
        jid = task["id"]
        factory.manifest[jid] = {"status": "failed", "spec": factory.spec(factory.by_id[jid]), "tries": 2}
        factory.save_manifest()
        with patch.dict(local_gpu.RUNNERS, {"fake_rig": None}):
            self.run_bounded(factory)
        self.assertEqual(factory.entry(jid)["status"], "failed")
        self.assertEqual(factory.entry(jid)["tries"], 2)


if __name__ == "__main__":
    unittest.main()
