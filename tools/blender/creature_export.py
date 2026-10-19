"""A rigged creature as a game-ready skinned GLB: its predicted bones mapped onto the order's
body plan (or skeleton template), the order's motions made in those roles, and the reports
that say whether the result can be trusted.

Runs with a Python that imports bpy (Blender as a module):

  python creature_export.py --rig RIG.glb --out DIR --spec SPEC.json [--name model]
      [--front +z|-z|+x|-x] [--height 1.8 | --length 2.0] [--tris 25000]
      [--clips LIBRARY.json] [--library OTHER.json ...] [--bone-map MAP.json]
      [--metal-roughness MAP.png] [--image-format lossless|compact]
      [--max-foot-slide 2] [--max-reach 1] [--max-stretch 0.5] [--max-below-ground 3]

SPEC.json: {"body": the order's body (a plan or a template), "motions": the order's
motions list}. Steps (formats in docs/conventions.md):
1. The rig is turned so that its front (--front, the glTF axis the generated front points
   along) faces glTF +Z, scaled to --height (or --length, front to back), stood on the
   ground with its root over the origin.
2. The bone map: a map given with --bone-map wins, then <out>/bone_map.json marked
   "manual": true, then the proposal from the skeleton's tree and the body plan
   (creature_bones.py). An invalid map stops the export (exit code 3), its report in
   <out>/bone_map.json.
3. The mesh: reduced only above --tris, weights limited to 4 influences and normalised; the
   paint stage's metallic-roughness map replaces the rig's. No UV layer or no base-colour
   texture stops it (exit code 4). Item bones ("item", "item_l") at the tips of the roles
   the plan's "holds" names, with "item_ref" (the identity of item space) on "item".
4. Every motions entry is made from its source (creature_motion.py: procedural kinds, or a
   library clip in role space) and keyed, one animation per entry, named as the entry.
5. Writes <out>/<name>.glb, <out>/model.json, <out>/mesh_report.json and <out>/clips.json
   (every clip in role space: forge-creature-clips).
6. With motions, the GLB is loaded again and every clip played on it:
   <out>/motion_report.json (foot slide in the contact frames, reach shortfall of the legs'
   inverse kinematics, edge stretch, depth below the ground; flags over the thresholds).
"""
import argparse
import json
import math
import os
import sys
import time

import bpy  # first: bmesh and mathutils come with it
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import creature_bones as cb  # noqa: E402
import creature_motion as cm  # noqa: E402
import forge_blender as fb  # noqa: E402

MAX_INFLUENCES = 4
ap = argparse.ArgumentParser()
ap.add_argument("--rig", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--spec", required=True)
ap.add_argument("--name", default="model")
ap.add_argument("--front", default="+z", choices=tuple(fb.FRONT_YAW))
ap.add_argument("--height", type=float, default=0.0)
ap.add_argument("--length", type=float, default=0.0)
ap.add_argument("--tris", type=int, default=25000)
ap.add_argument("--clips", default="")
ap.add_argument("--library", action="append", default=[])
ap.add_argument("--bone-map", default="")
ap.add_argument("--metal-roughness", default="")
ap.add_argument("--image-format", default="lossless", choices=("lossless", "compact"))
ap.add_argument("--max-foot-slide", type=float, default=2.0)
ap.add_argument("--max-reach", type=float, default=1.0)
ap.add_argument("--max-stretch", type=float, default=0.5)
ap.add_argument("--max-below-ground", type=float, default=3.0)
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
print("BPY", bpy.app.version_string, flush=True)
with open(a.spec, encoding="utf-8-sig") as f:
    SPEC = json.load(f)
BODY = SPEC["body"]
MOTIONS = SPEC.get("motions") or []
_problems = cb.check_body(BODY)
if _problems:
    fb.fail(2, "invalid body: " + "; ".join(_problems))
PLAN = cb.body_plan(BODY)
LIBRARIES = {}
for path in ([a.clips] if a.clips else []) + list(a.library):
    with open(path, encoding="utf-8-sig") as f:
        LIBRARIES[os.path.abspath(path)] = json.load(f)
DEFAULT_LIBRARY = os.path.abspath(a.clips) if a.clips else None
for m in MOTIONS:
    lib = m.get("library") if isinstance(m, dict) else None
    if isinstance(lib, dict) and lib.get("file"):
        lib["file"] = os.path.abspath(lib["file"])

# ---- the rig -------------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
fb.import_glb(a.rig)
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
if len(arms) != 1:
    fb.fail(2, "the rig must have exactly one armature, it has %d" % len(arms))
arm = arms[0]
meshes = [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == arm or any(
    m.type == "ARMATURE" and m.object == arm for m in o.modifiers))]
