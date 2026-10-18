"""A rigged humanoid as a game-ready skinned GLB: bones renamed to Godot's humanoid profile,
the motion library retargeted onto it, a normal map baked from the high-resolution mesh, a
regions mask and the reports that say whether the result can be trusted.

Runs with a Python that imports bpy (Blender as a module):

  python humanoid_export.py --rig RIG.glb --out DIR [--name model] [--tris 25000] [--tex 2048]
      [--height 1.8] [--head 1.0] [--hands 1.0] [--feet 1.0] [--widen 1.0]
      [--clips LIBRARY.json] [--items DIR] [--glow [--glow-hues 165,225]]
      [--bake-source HIGH.glb] [--bone-map MAP.json] [--device GPU|CPU] [--only idle,walk]
      [--max-foot-slide 2] [--max-hand-socket 2] [--max-reach 1] [--max-stretch 0.5]
      [--max-penetration 2]

Steps (formats in docs/conventions.md):
1. The bone map: a map given with --bone-map wins, then <out>/bone_map.json when it is
   marked "manual": true, then the proposal from the skeleton's tree
   (humanoid_bones.py). It is validated; an invalid map stops the export with its report in
   <out>/bone_map.json (exit code 3). The exporter never guesses.
2. The model stands on z = 0 over the hips, scaled to --height metres, widened across
   (--widen); the mapped bones take their SkeletonProfileHumanoid names, the others keep
   theirs.
3. The mesh is the painted game mesh of the rig. It is reduced only when it has more than
   --tris triangles (the report says so). The normal map (--tex pixels, tangent space,
   Cycles on --device) is baked from --bake-source (the raw high mesh, in the rig mesh's
   coordinates: checked to line up with the game mesh, fitted by a uniform scale and a
   translation when it does not, and left out, with a mesh flag, when it still does not)
   or, without it, from the mesh before the reduction; the relief of the
   painted texture is added to it. The painted texture keeps its size. Weights are limited
   to 4 influences and normalised; <out>/mesh_report.json checks the result.
4. With --clips, every clip of the library (canonical joints in body coordinates) is
   retargeted by bone directions, the feet planted as the clip says and the hands put on
   the held item's sockets by IK. Without it the GLB has the rest pose only.
5. Writes <out>/<name>.glb, <out>/model.json and <out>/regions_mask.png (red: skin, green:
   hair of the head, blue: what glows, with --glow).
6. With --clips, the exported GLB is loaded again and every clip played on it:
   <out>/motion_report.json (foot slide, hand to socket, reach shortfall, deformation,
   held-item penetration; flags over the thresholds). Every candidate is pending review.
"""
import argparse
import json
import math
import os
import sys
import time

import bpy  # first: bmesh and mathutils come with it
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import humanoid_bones as hb  # noqa: E402

UP = Vector((0.0, 0.0, 1.0))
MAX_INFLUENCES = 4

ap = argparse.ArgumentParser()
ap.add_argument("--rig", required=True, help="the rigged game mesh (GLB)")
ap.add_argument("--out", required=True)
ap.add_argument("--name", default="model")
ap.add_argument("--tris", type=int, default=25000, help="triangle budget; reduced only when above it")
ap.add_argument("--tex", type=int, default=2048, help="normal map size in pixels")
ap.add_argument("--height", type=float, default=1.8, help="height in metres")
ap.add_argument("--head", type=float, default=1.0)
ap.add_argument("--hands", type=float, default=1.0)
ap.add_argument("--feet", type=float, default=1.0)
ap.add_argument("--widen", type=float, default=1.0, help="body width scale (across, both horizontal axes)")
ap.add_argument("--clips", default="", help="motion library (JSON)")
ap.add_argument("--items", default="", help="folder of held-item socket files (<name>.json)")
ap.add_argument("--glow", action="store_true", help="mark what glows in the mask's blue channel")
ap.add_argument("--glow-hues", default="165,225", help="hue range in degrees that glows: low,high")
ap.add_argument("--bake-source", default="", help="raw high-resolution mesh to bake the normal map from")
ap.add_argument("--bone-map", default="", help="bone map given by the order (wins over everything)")
ap.add_argument("--device", default="GPU", choices=("GPU", "CPU"), help="Cycles device for the bake")
ap.add_argument("--only", default="", help="only the clips whose names start with these (comma separated)")
ap.add_argument("--max-foot-slide", type=float, default=2.0, help="cm, stance frames")
ap.add_argument("--max-hand-socket", type=float, default=2.0, help="cm, holding poses")
ap.add_argument("--max-reach", type=float, default=1.0, help="cm of IK target beyond reach")
ap.add_argument("--max-stretch", type=float, default=0.5, help="95th percentile of edge stretch (|length / rest - 1|)")
ap.add_argument("--max-penetration", type=float, default=2.0, help="cm of the item's box inside the body")
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
print("BPY", bpy.app.version_string, flush=True)


def write_json(name, data):
    path = os.path.join(OUT, name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)
    return path


