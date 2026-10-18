"""Machine-wide GPU coordination with the fake server: the lock file (a second factory
refuses to start, a stale lock is taken over), local GPU jobs that wait for the whole
ComfyUI queue and for the other processes on the card, send nothing while they run and
are retried once after the queue drains."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import gpu_lock, local_gpu  # noqa: E402
from forge.factory_core import Factory, out_of_memory  # noqa: E402

PLAIN = {"id": "extra/backdrop/plain", "workflow": "qwen_image_21_turbo_t2i.json",
         "values": {"$PROMPT$": "A plain backdrop.", "$NEGATIVE$": "", "$SEED$": 42}, "roles": {"12": "rgb"}}
GPU_JOB = {"id": "extra/backdrop/rig", "kind": "local_gpu", "tool": "fake_rig", "group": "local_gpu",
           "args": {"seed": 5, "beams": 10, "postprocess": False},
           "ignored": {"postprocess": "the test tool ignores it"}}


class FakeTool:
    """A local GPU runner: records when it ran; fails as told."""

    def __init__(self, fake, fail=0, external=0.0):
        self.fake, self.fail, self.external = fake, fail, external
        self.calls = []

    def __call__(self, factory, job, out_dir):
        t0 = time.time()
        os.makedirs(out_dir, exist_ok=True)
        n = len(self.calls)
        time.sleep(0.3)
        if n < self.fail:
            if self.external:
                self.fake.external(self.external)  # an external generation starts meanwhile
            log = os.path.join(out_dir, "rig.stderr.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write("Traceback ...\ntorch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB\n")
            self.calls.append((t0, time.time()))
            raise local_gpu.ToolError("rig failed (exit 1), see %s" % log, log)
        path = os.path.join(out_dir, "rigged.glb")
        with open(path, "wb") as f:
            f.write(b"glTF fake rig %d" % n)
        self.calls.append((t0, time.time()))
        return {"files": {"rig": path}, "passed": {"seed": job["args"]["seed"], "beams": 8},
                "provenance": {"tools": {"fake": {"version": "1"}}, "settings": {"beams": 8}}}


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_gpu_")
        self.out = os.path.join(self.tmp, "out")
        self.order = os.path.join(self.tmp, "order.json")
        self.fake = FakeComfy()
        self.write([GPU_JOB, PLAIN])

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, tasks):
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "style": {}, "families": [], "tasks": tasks}, f)

    def factory(self, tool, processes=lambda: []):
        a = SimpleNamespace(order=self.order, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = processes
        self.patch = patch.dict(local_gpu.RUNNERS, {"fake_rig": tool})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        return f

    def run_quiet(self, f):
        """Factory.run with its console lines kept out of the test output."""
        with contextlib.redirect_stdout(io.StringIO()):
            return f.run()

    def log(self):
        with open(os.path.join(self.out, "factory.log"), encoding="utf-8") as f:
            return f.read()

    def manifest(self):
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def frees(self):
        return [t for t, m, p in self.fake.requests if (m, p) == ("POST", "/free")]

    def test_waits_for_another_clients_prompt_and_sends_nothing_while_it_runs(self):
        tool = FakeTool(self.fake)
        f = self.factory(tool)
        done_at = self.fake.external(0.8)
        self.assertEqual(self.run_quiet(f), 0)
        [(start, end)] = tool.calls
        self.assertGreaterEqual(start, done_at)
        self.assertTrue(any(done_at <= t <= start for t in self.frees()), "no /free before the job")
        self.assertEqual(self.fake.requests_between(start, end), [])
        # The ComfyUI job waited for the local GPU job.
        self.assertEqual([s[0] for s in self.fake.submitted], ["extra/backdrop/plain"])
        prompt_at = [t for t, m, p in self.fake.requests if (m, p) == ("POST", "/prompt")]
        self.assertGreater(min(prompt_at), end)
        log = self.log()
        self.assertIn("local GPU job extra/backdrop/rig waits for an empty ComfyUI queue (1 running, 0 pending)", log)
        self.assertIn("no other compute process on the card", log)
        m = self.manifest()
        e = m["extra/backdrop/rig"]
        self.assertEqual((e["status"], e["tries"]), ("done", 1))
        self.assertEqual(sorted(e["files"]), ["rig"])
        params = e["provenance"]["parameters"]
        self.assertEqual(params["seed"], {"requested": 5, "effective": 5})
        self.assertEqual(params["beams"], {"requested": 10, "effective": 8, "changed": True})
        self.assertIsNone(params["postprocess"]["effective"])
        self.assertEqual(e["provenance"]["tools"], {"fake": {"version": "1"}})
        # Run again: nothing to do.
        tool.calls.clear()
        self.assertEqual(self.run_quiet(self.factory(tool)), 0)
        self.assertEqual(tool.calls, [])

    def test_waits_while_another_process_holds_the_card(self):
        tool = FakeTool(self.fake)
        t0 = time.time()

        def processes():
            mb = 9000 if time.time() - t0 < 0.6 else 300
            return [{"pid": 4242, "name": "game.exe", "mb": mb}, {"pid": 4343, "name": "shell.exe", "mb": None}]
        f = self.factory(tool, processes)
        f.gpu_wait = 30
        self.assertEqual(self.run_quiet(f), 0)
        self.assertGreaterEqual(tool.calls[0][0], t0 + 0.6)
        log = self.log()
        self.assertIn("processes on the card (2): game.exe (4242, 9000 MB), shell.exe (4343, n/a)", log)
        self.assertIn("waits (up to 30 s) while other processes hold the card: busy: game.exe (4242, 9000 MB)", log)

    def test_goes_after_the_wait_and_without_nvidia_smi(self):
        tool = FakeTool(self.fake)
        f = self.factory(tool, lambda: [{"pid": 4242, "name": "game.exe", "mb": 9000}])
        f.gpu_wait = 0.3
        self.assertEqual(self.run_quiet(f), 0)
        self.assertIn("goes after 0 s although other processes hold the card", self.log())
        self.assertEqual(gpu_lock.gpu_processes(exe=os.path.join(self.tmp, "no-nvidia-smi.exe")), None)
        self.assertIn("could not be listed", gpu_lock.describe_processes(None))
        shutil.rmtree(self.out)
        f = self.factory(FakeTool(self.fake), lambda: None)
        self.assertEqual(self.run_quiet(f), 0)
        self.assertIn("other GPU processes could not be listed", self.log())

    def test_failed_job_is_retried_once_after_the_queue_drains(self):
        tool = FakeTool(self.fake, fail=1, external=0.8)
        f = self.factory(tool)
        self.assertEqual(self.run_quiet(f), 0)
        self.assertEqual(len(tool.calls), 2)
        (s1, e1), (s2, e2) = tool.calls
        external_done = max(p["done_at"] for pid, p in self.fake.prompts.items() if pid.startswith("external-"))
        self.assertGreaterEqual(s2, external_done)
        self.assertEqual(self.fake.requests_between(s1, e1 - 0.01), [])
        self.assertEqual(self.fake.requests_between(s2, e2), [])
        self.assertTrue(any(external_done <= t <= s2 for t in self.frees()))
        log = self.log()
        self.assertIn("FAILED local GPU job extra/backdrop/rig (try 1)", log)
        self.assertIn("cause: out of memory on the card", log)
        self.assertIn("an external ComfyUI prompt started meanwhile (1 running, 0 pending)", log)
        self.assertIn("will be tried again once the ComfyUI queue is empty", log)
        e = self.manifest()["extra/backdrop/rig"]
        self.assertEqual((e["status"], e["tries"]), ("done", 2))
        # Two failures: failed, only its dependants wait.
        shutil.rmtree(self.out)
        tool = FakeTool(self.fake, fail=2)
        self.write([GPU_JOB, dict(PLAIN, after=["extra/backdrop/rig"]),
                    dict(PLAIN, id="extra/other/plain")])
        self.assertEqual(self.run_quiet(self.factory(tool)), 0)
        self.assertEqual(len(tool.calls), 2)
        m = self.manifest()
        self.assertEqual((m["extra/backdrop/rig"]["status"], m["extra/backdrop/rig"]["out_of_memory"]), ("failed", True))
        self.assertNotIn("extra/backdrop/plain", m)
        self.assertEqual(m["extra/other/plain"]["status"], "done")

    def test_order_check_knows_the_local_gpu_tools(self):
        from forge.config import load_config
        from forge.order import check_order, load_order
        order = load_order(self.order)
        [problem] = check_order(order, load_config(None))
        self.assertIn("job extra/backdrop/rig: unknown local GPU tool 'fake_rig'", problem)
        with patch.dict(local_gpu.RUNNERS, {"fake_rig": FakeTool(self.fake)}):
            self.assertEqual(check_order(order, load_config(None)), [])

    def test_out_of_memory_detection(self):
        self.assertTrue(out_of_memory(RuntimeError("CUDA out of memory")))
        self.assertFalse(out_of_memory(RuntimeError("exit 1")))


class LockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_lock_")
        self.lock_dir = os.path.join(self.tmp, "lock")
        self.fake = FakeComfy()
        self.order = os.path.join(self.tmp, "order.json")
        with open(self.order, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "style": {}, "families": [], "tasks": [PLAIN]}, f)
        self.env = dict(os.environ, COMFY_SERVER=self.fake.url, FORGE_PASS_SLEEP="0.02", FORGE_ERROR_SLEEP="0.1",
                        PYTHONDONTWRITEBYTECODE="1", FORGE_LOCK_DIR=self.lock_dir)

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cmd(self, out):
        return [sys.executable, "-m", "forge", "--order", self.order, "--out", os.path.join(self.tmp, out)]

    def test_second_factory_refuses_to_start(self):
        self.fake.delay = 2.0
        first = subprocess.Popen(self.cmd("a"), env=self.env, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 30
            while not self.fake.submitted and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(self.fake.submitted, "the first factory sent nothing")
            holder = gpu_lock.holder(self.lock_dir)
            self.assertEqual(holder["pid"], first.pid)
            res = subprocess.run(self.cmd("b"), env=self.env, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
            self.assertIn("another art-forge process holds the GPU lock (pid %d" % first.pid, res.stdout)
            self.assertEqual(len(self.fake.submitted), 1)
            # A local tool started by hand is refused too.
            with self.assertRaises(gpu_lock.GpuBusy):
                gpu_lock.acquire("by hand", self.lock_dir)
        finally:
            out, _ = first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, out)
        self.assertIsNone(gpu_lock.holder(self.lock_dir))
        res = subprocess.run(self.cmd("b"), env=self.env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_stale_lock_is_taken_over_and_logged(self):
        child = subprocess.Popen([sys.executable, "-c", "import sys, time; sys.path.insert(0, %r)\n"
                                  "from forge import gpu_lock\ngpu_lock.acquire('a run that crashes', %r)\n"
                                  "print('held', flush=True)\ntime.sleep(120)" % (ROOT, self.lock_dir)],
                                 stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "held")
        child.kill()
        child.wait()
        child.stdout.close()
        time.sleep(0.2)
        with open(gpu_lock.lock_path(self.lock_dir), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["pid"], child.pid)  # left behind by the killed process
        res = subprocess.run(self.cmd("a"), env=self.env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("taken over from pid %d (a run that crashes" % child.pid, res.stdout)
        self.assertIn("(gone)", res.stdout)

    def test_reentrant_in_one_process(self):
        with gpu_lock.hold("outer", self.lock_dir):
            with gpu_lock.hold("inner", self.lock_dir):
                self.assertEqual(gpu_lock.holder(self.lock_dir)["what"], "outer")
            self.assertIsNotNone(gpu_lock.holder(self.lock_dir))
        self.assertIsNone(gpu_lock.holder(self.lock_dir))
        self.assertTrue(gpu_lock.lock_dir().startswith(os.environ["FORGE_LOCK_DIR"]))


if __name__ == "__main__":
    unittest.main()
