"""Rigged synthetic figures of different proportions, bulk and facing for the approved-body
tests (tests/test_humanoid_body_smoke.py): the capsule figures of tests/humanoid_synth.py
rigged in the rig tools' style (bones bone_N, three fingers of three bones under each hand,
each capsule skinned to its bone) and painted as make_fixtures.py paints its figure.

  <blender python> make_body_fixtures.py --out FILE.glb [--height 1] [--arms 1] [--legs 1]
      [--bulk 1] [--yaw 0] [--sole 0]
"""
import argparse
import os
import sys

import bpy  # first: mathutils comes with it
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import humanoid_synth as hs  # noqa: E402
import make_fixtures as mf  # noqa: E402

# Bone order (rig tools number depth first) and each role's tail joint (None: an end bone,
# along its parent's line).
ORDER = ["hips", "spine", "chest", "upper_chest", "neck", "head"]
for _s in ("left", "right"):
    ORDER += [_s + "_shoulder", _s + "_upper_arm", _s + "_lower_arm", _s + "_hand"]
for _s in ("left", "right"):
    ORDER += [_s + "_upper_leg", _s + "_lower_leg", _s + "_foot", _s + "_toes"]
PARENT = {"hips": None, "spine": "hips", "chest": "spine", "upper_chest": "chest", "neck": "upper_chest",
          "head": "neck"}
TAIL = {"hips": "spine", "spine": "chest", "chest": "upper_chest", "upper_chest": "neck", "neck": "head", "head": None}
for _s in ("left", "right"):
    PARENT.update({_s + "_shoulder": "upper_chest", _s + "_upper_arm": _s + "_shoulder",
                   _s + "_lower_arm": _s + "_upper_arm", _s + "_hand": _s + "_lower_arm",
                   _s + "_upper_leg": "hips", _s + "_lower_leg": _s + "_upper_leg", _s + "_foot": _s + "_lower_leg",
                   _s + "_toes": _s + "_foot"})
    TAIL.update({_s + "_shoulder": _s + "_upper_arm", _s + "_upper_arm": _s + "_lower_arm",
                 _s + "_lower_arm": _s + "_hand", _s + "_hand": None, _s + "_upper_leg": _s + "_lower_leg",
                 _s + "_lower_leg": _s + "_foot", _s + "_foot": _s + "_toes", _s + "_toes": None})


def build(path, height=1.0, arms=1.0, legs=1.0, bulk=1.0, yaw=0.0, sole=0.0):
    fig = hs.figure(height=height, arms=arms, legs=legs, bulk=bulk, yaw=yaw, sole=sole)
    j = fig["joints"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data = bpy.data.armatures.new("Armature")
    arm = bpy.data.objects.new("Armature", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    name = {}
    for k, role in enumerate(ORDER):
        name[role] = "bone_%d" % k
        eb = data.edit_bones.new(name[role])
        eb.head = tuple(j[role])
        if TAIL[role]:
            eb.tail = tuple(j[TAIL[role]])
        else:
            par = j[PARENT[role]]
            d = np.asarray(j[role]) - np.asarray(par)
            eb.tail = tuple(np.asarray(j[role]) + d / np.linalg.norm(d) * 0.05 * height)
        if PARENT[role]:
            eb.parent = data.edit_bones[name[PARENT[role]]]
    n = len(ORDER)
    for s in ("left", "right"):  # three fingers of three bones: a fan under the hand
        hand = data.edit_bones[name[s + "_hand"]]
        d = (hand.tail - hand.head).normalized()
        for f in range(3):
            parent = hand
            start = hand.tail.copy()
            start.y += (f - 1) * 0.01 * height
            for k in range(3):
                eb = data.edit_bones.new("bone_%d" % n)
                n += 1
                eb.head = start
                eb.tail = start + d * 0.015 * height
                eb.parent = parent
                parent, start = eb, eb.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    me = bpy.data.meshes.new("figure")
    me.from_pydata([tuple(p) for p in fig["points"]], [], [tuple(int(i) for i in t) for t in fig["triangles"]])
    me.update()
    body = bpy.data.objects.new("figure", me)
    bpy.context.scene.collection.objects.link(body)
    groups = {role: body.vertex_groups.new(name=name[role]) for role in ORDER}
    caps = fig["roles"]
    for i, c in enumerate(fig["index"][:, 0]):
        role = caps[c]
        groups[role if role in groups else "chest"].add([i], 1.0, "REPLACE")
    mf._paint(body, os.path.dirname(os.path.abspath(path)))
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    mf._export(os.path.abspath(path), [arm, body])
    print("FIGURE", path, len(fig["points"]), "vertices", flush=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    for k in ("height", "arms", "legs", "bulk"):
        ap.add_argument("--" + k, type=float, default=1.0)
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--sole", type=float, default=0.0)
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    build(a.out, a.height, a.arms, a.legs, a.bulk, a.yaw, a.sole)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