def finish(code):
    """Exit now: bpy can crash while tearing down at exit, after the work is done."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def fail(code, msg):
    print("FAILED", msg, flush=True)
    finish(code)


# A bake source lines up with the game mesh when 90 % of the game mesh's vertices lie within
# this share of the height from its surface (the bake's ray reaches 2 % of the extent).
BAKE_TOLERANCE = 0.01
BAKE_SAMPLES = 4000


def _bounds(ob):
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    return co.min(0), co.max(0)


def _distances(high, body):
    """Median and 90th percentile distance (metres) from game-mesh vertices to the bake
    source's surface."""
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromObject(high, bpy.context.evaluated_depsgraph_get())
    verts = body.data.vertices
    step = max(1, len(verts) // BAKE_SAMPLES)
    d = []
    for i in range(0, len(verts), step):
        hit = tree.find_nearest(verts[i].co)
        d.append(hit[3] if hit[0] is not None else float("inf"))
    d = np.array(d)
    return {"median": round(float(np.median(d)), 5), "p90": round(float(np.percentile(d, 90)), 5)}


def fit_bake_source(high, body):
    """Check that the bake source (in the game mesh's coordinates) lines up with the game
    mesh; when it does not, fit its bounds to the game mesh's (uniform scale and translation,
    the change a re-centring or rescaling tool makes) and measure again. Returns (bounds
    offset in metres after any fit, record for mesh_report.json); record["used"] is False
    when it still does not line up."""
    tol = BAKE_TOLERANCE * a.height
    lo_b, hi_b = _bounds(body)
    lo_h, hi_h = _bounds(high)
    offset = float(max(np.abs(lo_h - lo_b).max(), np.abs(hi_h - hi_b).max()))
    dist = _distances(high, body)
    rec = {"tolerance_m": round(tol, 4), "bounds_offset_before_m": round(offset, 4), "distance_before_m": dist,
           "corrected": False, "used": True, "distance_after_m": dist}
    if dist["p90"] <= tol:
        return offset, rec
    eh, eb = hi_h - lo_h, hi_b - lo_b
    s = float((eh * eb).sum() / max(float((eh * eh).sum()), 1e-12))
    t = (lo_b + hi_b) / 2.0 - s * (lo_h + hi_h) / 2.0
    high.data.transform(Matrix.Translation(Vector(t.tolist())) @ Matrix.Scale(s, 4))
    high.data.update()
    lo_h, hi_h = _bounds(high)
    offset = float(max(np.abs(lo_h - lo_b).max(), np.abs(hi_h - hi_b).max()))
    dist = _distances(high, body)
    rec.update({"corrected": True, "scale": round(s, 6), "translation_m": [round(float(x), 5) for x in t],
                "bounds_offset_after_m": round(offset, 4), "distance_after_m": dist, "used": dist["p90"] <= tol})
    return offset, rec


library = {}
if a.clips:
    with open(a.clips, encoding="utf-8-sig") as f:
        library = json.load(f)
    if not isinstance(library.get("anims"), dict):
        fail(2, "the motion library has no \"anims\" object: %s" % a.clips)

# ---- the rig -----------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.path.abspath(a.rig))
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
if len(arms) != 1:
    fail(2, "the rig must have exactly one armature, it has %d" % len(arms))
arm = arms[0]
meshes = [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == arm or any(
    m.type == "ARMATURE" and m.object == arm for m in o.modifiers))]
if not meshes:
    fail(2, "no mesh is skinned to the armature")
for o in list(bpy.data.objects):
    if o.type == "MESH" and o not in meshes:
        bpy.data.objects.remove(o)
if len(meshes) > 1:
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
body = meshes[0]
# The importer splits vertices along UV seams; merged again, so that the seams cannot open
# when the mesh is reduced or skinned (UVs stay per corner, weights are kept).
import bmesh  # noqa: E402
_bm = bmesh.new()
_bm.from_mesh(body.data)
_size = max(body.dimensions) or 1.0
_before = len(_bm.verts)
bmesh.ops.remove_doubles(_bm, verts=list(_bm.verts), dist=1e-5 * _size)
_merged = _before - len(_bm.verts)
_bm.to_mesh(body.data)
_bm.free()
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.context.view_layer.objects.active = body
if body.data.has_custom_normals:
    bpy.ops.mesh.customdata_custom_splitnormals_clear()
bpy.ops.object.shade_smooth()
print("SEAMS", _merged, "duplicate vertices merged", flush=True)

# ---- 1. the bone map ----------------------------------------------------------------------
map_path = os.path.join(OUT, "bone_map.json")
doc = hb.choose_bone_map(arm, given=a.bone_map or None, existing=map_path)
if doc["source"] == "manual":
    write_json("bone_map_report.json", doc)  # the manual file stays as written
else:
    write_json("bone_map.json", doc)
if not doc["valid"]:
    for p in doc["problems"]:
        print("BONE MAP PROBLEM", p, flush=True)
    fail(3, "invalid bone map (%s), see %s" % (doc["source"], map_path))
ROLES = doc["roles"]
TREE = hb.tree_from_armature(arm)
CH0 = hb.chains(TREE, ROLES)
FRONT = Vector(doc["front"])
LEFT = Vector(doc["left"])
print("BONE MAP", doc["source"], len(ROLES), "roles", flush=True)

# ---- 2. stand, scale, widen, rename -------------------------------------------------------
coords = [body.matrix_world @ v.co for v in body.data.vertices]
_floor = min(c.z for c in coords)
_H0 = max(c.z for c in coords) - _floor
_hips = arm.matrix_world @ arm.data.bones[ROLES["hips"]].head_local
_k = a.height / _H0
TRANSFORM = (Matrix.Diagonal((a.widen, a.widen, 1.0, 1.0)) @ Matrix.Diagonal((_k, _k, _k, 1.0))
             @ Matrix.Translation(Vector((-_hips.x, -_hips.y, -_floor))))
arm.matrix_world = TRANSFORM @ arm.matrix_world
bpy.ops.object.select_all(action="DESELECT")
arm.select_set(True)
body.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.context.view_layer.update()

NEW = hb.renames(ROLES)
taken = set(NEW.values())
for b in list(arm.data.bones):  # an unmapped bone that already has a profile name steps aside
    if b.name in taken and b.name not in NEW:
        b.name = b.name + "_rig"
for old, new in NEW.items():
    arm.data.bones[old].name = new
    if old in body.vertex_groups and new not in body.vertex_groups:
        body.vertex_groups[old].name = new


def nm(bone):
    return NEW.get(bone, bone) if bone else None


CH = {"hips": nm(CH0["hips"]), "spine": [nm(b) for b in CH0["spine"]], "head": [nm(b) for b in CH0["head"]],
      "arms": {s: [nm(b) for b in CH0["arms"][s]] for s in hb.SIDES},
      "legs": {s: [nm(b) for b in CH0["legs"][s]] for s in hb.SIDES}}

# ---- 3. the mesh: reduction, weights, normal map ------------------------------------------
mesh_report = {"format": "forge-mesh-report", "version": 1, "status": "pending review", "problems": []}
tris_in = sum(len(p.vertices) - 2 for p in body.data.polygons)
if not body.data.uv_layers:
    mesh_report["problems"].append("the mesh has no UV layer")
mats = [m for m in body.data.materials if m is not None]
base_node = None
if mats and mats[0].use_nodes:
    nt = mats[0].node_tree
    base_node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None and any(
        lk.to_socket.name == "Base Color" for lk in n.outputs["Color"].links)), None)
if len(mats) != 1:
    mesh_report["problems"].append("the mesh has %d materials, 1 is expected" % len(mats))
if base_node is None:
    mesh_report["problems"].append("the material has no base-colour texture")
if mesh_report["problems"]:
    mesh_report.update({"valid": False, "triangles_in": tris_in})
    write_json("mesh_report.json", mesh_report)
    fail(4, "the mesh cannot be exported: " + "; ".join(mesh_report["problems"]))
mat = mats[0]
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

