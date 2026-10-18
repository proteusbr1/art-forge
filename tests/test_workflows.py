"""The shipped workflows: production ones only, no paid API node, the markers of the derived
ones, and the optional texture branch of TRELLIS.2."""

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from forge.comfy_jobs import drop_nodes  # noqa: E402
from forge.factory_core import WORKFLOWS, load_workflow  # noqa: E402
from forge.stages import HUNYUAN21_PAINT_NODES, HUNYUAN21_RAW_NODES, TRELLIS2_TEXTURE_NODES  # noqa: E402

SHIPPED = {
    "qwen_image_21_base_t2i.json", "qwen_image_21_turbo_t2i.json", "qwen_image_21_turbo_hires.json",
    "qwen_image_21_turbo_inpaint.json", "qwen_image_21_turbo_tile.json", "qwen_edit_2511.json",
    "qwen_edit_character.json", "qwen_edit_view_angles.json", "klein_t2i.json",
    "hunyuan21_shape_paint.json", "hunyuan21_paint_mesh.json", "trellis2_mesh.json",
}


def json_copy(wf):
    return json.loads(json.dumps(wf))


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
        saves = sorted(nid for nid, n in wf.items() if n["class_type"] == "Save3DAdvanced")
        self.assertEqual(saves, ["403", "405"])  # the reduced mesh and the raw mesh
        self.assertFalse(any(n["class_type"] == "BakeTextureFromVoxel" for n in wf.values()))

    def test_raw_mesh_outputs(self):
        """TRELLIS.2 saves the remeshed shape before the reduction (405) next to the reduced
        one (403); Hunyuan3D 2.1 exports the shape before its face reduction (16)."""
        wf, _ = load_workflow("trellis2_mesh.json")
        self.assertEqual(wf["403"]["inputs"]["model_3d"], ["402", 0])
        self.assertEqual(wf["402"]["inputs"]["mesh"], ["186", 0])
        self.assertEqual(wf["186"]["class_type"], "DecimateMesh")
        self.assertEqual(wf["186"]["inputs"]["mesh"], ["241", 0])
        self.assertEqual(wf["405"]["inputs"]["model_3d"], ["404", 0])
        self.assertEqual(wf["404"]["inputs"]["mesh"], ["241", 0])
        self.assertEqual(wf["241"]["class_type"], "RemeshMesh")
        hy, _ = load_workflow("hunyuan21_shape_paint.json")
        self.assertEqual(hy["16"]["inputs"]["trimesh"], ["15", 0])
        self.assertEqual(hy["15"]["inputs"]["trimesh"], ["4", 0])
        self.assertFalse(hy["15"]["inputs"]["reduce_faces"])
        self.assertTrue(hy["5"]["inputs"]["reduce_faces"])
        # Shape only (the humanoid's mesh stage) and without the raw export (core recipes).
        shape = json_copy(hy)
        drop_nodes(shape, list(HUNYUAN21_PAINT_NODES), "hunyuan21_shape_paint.json")
        self.assertEqual(sorted(shape), ["1", "15", "16", "2", "3", "4", "5", "6"])
        core = json_copy(hy)
        drop_nodes(core, list(HUNYUAN21_RAW_NODES), "hunyuan21_shape_paint.json")
        self.assertNotIn("16", core)


if __name__ == "__main__":
    unittest.main()
