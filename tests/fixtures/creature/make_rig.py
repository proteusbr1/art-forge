"""A synthetic rigged creature for the creature exporter's smoke test, as the rig tools
deliver one: an armature of a skeleton of tests/creature_skeletons.py (bones bone_0,
bone_1, ...), a mesh of tubes around its bones skinned to them (each vertex to its nearest
bone and that bone's parent), UVs and a painted base-colour texture, facing glTF +Z.

  <blender_python> make_rig.py --kind serpent|beast|insect|hover --out DIR  (DIR/<kind>.glb)
"""
import argparse
import math
import os
import sys

import bpy
import bmesh
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # tests/
import creature_skeletons as sk  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--kind", required=True, choices=("serpent", "beast", "insect", "hover"))
ap.add_argument("--out", required=True)
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
os.makedirs(a.out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
tree, _ = getattr(sk, a.kind)().tree()

arm_data = bpy.data.armatures.new("rig")
arm = bpy.data.objects.new("rig", arm_data)
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
for name, b in tree.items():
    eb = arm_data.edit_bones.new(name)
    eb.head, eb.tail = Vector(b["head"]), Vector(b["tail"])
for name, b in tree.items():
    if b["parent"]:
        arm_data.edit_bones[name].parent = arm_data.edit_bones[b["parent"]]
bpy.ops.object.mode_set(mode="OBJECT")

bm = bmesh.new()
owner = []
for name, b in tree.items():
    h, t = Vector(b["head"]), Vector(b["tail"])
    d = t - h
    ln = d.length
    r = max(0.025, min(0.07, 0.25 * ln))
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=r, radius2=r * 0.85, depth=ln * 1.05)
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    mid = (h + t) / 2.0
    for v in res["verts"]:
        v.co = rot @ v.co + mid
        owner.append(name)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
me = bpy.data.meshes.new("body")
bm.to_mesh(me)
bm.free()
body = bpy.data.objects.new("body", me)
bpy.context.scene.collection.objects.link(body)
for name in tree:
    body.vertex_groups.new(name=name)
for i, v in enumerate(me.vertices):
    name = owner[i]
    parent = tree[name]["parent"]
    w = 1.0
    if parent:
        h = Vector(tree[name]["head"])
        near = max(0.0, 1.0 - (v.co - h).length / 0.1)
        if near > 0:
            body.vertex_groups[parent].add([i], 0.5 * near, "REPLACE")
            w = 1.0 - 0.5 * near
    body.vertex_groups[name].add([i], w, "REPLACE")
mod = body.modifiers.new("rig", "ARMATURE")
mod.object = arm
body.parent = arm
bpy.context.view_layer.objects.active = body
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=0.02)
bpy.ops.object.mode_set(mode="OBJECT")
img = bpy.data.images.new("painted", 64, 64)
px = np.zeros((64, 64, 4), np.float32)
yy, xx = np.mgrid[0:64, 0:64]
px[..., 0] = 0.25 + 0.3 * ((xx // 8 + yy // 8) % 2)
px[..., 1] = 0.45
px[..., 2] = 0.3
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
bpy.ops.object.select_all(action="SELECT")
path = os.path.join(a.out, a.kind + ".glb")
bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_skins=True,
                          export_animations=False, export_draco_mesh_compression_enable=False)
print("WROTE", path, flush=True)
sys.stdout.flush()
os._exit(0)