high = None
bake_offset = None
BAKE_FROM = None  # the file name of the bake source when it is used
if a.bake_source:
    before = set(bpy.data.objects)
    ext = os.path.splitext(a.bake_source)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=os.path.abspath(a.bake_source))
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=os.path.abspath(a.bake_source))
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=os.path.abspath(a.bake_source))
    else:
        fail(2, "unknown bake source format: %s" % a.bake_source)
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    new = [o for o in new if o.name in bpy.data.objects and o.type == "MESH"]
    if not new:
        fail(2, "the bake source has no mesh: %s" % a.bake_source)
    bpy.ops.object.select_all(action="DESELECT")
    for o in new:
        o.parent = None
        o.select_set(True)
    bpy.context.view_layer.objects.active = new[0]
    if len(new) > 1:
        bpy.ops.object.join()
    high = bpy.context.view_layer.objects.active
    high.matrix_world = TRANSFORM @ high.matrix_world
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bake_offset, bake_fit = fit_bake_source(high, body)
    mesh_report["bake_source_fit"] = bake_fit
    if not bake_fit["used"]:
        # A normal map baked from a mesh that does not line up would be broken: bake
        # without it (from the mesh before the reduction, or the texture relief only).
        mesh_report["problems"].append(
            "the bake source does not line up with the game mesh (90th percentile distance %.3f m, tolerance "
            "%.3f m, after fitting its bounds): not used for the normal map" % (
                bake_fit["distance_after_m"]["p90"], bake_fit["tolerance_m"]))
        print("BAKE SOURCE REJECTED", bake_fit, flush=True)
        bpy.data.objects.remove(high)
        high = None
    else:
        BAKE_FROM = os.path.basename(a.bake_source)
        if bake_fit["corrected"]:
            print("BAKE SOURCE FITTED scale %.4f translation %s" % (bake_fit["scale"], bake_fit["translation_m"]),
                  flush=True)

REDUCED = tris_in > a.tris
if REDUCED and high is None:
    high = body.copy()
    high.data = body.data.copy()
    bpy.context.scene.collection.objects.link(high)
    high.parent = None
if high is not None:
    for m in list(high.modifiers):
        high.modifiers.remove(m)
    high.vertex_groups.clear()
if REDUCED:
    mod = body.modifiers.new("cut", "DECIMATE")
    mod.ratio = a.tris / tris_in
    bpy.context.view_layer.objects.active = body
    while body.modifiers[0].name != "cut":
        bpy.ops.object.modifier_move_up(modifier="cut")
    bpy.ops.object.modifier_apply(modifier="cut")
    bm = bmesh.new()
    bm.from_mesh(body.data)
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(body.data)
    bm.free()
tris_out = sum(len(p.vertices) - 2 for p in body.data.polygons)
print("MESH", tris_in, "->", tris_out, "triangles", "(reduced)" if REDUCED else "(kept)", flush=True)

# Weights: at most 4 influences, normalised.
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.context.view_layer.objects.active = body
if body.vertex_groups:
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=MAX_INFLUENCES)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
bone_names = set(b.name for b in arm.data.bones)
deform_idx = set(g.index for g in body.vertex_groups if g.name in bone_names)
unweighted = 0
max_inf = 0
worst_sum = 0.0
for v in body.data.vertices:
    ws = [g.weight for g in v.groups if g.group in deform_idx and g.weight > 0.0]
    if not ws:
        unweighted += 1
        continue
    max_inf = max(max_inf, len(ws))
    worst_sum = max(worst_sum, abs(sum(ws) - 1.0))

normal_img = bpy.data.images.new("body_normal", a.tex, a.tex, alpha=False)
normal_img.colorspace_settings.name = "Non-Color"
BAKED = high is not None
DEVICE_USED = None
if BAKED:
    bake_node = nt.nodes.new("ShaderNodeTexImage")
    bake_node.image = normal_img
    nt.nodes.active = bake_node
    high.data.materials.clear()
    high.data.materials.append(bpy.data.materials.new("high"))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    DEVICE_USED = "CPU"
    if a.device == "GPU":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = kind
            except TypeError:
                continue
            prefs.get_devices()
            usable = [d for d in prefs.devices if d.type == kind]
            if usable:
                for d in prefs.devices:
                    d.use = d.type == kind
                DEVICE_USED = "GPU " + kind
                break
        if DEVICE_USED == "CPU":
            print("WARNING no GPU device for Cycles; baking on the CPU", flush=True)
    scene.cycles.device = "GPU" if DEVICE_USED != "CPU" else "CPU"
    scene.cycles.samples = 1
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    _ext = max(body.dimensions)
    bpy.ops.object.bake(type="NORMAL", use_selected_to_active=True, cage_extrusion=0.006 * _ext,
                        max_ray_distance=0.02 * _ext, normal_space="TANGENT", margin=8)
    nt.nodes.remove(bake_node)
    bpy.data.objects.remove(high)
    print("BAKE", a.tex, "on", DEVICE_USED, "from", "the bake source" if BAKE_FROM else "the mesh before reduction",
          flush=True)
_nn = nt.nodes.new("ShaderNodeTexImage")
_nn.image = normal_img
_nm = nt.nodes.new("ShaderNodeNormalMap")
nt.links.new(_nn.outputs["Color"], _nm.inputs["Color"])
nt.links.new(_nm.outputs["Normal"], bsdf.inputs["Normal"])

if unweighted:
    mesh_report["problems"].append("%d vertices have no weight" % unweighted)
if worst_sum > 1e-3:
    mesh_report["problems"].append("weights are not normalised (worst sum off by %.4f)" % worst_sum)
mesh_report.update({
    "uv_layers": len(body.data.uv_layers), "materials": len(mats), "base_color_texture": base_node.image.name,
    "texture_size": list(base_node.image.size), "vertices": len(body.data.vertices),
    "unweighted_vertices": unweighted, "weight_sum_error": round(worst_sum, 6),
    "max_influences": max_inf, "influence_limit": MAX_INFLUENCES,
    "triangles_in": tris_in, "triangles": tris_out, "triangle_budget": a.tris, "reduced": REDUCED,
    "seam_vertices_merged": _merged,
    "normal_map": a.tex, "normal_baked": BAKED, "bake_device": DEVICE_USED,
    "bake_source": BAKE_FROM or (None if not BAKED else "mesh before reduction"),
    "bake_source_rejected": os.path.basename(a.bake_source) if a.bake_source and not BAKE_FROM else None,
    "bake_source_offset_m": None if bake_offset is None else round(bake_offset, 4),
})
mesh_report["valid"] = not mesh_report["problems"]
mesh_report["flags"] = list(mesh_report["problems"])
write_json("mesh_report.json", mesh_report)