if not meshes:
    fb.fail(2, "no mesh is skinned to the armature")
for o in list(bpy.data.objects):
    if o.type == "MESH" and o not in meshes:
        bpy.data.objects.remove(o)
body = fb.join_meshes(meshes)
merged = fb.merge_seams(body)
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.context.view_layer.objects.active = body
if body.data.has_custom_normals:
    bpy.ops.mesh.customdata_custom_splitnormals_clear()
bpy.ops.object.shade_smooth()
print("SEAMS", merged, "duplicate vertices merged", flush=True)

# ---- 1. frame and scale ----------------------------------------------------------------------
turned = fb.turn_to_front([arm], a.front)
bpy.context.view_layer.update()
coords = np.array([list(body.matrix_world @ v.co) for v in body.data.vertices])
tree0 = cb.tree_from_armature(arm)
path0 = cb.Tree(tree0).root_path()
hub = Vector(tree0[path0[-1]]["head"])
floor = float(coords[:, 2].min())
if a.length > 0:
    k = a.length / max(float(np.ptp(coords[:, 1])), 1e-9)
elif a.height > 0:
    k = a.height / max(float(np.ptp(coords[:, 2])), 1e-9)
else:
    k = 1.0
TRANSFORM = Matrix.Diagonal((k, k, k, 1.0)) @ Matrix.Translation(Vector((-hub.x, -hub.y, -floor)))
arm.matrix_world = TRANSFORM @ arm.matrix_world
bpy.ops.object.select_all(action="DESELECT")
arm.select_set(True)
body.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.context.view_layer.update()
coords = np.array([list(body.matrix_world @ v.co) for v in body.data.vertices])
LO, HI = coords.min(0), coords.max(0)
H = float(HI[2] - LO[2])
print("FRAME front %s turned %.1f scale %.4f height %.3f" % (a.front, turned, k, H), flush=True)

# ---- 2. the bone map -------------------------------------------------------------------------
map_path = os.path.join(OUT, "bone_map.json")
TREE = cb.tree_from_armature(arm)
doc = cb.choose(TREE, PLAN, given=a.bone_map or None, existing=map_path)
if doc["source"] == "manual":
    fb.write_json(OUT, "bone_map_report.json", doc)
else:
    fb.write_json(OUT, "bone_map.json", doc)
if not doc["valid"]:
    for p in doc["problems"]:
        print("BONE MAP PROBLEM", p, flush=True)
    fb.fail(3, "invalid bone map (%s), see %s" % (doc["source"], map_path))
ROLES = doc["roles"]
print("BONE MAP", doc["source"], len(ROLES), "roles", flush=True)

