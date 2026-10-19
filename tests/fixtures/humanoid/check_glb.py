"""Reads an exported humanoid GLB back in a fresh Blender (bpy) and prints what the tests
check, as one line "RESULT <json>": bone names, animations and their durations, the rest
height, the textures, and per holding animation the largest distance from each wrist to its
item socket.

  <blender python> check_glb.py --glb MODEL.glb [--model model.json]
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

ap = argparse.ArgumentParser()
ap.add_argument("--glb", required=True)
ap.add_argument("--model", default="")
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
info = json.load(open(a.model, encoding="utf-8")) if a.model else {}

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.render.fps = 24
bpy.ops.import_scene.gltf(filepath=a.glb)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
body = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("body"))
ref = next((o for o in bpy.data.objects if o.name.startswith("item_ref")), None)
zs = [(body.matrix_world @ v.co).z for v in body.data.vertices]
images = {}
normal_map = None
for mat in body.data.materials:
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            images[n.image.name] = list(n.image.size)
        if n.type == "NORMAL_MAP" and n.inputs["Color"].links:
            src = n.inputs["Color"].links[0].from_node
            if src.type == "TEX_IMAGE" and src.image is not None:
                normal_map = list(src.image.size)
base_color = None
for mat in body.data.materials:
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED" and n.inputs["Base Color"].links:
            src = n.inputs["Base Color"].links[0].from_node
            if src.type == "TEX_IMAGE" and src.image is not None:
                base_color = list(src.image.size)


def _fmt(image):
    data = image.packed_file.data[:4] if image.packed_file else b""
    return "png" if data[:4] == b"\x89PNG" else "jpeg" if data[:3] == b"\xff\xd8\xff" else "other"


def _image_above(socket, depth=4):
    """The image node feeding a socket through Separate Color or Normal Map nodes, or None."""
    if not socket.is_linked:
        return None
    node = socket.links[0].from_node
    if node.type == "TEX_IMAGE":
        return node if node.image is not None else None
    if depth == 0:
        return None
    for inp in node.inputs:
        found = _image_above(inp, depth - 1)
        if found is not None:
            return found
    return None


# The material as Blender's importer rebuilt it: per slot the image's format and size.
material = {}
for mat in body.data.materials:
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        continue
    for slot, socket in (("base_color", "Base Color"), ("normal", "Normal"), ("metallic_roughness", "Roughness"),
                         ("emissive", "Emission Color")):
        node = _image_above(bsdf.inputs[socket]) if socket in bsdf.inputs else None
        material[slot] = {"format": _fmt(node.image), "size": list(node.image.size)} if node else None
    material["opacity"] = bsdf.inputs["Alpha"].is_linked
groups = set(g.name for g in body.vertex_groups)
bones = [b.name for b in arm.data.bones]
anims = {}
fps = bpy.context.scene.render.fps
if arm.animation_data:
    for tr in arm.animation_data.nla_tracks:
        tr.mute = True
for act in bpy.data.actions:
    lo, hi = act.frame_range
    anims[act.name] = {"seconds": (hi - lo) / fps}
hands = {}
for clip in info.get("animations", []):
    if not clip.get("hands") or ref is None:
        continue
    act = next((x for x in bpy.data.actions if x.name == clip["name"] or x.name.startswith(clip["name"] + "_")), None)
    if act is None:
        continue
    arm.animation_data.action = act
    sockets = info["classes"][clip["class"]]["sockets"]
    worst = {}
    for i in range(clip["frames"]):
        f = i / clip["fps"] * fps
        bpy.context.scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
        for side, sock in clip["hands"].items():
            wrist = arm.matrix_world @ arm.pose.bones["LeftHand" if side == "left" else "RightHand"].head
            d = (wrist - ref.matrix_world @ Vector(sockets[sock])).length
            worst[side] = max(worst.get(side, 0.0), d)
    hands[clip["name"]] = worst
print("RESULT " + json.dumps({"bones": bones, "groups_match": groups <= set(bones), "animations": anims,
                              "height": max(zs) - min(zs), "floor": min(zs), "images": images,
                              "base_color": base_color, "normal_map": normal_map, "material": material,
                              "hands": hands,
                              "triangles": sum(len(p.vertices) - 2 for p in body.data.polygons)}), flush=True)
sys.stdout.flush()
os._exit(0)