# ---- 4. the skeleton and the retarget -----------------------------------------------------
mw = arm.matrix_world.copy()
mw3 = mw.to_3x3()
bones = arm.data.bones


def head(name):
    return mw @ bones[name].head_local


def tail(name):
    return mw @ bones[name].tail_local


coords = [body.matrix_world @ v.co for v in body.data.vertices]
zmin = min(c.z for c in coords)
zmax = max(c.z for c in coords)
H = zmax - zmin
cx, cy = head(CH["hips"]).x, head(CH["hips"]).y
LEGS = [CH["legs"]["left"], CH["legs"]["right"]]
ARMS = [CH["arms"]["left"], CH["arms"]["right"]]
ANKLE = (head(LEGS[0][2]).z - zmin) / H


def bdir(v):
    """A direction in body coordinates (side, forward, up) in the rig's world."""
    return LEFT * v[0] + FRONT * v[1] + UP * v[2]


def rest_body_point(side, fwd, up):
    """A point in body space (body heights) on the rest model, widened across."""
    return Vector((cx, cy, zmin)) + LEFT * (side * H * a.widen) + FRONT * (fwd * H) + UP * (up * H)


# Held-item classes with their sockets averaged over the items (metres, item space) and the
# box (item space) that contains them.
CLASSES, ITEMS = {}, {}


def _item_box(path, it):
    if isinstance(it.get("box"), list) and len(it["box"]) == 2:
        return [list(map(float, it["box"][0])), list(map(float, it["box"][1]))]
    glb = os.path.join(os.path.dirname(path), it.get("name", os.path.splitext(os.path.basename(path))[0]) + ".glb")
    if not os.path.exists(glb):
        return None
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=glb)
    new = [o for o in bpy.data.objects if o not in before]
    pts = [o.matrix_world @ v.co for o in new if o.type == "MESH" for v in o.data.vertices]
    for o in new:
        bpy.data.objects.remove(o)
    if not pts:
        return None
    return [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]]


if a.items:
    for fn in sorted(os.listdir(a.items)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(a.items, fn)
        with open(path, encoding="utf-8-sig") as f:
            it = json.load(f)
        if not isinstance(it, dict) or "class" not in it or "sockets" not in it:
            continue
        name = it.get("name", fn[:-5])
        ITEMS[name] = it
        c = CLASSES.setdefault(it["class"], {"items": [], "sum": {}, "box": None})
        c["items"].append(name)
        for k, v in it["sockets"].items():
            s = c["sum"].setdefault(k, [Vector((0, 0, 0)), 0])
            s[0] += Vector(v)
            s[1] += 1
        box = _item_box(path, it)
        if box:
            c["box"] = box if c["box"] is None else [[min(c["box"][0][i], box[0][i]) for i in range(3)],
                                                   [max(c["box"][1][i], box[1][i]) for i in range(3)]]
SOCKETS = {cls: {k: s[0] / s[1] for k, s in c["sum"].items()} for cls, c in CLASSES.items()}
for cls in SOCKETS:
    SOCKETS[cls].setdefault("grip", Vector((0, 0, 0)))


def place(cls, socket, at, direction, roll_up=UP):
    """The item's matrix (item space metres -> rig world) with `socket` at `at`, its +x along
    `direction`."""
    x = direction.normalized()
    y = roll_up.cross(x)
    if y.length < 1e-6:
        y = FRONT.cross(x)
    y = y.normalized()
    z = x.cross(y).normalized()
    rot = Matrix((x, y, z)).transposed()
    m = rot.to_4x4()
    m.translation = at - rot @ socket
    return m


LIMB_BONES = {"larm": ARMS[0][2], "rarm": ARMS[1][2], "lleg": LEGS[0][1], "rleg": LEGS[1][1]}
LIMB_LENGTH = {k: (bones[b].length + bones[bones[b].parent.name].length) for k, b in LIMB_BONES.items()}
empties = {}
for name in LIMB_BONES:
    t = bpy.data.objects.new("ik_" + name, None)
    p = bpy.data.objects.new("pole_" + name, None)
    bpy.context.scene.collection.objects.link(t)
    bpy.context.scene.collection.objects.link(p)
    empties[name] = (t, p)
SHORT = {}  # limb -> worst reach shortfall (model units) of the clip being made


def reset():
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)
        pb.location = (0, 0, 0)
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    arm.pose.bones[CH["head"][-1]].scale = (a.head,) * 3
    for ch in ARMS:
        arm.pose.bones[ch[3]].scale = (a.hands,) * 3
    for ch in LEGS:
        arm.pose.bones[ch[2]].scale = (a.feet,) * 3
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()


def world_of(name):
    pb = arm.pose.bones[name]
    return mw @ pb.head, mw @ pb.tail


def aim_bone(name, direction):
    """Turns a bone (pose) so that it points along `direction` (rig world)."""
    pb = arm.pose.bones[name]
    m = pb.matrix.copy()
    cur = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
    want = (mw3.inverted() @ direction).normalized()
    q = cur.rotation_difference(want)
    new = (q.to_matrix() @ m.to_3x3()).to_4x4()
    new.translation = m.translation
    pb.matrix = new
    bpy.context.view_layer.update()


def frame_rot(l0, u0, l1, u1):
    """The rotation that takes the frame (left l0, up u0) to (l1, u1)."""
    def basis(lv, u):
        u = u.normalized()
        lv = (lv - u * lv.dot(u)).normalized()
        return Matrix((lv, u, lv.cross(u))).transposed()
    return basis(l1, u1) @ basis(l0, u0).transposed()