# ---- 3. the mesh -----------------------------------------------------------------------------
import bmesh  # noqa: E402
report = {"format": "forge-mesh-report", "version": 2, "kind": "creature", "status": "pending review", "problems": []}
tris_in = sum(len(p.vertices) - 2 for p in body.data.polygons)
mats = [m for m in body.data.materials if m is not None]
base_node, bsdf = None, None
if mats and mats[0].use_nodes:
    bsdf = next((n for n in mats[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        base_node = fb.find_slots(mats[0].node_tree, bsdf).get("base_color")
if not body.data.uv_layers:
    report["problems"].append("the mesh has no UV layer")
if base_node is None:
    report["problems"].append("the material has no base-colour texture")
if report["problems"]:
    report.update(valid=False, triangles_in=tris_in, flags=list(report["problems"]))
    fb.write_json(OUT, "mesh_report.json", report)
    fb.fail(4, "the mesh cannot be exported: " + "; ".join(report["problems"]))
if len(mats) != 1:
    report["problems"].append("the mesh has %d materials, 1 is expected" % len(mats))
mat = mats[0]
SOURCES = {s: "the rig's material" for s in fb.find_slots(mat.node_tree, bsdf)}
NOT_CARRIED = {}
if a.metal_roughness:
    img, why = fb.load_map(a.metal_roughness)
    if img is None:
        NOT_CARRIED["metallic_roughness"] = "the paint stage's map %s could not be carried: %s" % (
            os.path.basename(a.metal_roughness), why)
    else:
        fb.link_metal_roughness(mat, img)
    SOURCES["metallic_roughness"] = "the paint stage's map %s" % os.path.basename(a.metal_roughness)
REDUCED = tris_in > a.tris
if REDUCED:
    mod = body.modifiers.new("cut", "DECIMATE")
    mod.ratio = a.tris / tris_in
    bpy.context.view_layer.objects.active = body
    while body.modifiers[0].name != "cut":
        bpy.ops.object.modifier_move_up(modifier="cut")
    bpy.ops.object.modifier_apply(modifier="cut")
tris_out = sum(len(p.vertices) - 2 for p in body.data.polygons)
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.context.view_layer.objects.active = body
if body.vertex_groups:
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=MAX_INFLUENCES)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
bone_names = set(b.name for b in arm.data.bones)
deform_idx = set(g.index for g in body.vertex_groups if g.name in bone_names)
unweighted, max_inf, worst_sum = 0, 0, 0.0
for v in body.data.vertices:
    ws = [g.weight for g in v.groups if g.group in deform_idx and g.weight > 0.0]
    if not ws:
        unweighted += 1
        continue
    max_inf = max(max_inf, len(ws))
    worst_sum = max(worst_sum, abs(sum(ws) - 1.0))
if unweighted:
    report["problems"].append("%d vertices have no weight" % unweighted)
if worst_sum > 1e-3:
    report["problems"].append("weights are not normalised (worst sum off by %.4f)" % worst_sum)

# Item bones at the tips of the holding roles.
ITEM_LEN = 0.1 * H
item_bones = {}
holds = PLAN.get("holds") or {}
if holds:
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for side, bone_name in (("right", "item"), ("left", "item_l")):
        role = holds.get(side)
        if not role or role not in ROLES:
            continue
        tip = ROLES[role][-1]
        eb = arm.data.edit_bones.new(bone_name)
        parent = arm.data.edit_bones[tip]
        eb.head = parent.tail.copy()
        eb.tail = eb.head + Vector((0.0, -ITEM_LEN, 0.0))
        eb.align_roll(Vector((0.0, 0.0, 1.0)))
        eb.parent = parent
        eb.use_deform = False
        item_bones[side] = bone_name
    bpy.ops.object.mode_set(mode="OBJECT")
ref = None
if "right" in item_bones:
    ref_me = bpy.data.meshes.new("item_ref")
    s = 0.002
    ref_me.from_pydata([(0, 0, 0), (s, 0, 0), (0, s, 0)], [], [(0, 1, 2)])
    ref = bpy.data.objects.new("item_ref", ref_me)
    bpy.context.scene.collection.objects.link(ref)
    ref.parent = arm
    ref.parent_type = "BONE"
    ref.parent_bone = "item"
    ref.matrix_parent_inverse = Matrix.Identity(4)
    # Item space (+x front, +z up, +y left) in the item bone's axes (Y along the bone, Z up),
    # from the bone's head.
    rot = Matrix(((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))).to_4x4()
    ref.matrix_basis = Matrix.Translation(Vector((0, -ITEM_LEN, 0))) @ rot
bpy.context.view_layer.update()

# ---- 4. the motions --------------------------------------------------------------------------
TREE = cb.tree_from_armature(arm)
rig = cm.Rig(TREE, ROLES, doc["root"], H, doc.get("joints"))
ORDER = list(rig.order)
FPS = 120.0
bpy.context.scene.render.fps = int(FPS)
bpy.context.scene.render.fps_base = 1.0
REST = {b.name: b.matrix_local.to_3x3().normalized() for b in arm.data.bones}
KEY_SIDE = {}
clips = []
for entry in MOTIONS:
    out = cm.make_clip(rig, PLAN, entry, LIBRARIES, DEFAULT_LIBRARY)
    out["holds"] = entry.get("holds")
    out["source"] = entry["source"]
    clips.append(out)
    act = bpy.data.actions.new(entry["name"])
    act.use_fake_user = True
    arm.animation_data_create()
    arm.animation_data.action = act
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    n = out["frames"]
    seq = list(range(n)) + [0 if out["loop"] else n - 1]
    for i, fi in enumerate(seq):
        rots = out["rotations"][fi]
        off = Vector(out["offsets"][fi])
        f = i * FPS / out["fps"]
        for name in ORDER:
            pb = arm.pose.bones[name]
            pb.rotation_mode = "QUATERNION"
            M = REST[name]
            q = rots.get(name)
            R = Quaternion(q).to_matrix() if q else Matrix.Identity(3)
            local = (M.transposed() @ R @ M).to_quaternion()
            refq = KEY_SIDE.setdefault(name, local.copy())
            if local.dot(refq) < 0.0:
                local = -local
            pb.rotation_quaternion = local
            pb.location = (M.transposed() @ off) if name == rig.root else Vector((0, 0, 0))
            pb.keyframe_insert("rotation_quaternion", frame=f)
            pb.keyframe_insert("location", frame=f)
    bpy.ops.object.mode_set(mode="OBJECT")
    for fc in act.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    track = arm.animation_data.nla_tracks.new()
    track.name = entry["name"]
    track.strips.new(entry["name"], 0, act)
    arm.animation_data.action = None
    print("ANIMATION", entry["name"], entry["source"], out["kind"], n, "frames", flush=True)
if arm.animation_data is not None:
    arm.animation_data.action = None
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="POSE")
for pb in arm.pose.bones:
    pb.rotation_quaternion = Quaternion()
    pb.location = Vector()
bpy.ops.object.mode_set(mode="OBJECT")

# ---- 5. export ---------------------------------------------------------------------------------
body.name = "body"
GLB = os.path.join(OUT, a.name + ".glb")
MAPS = os.path.join(OUT, "_maps")
fb.prepare_images(mat, a.image_format, MAPS)
fb.export_glb(GLB, [arm, body] + ([ref] if ref else []), animations=bool(clips))
if os.path.isdir(MAPS):
    import shutil  # noqa: E402
    shutil.rmtree(MAPS, ignore_errors=True)
materials, mflags = fb.material_report(GLB, "body", SOURCES, a.image_format, NOT_CARRIED)
for slot in sorted(mflags):
    report["problems"].append("material slot %s: %s" % (slot, mflags[slot]))
report.update({"uv_layers": len(body.data.uv_layers), "material_count": len(mats),
               "base_color_texture": base_node.image.name, "texture_size": list(base_node.image.size),
               "vertices": len(body.data.vertices), "unweighted_vertices": unweighted,
               "weight_sum_error": round(worst_sum, 6), "max_influences": max_inf, "influence_limit": MAX_INFLUENCES,
               "triangles_in": tris_in, "triangles": tris_out, "triangle_budget": a.tris, "reduced": REDUCED,
               "seam_vertices_merged": merged, "normal_map": None, "materials": materials})
report["valid"] = not report["problems"]
report["flags"] = list(report["problems"])
fb.write_json(OUT, "mesh_report.json", report)
lib_doc = cm.library(rig, clips)
for c in clips:
    lib_doc["anims"][c["name"]]["source"] = c["source"]
fb.write_json(OUT, "clips.json", lib_doc)
size = HI - LO
info = {"format": "forge-creature", "version": 1, "status": "pending review", "name": a.name,
        "height": H, "front": [0.0, 0.0, 1.0], "axes": "glTF: y up, front +Z, left +X; metres",
        "size_m": [round(float(size[0]), 4), round(float(size[2]), 4), round(float(size[1]), 4)],
        "center_m": [round(float((LO[0] + HI[0]) / 2), 4), round(float((LO[2] + HI[2]) / 2), 4),
                     round(float(-(LO[1] + HI[1]) / 2), 4)],
        "plan": PLAN, "roles": ROLES, "root": doc["root"], "joints": doc.get("joints"),
        "unmapped_bones": doc["unmapped"], "bone_map": {"source": doc["source"], "file": "bone_map.json"},
        "item_bones": dict(item_bones, reference="item_ref" if ref else None),
        "ends": {r: bones[-1] for r, bones in ROLES.items()},
        "animations": [{"name": c["name"], "kind": c["kind"], "source": c["source"], "frames": c["frames"],
                        "fps": c["fps"], "loop": c["loop"], "seconds": c["frames"] / c["fps"], "holds": c["holds"],
                        "stance": c["stance"], "travel": c["travel"], "params": c["params"]} for c in clips],
        "clips": "clips.json",
        "mesh": {"triangles": tris_out, "reduced": REDUCED, "report": "mesh_report.json",
                 "image_format": a.image_format},
        "seconds_export": round(time.time() - T0, 1)}
fb.write_json(OUT, "model.json", info)
if not clips:
    print("DONE 0 animations", flush=True)
    fb.finish(0)

# ---- 6. the motion report ----------------------------------------------------------------------
THRESH = {"foot_slide_cm": a.max_foot_slide, "reach_shortfall_cm": a.max_reach, "stretch_p95": a.max_stretch,
          "below_ground_cm": a.max_below_ground}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.render.fps = 24
fb.import_glb(GLB)
rarm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
rbody = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("body"))
if rarm.animation_data is None:
    rarm.animation_data_create()
