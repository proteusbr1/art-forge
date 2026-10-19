"""A synthetic painted mesh for the held-item tool's smoke test: a carbine of boxes (stock,
receiver, barrel, pistol grip, magazine, handguard, sight) as a mesh generator might write it
from a side view (front glTF +x, top glTF +y), turned 15 degrees nose up and at the
generator's own scale, one mesh with UVs and a painted base-colour texture, written as a GLB
like the paint stage's.

  <blender_python> make_carbine.py --out DIR      (writes DIR/painted_carbine.glb)
"""
import argparse
import math
import os
import sys

import bpy
import bmesh
import numpy as np
from mathutils import Matrix

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
os.makedirs(a.out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)

# Item space (metres): +x front, +z top, +y left; Blender's axes are the same here.
BOXES = (((0.0, -0.02, 0.05), (0.22, 0.02, 0.14)), ((0.22, -0.025, 0.10), (0.55, 0.025, 0.19)),
         ((0.55, -0.012, 0.14), (0.90, 0.012, 0.165)), ((0.26, -0.015, 0.0), (0.31, 0.015, 0.10)),
         ((0.36, -0.015, 0.02), (0.42, 0.015, 0.10)), ((0.55, -0.022, 0.11), (0.72, 0.022, 0.18)),
         ((0.35, -0.01, 0.19), (0.45, 0.01, 0.22)))
bm = bmesh.new()
for lo, hi in BOXES:
    res = bmesh.ops.create_cube(bm, size=1.0)
    for v in res["verts"]:
        v.co.x = lo[0] if v.co.x < 0 else hi[0]
        v.co.y = lo[1] if v.co.y < 0 else hi[1]
        v.co.z = lo[2] if v.co.z < 0 else hi[2]
bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=3, use_grid_fill=True)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
me = bpy.data.meshes.new("carbine")
bm.to_mesh(me)
bm.free()
# The generator's scale and a 15 degree tilt, nose up (about the item's y axis).
me.transform(Matrix.Translation((0.2, 0.1, -0.3)) @ Matrix.Rotation(math.radians(-15.0), 4, "Y")
             @ Matrix.Scale(0.45, 4) @ Matrix.Translation((-0.45, 0.0, -0.11)))
ob = bpy.data.objects.new("carbine", me)
bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
ob.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=0.02)
bpy.ops.object.mode_set(mode="OBJECT")

img = bpy.data.images.new("painted", 128, 128)
px = np.zeros((128, 128, 4), np.float32)
yy, xx = np.mgrid[0:128, 0:128]
px[..., 0] = 0.25 + 0.3 * ((xx // 16 + yy // 16) % 2)
px[..., 1] = 0.3
px[..., 2] = 0.32 + 0.002 * yy
px[..., 3] = 1.0
img.pixels.foreach_set(px.ravel())
img.pack()
mat = bpy.data.materials.new("painted")
mat.use_nodes = True
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = img
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
me.materials.append(mat)
path = os.path.join(a.out, "painted_carbine.glb")
bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                          export_draco_mesh_compression_enable=False)
print("WROTE", path, flush=True)
sys.stdout.flush()
os._exit(0)
