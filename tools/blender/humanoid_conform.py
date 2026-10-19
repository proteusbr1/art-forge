"""A rigged humanoid variant conformed to an approved body: its mesh moved by its own skin so
that its joints land on the body's, then bound to the body's skeleton (humanoid_fit.py holds
the geometry). The result is a rig the humanoid exporter takes as it is (with --body-frame
and this tool's frame.json, which keeps the body's frame).

Runs with a Python that imports bpy (Blender as a module):

  python humanoid_conform.py --rig RIG.glb --body BODY.glb --body-model MODEL.json --out DIR
      [--report REPORT.json] [--bone-map MAP.json] [--bake-source HIGH.glb]
      [--max-joint-cm 4] [--max-outside-cm 1.5] [--max-scale 1.5] [--max-torn 0.002] [--max-floor-cm 4]

Steps:
1. The body: a GLB the humanoid exporter wrote (its model.json next to it, or --body-model):
   its skeleton without the item bones, its joints by role (model.json "bones"), its surface
   (for the measures), its frame (height, floor, widen, head, hands, feet).
2. The variant's bone map, as the exporter chooses it: --bone-map (the order's) wins, then
   <out>/bone_map.json marked "manual": true, then the proposal from the skeleton's tree. It is
   validated; an invalid map stops the conform (exit code 3) with its report in
   <out>/bone_map.json.
3. The conform (humanoid_fit.conform): the mesh moved onto the body's joints by its own
   weights; then its weights renamed to the body's bones (a bone without a role counted with
   its nearest mapped ancestor: the fingers with the hand) and the mesh bound to the body's
   skeleton.
4. --bake-source (the raw high mesh, in the rig's coordinates): moved the same way, each
   vertex with the weights of the variant's nearest vertex.
5. Writes <out>/conformed.glb (the body's skeleton, the conformed mesh, the variant's
   material), <out>/conformed_raw.glb with --bake-source, <out>/frame.json (the body's frame
   for the exporter), <out>/body_map.json (the body's roles, the exporter's bone map) and the
   conform report (--report, else <out>/conform_report.json), with its flags.
"""
import argparse
import json
import os
import sys

import bpy  # first: bmesh and mathutils come with it
import numpy as np
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forge_blender as fb  # noqa: E402
import humanoid_bones as hb  # noqa: E402
import humanoid_fit as hf  # noqa: E402

ITEM_BONES = ("item", "item_l")
ap = argparse.ArgumentParser()
ap.add_argument("--rig", required=True, help="the variant's rig (a rig tool's GLB)")
ap.add_argument("--body", required=True, help="the approved body (a GLB the humanoid exporter wrote)")
ap.add_argument("--body-model", default="", help="the body's model.json (default: next to the body)")
ap.add_argument("--out", required=True)
ap.add_argument("--report", default="", help="where the conform report goes (default <out>/conform_report.json)")
ap.add_argument("--bone-map", default="", help="the variant's bone map given by the order (wins over everything)")
ap.add_argument("--bake-source", default="", help="the variant's raw high mesh, moved the same way")
ap.add_argument("--max-joint-cm", type=float, default=hf.THRESHOLDS["joint_cm"])
ap.add_argument("--max-outside-cm", type=float, default=hf.THRESHOLDS["outside_cm"])
ap.add_argument("--max-scale", type=float, default=hf.THRESHOLDS["scale"])
ap.add_argument("--max-torn", type=float, default=hf.THRESHOLDS["torn_share"])
ap.add_argument("--max-floor-cm", type=float, default=hf.THRESHOLDS["floor_cm"])
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
REPORT = os.path.abspath(a.report) if a.report else os.path.join(OUT, "conform_report.json")
print("BPY", bpy.app.version_string, flush=True)


def write(path, data):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


def skinned(arm):
    return [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == arm or any(
        m.type == "ARMATURE" and m.object == arm for m in o.modifiers))]