for tr in rarm.animation_data.nla_tracks:
    tr.mute = True
scene = bpy.context.scene
scene_fps = scene.render.fps / scene.render.fps_base
dg = bpy.context.evaluated_depsgraph_get()
me = rbody.data
ev = np.empty(len(me.edges) * 2, np.int64)
me.edges.foreach_get("vertices", ev)
ev = ev.reshape(-1, 2)
co0 = np.empty(len(me.vertices) * 3, np.float64)
me.vertices.foreach_get("co", co0)
co0 = co0.reshape(-1, 3)
rest_len = np.linalg.norm(co0[ev[:, 0]] - co0[ev[:, 1]], axis=1)
keep = rest_len > 1e-6
EDGES, REST_LEN = ev[keep], rest_len[keep]


def posed_mesh():
    ob = rbody.evaluated_get(dg)
    m = ob.to_mesh()
    co = np.empty(len(m.vertices) * 3, np.float64)
    m.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mwb = np.array(ob.matrix_world)
    co = co @ mwb[:3, :3].T + mwb[:3, 3]
    ob.to_mesh_clear()
    return co


def find_action(name):
    if name in bpy.data.actions:
        return bpy.data.actions[name]
    names = set(c["name"] for c in clips)
    for act in sorted(bpy.data.actions, key=lambda x: len(x.name)):
        rest = act.name[len(name):]
        if act.name.startswith(name) and rest[:1] in ("_", ".") and act.name not in names:
            return act
    return None


