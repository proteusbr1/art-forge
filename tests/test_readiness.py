"""The environment check (forge.readiness), separate from the order check, against a fake
/object_info: node classes, model files in the loaders' lists, list values of settings,
local weight files, and an unreachable server ("not verified", never ready)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import stages  # noqa: E402
from forge.comfy_server import Server  # noqa: E402
from forge.config import TOOL_WEIGHTS, load_config  # noqa: E402
from forge.factory_core import load_workflow  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.readiness import MODEL_EXTENSIONS, NOT_READY, NOT_VERIFIED, VERIFIED, check_environment  # noqa: E402

GENERIC = os.path.join(HERE, "fixtures", "order_generic.json")
SAMPLERS = ["euler", "dpmpp_2m"]
SCHEDULERS = ["normal", "simple", "karras"]


def object_info():
    """A fake /object_info with every node class of the shipped workflows; inputs naming a
    model file are list inputs holding that file (both list forms of the real server), and
    the sampler and scheduler inputs list a few names."""
    folder = os.path.join(ROOT, "forge", "workflows")
    info = {}
    for n, name in enumerate(sorted(os.listdir(folder))):
        wf, _ = load_workflow(name)
        for node in wf.values():
            cls = info.setdefault(node["class_type"], {"input": {"required": {}, "optional": {}}})
            req = cls["input"]["required"]
            for inp, v in node["inputs"].items():
                if isinstance(v, str) and v.lower().endswith(MODEL_EXTENSIONS):
                    spec = req.setdefault(inp, [[], {}] if n % 2 else ["COMBO", {"options": []}])
                    opts = spec[0] if isinstance(spec[0], list) else spec[1]["options"]
                    if v not in opts:
                        opts.append(v)
                elif inp == "sampler_name":
                    req[inp] = [list(SAMPLERS), {}]
                elif inp == "scheduler":
                    req[inp] = ["COMBO", {"options": list(SCHEDULERS)}]
                elif not isinstance(v, list):
                    req[inp] = ["INT" if isinstance(v, int) else "STRING", {}]
    return info


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_ready_")
        self.config = load_config(None)
        self.order = load_order(GENERIC)
        self.assertEqual(check_order(self.order, self.config), [])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertProblem(self, env, text):
        self.assertTrue(any(text in p for p in env.problems), "no problem with %r in %s" % (text, env.problems))

    def test_verified(self):
        env = check_environment(self.order, self.config, object_info())
        self.assertEqual((env.status, env.problems), (VERIFIED, []))
        self.assertEqual(env.lines()[0], "environment: verified")
        self.assertTrue(any("model files checked" in n for n in env.notes))

    def test_missing_node_class(self):
        info = object_info()
        del info["Hy3D21MeshGenerator"]
        env = check_environment(self.order, self.config, info)
        self.assertEqual(env.status, NOT_READY)
        self.assertEqual(env.problems, ["node class Hy3D21MeshGenerator (workflow hunyuan21_shape_paint.json) "
                                        "is not on the server"])
        self.assertTrue(env.lines()[0].startswith("environment: not ready"))

    def test_model_file_missing_from_a_loader_list(self):
        for form in (0, 1):
            info = object_info()
            spec = info["UNETLoader"]["input"]["required"]["unet_name"]
            opts = spec[0] if isinstance(spec[0], list) else spec[1]["options"]
            opts.remove("flux-2-klein-9b-fp8.safetensors")
            if form:  # the other list form of the server
                info["UNETLoader"]["input"]["required"]["unet_name"] = (
                    ["COMBO", {"options": opts}] if isinstance(spec[0], list) else [opts, {}])
            env = check_environment(self.order, self.config, info)
            self.assertEqual(env.status, NOT_READY)
            self.assertProblem(env, "model file flux-2-klein-9b-fp8.safetensors of workflow klein_t2i.json "
                                    "(node 1 UNETLoader.unet_name) is not in the server's list")

    def test_setting_value_not_in_the_server_list(self):
        order = json.loads(json.dumps(self.order))
        order["families"][2]["assets"][0]["stages"] = {"concept": {"settings": {"sampler": "no_such_sampler"}}}
        self.assertEqual(check_order(order, self.config), [])  # a name: valid for the order check
        env = check_environment(order, self.config, object_info())
        self.assertEqual(env.status, NOT_READY)
        self.assertProblem(env, "value 'no_such_sampler' of workflow klein_t2i.json (node 9 KSamplerSelect.sampler_name)")
        order["families"][2]["assets"][0]["stages"] = {"concept": {"settings": {"sampler": "dpmpp_2m"}}}
        self.assertEqual(check_environment(order, self.config, object_info()).status, VERIFIED)

    def test_missing_local_weight_file(self):
        checkout = os.path.join(self.tmp, "SkinTokens")
        os.makedirs(checkout)
        open(os.path.join(checkout, "demo.py"), "w").close()
        fake = {"rig": {"default": "skintokens", "skippable": True,
                        "options": {"skintokens": {"kind": "local_gpu", "tool": "rig", "tools": ["skintokens_dir"]}}}}
        config = dict(self.config, skintokens_dir=checkout)
        with patch.dict(stages.CATALOGUE, {"concept": fake}):
            env = check_environment(self.order, config, object_info())
            self.assertEqual(env.status, NOT_READY)
            self.assertEqual(len(env.problems), len(TOOL_WEIGHTS["skintokens_dir"]))
            self.assertProblem(env, "skintokens_dir has no weight file experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt")
            for rel in TOOL_WEIGHTS["skintokens_dir"]:
                path = os.path.join(checkout, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
            self.assertEqual(check_environment(self.order, config, object_info()).status, VERIFIED)
            # A tool that is not set at all.
            env = check_environment(self.order, self.config, object_info())
            self.assertProblem(env, "skintokens_dir is not set in the configuration")
        # The UniRig weights are found under any snapshot of the Hugging Face cache.
        hf = os.path.join(self.tmp, "hf")
        config = dict(self.config, unirig_hf=hf)
        os.makedirs(hf)
        from forge.config import missing_weights
        self.assertEqual(len(missing_weights(config, ["unirig_hf"])), 3)
        for rel in TOOL_WEIGHTS["unirig_hf"]:
            path = os.path.join(hf, *rel.replace("*", "0123abc").split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()
        self.assertEqual(missing_weights(config, ["unirig_hf"]), [])

    def test_unreachable_server_is_not_verified(self):
        env = check_environment(self.order, self.config, Server("http://127.0.0.1:1"))
        self.assertEqual(env.status, NOT_VERIFIED)
        self.assertNotEqual(env.status, VERIFIED)
        self.assertIn("local validation done, environment not checked", env.lines()[0])
        self.assertEqual(check_environment(self.order, self.config, None).status, NOT_VERIFIED)
        # Local problems are still listed, the result is still "not verified".
        fake = {"review": {"default": "sheet", "skippable": False,
                           "options": {"sheet": {"kind": "local_gpu", "tools": ["godot"]}}}}
        with patch.dict(stages.CATALOGUE, {"concept": fake}):
            env = check_environment(self.order, self.config, None)
        self.assertEqual(env.status, NOT_VERIFIED)
        self.assertTrue(any("godot is not set" in p for p in env.problems))


class CheckCommandTest(unittest.TestCase):
    """-Check (python -m forge --check) prints both results and sends nothing but
    GET /object_info."""

    def setUp(self):
        self.fake = FakeComfy()
        self.tmp = tempfile.mkdtemp(prefix="forge_check_")

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, server, order=GENERIC):
        cmd = [sys.executable, "-m", "forge", "--order", order, "--out", os.path.join(self.tmp, "out"),
               "--check", "--server", server]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ROOT,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return res.returncode, res.stdout.splitlines()

    def test_two_results(self):
        self.fake.object_info = object_info()
        code, lines = self.check(self.fake.url)
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[0].startswith("order: valid"), lines)
        self.assertEqual(lines[1], "environment: verified")
        self.assertEqual([(m, p) for _, m, p in self.fake.requests], [("GET", "/object_info")])
        self.assertEqual(self.fake.submitted, [])
        del self.fake.object_info["SaveImage"]
        code, lines = self.check(self.fake.url)
        self.assertEqual(code, 1)
        self.assertTrue(lines[1].startswith("environment: not ready"), lines)
        self.assertIn("  problem: node class SaveImage", "\n".join(lines))
        code, lines = self.check("http://127.0.0.1:1")
        self.assertEqual(code, 3)
        self.assertTrue(lines[0].startswith("order: valid"))
        self.assertTrue(lines[1].startswith("environment: not verified (server http://127.0.0.1:1 did not answer"), lines)
        bad = os.path.join(self.tmp, "bad.json")
        with open(GENERIC, encoding="utf-8") as f:
            order = json.load(f)
        order["families"][1]["assets"][0]["stages"] = {"mesh": {"settings": {"shape_steps": 0}}}
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(order, f)
        code, lines = self.check(self.fake.url, bad)
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], "order: 1 problem(s) in %s" % bad)
        self.assertIn("shape_steps", lines[1])
        self.assertEqual(lines[-1], "environment: not verified (the order has problems)")


if __name__ == "__main__":
    unittest.main()