def retarget(j, src_pelvis):
    """FK: every mapped bone pointed as the clip's joints `j` (body coordinates) point."""
    P = {k: Vector(v) for k, v in j.items()}

    def seg(s, e):
        return bdir(P[e] - P[s]).normalized()
    pb = arm.pose.bones[CH["hips"]]
    l0 = head(LEGS[0][0]) - head(LEGS[1][0])
    u0 = tail(CH["hips"]) - head(CH["hips"])
    R = frame_rot(l0, u0, bdir(P["l_hip"] - P["r_hip"]), bdir(P["spine"] - P["pelvis"]))
    m = pb.matrix.copy()
    new = (mw3.inverted() @ R @ mw3 @ m.to_3x3()).to_4x4()
    shift = mw3.inverted() @ (LEFT * (P["pelvis"][0] * H) + FRONT * (P["pelvis"][1] * H)
                              + UP * ((P["pelvis"][2] - src_pelvis) * H))
    new.translation = m.translation + shift
    pb.matrix = new
    bpy.context.view_layer.update()
    pts = [P["pelvis"], P["spine"], P["chest"], P["neck"]]
    n = len(CH["spine"])
    for i, b in enumerate(CH["spine"]):
        t0, t1 = 3 * i / n, 3 * (i + 1) / n

        def at(t):
            k = min(int(t), 2)
            return pts[k].lerp(pts[k + 1], t - k)
        aim_bone(b, bdir(at(t1) - at(t0)))
    if len(CH["head"]) == 2:
        aim_bone(CH["head"][0], seg("neck", "head"))
        aim_bone(CH["head"][1], seg("head", "head_top"))
    else:
        aim_bone(CH["head"][0], seg("neck", "head_top"))
    for side, ch in (("l", ARMS[0]), ("r", ARMS[1])):
        if ch[0]:
            aim_bone(ch[0], seg(side + "_collar", side + "_shoulder"))
        aim_bone(ch[1], seg(side + "_shoulder", side + "_elbow"))
        aim_bone(ch[2], seg(side + "_elbow", side + "_wrist"))
        aim_bone(ch[3], seg(side + "_wrist", side + "_hand"))
    for side, ch in (("l", LEGS[0]), ("r", LEGS[1])):
        aim_bone(ch[0], seg(side + "_hip", side + "_knee"))
        aim_bone(ch[1], seg(side + "_knee", side + "_ankle"))
        aim_bone(ch[2], seg(side + "_ankle", side + "_toe"))


def ik(name, target, pole):
    bone = LIMB_BONES[name]
    t, p = empties[name]
    t.location = target
    p.location = pole
    root = world_of(bones[bone].parent.name)[0]
    short = (target - root).length - LIMB_LENGTH[name]
    SHORT[name] = max(SHORT.get(name, 0.0), short)
    c = arm.pose.bones[bone].constraints.new("IK")
    c.target = t
    c.pole_target = p
    c.pole_angle = 0.0 if name.endswith("leg") else -math.pi / 2
    c.chain_count = 2
    c.use_tail = True


def pole_of(upper, lower):
    """A pole beyond the middle joint (elbow, knee), on the side it bends to now (FK)."""
    start = world_of(upper)[0]
    mid, end = world_of(lower)
    out = mid - (start + end) / 2
    if out.length < 1e-6:
        out = -FRONT
    return mid + out.normalized() * 0.4 * H


def chest_delta():
    """How the top of the spine has moved from the rest pose: a rig-world matrix."""
    top = CH["spine"][-1]
    return (mw @ arm.pose.bones[top].matrix) @ (mw @ bones[top].matrix_local).inverted()


def hand_matrix(ch):
    """The palm (the hand bone's middle) with item axes: x forward from the fist (across the
    hand bone, towards the body's front), z along the forearm, rig world."""
    h0, h1 = world_of(ch[3])
    along = (h1 - h0).normalized()
    x = FRONT - along * FRONT.dot(along)
    if x.length < 1e-4:
        x = LEFT.copy()
    x = x.normalized()
    z = -along
    y = z.cross(x).normalized()
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = h0 + (h1 - h0) * 0.5
    return m


def placement(anim, kind):
    holds = library.get("holds") or {}
    cls = kind.split("_")[0]
    p = anim.get("hold") or holds.get(kind) or holds.get(cls)
    if p:
        return dict(p)
    # No placement in the library: the item follows the right palm and both hands stay as
    # the clip has them.
    return {"follow": "right_hand", "socket": "grip", "hands": {}}


def hold(cls, pl):
    """The held item's matrix (item space -> rig world), or None for an item that only
    follows the right palm without sockets."""
    follow = pl.get("follow", "right_hand")
    sockets = SOCKETS.get(cls, {"grip": Vector((0, 0, 0))})
    socket = sockets.get(pl.get("socket", "grip"), Vector((0, 0, 0)))
    if follow in ("chest", "body"):
        m = place(cls, socket, rest_body_point(*pl.get("at", (0.0, 0.2, 0.6))),
                  bdir(pl.get("point", (0.0, 1.0, 0.0))), bdir(pl.get("up", (0.0, 0.0, 1.0))))
        return chest_delta() @ m if follow == "chest" else m
    if follow == "right_hand":
        return hand_matrix(ARMS[1]) @ Matrix.Translation(-socket)
    raise ValueError("unknown hold follow mode: %s" % follow)


def hands_on(cls, m, pl):
    hands = pl.get("hands") or {}
    sockets = SOCKETS.get(cls, {})
    carried = bool(pl.get("arms_from_rest"))
    if carried:
        for ch in ARMS:
            for b in ch[1:4]:
                pb = arm.pose.bones[b]
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.location = (0, 0, 0)
        bpy.context.view_layer.update()
    chest = chest_delta()
    targets = {}
    for side, limb, ch, sx in (("right", "rarm", ARMS[1], -1.0), ("left", "larm", ARMS[0], 1.0)):
        name = hands.get(side)
        if not name or name not in sockets:
            continue
        target = m @ sockets[name]
        pole = chest @ rest_body_point(0.22 * sx, -0.06, 0.48) if carried else pole_of(ch[1], ch[2])
        ik(limb, target, pole)
        targets[side] = name
    return targets


def plant(j):
    """The feet where the clip stood (its stance, not widened), lifted no lower than the rest
    ankle."""
    for side, ch, limb in (("l", LEGS[0], "lleg"), ("r", LEGS[1], "rleg")):
        ank = Vector(j[side + "_ankle"])
        at = Vector((cx, cy, zmin)) + LEFT * (ank[0] * H) + FRONT * (ank[1] * H) + UP * (max(ank[2], ANKLE) * H)
        ik(limb, at, pole_of(ch[0], ch[1]))


def ground_lying():
    lowest = min(world_of(b.name)[0].z for b in arm.data.bones if b.name not in ("item", "item_l"))
    pb = arm.pose.bones[CH["hips"]]
    m = pb.matrix.copy()
    m.translation = m.translation + mw3.inverted() @ Vector((0, 0, zmin + 0.035 * H - lowest))
    pb.matrix = m
    bpy.context.view_layer.update()


def ground():
    lowest = min(world_of(LEGS[0][2])[0].z, world_of(LEGS[1][2])[0].z)
    pb = arm.pose.bones[CH["hips"]]
    m = pb.matrix.copy()
    m.translation = m.translation + mw3.inverted() @ Vector((0, 0, zmin + ANKLE * H - lowest))
    pb.matrix = m
    bpy.context.view_layer.update()


