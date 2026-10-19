"""The reference figure of the held-item recipe made ready for the humanoid exporter: a
rigged humanoid GLB (a rig tool's output, or a figure the humanoid exporter already wrote)
without the item bones, the item_ref mesh and the animations an exported figure carries,
written again as a rig (one armature, its skinned mesh, no animation).

Runs with a Python that imports bpy (Blender as a module):

  python held_item_figure.py --figure FIGURE.glb --out RIG.glb [--report REPORT.json]

Exit code 2 when the figure has no armature or no skinned mesh.
"""
import argparse
import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forge_blender as fb  # noqa: E402

ITEM_BONES = ("item", "item_l")
ap = argparse.ArgumentParser()
ap.add_argument("--figure", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--report", default="")
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
print("BPY", bpy.app.version_string, flush=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
new = fb.import_glb(a.figure)
arms = [o for o in new if o.type == "ARMATURE"]
if len(arms) != 1:
    fb.fail(2, "the figure must have exactly one armature, it has %d" % len(arms))
arm = arms[0]
removed = {"meshes": [], "bones": [], "actions": []}
for o in list(bpy.data.objects):
    if o.type == "MESH" and o.name.split(".")[0] == "item_ref":
        removed["meshes"].append(o.name)
        bpy.data.objects.remove(o)
skinned = [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == arm or any(
    m.type == "ARMATURE" and m.object == arm for m in o.modifiers))]
if not skinned:
    fb.fail(2, "no mesh is skinned to the figure's armature")
for o in list(bpy.data.objects):
    if o.type == "ARMATURE" or o in skinned:
        continue
    if o.type == "MESH":
        removed["meshes"].append(o.name)
    bpy.data.objects.remove(o)
if arm.animation_data is not None:
    for tr in list(arm.animation_data.nla_tracks):
        arm.animation_data.nla_tracks.remove(tr)
    arm.animation_data.action = None
for act in list(bpy.data.actions):
    removed["actions"].append(act.name)
    bpy.data.actions.remove(act)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
for name in ITEM_BONES:
    eb = arm.data.edit_bones.get(name)
    if eb is not None:
        removed["bones"].append(name)
        arm.data.edit_bones.remove(eb)
bpy.ops.object.mode_set(mode="OBJECT")
for o in skinned:
    for name in ITEM_BONES:
        g = o.vertex_groups.get(name)
        if g is not None:
            o.vertex_groups.remove(g)
bpy.ops.object.select_all(action="DESELECT")
for o in [arm] + skinned:
    o.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.abspath(a.out), export_format="GLB", use_selection=True,
                          export_animations=False, export_skins=True, export_image_format="AUTO",
                          export_draco_mesh_compression_enable=False)
print("FIGURE", json.dumps(removed), flush=True)
if a.report:
    with open(a.report, "w", encoding="utf-8") as f:
        json.dump({"figure": os.path.basename(a.figure), "removed": removed,
                   "bones": len(arm.data.bones), "meshes": [o.name for o in skinned]}, f, indent=1)
fb.finish(0)
