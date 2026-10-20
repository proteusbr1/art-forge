"""A small painted box as a GLB for the iso_tile render's smoke test (run by blender_python):
0.6 x 0.6 x 0.86 units (a tile edge is 1; the box stands 0.7 levels high), an orange image
texture with a darker band, so the albedo pass can be told from the lighting.

    blender_python make_box.py OUT.glb
"""
import os
import sys

import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1)
ob = bpy.context.active_object
ob.scale = (0.6, 0.6, 0.86)
bpy.ops.object.transform_apply(scale=True)
img = bpy.data.images.new("paint", 64, 64)
px = []
for y in range(64):
    for x in range(64):
        px += [0.8, 0.4, 0.1, 1.0] if (y // 8) % 4 else [0.45, 0.2, 0.05, 1.0]
img.pixels = px
img.pack()
mat = bpy.data.materials.new("paint")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
ob.data.materials.append(mat)
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format="GLB")
sys.stdout.flush()
os._exit(0)  # bpy can crash while tearing down at exit