# The item bones: "item" (the right hand's item, at its grip with the item's axes) under the
# hips, "item_l" on the left palm; item_ref, a tiny mesh on "item" with the identity in item
# space, tells the game how an item GLB sits on that bone.
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
ITEM_LEN = 0.1 * H
eb = arm.data.edit_bones.new("item")
eb.head = (0.0, 0.0, 0.0)
eb.tail = (0.0, ITEM_LEN, 0.0)
eb.parent = arm.data.edit_bones[CH["hips"]]
eb.use_deform = False
_lh = arm.data.edit_bones[ARMS[0][3]]
_palm = (_lh.head + _lh.tail) * 0.5
el = arm.data.edit_bones.new("item_l")
el.head = _palm
el.tail = _palm + Vector((0.0, ITEM_LEN, 0.0))
el.parent = _lh
el.use_deform = False
bpy.ops.object.mode_set(mode="OBJECT")
bones = arm.data.bones
_ref_mesh = bpy.data.meshes.new("item_ref")
_ref_mesh.from_pydata([(0, 0, 0), (0.01, 0, 0), (0, 0, 0.01)], [], [(0, 1, 2)])
ref = bpy.data.objects.new("item_ref", _ref_mesh)
bpy.context.scene.collection.objects.link(ref)
ref.parent = arm
ref.parent_type = "BONE"
ref.parent_bone = "item"
ref.matrix_parent_inverse = Matrix.Identity(4)
ref.matrix_basis = Matrix.Translation(Vector((0, -ITEM_LEN, 0)))

ORDER = []
_seen = set()
for b in arm.data.bones:  # parents before children
    stack = [b]
    while stack[-1].parent is not None and stack[-1].parent.name not in _seen:
        stack.append(stack[-1].parent)
    for x in reversed(stack):
        if x.name not in _seen:
            _seen.add(x.name)
            ORDER.append(x.name)


def capture(m):
    """Every bone's posed armature-space matrix now (constraints applied), the item's too."""
    bpy.context.view_layer.update()
    mats = {n: arm.pose.bones[n].matrix.copy() for n in ORDER if n != "item"}
    if m is not None:
        mats["item"] = mw.inverted() @ Matrix.Translation(m.translation) @ m.to_3x3().normalized().to_4x4()
    else:
        mats["item"] = mw.inverted() @ Matrix.Translation(Vector((cx, cy, zmin))) @ Matrix.Diagonal((0.001,) * 3 + (1.0,))
    return mats


FPS = 120.0  # scene frames per second: keys sit at exact clip times
bpy.context.scene.render.fps = int(FPS)
bpy.context.scene.render.fps_base = 1.0
KEY_SIDE = {}


def key(action_name, frames, fps, loop):
    """An action keyed from captured matrices, one key a clip frame, plus a closing key at
    the clip's end (the first frame again for a loop, the last held otherwise)."""
    reset()
    act = bpy.data.actions.new(action_name)
    act.use_fake_user = True
    arm.animation_data_create()
    arm.animation_data.action = act
    bpy.ops.object.mode_set(mode="POSE")
    seq = list(frames) + [frames[0] if loop else frames[-1]]
    for i, mats in enumerate(seq):
        for n in ORDER:
            arm.pose.bones[n].matrix = mats[n]
            bpy.context.view_layer.update()
        f = i * FPS / fps
        for n in ORDER:
            pb = arm.pose.bones[n]
            # One side of the quaternion double cover per bone (blends go the short way).
            q = pb.rotation_quaternion
            refq = KEY_SIDE.setdefault(n, q.copy())
            if q.dot(refq) < 0.0:
                pb.rotation_quaternion = -q
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)
            pb.keyframe_insert("scale", frame=f)
    bpy.ops.object.mode_set(mode="OBJECT")
    for fc in act.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    track = arm.animation_data.nla_tracks.new()
    track.name = action_name
    track.strips.new(action_name, 0, act)
    arm.animation_data.action = None
    return len(frames) / fps


info = {"format": "forge-humanoid", "version": 1, "status": "pending review", "name": a.name,
        "height": H, "widen": a.widen, "head": a.head, "hands": a.hands, "feet": a.feet,
        "ankle_height": ANKLE * H, "front": [FRONT.x, FRONT.z, -FRONT.y], "animations": [],
        "bones": {r: hb.godot_name(r) for r in ROLES}, "unmapped_bones": [nm(b) for b in doc["unmapped"]],
        "bone_map": {"source": doc["source"], "file": "bone_map.json"},
        "item_bones": {"right": "item", "left": "item_l", "reference": "item_ref"},
        "classes": {cls: {"items": c["items"], "sockets": {k: list(v) for k, v in SOCKETS[cls].items()},
                          "box": c["box"]} for cls, c in CLASSES.items()},
        "items": {n: {"class": it["class"]} for n, it in ITEMS.items()},
        "mesh": {"triangles": tris_out, "reduced": REDUCED, "normal_map": a.tex, "normal_baked": BAKED,
                 "report": "mesh_report.json"}}

only = [w for w in a.only.split(",") if w]
REACH = {}
for mod in body.modifiers:  # the skinned mesh is not needed while posing
    mod.show_viewport = False
for name, anim in (library.get("anims") or {}).items():
    if only and not any(name.startswith(w) for w in only):
        continue
    frames_in = anim.get("frames") or []
    if not frames_in:
        continue
    kind = anim.get("holds")
    cls = kind.split("_")[0] if kind else None
    pl = placement(anim, kind) if kind else None
    src_pelvis = float(anim.get("pelvis_stand", frames_in[0]["pelvis"][2]))
    src_ankle = min(f[s + "_ankle"][2] for f in frames_in for s in ("l", "r"))
    feet = anim.get("feet", "clip")
    SHORT.clear()
    frames = []
    hand_targets = {}
    for j in frames_in:
        reset()
        retarget(j, src_pelvis)
        if feet == "clip":
            ground()
            plant(j)
        elif feet == "lie":
            ground_lying()
        elif feet == "walk":
            for side, ch, limb in (("l", LEGS[0], "lleg"), ("r", LEGS[1], "rleg")):
                ank = Vector(j[side + "_ankle"])
                at = Vector((cx, cy, zmin)) + LEFT * (ank[0] * H) + FRONT * (ank[1] * H) \
                    + UP * ((ANKLE + ank[2] - src_ankle) * H)
                ik(limb, at, pole_of(ch[0], ch[1]))
        m = None
        if pl is not None:
            bpy.context.view_layer.update()
            m = hold(cls, pl)
            hand_targets = hands_on(cls, m, pl)
        frames.append(capture(m))
    seconds = key(name, frames, float(anim.get("fps", 24.0)), bool(anim.get("loop", False)))
    REACH[name] = max([0.0] + list(SHORT.values())) * 100.0
    clip_kind = anim.get("kind") or next((k for k in ("idle", "walk", "start", "stop") if name.startswith(k)), None)
    info["animations"].append({
        "name": name, "kind": clip_kind, "frames": len(frames), "fps": float(anim.get("fps", 24.0)),
        "loop": bool(anim.get("loop", False)), "seconds": seconds, "holds": kind, "class": cls,
        "follow": pl.get("follow") if pl else None, "hands": hand_targets, "feet": feet,
        "stance": anim.get("stance"), "travel": anim.get("travel"), "source": anim.get("source"),
        "pose_frame": anim.get("pose_frame")})
    print("ANIMATION", name, len(frames), "frames, reach shortfall %.2f cm" % REACH[name], flush=True)
