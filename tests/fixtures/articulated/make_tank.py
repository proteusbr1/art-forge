"""A synthetic painted mesh for the articulated exporter's smoke test: a tracked vehicle of
boxes (a hull, a narrow turret ring, a turret and a barrel pointing to glTF +Z), one mesh
with UVs and a painted base-colour texture, written as a GLB like the paint stage's.

  <blender_python> make_tank.py --out DIR      (writes DIR/painted_tank.glb)
"""
import argparse
import os
import sys

import bpy
import bmesh
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
os.makedirs(a.out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)

# Blender axes (front -Y): hull 1.2 x 2.0 x 0.5, ring, turret, barrel to the front.
BOXES = (((-0.6, -1.0, 0.0), (0.6, 1.0, 0.5)), ((-0.25, -0.25, 0.5), (0.25, 0.25, 0.56)),
         ((-0.45, -0.5, 0.56), (0.45, 0.5, 0.9)), ((-0.05, -1.4, 0.7), (0.05, -0.5, 0.78)))
bm = bmesh.new()
for lo, hi in BOXES:
    res = bmesh.ops.create_cube(bm, size=1.0)
    for v in res["verts"]:
        v.co.x = lo[0] if v.co.x < 0 else hi[0]
        v.co.y = lo[1] if v.co.y < 0 else hi[1]
        v.co.z = lo[2] if v.co.z < 0 else hi[2]
# Subdivided a little, as a generated mesh is dense.
bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=3, use_grid_fill=True)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
me = bpy.data.meshes.new("tank")
bm.to_mesh(me)
bm.free()
ob = bpy.data.objects.new("tank", me)
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
px[..., 0] = 0.3 + 0.4 * ((xx // 16 + yy // 16) % 2)
px[..., 1] = 0.35
px[..., 2] = 0.2 + 0.002 * yy
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
path = os.path.join(a.out, "painted_tank.glb")
bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                          export_draco_mesh_compression_enable=False)
print("WROTE", path, flush=True)
sys.stdout.flush()
os._exit(0)
