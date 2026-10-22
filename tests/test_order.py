"""Tests of forge.order: family defaults merged into pieces, input paths and the order check."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from forge import stages  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_core import load_workflow  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402

STYLE = {"global": "Plain test style.", "view_object": "Single object, plain light background.",
         "view_prop": "Single prop, plain light background."}


def every_class():
    """A fake /object_info answer with every node class of the shipped workflows."""
    folder = os.path.join(os.path.dirname(HERE), "forge", "workflows")
    classes = {}
    for name in os.listdir(folder):
        wf, _ = load_workflow(name)
        for node in wf.values():
            classes[node["class_type"]] = {}
    return classes


class OrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_order_")
        self.config = load_config(None)
        os.makedirs(os.path.join(self.tmp, "refs"))
        with open(os.path.join(self.tmp, "refs", "crate.png"), "wb") as f:
            f.write(b"not really a picture")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, order, name="order.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return path

    def order(self, piece=None, family=None):
        fam = {"id": "things", "recipe": "object_3d",
               "assets": [dict({"id": "crate", "brief": "A wooden crate."}, **(piece or {}))]}
        fam.update(family or {})
        return {"version": 1, "style": dict(STYLE), "families": [fam]}

    def problems(self, piece=None, family=None, server=None, config=None):
        order = load_order(self.write(self.order(piece, family)))
        return check_order(order, config or self.config, server)

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    # --- load_order
    def test_family_defaults_merge_into_pieces(self):
        order = load_order(self.write({"version": 1, "style": STYLE, "families": [
            {"id": "things", "recipe": "object_3d", "variants": 3, "params": {"tris": 9000, "texture": 1024},
             "review": {"pitch": 60, "directions": 8}, "skip": ["x"],
             "assets": [{"id": "a", "brief": "A."},
                        {"id": "b", "brief": "B.", "recipe": "prop", "variants": 1, "params": {"tris": 500},
                         "review": {"pitch": 30}, "skip": []}]}]}))
        a, b = order["families"][0]["assets"]
        self.assertEqual((a["recipe"], a["variants"], a["params"], a["skip"]),
                         ("object_3d", 3, {"tris": 9000, "texture": 1024}, ["x"]))
        self.assertEqual(a["review"], {"pitch": 60, "directions": 8})
        self.assertEqual((b["recipe"], b["variants"], b["skip"]), ("prop", 1, []))
        self.assertEqual(b["params"], {"tris": 500, "texture": 1024})
        self.assertEqual(b["review"], {"pitch": 30, "directions": 8})

    def test_relative_inputs_resolve_against_the_order_file(self):
        absolute = os.path.join(self.tmp, "refs", "crate.png")
        path = self.write({"version": 1, "style": STYLE, "families": [
            {"id": "things", "recipe": "object_3d", "inputs": {"reference": "refs/crate.png"},
             "assets": [{"id": "a", "brief": "A."}, {"id": "b", "brief": "B.", "inputs": {"reference": absolute}}]}],
            "tasks": [{"id": "extra/plain", "workflow": "qwen_image_21_turbo_t2i.json",
                       "values": {"$PROMPT$": "x", "$NEGATIVE$": "", "$SEED$": 1,
                                  "$IMAGE$": {"file": "refs/crate.png"}}}]})
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(os.path.dirname(HERE))  # the working folder must not matter
        order = load_order(path)
        a, b = order["families"][0]["assets"]
        self.assertEqual(a["inputs"]["reference"], os.path.normpath(absolute))
        self.assertEqual(b["inputs"]["reference"], absolute)
        self.assertEqual(order["tasks"][0]["values"]["$IMAGE$"]["file"], os.path.normpath(absolute))
        # Another base folder (the launcher's frozen copy of the order) resolves the same way.
        copy = os.path.join(self.tmp, "program")
        os.makedirs(copy)
        shutil.copy(path, os.path.join(copy, "order.json"))
        again = load_order(os.path.join(copy, "order.json"), self.tmp)
        self.assertEqual(again["families"][0]["assets"][0]["inputs"]["reference"], os.path.normpath(absolute))

    # --- check_order
    def test_valid_order_has_no_problems(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems({"variants": 2, "params": {"tris": 9000, "texture": 1024},
                                        "inputs": {"reference": "refs/crate.png"}}, server=every_class()), [])

    def test_unknown_recipe(self):
        self.assertProblem(self.problems({"recipe": "spaceship"}), "unknown recipe 'spaceship'")

    def test_missing_stage_option(self):
        # The core recipes have few options per stage (concept, mesh, paint); material has none.
        self.assertProblem(self.problems({"stages": {"rig": "unirig"}}), "has no stage 'rig'")
        self.assertProblem(self.problems({"stages": {"mesh": "unirig"}}), "stage mesh of recipe object_3d has no option 'unirig'")
        # mesh trellis2 (2026-10-22) needs its own texture or hunyuan_concept, not the Paint inside a Hunyuan3D job
        self.assertProblem(self.problems({"stages": {"mesh": "trellis2"}}), "mesh trellis2 needs paint")
        self.assertEqual(self.problems({"stages": {"mesh": "trellis2", "paint": "trellis2_texture"}}), [])
        self.assertEqual(self.problems({"stages": {"mesh": "hunyuan21", "concept": "klein"}}), [])
        fake = {"mesh": {"default": "a", "skippable": False, "options": {"a": {"kind": "comfy"}}}}
        with patch.dict(stages.CATALOGUE, {"object_3d": fake}):
            self.assertProblem(self.problems({"stages": {"mesh": "b"}}), "stage mesh of recipe object_3d has no option 'b'")
            self.assertEqual(self.problems({"stages": {"mesh": "a"}}), [])

    def test_invalid_skip(self):
        self.assertProblem(self.problems({"skip": ["rig"]}), "stage 'rig' cannot be skipped")
        fake = {"paint": {"default": "a", "skippable": True, "options": {"a": {"kind": "comfy"}}},
                "mesh": {"default": "m", "skippable": False, "options": {"m": {"kind": "comfy"}}}}
        with patch.dict(stages.CATALOGUE, {"object_3d": fake}):
            self.assertEqual(self.problems({"skip": ["paint"]}), [])
            self.assertProblem(self.problems({"skip": ["mesh"]}), "stage 'mesh' cannot be skipped")
        self.assertProblem(self.problems({"skip": "paint"}), "skip must be a list")

    def test_zero_variants(self):
        self.assertProblem(self.problems({"variants": 0}), "variants must be a whole number of at least 1")
        self.assertProblem(self.problems({}, {"variants": 1.5}), "variants must be")
        self.assertProblem(self.problems({"variants": True}), "variants must be")

    def test_params_types(self):
        self.assertProblem(self.problems({"params": {"tris": "many"}}), "param tris must be a whole number")
        self.assertProblem(self.problems({"params": {"texture": 0}}), "param texture must be")
        self.assertProblem(self.problems({"params": {"colour": 3}}), "unknown param 'colour'")
        self.assertProblem(self.problems({"params": {"height": 1.8}}), "param 'height' is not used by recipe object_3d")

    def test_missing_input_file(self):
        self.assertProblem(self.problems({"inputs": {"reference": "refs/missing.png"}}), "input reference not found")
        self.assertProblem(self.problems({"inputs": {"clips": "refs/crate.png"}}), "input 'clips' is not used")

    def test_missing_input_file_of_a_raw_task(self):
        order = self.order()
        order["tasks"] = [{"id": "extra/plain", "workflow": "qwen_image_21_turbo_t2i.json",
                           "values": {"$PROMPT$": "x", "$NEGATIVE$": "", "$SEED$": 1,
                                      "$IMAGE$": {"file": "refs/gone.png"}}}]
        problems = check_order(load_order(self.write(order)), self.config)
        self.assertProblem(problems, "input file not found")

    def test_tool_missing_from_the_configuration(self):
        fake = {"review": {"default": "sheet", "skippable": False,
                           "options": {"sheet": {"kind": "local_gpu", "tools": ["godot"]}}}}
        with patch.dict(stages.CATALOGUE, {"object_3d": fake}):
            self.assertProblem(self.problems(), "godot is not set in the configuration")
            config = dict(self.config, godot=os.path.join(self.tmp, "no-godot.exe"))
            self.assertProblem(self.problems(config=config), "godot points to a missing path")
            config = dict(self.config, godot=os.path.join(self.tmp, "refs", "crate.png"))
            self.assertEqual(self.problems(config=config), [])
            # A review camera is checked only for recipes with a review stage.
            self.assertProblem(self.problems({"review": {"pitch": "high"}}, config=config), "review pitch must be")
        self.assertProblem(self.problems({"review": {"pitch": 60}}), "has no review stage")

    def test_node_class_missing_from_the_server(self):
        info = every_class()
        del info["Hy3D21MeshGenerator"]
        problems = self.problems(server=info)
        self.assertEqual(problems, ["workflow hunyuan21_shape_paint.json uses node class Hy3D21MeshGenerator, "
                                    "which the server does not have"])

    def test_style_text_a_recipe_needs(self):
        order = self.order()
        del order["style"]["view_object"]
        problems = check_order(load_order(self.write(order)), self.config)
        self.assertProblem(problems, "missing key 'view_object'")

    def test_generic_sample_order_is_valid(self):
        order = load_order(os.path.join(HERE, "fixtures", "order_generic.json"))
        self.assertEqual(check_order(order, self.config, every_class()), [])

    def test_brief_required_and_version(self):
        order = self.order({"brief": ""})
        order["version"] = 2
        problems = check_order(load_order(self.write(order)), self.config)
        self.assertProblem(problems, "brief is required")
        self.assertProblem(problems, "version must be 1")


class ConfigTest(unittest.TestCase):
    def test_keys_defaults_and_relative_paths(self):
        tmp = tempfile.mkdtemp(prefix="forge_config_")
        try:
            path = os.path.join(tmp, "forge.local.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"comfy_url": "http://127.0.0.1:9999", "godot": "tools/godot.exe", "_note": "x"}, f)
            cfg = load_config(path)
            self.assertEqual(set(cfg), {"comfy_url", "python", "blender_python", "unirig_dir", "unirig_venv",
                                        "unirig_hf", "unirig_shim", "skintokens_dir", "skintokens_python",
                                        "godot", "bake_device", "gpu_wait", "gpu_busy_mb", "gpu_used_busy_mb",
                                        "queue_wait_warn", "hymotion_python", "hymotion_dir", "hymotion_model",
                                        "hymotion_text_encoder", "hymotion_clip", "hymotion_pythonpath"})
            self.assertEqual((cfg["gpu_wait"], cfg["gpu_busy_mb"], cfg["gpu_used_busy_mb"], cfg["queue_wait_warn"]),
                             (300, 2048, 4096, 3600))
            self.assertEqual(cfg["bake_device"], "GPU")
            self.assertEqual(cfg["comfy_url"], "http://127.0.0.1:9999")
            self.assertEqual(cfg["godot"], os.path.join(tmp, "tools", "godot.exe"))
            # A copy of the file elsewhere (the launcher's snapshot) resolves against the
            # folder it is told, as the original would.
            copy = os.path.join(tmp, "program")
            os.makedirs(copy)
            shutil.copy(path, os.path.join(copy, "forge.local.json"))
            self.assertEqual(load_config(os.path.join(copy, "forge.local.json"))["godot"],
                             os.path.join(copy, "tools", "godot.exe"))
            self.assertEqual(load_config(os.path.join(copy, "forge.local.json"), tmp)["godot"],
                             os.path.join(tmp, "tools", "godot.exe"))
            self.assertIsNone(cfg["unirig_dir"])
            self.assertEqual(load_config(os.path.join(tmp, "absent.json"))["comfy_url"], "http://127.0.0.1:8188")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"godott": "x"}, f)
            with self.assertRaisesRegex(ValueError, "unknown keys"):
                load_config(path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"bake_device": "cpu"}, f)
            self.assertEqual(load_config(path)["bake_device"], "CPU")  # a value, not a path
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"bake_device": "TPU"}, f)
            with self.assertRaisesRegex(ValueError, "bake_device"):
                load_config(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