for mod in body.modifiers:
    mod.show_viewport = True

# ---- 5. the regions mask and the texture relief ------------------------------------------
from PIL import Image as PILImage, ImageDraw  # noqa: E402

img = base_node.image
w, h = img.size
base = np.empty(w * h * 4, np.float32)
img.pixels.foreach_get(base)
base = base.reshape(h, w, 4)
neck_z = head(CH["head"][0]).z
uv = body.data.uv_layers.active.data
m_head = PILImage.new("L", (w, h), 0)
m_top = PILImage.new("L", (w, h), 0)
dh, dt = ImageDraw.Draw(m_head), ImageDraw.Draw(m_top)
head_top = zmax - (zmax - neck_z) * 0.45
for poly in body.data.polygons:
    zs = [(body.matrix_world @ body.data.vertices[v].co).z for v in poly.vertices]
    if min(zs) < neck_z:
        continue
    pts = [(uv[li].uv[0] * w, (1 - uv[li].uv[1]) * h) for li in poly.loop_indices]
    dh.polygon(pts, fill=255)
    if min(zs) > head_top:
        dt.polygon(pts, fill=255)
in_head = np.flipud(np.asarray(m_head) > 0)
in_top = np.flipud(np.asarray(m_top) > 0)
rgb = base[..., :3]
mx, mn = rgb.max(-1), rgb.min(-1)
sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
warm = (rgb[..., 0] >= rgb[..., 1]) & (rgb[..., 1] >= rgb[..., 2] * 0.85)
skin = in_head & warm & (mx > 0.30) & (sat > 0.12) & (sat < 0.70)
hair = in_top & ~skin & warm & (sat > 0.18) & (mx < 0.45)
lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
mask = np.zeros((h, w, 3), np.uint8)
mask[..., 0] = skin * 255
mask[..., 1] = hair * 255
regions = {"mask": "regions_mask.png", "skin": float(lum[skin].mean()) if skin.any() else None,
           "hair": float(lum[hair].mean()) if hair.any() else None, "skin_pixels": int(skin.sum()),
           "hair_pixels": int(hair.sum()), "glow_pixels": None}
if a.glow:
    _hue = np.degrees(np.arctan2(np.sqrt(3.0) * (rgb[..., 1] - rgb[..., 2]),
                                 2.0 * rgb[..., 0] - rgb[..., 1] - rgb[..., 2])) % 360.0
    _lo, _hi = (float(x) for x in a.glow_hues.split(","))
    glow = ~skin & ~hair & (mx > 0.55) & (sat > 0.35) & (_hue > _lo) & (_hue < _hi)
    mask[..., 2] = glow * 255
    regions["glow_pixels"] = int(glow.sum())
    regions["glow_hues"] = [_lo, _hi]
PILImage.fromarray(np.flipud(mask)).save(os.path.join(OUT, "regions_mask.png"))
info["regions"] = regions


def _blur(x, r):
    k = np.ones(2 * r + 1, np.float32) / (2 * r + 1)
    x = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, x)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, x)


# The relief of the painted texture as normal detail: the slopes of a high-passed value.
_hgt = _blur(lum, 1) - _blur(lum, 12)
_gy, _gx = np.gradient(_hgt)
_n = np.dstack([-_gx * 6.0, -_gy * 6.0, np.ones_like(_gx)])
_n /= np.linalg.norm(_n, axis=-1, keepdims=True)
_nb = np.empty(normal_img.size[0] * normal_img.size[1] * 4, np.float32)
normal_img.pixels.foreach_get(_nb)
_nb = _nb.reshape(normal_img.size[1], normal_img.size[0], 4)
if _nb.shape[:2] != _n.shape[:2]:
    _n = np.asarray(PILImage.fromarray(((_n * 0.5 + 0.5) * 255).astype(np.uint8)).resize(
        (_nb.shape[1], _nb.shape[0]))).astype(np.float32) / 255.0 * 2.0 - 1.0
if BAKED:
    _b = _nb[..., :3] * 2.0 - 1.0
    _m = np.dstack([_b[..., 0] + _n[..., 0], _b[..., 1] + _n[..., 1], _b[..., 2] * _n[..., 2]])
    _m /= np.linalg.norm(_m, axis=-1, keepdims=True)
else:
    _m = _n
_nb[..., :3] = _m * 0.5 + 0.5
_nb[..., 3] = 1.0
normal_img.pixels.foreach_set(_nb.ravel())
normal_img.update()

# ---- export -------------------------------------------------------------------------------
reset()
bpy.ops.object.select_all(action="DESELECT")
arm.select_set(True)
body.select_set(True)
ref.select_set(True)
body.name = "body"
GLB = os.path.join(OUT, a.name + ".glb")
bpy.ops.export_scene.gltf(filepath=GLB, export_format="GLB", use_selection=True,
                          export_animations=True, export_animation_mode="NLA_TRACKS", export_skins=True,
                          export_def_bones=False, export_frame_range=False, export_force_sampling=False,
                          export_optimize_animation_size=False, export_image_format="JPEG",
                          export_jpeg_quality=92,
                          # No Draco: the GLB stays plain (and the exporter does not log the
                          # missing Draco library as an error).
                          export_draco_mesh_compression_enable=False)
print("EXPORTED", GLB, flush=True)
# Blender's glTF exporter checks for its Draco library whenever it starts and logs "ERROR
# Draco mesh compression is not available" when the module build has none.
print("NOTE the Draco line above is harmless: the GLB is written without Draco compression", flush=True)
info["seconds_export"] = round(time.time() - T0, 1)
write_json("model.json", info)

# ---- 6. the motion report: every clip played on the exported GLB --------------------------
if not library:
    print("DONE", len(info["animations"]), "animations", flush=True)
    finish(0)