def world_coords(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(ob.matrix_world)
    return co @ mw[:3, :3].T + mw[:3, 3]


def triangles(ob):
    me = ob.data
    me.calc_loop_triangles()
    t = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", t)
    return t.reshape(-1, 3)


def kd_nearest(co):
    tree = KDTree(len(co))
    for i, p in enumerate(co):
        tree.insert(p, i)
    tree.balance()

    def find(points):
        return np.array([tree.find(p)[1] for p in points], np.int64)
    return find


class BvhSurface:
    """humanoid_fit's surface questions answered by Blender's BVH tree (fast on full meshes)."""

    def __init__(self, co, tris):
        from mathutils.bvhtree import BVHTree
        self.tree = BVHTree.FromPolygons([tuple(p) for p in co], [tuple(int(i) for i in t) for t in tris])

    def escape(self, points, dirs):
        from mathutils import Vector
        ds = [Vector(d) for d in dirs]
        return np.array([[self.tree.ray_cast(Vector(p), d)[0] is None for d in ds] for p in points], bool)

    def distance(self, points):
        from mathutils import Vector
        out = []
        for p in points:
            hit = self.tree.find_nearest(Vector(p))
            out.append(hit[3] if hit[0] is not None else 0.0)
        return np.array(out, np.float64)


# ---- 1. the body ---------------------------------------------------------------------------
model_path = a.body_model or os.path.join(os.path.dirname(os.path.abspath(a.body)), "model.json")
with open(model_path, encoding="utf-8-sig") as f:
    MODEL = json.load(f)
if MODEL.get("format") != "forge-humanoid" or not isinstance(MODEL.get("bones"), dict):
    fb.fail(2, "the body's model.json is not a humanoid exporter's model: %s" % model_path)
bpy.ops.wm.read_factory_settings(use_empty=True)
new = fb.import_glb(a.body)
arms = [o for o in new if o.type == "ARMATURE"]
if len(arms) != 1:
    fb.fail(2, "the body must have exactly one armature, it has %d" % len(arms))
BODY = arms[0]
BODY.name = "Armature"
body_meshes = skinned(BODY)
if not body_meshes:
    fb.fail(2, "no mesh of the body is skinned to its armature")
BODY_POINTS = np.concatenate([world_coords(o) for o in body_meshes])
for o in list(bpy.data.objects):  # only the skeleton stays: no mesh, no item_ref
    if o is not BODY:
        bpy.data.objects.remove(o)
if BODY.animation_data is not None:
    for tr in list(BODY.animation_data.nla_tracks):
        BODY.animation_data.nla_tracks.remove(tr)
    BODY.animation_data.action = None
for act in list(bpy.data.actions):
    bpy.data.actions.remove(act)
bpy.context.view_layer.objects.active = BODY
bpy.ops.object.mode_set(mode="EDIT")
for name in ITEM_BONES:
    eb = BODY.data.edit_bones.get(name)
    if eb is not None:
        BODY.data.edit_bones.remove(eb)
bpy.ops.object.mode_set(mode="OBJECT")
BODY_ROLES = {r: n for r, n in MODEL["bones"].items() if n in BODY.data.bones}
lost = sorted(set(MODEL["bones"]) - set(BODY_ROLES))
if lost:
    fb.fail(2, "the body's model.json names bones its skeleton lacks: %s" % ", ".join(lost))
bmw = BODY.matrix_world
BJ = {r: tuple(bmw @ BODY.data.bones[n].head_local) for r, n in BODY_ROLES.items()}
# The exporter stood the body on its floor: its ankle height is measured from there.
foot = BODY_ROLES.get("left_foot")
FLOOR = BJ["left_foot"][2] - float(MODEL.get("ankle_height", BJ["left_foot"][2])) if foot else 0.0
FRAME = {"format": "forge-body-frame", "version": 1, "height": float(MODEL["height"]), "floor": FLOOR,
         "widen": float(MODEL.get("widen", 1.0)), "head": float(MODEL.get("head", 1.0)),
         "hands": float(MODEL.get("hands", 1.0)), "feet": float(MODEL.get("feet", 1.0)),
         "front": MODEL.get("front"), "body": os.path.basename(a.body)}
print("BODY", len(BODY.data.bones), "bones,", len(BODY_ROLES), "roles, height %.4f" % FRAME["height"], flush=True)

# ---- 2. the variant and its bone map -------------------------------------------------------
new = fb.import_glb(a.rig)
varms = [o for o in new if o.type == "ARMATURE"]
if len(varms) != 1:
    fb.fail(2, "the rig must have exactly one armature, it has %d" % len(varms))
VAR = varms[0]
vmeshes = skinned(VAR)
if not vmeshes:
    fb.fail(2, "no mesh is skinned to the rig's armature")
for o in new:
    if o is not VAR and o not in vmeshes:
        bpy.data.objects.remove(o)
mesh = fb.join_meshes(vmeshes)
for m in list(mesh.modifiers):
    mesh.modifiers.remove(m)
_mw = mesh.matrix_world.copy()
mesh.parent = None
mesh.matrix_world = _mw
fb.apply_transforms([VAR, mesh])
merged = fb.merge_seams(mesh)
map_path = os.path.join(OUT, "bone_map.json")
doc = hb.choose_bone_map(VAR, given=a.bone_map or None, existing=map_path)
if doc["source"] == "manual":
    write(os.path.join(OUT, "bone_map_report.json"), doc)  # the manual file stays as written
else:
    write(map_path, doc)
if not doc["valid"]:
    for p in doc["problems"]:
        print("BONE MAP PROBLEM", p, flush=True)
    write(REPORT, {"format": "forge-conform-report", "version": 1, "status": "pending review", "valid": False,
                   "bone_map": {"source": doc["source"], "problems": doc["problems"]},
                   "flags": ["bone map: the variant's bone map is invalid"]})
    fb.fail(3, "invalid bone map (%s), see %s" % (doc["source"], map_path))
ROLES = doc["roles"]
VJ = {r: tuple(VAR.data.bones[b].head_local) for r, b in ROLES.items()}
common = hf.common_roles(ROLES, BODY_ROLES)
parents = {b.name: (b.parent.name if b.parent else None) for b in VAR.data.bones}
BONE_ROLE = hf.bone_roles(parents, ROLES, common)
print("BONE MAP", doc["source"], len(ROLES), "roles,", len(common), "shared with the body", flush=True)

# The variant's skin, by shared role.
groups = {g.index: g.name for g in mesh.vertex_groups}
cols = []
rows = []
for v in mesh.data.vertices:
    acc = {}
    for g in v.groups:
        name = groups.get(g.group)
        if name in BONE_ROLE and g.weight > 0.0:
            r = BONE_ROLE[name]
            acc[r] = acc.get(r, 0.0) + g.weight
    rows.append(acc)
    for r in acc:
        if r not in cols:
            cols.append(r)
K = max([len(x) for x in rows] + [1])
INDEX = np.zeros((len(rows), K), np.int64)
WEIGHT = np.zeros((len(rows), K), np.float64)
for i, acc in enumerate(rows):
    for k, (r, w) in enumerate(acc.items()):
        INDEX[i, k] = cols.index(r)
        WEIGHT[i, k] = w
if not cols:
    fb.fail(2, "the rig's mesh has no weights on its skeleton's bones")

# ---- 3. the conform ------------------------------------------------------------------------
POINTS = world_coords(mesh)
TRIS = triangles(mesh)
thresholds = {"joint_cm": a.max_joint_cm, "outside_cm": a.max_outside_cm, "scale": a.max_scale,
              "torn_share": a.max_torn, "floor_cm": a.max_floor_cm}
res = hf.conform(VJ, BJ, ROLES, BODY_ROLES, doc["front"], POINTS, INDEX, WEIGHT, cols, TRIS,
                 body_points=BODY_POINTS, body_floor=FLOOR, surface_of=BvhSurface, thresholds=thresholds)
mesh.data.vertices.foreach_set("co", res["points"].astype(np.float32).ravel())
mesh.data.update()
for g in list(mesh.vertex_groups):
    mesh.vertex_groups.remove(g)
out_groups = {}
for i, acc in enumerate(rows):
    for r, w in acc.items():
        name = BODY_ROLES[r]
        if name not in out_groups:
            out_groups[name] = mesh.vertex_groups.new(name=name)
        out_groups[name].add([i], w, "ADD")
mesh.parent = BODY
mesh.matrix_parent_inverse = BODY.matrix_world.inverted()
mod = mesh.modifiers.new("Armature", "ARMATURE")
mod.object = BODY
mesh.name = "body"
bpy.data.objects.remove(VAR)
CONFORMED = os.path.join(OUT, "conformed.glb")
fb.export_glb(CONFORMED, [BODY, mesh])

# ---- 4. the bake source --------------------------------------------------------------------
raw_out = None
if a.bake_source:
    new = fb.import_glb(a.bake_source)
    highs = [o for o in new if o.type == "MESH"]
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    if not highs:
        fb.fail(2, "the bake source has no mesh: %s" % a.bake_source)
    for o in highs:
        o.parent = None
    high = fb.join_meshes(highs)
    fb.apply_transforms([high])
    HP = world_coords(high)
    near = kd_nearest(POINTS)(HP)
    order = list(res["mats"])
    stack = np.array([res["mats"][r] for r in order])
    col = np.array([order.index(r) for r in cols], np.int64)
    moved = hf.skin(hf.apply(res["prefit"], HP), col[INDEX[near]], WEIGHT[near], stack)
    high.data.vertices.foreach_set("co", moved.astype(np.float32).ravel())
    high.data.update()
    raw_out = os.path.join(OUT, "conformed_raw.glb")
    fb.export_glb(raw_out, [high])

# ---- 5. reports ----------------------------------------------------------------------------
write(os.path.join(OUT, "frame.json"), FRAME)
write(os.path.join(OUT, "body_map.json"), {"format": hb.FORMAT, "version": 1, "source": "body",
                                           "roles": dict(BODY_ROLES)})
report = {"format": "forge-conform-report", "version": 1, "status": "pending review", "valid": True,
          "body": {"file": os.path.basename(a.body), "height": FRAME["height"], "roles": len(BODY_ROLES),
                   "bones": len(BODY.data.bones)},
          "bone_map": {"source": doc["source"], "file": "bone_map.json"},
          "weights": "the variant's own, renamed to the body's bones (a bone without a role with its nearest "
                     "mapped ancestor)",
          "bones_folded": sorted(b for b in BONE_ROLE if b not in ROLES.values()),
          "vertices": len(POINTS), "seam_vertices_merged": merged, "bake_source": bool(raw_out)}
report.update(res["report"])
report["valid"] = True
write(REPORT, report)
print("CONFORM joints %.2f cm, outside %.2f cm, stretch p95 %.3f, torn %.5f, %d flags" % (
    report["joint_error_cm"], report["joint_outside_max_cm"], report["stretch_p95"], report["torn_share"],
    len(report["flags"])), flush=True)
for flag in report["flags"]:
    print("FLAG", flag, flush=True)
fb.finish(0)
