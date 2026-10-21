"""Smooth normals and the normal map of a GLB without Blender (tools/blender/glb_normals.py)
and the refresh command (forge/normals.py) without a raw mesh, on synthetic GLBs."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import glb_normals as gn  # noqa: E402
import glb_synth  # noqa: E402
from forge import normals  # noqa: E402


def png(rgb):
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def sphere_glb(path, flat=True, normals_attr=True, emissive=True, rings=12, segments=24):
    """A UV sphere (with a cube of hard edges beside it) as one textured primitive: flat (one
    vertex per triangle corner, each with its triangle's normal), or without normals."""
    verts, uvs, tris = [], [], []
    for i in range(rings + 1):
        th = np.pi * i / rings
        for j in range(segments + 1):
            ph = 2 * np.pi * j / segments
            verts.append([np.sin(th) * np.cos(ph), np.cos(th), np.sin(th) * np.sin(ph)])
            uvs.append([j / segments, i / rings])
    for i in range(rings):
        for j in range(segments):
            a = i * (segments + 1) + j
            b = a + segments + 1
            tris += [[a, a + 1, b], [a + 1, b + 1, b]]
    base = len(verts)
    for x in (0, 1):
        for y in (0, 1):
            for z in (0, 1):
                verts.append([3 + x, y, z])
                uvs.append([0.5, 0.5])
    for f in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        q = [base + k for k in f]
        tris += [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]
    pos = np.array(verts, np.float32)
    uv = np.array(uvs, np.float32)
    tri = np.array(tris, np.int64)
    if flat:
        pos, uv = pos[tri.reshape(-1)], uv[tri.reshape(-1)]
        tri = np.arange(len(pos)).reshape(-1, 3)
    fn, _ = gn.face_normals(pos.astype(np.float64), tri)
    nrm = np.repeat(fn, 3, axis=0).astype(np.float32) if flat else None
    blobs, views, accessors = [], [], []

    def add(arr, kind, ct, target):
        data = np.ascontiguousarray(arr).tobytes()
        off = sum(len(b) for b in blobs)
        blobs.append(data + b"\0" * (-len(data) % 4))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": ct, "count": len(arr), "type": kind}
        if kind == "VEC3" and ct == 5126:
            acc.update(min=arr.min(0).tolist(), max=arr.max(0).tolist())
        accessors.append(acc)
        return len(accessors) - 1
    attrs = {"POSITION": add(pos, "VEC3", 5126, 34962), "TEXCOORD_0": add(uv, "VEC2", 5126, 34962)}
    if normals_attr and nrm is not None:
        attrs["NORMAL"] = add(nrm, "VEC3", 5126, 34962)
    ind = add(tri.reshape(-1, 1).astype(np.uint32), "SCALAR", 5125, 34963)
    images = []
    rng = np.random.default_rng(1)
    for name, rgb in (("base", (rng.random((64, 64, 3)) * 255).astype(np.uint8)),
                      ("glow", np.full((64, 64, 3), 200, np.uint8))):
        data = png(rgb)
        off = sum(len(b) for b in blobs)
        blobs.append(data + b"\0" * (-len(data) % 4))
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        images.append({"bufferView": len(views) - 1, "mimeType": "image/png", "name": name})
    mat = {"name": "painted", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
    if emissive:
        mat.update(emissiveTexture={"index": 1}, emissiveFactor=[1, 1, 1])
    binary = b"".join(blobs)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"name": "thing", "mesh": 0}],
           "meshes": [{"name": "thing", "primitives": [{"attributes": attrs, "indices": ind, "material": 0}]}],
           "materials": [mat], "textures": [{"source": 0}, {"source": 1}], "images": images,
           "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(binary)}]}
    glb_synth.write_glb(path, doc, binary)
    return path