def stance_runs(frames_idx, n, loop):
    if frames_idx == "all":
        frames_idx = list(range(n))
    s = sorted(set(int(f) for f in frames_idx if 0 <= int(f) < n))
    if not s:
        return []
    runs, cur = [], [s[0]]
    for f in s[1:]:
        if f == cur[-1] + 1:
            cur.append(f)
        else:
            runs.append(cur)
            cur = [f]
    runs.append(cur)
    if loop and len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == n - 1:
        runs[0] = runs[-1] + [f + n for f in runs[0]]
        runs.pop()
    return runs


FRONT, LEFT = Vector(cb.FRONT), Vector(cb.LEFT)
motion = {"format": "forge-motion-report", "version": 1, "kind": "creature", "status": "pending review",
          "model": os.path.basename(GLB), "thresholds": THRESH, "clips": {}, "flags": []}
for c in clips:
    act = find_action(c["name"])
    entry = {"frames": c["frames"], "found": act is not None}
    if act is None:
        motion["clips"][c["name"]] = entry
        motion["flags"].append({"clip": c["name"], "measure": "missing", "value": None, "threshold": None})
        continue
    rarm.animation_data.action = act
    n, fps = c["frames"], c["fps"]
    feet = {role: [] for role in (c["stance"] or {}) if role in ROLES}
    stretch, below = [], []
    for i in range(n):
        f = i / fps * scene_fps
        scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
        dg.update()
        for role in feet:
            feet[role].append(rarm.matrix_world @ rarm.pose.bones[ROLES[role][-1]].head)
        co = posed_mesh()
        ln = np.linalg.norm(co[EDGES[:, 0]] - co[EDGES[:, 1]], axis=1)
        stretch.append(float(np.percentile(np.abs(ln / REST_LEN - 1.0), 95)))
        below.append(max(0.0, -float(co[:, 2].min())) * 100.0)
    slide = None
    if feet:
        trav = c.get("travel") or [0.0, 0.0]
        step = (LEFT * float(trav[0]) + FRONT * float(trav[1])) * (H / max(n, 1))
        slide = 0.0
        for role, pts_all in feet.items():
            for run in stance_runs(c["stance"][role], n, c["loop"]):
                pts = [pts_all[kk % n] + step * kk for kk in run]
                slide = max([slide] + [(p - pts[0]).length * 100.0 for p in pts])
    entry.update({"foot_slide_cm": None if slide is None else round(slide, 3),
                  "reach_shortfall_cm": round(float(c.get("reach_shortfall_m") or 0.0) * 100.0, 3),
                  "stretch_p95": round(max(stretch), 4) if stretch else None,
                  "below_ground_cm": round(max(below), 3) if below else None})
    motion["clips"][c["name"]] = entry
    for kk, limit in THRESH.items():
        v = entry.get(kk)
        if v is not None and v > limit:
            motion["flags"].append({"clip": c["name"], "measure": kk, "value": v, "threshold": limit})
fb.write_json(OUT, "motion_report.json", motion)
print("MOTION REPORT", len(motion["clips"]), "clips,", len(motion["flags"]), "flags", flush=True)
print("DONE", len(clips), "animations", flush=True)
fb.finish(0)