THRESH = {"foot_slide_cm": a.max_foot_slide, "hand_socket_cm": a.max_hand_socket,
          "reach_shortfall_cm": a.max_reach, "stretch_p95": a.max_stretch, "penetration_cm": a.max_penetration}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.render.fps = 24
bpy.ops.import_scene.gltf(filepath=GLB)
rarm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
rbody = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("body"))
rref = next((o for o in bpy.data.objects if o.name.startswith("item_ref")), None)
if rarm.animation_data is None:
    rarm.animation_data_create()
for tr in rarm.animation_data.nla_tracks:
    tr.mute = True
scene = bpy.context.scene
scene_fps = scene.render.fps / scene.render.fps_base
dg = bpy.context.evaluated_depsgraph_get()


def rest_edges():
    me = rbody.data
    ev = np.empty(len(me.edges) * 2, np.int64)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    ln = np.linalg.norm(co[ev[:, 0]] - co[ev[:, 1]], axis=1)
    keep = ln > 1e-6
    return ev[keep], ln[keep]


EDGES, REST_LEN = rest_edges()


def posed_mesh():
    ob = rbody.evaluated_get(dg)
    me = ob.to_mesh()
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mwb = np.array(ob.matrix_world)
    co = co @ mwb[:3, :3].T + mwb[:3, 3]
    ob.to_mesh_clear()
    return co


def bone_head(name):
    return rarm.matrix_world @ rarm.pose.bones[name].head


def find_action(name):
    """The re-imported action of a clip (the importer may add the armature's name)."""
    if name in bpy.data.actions:
        return bpy.data.actions[name]
    names = set(c["name"] for c in info["animations"])
    for act in sorted(bpy.data.actions, key=lambda x: len(x.name)):
        rest = act.name[len(name):]
        if act.name.startswith(name) and rest[:1] in ("_", ".") and act.name not in names:
            return act
    return None


def stance_runs(frames_idx, n, loop):
    """Runs of consecutive stance frames (unwrapped indices; a loop's run may wrap)."""
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


def contact_free_vertices():
    """Vertices of the body whose main bone is not a hand, a finger or a forearm: the parts
    a held item may not enter (the hands hold it)."""
    hands = set()
    for name in ("LeftHand", "RightHand"):
        if name in rarm.data.bones:
            b = rarm.data.bones[name]
            hands.update([name] + [c.name for c in b.children_recursive])
    hands.update(n for n in ("LeftLowerArm", "RightLowerArm") if n in rarm.data.bones)
    idx = {g.index: g.name for g in rbody.vertex_groups}
    keep = []
    for v in rbody.data.vertices:
        if v.groups:
            g = max(v.groups, key=lambda x: x.weight)
            if idx.get(g.group) in hands:
                continue
        keep.append(v.index)
    return np.array(keep, np.int64)


FREE = contact_free_vertices()


def box_depth(co, item_m, box):
    """How deep the body's contact-free vertices are inside the item's box (metres)."""
    inv = np.array(item_m.inverted())
    p = co[FREE] @ inv[:3, :3].T + inv[:3, 3]
    lo, hi = np.array(box[0]), np.array(box[1])
    inside = np.all((p > lo) & (p < hi), axis=1)
    if not inside.any():
        return 0.0
    q = p[inside]
    return float(np.minimum(q - lo, hi - q).min(axis=1).max())


report = {"format": "forge-motion-report", "version": 1, "status": "pending review", "model": os.path.basename(GLB),
          "thresholds": THRESH, "clips": {}, "flags": []}
SIDE_BONES = {"left": ("LeftFoot", "LeftHand"), "right": ("RightFoot", "RightHand")}
for clip in info["animations"]:
    act = find_action(clip["name"])
    entry = {"frames": clip["frames"], "found": act is not None}
    if act is None:
        report["clips"][clip["name"]] = entry
        report["flags"].append({"clip": clip["name"], "measure": "missing", "value": None, "threshold": None})
        continue
    rarm.animation_data.action = act
    n, fps = clip["frames"], clip["fps"]
    feet_pos = {"left": [], "right": []}
    hand_d, stretch, pen = [], [], []
    cls = clip["class"]
    sockets = info["classes"].get(cls, {}).get("sockets", {}) if cls else {}
    box = info["classes"].get(cls, {}).get("box") if cls else None
    for i in range(n):
        f = i / fps * scene_fps
        scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
        dg.update()
        for side in hb.SIDES:
            feet_pos[side].append(bone_head(SIDE_BONES[side][0]))
        item_m = rref.matrix_world.copy() if rref is not None else None
        if item_m is not None and clip["hands"]:
            for side, sock in clip["hands"].items():
                if sock in sockets:
                    hand_d.append((bone_head(SIDE_BONES[side][1]) - item_m @ Vector(sockets[sock])).length * 100.0)
        co = posed_mesh()
        ln = np.linalg.norm(co[EDGES[:, 0]] - co[EDGES[:, 1]], axis=1)
        stretch.append(float(np.percentile(np.abs(ln / REST_LEN - 1.0), 95)))
        if box and item_m is not None and clip["holds"] and len(FREE):
            pen.append(box_depth(co, item_m, box) * 100.0)
    slide = None
    if clip.get("stance"):
        trav = clip.get("travel") or [0.0, 0.0]
        step = (LEFT * float(trav[0]) + FRONT * float(trav[1])) * (info["height"] / max(n, 1))
        # LEFT/FRONT are the rig's world axes; the GLB is in glTF axes after the round trip
        # through Blender's importer, which brings it back to the same Blender axes.
        slide = 0.0
        for side in hb.SIDES:
            for run in stance_runs(clip["stance"].get(side, []), n, clip["loop"]):
                pts = [feet_pos[side][k % n] + step * k for k in run]
                slide = max([slide] + [(p - pts[0]).length * 100.0 for p in pts])
    entry.update({
        "foot_slide_cm": None if slide is None else round(slide, 3),
        "hand_socket_cm": round(max(hand_d), 3) if hand_d else None,
        "reach_shortfall_cm": round(REACH.get(clip["name"], 0.0), 3),
        "stretch_p95": round(max(stretch), 4) if stretch else None,
        "penetration_cm": round(max(pen), 3) if pen else None,
    })
    report["clips"][clip["name"]] = entry
    for k, limit in THRESH.items():
        v = entry.get(k)
        if v is not None and v > limit:
            report["flags"].append({"clip": clip["name"], "measure": k, "value": v, "threshold": limit})
write_json("motion_report.json", report)
print("MOTION REPORT", len(report["clips"]), "clips,", len(report["flags"]), "flags", flush=True)
print("DONE", len(info["animations"]), "animations", flush=True)
finish(0)
