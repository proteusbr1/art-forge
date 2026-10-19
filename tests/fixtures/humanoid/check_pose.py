"""The legs of an exported humanoid GLB, for the pose tests (run by blender_python):

  <blender python> check_pose.py <figure.glb> <clip> <out.json>

Writes, at rest and at every frame of the clip: the knee angle of each leg (degrees between
hip -> knee and knee -> ankle, the bones' heads: the joints) and how far each ankle is from
under its hip across the ground (metres)."""
import json
import math
import os
import sys

import bpy

glb, clip, out = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:4]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
mw = arm.matrix_world
LEGS = {s: (s + "UpperLeg", s + "LowerLeg", s + "Foot") for s in ("Left", "Right")}


def legs():
    out = {}
    for s, (h, k, a) in LEGS.items():
        hip, knee, ankle = (mw @ arm.pose.bones[n].head for n in (h, k, a))
        bend = math.degrees((knee - hip).angle(ankle - knee))
        out[s] = {"knee_deg": round(bend, 2), "ankle_off_m": round((ankle - hip).xy.length, 4),
                  "hip_z": round(hip.z, 4), "ankle_z": round(ankle.z, 4)}
    return out


arm.animation_data_create()
for t in arm.animation_data.nla_tracks:
    t.mute = True
arm.animation_data.action = None
for pb in arm.pose.bones:
    pb.matrix_basis.identity()
bpy.context.view_layer.update()
res = {"rest": legs(), "frames": []}
act = next(a for a in bpy.data.actions if a.name == clip or a.name.startswith(clip + "_") or a.name.startswith(clip))
arm.animation_data.action = act
first, last = (int(x) for x in act.frame_range)
for f in range(first, last + 1, max(1, (last - first) // 6)):
    bpy.context.scene.frame_set(f)
    bpy.context.view_layer.update()
    res["frames"].append(legs())
with open(out, "w", encoding="utf-8") as fh:
    json.dump(res, fh, indent=1)
sys.stdout.flush()
os._exit(0)
