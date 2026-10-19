"""Synthetic humanoid fixtures for the local tools' tests: a capsule figure rigged in the rig
tools' style (bones bone_N, five finger chains per hand), its raw high-resolution mesh, a
held item "box" with its socket file, and a motion library with an idle pose holding the
box and an 8-frame walk.

  python make_fixtures.py --data DIR        the library and the socket file (no bpy needed)
  <blender python> make_fixtures.py --glbs DIR
                                            humanoid.glb (rigged, painted, about 12k
                                            triangles), humanoid_high.glb (the same figure,
                                            unrigged, finer and with surface relief) and
                                            box.glb
  <blender python> make_fixtures.py --figure FILE [--yaw DEG] [--skew]
                                            only the rigged figure, turned by DEG about the
                                            vertical (90: it faces +x) and, with --skew, its
                                            bones' tails off their children (as a rig tool
                                            whose joints carry their own orientations)

library.json and items/box.json next to this file are its --data output (committed); the
GLBs are made on the first test run, under local/.
The figure is 1 unit tall (not metres: the exporter scales it to --height), faces -y and
stands on z = 0. Body coordinates of the library are (side + = left, forward, up) in body
heights; in this figure's world they are (x, -y, z).
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The canonical joints at rest, in body coordinates (body heights).
REST = {
    "pelvis": (0.0, 0.0, 0.50), "l_hip": (0.06, 0.0, 0.50), "r_hip": (-0.06, 0.0, 0.50),
    "spine": (0.0, 0.0, 0.62), "chest": (0.0, 0.0, 0.72), "neck": (0.0, 0.0, 0.82),
    "head": (0.0, 0.0, 0.88), "head_top": (0.0, 0.0, 1.0),
    "l_collar": (0.03, 0.0, 0.80), "l_shoulder": (0.10, 0.0, 0.80), "l_elbow": (0.14, 0.0, 0.62),
    "l_wrist": (0.18, 0.0, 0.45), "l_hand": (0.19, 0.0, 0.39),
    "r_collar": (-0.03, 0.0, 0.80), "r_shoulder": (-0.10, 0.0, 0.80), "r_elbow": (-0.14, 0.0, 0.62),
    "r_wrist": (-0.18, 0.0, 0.45), "r_hand": (-0.19, 0.0, 0.39),
    "l_knee": (0.07, 0.0, 0.28), "l_ankle": (0.075, 0.0, 0.05), "l_toe": (0.075, 0.10, 0.01),
    "r_knee": (-0.07, 0.0, 0.28), "r_ankle": (-0.075, 0.0, 0.05), "r_toe": (-0.075, 0.10, 0.01),
}
CANON = list(REST)

BOX = {"name": "box", "class": "box", "length_m": 0.4,
       "sockets": {"grip": [0.0, 0.0, 0.0], "guard": [0.25, 0.0, 0.0]},
       "box": [[-0.10, -0.04, -0.06], [0.30, 0.04, 0.06]]}

HOLDS = {"box": {"follow": "chest", "socket": "grip", "at": [-0.07, 0.16, 0.60], "point": [1.0, 0.5, 0.0],
                 "hands": {"right": "grip", "left": "guard"}, "arms_from_rest": True}}


def _r(p):
    return [round(float(x), 5) for x in p]


def _frame(changes=None):
    j = {k: list(v) for k, v in REST.items()}
    for k, v in (changes or {}).items():
        j[k] = list(v)
    return {k: _r(v) for k, v in j.items()}


def _move(j, names, d):
    for n in names:
        j[n] = (j[n][0] + d[0], j[n][1] + d[1], j[n][2] + d[2])


UPPER = ("spine", "chest", "neck", "head", "head_top", "l_collar", "l_shoulder", "l_elbow", "l_wrist", "l_hand",
         "r_collar", "r_shoulder", "r_elbow", "r_wrist", "r_hand")


def idle_frames(n=10):
    out = []
    for f in range(n):
        j = dict(REST)
        breath = 0.004 * math.sin(2 * math.pi * f / n)
        _move(j, ("chest", "neck", "head", "head_top") + UPPER[5:], (0.0, 0.0, breath))
        out.append(_frame(j))
    return out


def walk_frames():
    """8 frames of one gait cycle walked in place: the stance foot slides back at the travel
    speed (0.04 body heights a frame, 0.32 a cycle), so it stands still on the ground while
    the body moves forward."""
    stance = [0.08, 0.04, 0.0, -0.04, -0.08]
    swing = [(-0.04, 0.03), (0.0, 0.05), (0.04, 0.03)]
    track = [(f, 0.0) for f in stance] + swing  # forward, lift; frame 0 = left heel strike
    out = []
    for f in range(8):
        j = dict(REST)
        for side, shift, sx in (("l", 0, 1), ("r", 4, -1)):
            fwd, lift = track[(f + shift) % 8]
            ank = (0.075 * sx, fwd, 0.05 + lift)
            j[side + "_ankle"] = ank
            j[side + "_toe"] = (ank[0], ank[1] + 0.10, max(0.01, ank[2] - 0.04))
            j[side + "_knee"] = (0.07 * sx, fwd * 0.5 + 0.02, 0.28 + lift * 0.5)
            # The arms swing against the legs.
            j[side + "_elbow"] = (REST[side + "_elbow"][0], -fwd * 0.3, REST[side + "_elbow"][2])
            j[side + "_wrist"] = (REST[side + "_wrist"][0], -fwd * 0.6, REST[side + "_wrist"][2])
            j[side + "_hand"] = (REST[side + "_hand"][0], -fwd * 0.7, REST[side + "_hand"][2])
        bob = 0.006 * math.cos(4 * math.pi * f / 8) - 0.025  # knees a little bent
        _move(j, ("pelvis", "l_hip", "r_hip") + UPPER, (0.0, 0.0, bob))
        out.append(_frame(j))
    return out


def library():
    return {
        "format": "forge-motion-library", "version": 1, "canon": CANON, "holds": HOLDS,
        "anims": {
            "idle": {"kind": "idle", "fps": 20.0, "loop": True, "holds": "box", "feet": "clip",
                     "pelvis_stand": REST["pelvis"][2], "stance": {"left": "all", "right": "all"},
                     "source": "synthetic breathing", "frames": idle_frames()},
            "walk": {"kind": "walk", "fps": 8.0, "loop": True, "holds": None, "feet": "walk",
                     "pelvis_stand": REST["pelvis"][2], "travel": [0.0, 0.32],
                     "stance": {"left": [0, 1, 2, 3, 4], "right": [4, 5, 6, 7, 0]},
                     "source": "synthetic gait cycle", "frames": walk_frames()},
        },
    }


def write_data(folder):
    os.makedirs(os.path.join(folder, "items"), exist_ok=True)
    for path, data in ((os.path.join(folder, "library.json"), library()),
                       (os.path.join(folder, "items", "box.json"), BOX)):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
            f.write("\n")


# ---- the GLBs (bpy) ---------------------------------------------------------------------

def body_to_world(p):
    return (p[0], -p[1], p[2])


# name, parent, head joint or point, tail joint or point, radius of its part (0: no part)
def skeleton():
    J = REST
    bones = [
        ("bone_0", None, (0, 0, 0.47), (0, 0, 0.53), 0.085),
        ("bone_1", "bone_0", (0, 0, 0.53), J["spine"], 0.085),
        ("bone_2", "bone_1", J["spine"], J["chest"], 0.09),
        ("bone_3", "bone_2", J["chest"], (0, 0, 0.80), 0.095),
        ("bone_4", "bone_3", J["neck"], J["head"], 0.035),
        ("bone_5", "bone_4", J["head"], J["head_top"], 0.0),
    ]
    n = 6
    hands = {}
    for s in ("l", "r"):
        names = ["bone_%d" % (n + k) for k in range(4)]
        n += 4
        pts = [J[s + "_collar"], J[s + "_shoulder"], J[s + "_elbow"], J[s + "_wrist"], J[s + "_hand"]]
        radii = (0.04, 0.035, 0.03, 0.025)
        parent = "bone_3"
        for k in range(4):
            bones.append((names[k], parent, pts[k], pts[k + 1], radii[k]))
            parent = names[k]
        hands[s] = names[3]
    for s in ("l", "r"):
        names = ["bone_%d" % (n + k) for k in range(4)]
        n += 4
        pts = [J[s + "_hip"], J[s + "_knee"], J[s + "_ankle"],
               (J[s + "_ankle"][0], 0.05, 0.015), J[s + "_toe"]]
        radii = (0.05, 0.04, 0.03, 0.025)
        parent = "bone_0"
        for k in range(4):
            bones.append((names[k], parent, pts[k], pts[k + 1], radii[k]))
            parent = names[k]
    for s in ("l", "r"):  # five fingers of three bones under each hand, no part of their own
        hand = J[s + "_hand"]
        sx = 1 if s == "l" else -1
        for f in range(5):
            fwd = -0.02 + 0.01 * f
            pts = [(hand[0], fwd, hand[2]), (hand[0] + 0.004 * sx, fwd, hand[2] - 0.018),
                   (hand[0] + 0.008 * sx, fwd, hand[2] - 0.034), (hand[0] + 0.012 * sx, fwd, hand[2] - 0.048)]
            parent = hands[s]
            for k in range(3):
                name = "bone_%d" % n
                n += 1
                bones.append((name, parent, pts[k], pts[k + 1], 0.0))
                parent = name
    return bones


def _parts(fine):
    """(bone, centre, axis, half length, radius) of each ellipsoid of the figure."""
    import mathutils
    parts = []
    for name, parent, h, t, r in skeleton():
        if r <= 0:
            continue
        a, b = mathutils.Vector(body_to_world(h)), mathutils.Vector(body_to_world(t))
        parts.append((name, (a + b) / 2, (b - a).normalized(), (b - a).length / 2 + r * 0.6, r))
    head = mathutils.Vector(body_to_world((0, 0, 0.93)))
    parts.append(("bone_5", head, mathutils.Vector((0, 0, 1)), 0.075, 0.065))
    return parts


def _figure_mesh(name, fine):
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
    bm = bmesh.new()
    tag = bm.verts.layers.int.new("owner")  # creation order is not the vertex order
    names = []
    u, v = (64, 32) if fine else (24, 12)
    for bone, c, axis, half, r in _parts(fine):
        rot = Vector((0, 0, 1)).rotation_difference(axis).to_matrix().to_4x4()
        m = Matrix.Translation(c) @ rot @ Matrix.Diagonal((r, r, half, 1.0))
        bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=1.0, matrix=m)
        names.append(bone)
        for vert in bm.verts:
            if vert[tag] == 0:
                vert[tag] = len(names)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if fine:
        # Surface relief only the fine mesh has: ridges around the body.
        for vert in bm.verts:
            vert.co += vert.normal * 0.004 * math.sin(vert.co.z * 220.0)
    owner = [names[vert[tag] - 1] for vert in bm.verts]
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob, owner


def _paint(ob, folder):
    """A 256x256 texture painted per face: skin on the head, hair on its top, cloth with
    stripes elsewhere."""
    import bpy
    import numpy as np
    from PIL import Image, ImageDraw
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    w = h = 256
    img = Image.new("RGB", (w, h), (90, 102, 128))
    draw = ImageDraw.Draw(img)
    uv = ob.data.uv_layers.active.data
    for poly in ob.data.polygons:
        z = poly.center.z
        if z > 0.95:
            col = (60, 36, 20)
        elif z > 0.865:
            col = (217, 158, 122)
        else:
            k = 1.0 if int(z * 60) % 2 else 0.8
            col = (int(90 * k), int(102 * k), int(128 * k))
        draw.polygon([(uv[i].uv[0] * w, (1 - uv[i].uv[1]) * h) for i in poly.loop_indices], fill=col)
    path = os.path.join(folder, "humanoid_paint.png")
    img.save(path)
    image = bpy.data.images.load(path)
    image.pack()
    mat = bpy.data.materials.new("figure")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    ob.data.materials.append(mat)
    del np


def _export(path, objects):
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_skins=True,
                              export_animations=False)


def build_glbs(folder):
    import bpy
    folder = os.path.abspath(folder)
    os.makedirs(folder, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm, body, bones = _rigged(folder)
    _export(os.path.join(folder, "humanoid.glb"), [arm, body])
    _rest_of_glbs(folder, bones, body)


def build_figure(path, yaw=0.0, skew=False):
    """The rigged figure alone, turned by yaw degrees about the vertical; skew: every bone's
    tail turned 60 degrees off its child (about a sideways axis, alternating), the length kept."""
    import bpy
    from mathutils import Matrix, Vector
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm, body, bones = _rigged(folder)
    if skew:
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        for k, eb in enumerate(arm.data.edit_bones):
            d = eb.tail - eb.head
            side = d.cross(Vector((0, 0, 1)))
            if side.length < 1e-6:
                side = Vector((1, 0, 0))
            eb.tail = eb.head + Matrix.Rotation(math.radians(60 if k % 2 else -60), 3, side.normalized()) @ d
            eb.roll = math.radians(37 * k)
        bpy.ops.object.mode_set(mode="OBJECT")
    if yaw:
        arm.matrix_world = Matrix.Rotation(math.radians(yaw), 4, "Z") @ arm.matrix_world
        bpy.ops.object.select_all(action="DESELECT")
        arm.select_set(True)
        body.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    _export(path, [arm, body])
    print("FIGURE", path, "yaw", yaw, "skew", skew, flush=True)


def _rigged(folder):
    import bpy
    from mathutils import Vector
    # The rigged game mesh.
    arm_data = bpy.data.armatures.new("Armature")
    arm = bpy.data.objects.new("Armature", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bones = skeleton()
    for name, parent, h, t, r in bones:
        eb = arm_data.edit_bones.new(name)
        eb.head = body_to_world(h)
        eb.tail = body_to_world(t)
        if parent:
            eb.parent = arm_data.edit_bones[parent]
            eb.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    body, owner = _figure_mesh("figure", fine=False)
    parent_of = {b[0]: b[1] for b in bones}
    heads = {b[0]: Vector(body_to_world(b[2])) for b in bones}
    radius = {b[0]: b[4] for b in bones}
    groups = {}
    for name, *_ in bones:
        groups[name] = body.vertex_groups.new(name=name)
    for vi, vert in enumerate(body.data.vertices):
        b = owner[vi]
        p = parent_of.get(b)
        d = (vert.co - heads[b]).length
        reach = max(radius[b], 0.03) * 1.5
        share = 0.5 * max(0.0, 1.0 - d / reach) if p and p in groups else 0.0
        groups[b].add([vi], 1.0 - share, "REPLACE")
        if share > 0:
            groups[p].add([vi], share, "REPLACE")
    _paint(body, folder)
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return arm, body, bones


def _rest_of_glbs(folder, bones, body):
    import bpy
    from mathutils import Vector
    # The raw high mesh of the same figure, unrigged and unpainted.
    high, _ = _figure_mesh("figure_high", fine=True)
    _export(os.path.join(folder, "humanoid_high.glb"), [high])
    # The box, in item space (metres, +x front, +z up, grip at the origin).
    import bmesh
    bm = bmesh.new()
    lo, hi = BOX["box"]
    bmesh.ops.create_cube(bm, size=1.0)
    for vert in bm.verts:
        vert.co = Vector([lo[i] + (vert.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3)])
    me = bpy.data.meshes.new("item_box")
    bm.to_mesh(me)
    bm.free()
    box = bpy.data.objects.new("item_box", me)
    bpy.context.scene.collection.objects.link(box)
    _export(os.path.join(folder, "box.glb"), [box])
    print("FIXTURES", folder, len(bones), "bones", sum(len(p.vertices) - 2 for p in body.data.polygons),
          "triangles", flush=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="")
    ap.add_argument("--glbs", default="")
    ap.add_argument("--figure", default="")
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--skew", action="store_true")
    a = ap.parse_args(argv)
    if a.data:
        write_data(a.data)
    if a.glbs:
        build_glbs(a.glbs)
    if a.figure:
        build_figure(a.figure, a.yaw, a.skew)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # bpy can crash while tearing down at exit; the work is done
