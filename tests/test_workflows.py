"""The shipped workflows: production ones only, no paid API node, the markers of the derived
ones, and the optional texture branch of TRELLIS.2."""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from forge.comfy_jobs import drop_nodes  # noqa: E402
from forge.factory_core import WORKFLOWS, load_workflow  # noqa: E402
from forge.stages import TRELLIS2_TEXTURE_NODES  # noqa: E402

SHIPPED = {
    "qwen_image_21_base_t2i.json", "qwen_image_21_turbo_t2i.json", "qwen_image_21_turbo_hires.json",
    "qwen_image_21_turbo_inpaint.json", "qwen_image_21_turbo_tile.json", "qwen_edit_2511.json",
    "qwen_edit_character.json", "qwen_edit_view_angles.json", "klein_t2i.json",
    "hunyuan21_shape_paint.json", "hunyuan21_paint_mesh.json", "trellis2_mesh.json",
}


def markers(wf):
    return set(re.findall(r'"(\$[A-Z0-9_]+\$)"', str(wf).replace("'", '"')))


class WorkflowsTest(unittest.TestCase):
    def test_only_production_workflows_ship_and_all_load(self):
        self.assertEqual(set(os.listdir(WORKFLOWS)), SHIPPED)
        for name in SHIPPED:
            with self.subTest(name=name):
                wf, digest = load_workflow(name)
                self.assertEqual(len(digest), 64)
                for node in wf.values():
                    self.assertIn("class_type", node)
                    self.assertIsInstance(node["inputs"], dict)

    def test_derived_markers(self):
        base, _ = load_workflow("qwen_image_21_base_t2i.json")
        self.assertEqual(markers(base) - {"$PREFIX$", "$PREFIX_MASK$"},
                         {"$PROMPT$", "$NEGATIVE$", "$WIDTH$", "$HEIGHT$", "$SEED$"})
        sampler = [n for n in base.values() if n["class_type"] == "KSampler"]
        self.assertEqual(len(sampler), 1)
        self.assertEqual(sampler[0]["inputs"]["steps"], 60)
        trellis, _ = load_workflow("trellis2_mesh.json")
        self.assertEqual(markers(trellis) - {"$PREFIX$"}, {"$IMAGE$", "$SEED$", "$TRIS$"})
        decimate = [n for n in trellis.values() if n["class_type"] == "DecimateMesh"]
        self.assertEqual(decimate[0]["inputs"]["target_face_count"], "$TRIS$")
        self.assertTrue(any(n["class_type"] == "RemeshMesh" for n in trellis.values()))
        paint, _ = load_workflow("hunyuan21_paint_mesh.json")
        self.assertEqual(markers(paint) - {"$PREFIX$", "$MESHNAME$"}, {"$IMAGE$", "$MESH$", "$SEED$", "$TEXTURE$"})
        views = [n for n in paint.values() if n["class_type"] == "Hy3DMultiViewsGenerator"][0]
        self.assertEqual((views["inputs"]["steps"], views["inputs"]["texture_size"]), (10, "$TEXTURE$"))

    def test_trellis_texture_branch_drops_cleanly(self):
        wf, _ = load_workflow("trellis2_mesh.json")
        drop_nodes(wf, list(TRELLIS2_TEXTURE_NODES), "trellis2_mesh.json")
        saves = [n for n in wf.values() if n["class_type"] == "Save3DAdvanced"]
        self.assertEqual(len(saves), 1)
        self.assertFalse(any(n["class_type"] == "BakeTextureFromVoxel" for n in wf.values()))


if __name__ == "__main__":
    unittest.main()