class Normals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_normals_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flat_sphere_gets_smooth_normals_and_the_cube_keeps_its_edges(self):
        src = sphere_glb(os.path.join(self.tmp, "flat.glb"))
        self.assertEqual(gn.normals_state(src)[0]["flat_share"], 1.0)
        out = os.path.join(self.tmp, "smooth.glb")
        rep = gn.smooth_glb(src, out)
        prim = rep["primitives"][0]
        self.assertTrue(prim["rewritten"])
        self.assertLess(prim["vertices_after"], prim["vertices_before"] / 2)
        self.assertEqual(prim["after"]["flat_share"], 0.0)
        glb = gn.Glb(out)
        p = glb.doc["meshes"][0]["primitives"][0]
        pos = glb.accessor(p["attributes"]["POSITION"]).astype(np.float64)
        nrm = glb.accessor(p["attributes"]["NORMAL"]).astype(np.float64)
        sphere = pos[:, 0] < 2.0
        # On the sphere the normal is the direction from its centre; on the cube a face's own.
        self.assertGreater(float((nrm[sphere] * pos[sphere] / np.linalg.norm(pos[sphere], axis=1)[:, None]).sum(1).min()),
                           0.97)
        cube = ~sphere
        self.assertTrue(np.allclose(np.abs(nrm[cube]).max(1), 1.0, atol=1e-5))
        self.assertEqual(len(glb.doc["images"]), 2)

    def test_missing_normals_and_the_check(self):
        src = sphere_glb(os.path.join(self.tmp, "none.glb"), flat=False, normals_attr=False)
        self.assertEqual(gn.normals_state(src)[0]["normals"], "missing")
        out = os.path.join(self.tmp, "smooth.glb")
        gn.smooth_glb(src, out)
        check = gn.check_glb(out)
        self.assertEqual(check["flags"], ["material painted has no normal map"])
        glb = gn.Glb(out)
        gn.set_normal_maps(glb, {0: gn.encode_normals(gn.relief_normals(np.zeros((32, 32, 3), np.uint8)))})
        glb.save(out)
        check = gn.check_glb(out)
        self.assertEqual(check["flags"], [])
        self.assertEqual((check["materials"][0]["format"], check["materials"][0]["lossless"]), ("png", True))

    def test_relief_is_flat_on_a_flat_picture_and_tilts_on_a_step(self):
        flat = gn.relief_normals(np.full((64, 64, 3), 128, np.uint8))
        self.assertTrue(np.allclose(flat, [0, 0, 1]))
        step = np.zeros((64, 64, 3), np.uint8)
        step[:, 32:] = 255  # brighter to the right: the slope tilts the normal to -x
        n = gn.relief_normals(step)
        self.assertLess(n[32, 32, 0], -0.05)
        self.assertAlmostEqual(float(n[32, 32, 1]), 0.0, places=4)

    def test_refresh_without_a_raw_mesh_keeps_everything_else(self):
        src = sphere_glb(os.path.join(self.tmp, "flat.glb"))
        out = os.path.join(self.tmp, "out")
        lines = []
        rep = normals.refresh(src, out, emit=lines.append)
        self.assertEqual(rep["flags"], [])
        self.assertFalse(rep["map"]["materials"]["0"]["baked"])
        written = gn.Glb(os.path.join(out, "flat.glb"))
        mat = written.doc["materials"][0]
        self.assertIn("normalTexture", mat)
        self.assertIn("emissiveTexture", mat)
        self.assertEqual(len(written.doc["images"]), 3)
        with open(os.path.join(out, "normals.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "forge-normals")
        # Again on its own output: the map this command wrote is replaced, not added.
        rep2 = normals.refresh(os.path.join(out, "flat.glb"), os.path.join(self.tmp, "again"), emit=lines.append)
        self.assertEqual(rep2["flags"], [])
        self.assertEqual(len(gn.Glb(os.path.join(self.tmp, "again", "flat.glb")).doc["images"]), 3)
        self.assertFalse(rep2["normals"]["primitives"][0]["rewritten"])

    def test_a_skin_and_animation_are_kept(self):
        path = os.path.join(self.tmp, "skin.glb")
        glb_synth.skeleton_glb(path, animation={"name": "wave", "node": "Head", "times": [0.0, 1.0],
                                                "rotations": [[0, 0, 0, 1], [0, 0.38268343, 0, 0.92387953]]})
        before = gn.Glb(path).doc
        out = os.path.join(self.tmp, "skin_out")
        rep = normals.refresh(path, out, make_map=False, emit=lambda *_: None)
        after = gn.Glb(os.path.join(out, "skin.glb")).doc
        self.assertEqual(rep["flags"], [])
        self.assertEqual([n["name"] for n in after["nodes"]], [n["name"] for n in before["nodes"]])
        self.assertEqual(len(after.get("animations", [])), len(before.get("animations", [])))
        self.assertEqual(len(after.get("skins", [])), len(before.get("skins", [])))


if __name__ == "__main__":
    unittest.main()
