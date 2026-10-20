"""A rigged humanoid as a game-ready skinned GLB: bones renamed to Godot's humanoid profile,
the motion library retargeted onto it, a normal map baked from the high-resolution mesh, a
regions mask and the reports that say whether the result can be trusted.

Runs with a Python that imports bpy (Blender as a module):

  python humanoid_export.py --rig RIG.glb --out DIR [--name model] [--tris 25000] [--tex 2048]
      [--height 1.8] [--head 1.0] [--hands 1.0] [--feet 1.0] [--widen 1.0]
      [--clips LIBRARY.json] [--items DIR] [--glow-colours cyan,emerald [--glow-reference PICTURE]
      [--glow-strength 2] [--glow-settings JSON]] [--glow [--glow-hues 165,225]]
      [--bake-source HIGH.glb] [--bone-map MAP.json] [--device GPU|CPU] [--only idle,walk]
      [--max-foot-slide 2] [--max-hand-socket 2] [--max-reach 1] [--max-stretch 0.5]
      [--max-penetration 2] [--metal-roughness MAP.png] [--material-source PAINTED.glb]
      [--image-format lossless|compact] [--body-frame FRAME.json]

Steps (formats in docs/conventions.md):
1. The bone map: a map given with --bone-map wins, then <out>/bone_map.json when it is
   marked "manual": true, then the proposal from the skeleton's tree
   (humanoid_bones.py). It is validated; an invalid map stops the export with its report in
   <out>/bone_map.json (exit code 3). The exporter never guesses.
2. The model stands on z = 0 over the hips, turned about the vertical so that its front is
   the glTF front (+z), scaled to --height metres, widened across (--widen); the mapped
   bones take their SkeletonProfileHumanoid names, the others keep theirs. The front is the
   body's (humanoid_bones.body_facing: perpendicular to the lines across the hips and the
   shoulders, the feet only breaking a tie), not snapped to an axis: a generated figure may
   stand turned between two axes. Cues that disagree (a twisted figure, feet pointing away)
   are mesh flags, and so is a figure that still faces more than FACING_TOLERANCE_DEG away
   from +z after the turn. With --body-frame (a figure conformed to an approved body,
   humanoid_conform.py's frame.json) the rig already stands in the body's frame: it is not
   stood, turned, scaled or widened, and the figure's floor and height are the body's (the
   clips are scaled by them, not by the outfit's mesh).
3. The mesh is the painted game mesh of the rig. It is reduced only when it has more than
   --tris triangles (the report says so). The normal map (--tex pixels, tangent space,
   Cycles on --device) is baked from --bake-source (the raw high mesh, in the rig mesh's
   coordinates: checked to line up with the game mesh, fitted by a uniform scale and a
   translation when it does not, and left out, with a mesh flag, when it still does not)
   or, without it, from the mesh before the reduction; the relief of the
   painted texture is added to it. The painted texture keeps its size. Weights are limited
   to 4 influences and normalised; <out>/mesh_report.json checks the result.
   The material keeps the rig's maps; --metal-roughness (the paint stage's map) replaces
   the rig's metallic-roughness map, and the maps the rig lacks (metallic-roughness,
   opacity, emissive, occlusion) are carried from --material-source (the painted mesh).
4. With --clips, every clip of the library (canonical joints in body coordinates) is
   retargeted by bone directions, the feet planted as the clip says and the hands put on
   the held item's sockets by IK. A bone's direction is the line from its joint to its
   child's joint (the next mapped bone; the bone's own axis only for the last bone of a
   chain): a rig tool's joints may carry orientations whose axes do not point to their
   children. Without a library the GLB has the rest pose only.
5. Writes <out>/<name>.glb, <out>/model.json and <out>/regions_mask.png (red: skin, green:
   hair of the head, blue: what glows). What glows (glow_regions.py, with --glow-colours, or
   --glow and --glow-hues): texels of the written base colour in those colours, bright,
   saturated and brighter than around them, in small regions, never skin or hair, weighed by
   the reference picture's lit colours (--glow-reference); the GLB's material gets their
   emissive map (strength --glow-strength, KHR_materials_emissive_strength) unless it already
   has an emissive map of its own. The normal, metallic-roughness and
   occlusion maps and an opacity mask are never JPEG: --image-format lossless (default)
   writes every map as PNG, compact the base colour as JPEG (quality 92). The written GLB
   is read back (glb_materials.py): mesh_report.json gets its "materials" section, and a
   slot whose source map existed but is missing or lossy, or a source map that could not
   be carried, is a flag.
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
ap.add_argument("--glow", action="store_true", help="mark what glows (the --glow-hues range) when no --glow-colours")
ap.add_argument("--glow-hues", default="165,225", help="hue range in degrees that glows: low,high")
ap.add_argument("--glow-colours", default="", help="glow colours: names or low-high degrees (glow_regions.py)")
ap.add_argument("--glow-reference", default="", help="the reference picture the paint stage projected (RGBA)")
ap.add_argument("--glow-strength", type=float, default=None, help="emissive strength of what glows")
ap.add_argument("--glow-settings", default="", help="detection settings (JSON object, glow_regions.DEFAULTS)")
ap.add_argument("--bake-source", default="", help="raw high-resolution mesh to bake the normal map from")
ap.add_argument("--bone-map", default="", help="bone map given by the order (wins over everything)")
ap.add_argument("--device", default="GPU", choices=("GPU", "CPU"), help="Cycles device for the bake")
ap.add_argument("--only", default="", help="only the clips whose names start with these (comma separated)")
ap.add_argument("--max-foot-slide", type=float, default=2.0, help="cm, stance frames")
ap.add_argument("--max-hand-socket", type=float, default=2.0, help="cm, holding poses")
ap.add_argument("--max-reach", type=float, default=1.0, help="cm of IK target beyond reach")
ap.add_argument("--max-stretch", type=float, default=0.5, help="95th percentile of edge stretch (|length / rest - 1|)")
ap.add_argument("--max-penetration", type=float, default=2.0, help="cm of the item's box inside the body")
ap.add_argument("--metal-roughness", default="",
                help="the paint stage's metallic-roughness map (roughness green, metallic blue)")
ap.add_argument("--material-source", default="",
                help="the painted mesh the rig was made from: maps the rig's material lacks are carried from it")
ap.add_argument("--image-format", default="lossless", choices=("lossless", "compact"),
                help="lossless: every map PNG; compact: base colour JPEG (quality 92), technical maps PNG")
ap.add_argument("--body-frame", default="",
                help="the frame of an approved body the rig was conformed to (humanoid_conform.py's frame.json): "
                     "the rig is not stood, turned or scaled, and the floor and height are the body's")
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
BODY_FRAME = None
if a.body_frame:
    with open(a.body_frame, encoding="utf-8-sig") as _f:
        _frame = json.load(_f)
    BODY_FRAME = {"height": float(_frame["height"]), "floor": float(_frame["floor"])}
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


# ---- material slots ------------------------------------------------------------------------
# The texture slots of a glTF material as Blender's glTF importer lays them out, and how a
# map missing from the rig's material is carried into it. The exported GLB is read back
# file by file (glb_materials.py): mesh_report.json "materials".
JPEG_QUALITY = 92
LOSSY_EXT = (".jpg", ".jpeg", ".jpe", ".webp")


def image_above(socket, depth=4):
    """(image node, the name of its output) that feeds a shader socket, directly or through
    Separate Color, Normal Map or math nodes; (None, None) when no image does."""
    if socket is None or not socket.is_linked:
        return None, None
    link = socket.links[0]
    node = link.from_node
    if node.type == "TEX_IMAGE":
        return (node, link.from_socket.name) if node.image is not None else (None, None)
    if depth == 0:
        return None, None
    for inp in node.inputs:
        found = image_above(inp, depth - 1)
        if found[0] is not None:
            return found
    return None, None


def socket_of(node, *names):
    return next((node.inputs[n] for n in names if n in node.inputs), None)


def occlusion_group(tree):
    """The glTF settings group node of a material (the one with an Occlusion input), or None."""
    return next((n for n in tree.nodes if n.type == "GROUP" and n.node_tree is not None and "Occlusion" in n.inputs),
                None)


def find_slots(tree, shader):
    """{slot: image node} of the texture slots a material has."""
    out = {"base_color": image_above(socket_of(shader, "Base Color"), 0)[0]}
    node, output = image_above(socket_of(shader, "Alpha"))
    out["opacity"] = node if output == "Alpha" else None
    out["metallic_roughness"] = (image_above(socket_of(shader, "Roughness"))[0]
                                 or image_above(socket_of(shader, "Metallic"))[0])
    out["normal"] = image_above(socket_of(shader, "Normal"))[0]
    out["emissive"] = image_above(socket_of(shader, "Emission Color", "Emission"))[0]
    grp = occlusion_group(tree)
    out["occlusion"] = image_above(grp.inputs["Occlusion"])[0] if grp is not None else None
    return {k: v for k, v in out.items() if v is not None}


def load_map(path):
    """(image, None) of a map file, or (None, why it cannot be used)."""
    if not os.path.isfile(path):
        return None, "not found"
    try:
        img = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    except RuntimeError as ex:
        return None, "cannot be read (%s)" % ex
    if img.size[0] == 0 or img.size[1] == 0:
        bpy.data.images.remove(img)
        return None, "cannot be read (not a picture Blender knows)"
    return img, None


def material_source(path):
    """({material, shader, slots}, None) of the painted mesh's material, or (None, why)."""
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))
    except Exception as ex:  # an importer failure is reported, not fatal
        return None, "cannot be imported (%s)" % ex
    new = [o for o in bpy.data.objects if o not in before]
    mat = next((m for o in new if o.type == "MESH" for m in o.data.materials if m is not None and m.use_nodes), None)
    for o in new:
        bpy.data.objects.remove(o)
    if mat is None:
        return None, "has no material"
    shader = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        return None, "its material has no Principled BSDF"
    mat.use_fake_user = True  # kept (with its images) while the export runs
    return {"material": mat, "shader": shader, "slots": find_slots(mat.node_tree, shader)}, None


def lossy_source(img):
    """True when Blender's glTF exporter would keep the image as JPEG or WebP (its source)."""
    if img.source != "FILE":
        return False
    if img.packed_file is not None:
        data = img.packed_file.data[:12]
        return data[:3] == b"\xff\xd8\xff" or data[8:12] == b"WEBP"
    return img.filepath_raw.lower().endswith(LOSSY_EXT)


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
# Turned about the vertical so that the figure's front is Blender's -y (glTF +z, the front of
# every exported model), whatever way the generated mesh faced: the front its hips and
# shoulders give (the feet's axis alone left figures standing turned 22 to 40 degrees).
_facing = hb.body_facing(arm, ROLES)
_front = Vector(_facing["front"]) if _facing["front"] else FRONT
_yaw = 0.0 if BODY_FRAME else -math.atan2(_front.x, -_front.y)
_turn = Matrix.Rotation(_yaw, 4, "Z")
if BODY_FRAME:  # the rig stands in the body's frame already
    TRANSFORM = Matrix.Identity(4)
    print("BODY FRAME height %.6f floor %.6f" % (BODY_FRAME["height"], BODY_FRAME["floor"]), flush=True)
else:
    TRANSFORM = (Matrix.Diagonal((a.widen, a.widen, 1.0, 1.0)) @ Matrix.Diagonal((_k, _k, _k, 1.0)) @ _turn
                 @ Matrix.Translation(Vector((-_hips.x, -_hips.y, -_floor))))
# The body directions of the retarget: the glTF front. A body's frame faces it too (the body was
# exported so), and every variant that wears the body gets the same.
FRONT = Vector((0.0, -1.0, 0.0))
LEFT = UP.cross(FRONT)
arm.matrix_world = TRANSFORM @ arm.matrix_world
bpy.ops.object.select_all(action="DESELECT")
arm.select_set(True)
body.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.context.view_layer.update()
_after = hb.body_facing(arm, ROLES)


def _facing_cues(f):
    return {"turn_deg": None if f["turn_deg"] is None else round(f["turn_deg"], 3),
            "cues": {k: None if v is None else round(v, 3) for k, v in f["cues"].items()}}


FACING = {"squared": not BODY_FRAME, "frame": "body" if BODY_FRAME else "rig", "source": _facing["source"],
          "turned_deg": round(math.degrees(_yaw), 3),
          "lines_apart_deg": None if _facing["lines_apart_deg"] is None else round(_facing["lines_apart_deg"], 3),
          "feet_apart_deg": None if _facing["feet_apart_deg"] is None else round(_facing["feet_apart_deg"], 3),
          "before": _facing_cues(_facing), "after": _facing_cues(_after),
          "limits": {"lines_apart_deg": hb.FACING_LINES_LIMIT_DEG, "feet_apart_deg": hb.FACING_FEET_LIMIT_DEG,
                     "after_deg": hb.FACING_TOLERANCE_DEG},
          "problems": list(_facing["problems"])}
if not BODY_FRAME and _after["turn_deg"] is not None and abs(_after["turn_deg"]) > hb.FACING_TOLERANCE_DEG:
    FACING["problems"].append("facing: the exported figure faces %.1f degrees away from glTF +z (more than %.1f)"
                              % (_after["turn_deg"], hb.FACING_TOLERANCE_DEG))
print("FACING", FACING["source"], "turned %.2f" % FACING["turned_deg"], "after", FACING["after"]["turn_deg"],
      len(FACING["problems"]), "problems", flush=True)

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
mesh_report = {"format": "forge-mesh-report", "version": 2, "status": "pending review", "problems": []}
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
RIG_SLOTS = find_slots(nt, bsdf)
# Where each slot's map comes from ("sources"), maps a better one replaced and source maps
# that could not be carried (each a flag of the mesh report).
MATERIAL = {"image_format": a.image_format, "sources": {s: "the rig's material" for s in RIG_SLOTS},
            "replaced": [], "not_carried": [], "not_taken": [], "notes": []}
NOT_CARRIED = {}  # slot -> why its source map could not be carried
SOURCE = None
if a.material_source:
    SOURCE, _why = material_source(a.material_source)
    if SOURCE is None:
        MATERIAL["not_carried"].append("material source %s: %s" % (os.path.basename(a.material_source), _why))
        NOT_CARRIED["material_source"] = "%s %s: none of its maps could be carried" % (
            os.path.basename(a.material_source), _why)
        print("MATERIAL SOURCE", _why, flush=True)

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
if "normal" in RIG_SLOTS:
    MATERIAL["replaced"].append("normal: the rig's normal map %s, by the exporter's" % RIG_SLOTS["normal"].image.name)
MATERIAL["sources"]["normal"] = "the exporter (%s)" % (
    "baked from %s, with the painted texture's relief" % (BAKE_FROM or "the mesh before reduction") if BAKED
    else "the painted texture's relief")


def texture(img, colour=True):
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    if not colour:
        img.colorspace_settings.name = "Non-Color"
    return node


def link_metal_roughness(img, roughness=True, metallic=True):
    """A metallic-roughness map (glTF: roughness green, metallic blue) on the shader."""
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(texture(img, colour=False).outputs["Color"], sep.inputs["Color"])
    if roughness:
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    if metallic:
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])


def carry(slot, src):
    """Carry one slot's map of the material source into the rig's material; None when it was
    carried, else why not."""
    img = src["slots"][slot].image
    sh = src["shader"]
    if slot == "metallic_roughness":
        rough, metal = socket_of(sh, "Roughness"), socket_of(sh, "Metallic")
        link_metal_roughness(img, rough is not None and rough.is_linked, metal is not None and metal.is_linked)
    elif slot == "emissive":
        nt.links.new(texture(img).outputs["Color"], socket_of(bsdf, "Emission Color", "Emission"))
        strength = socket_of(sh, "Emission Strength")
        if strength is not None and "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = strength.default_value or 1.0
    elif slot == "opacity":
        nt.links.new(texture(img).outputs["Alpha"], bsdf.inputs["Alpha"])
        for attr in ("blend_method", "surface_render_method"):
            try:
                setattr(mat, attr, getattr(src["material"], attr))
            except (AttributeError, TypeError):
                pass
    elif slot == "occlusion":
        group = occlusion_group(src["material"].node_tree)
        if group is None:
            return "its glTF settings group is missing"
        node = nt.nodes.new("ShaderNodeGroup")
        node.node_tree = group.node_tree
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(texture(img, colour=False).outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Red"], node.inputs["Occlusion"])
    else:
        return "the exporter does not carry this slot"
    return None


# The paint stage's metallic-roughness map wins over the rig's (the rig tool may have
# re-encoded it); then every map the rig's material lacks is carried from the painted mesh.
if a.metal_roughness:
    _name = os.path.basename(a.metal_roughness)
    _img, _why = load_map(a.metal_roughness)
    if _img is None:
        NOT_CARRIED["metallic_roughness"] = "the paint stage's map %s could not be carried: %s" % (_name, _why)
        MATERIAL["not_carried"].append("metallic_roughness: %s %s" % (_name, _why))
        MATERIAL["sources"].setdefault("metallic_roughness", "the paint stage's map %s (not carried)" % _name)
    else:
        if "metallic_roughness" in RIG_SLOTS:
            MATERIAL["replaced"].append("metallic_roughness: the rig's map %s, by the paint stage's %s" % (
                RIG_SLOTS["metallic_roughness"].image.name, _name))
        link_metal_roughness(_img)
        MATERIAL["sources"]["metallic_roughness"] = "the paint stage's map %s" % _name
def factor_note(slot, src):
    """A note when the source's map reaches its shader through more than the plain layout
    (a factor or another node the carry does not reproduce), else None."""
    sh = src["shader"]
    names = {"metallic_roughness": ("Roughness", "Metallic"), "emissive": ("Emission Color", "Emission")}.get(slot, ())
    for name in names:
        sock = socket_of(sh, name)
        if sock is not None and sock.is_linked and sock.links[0].from_node.type not in ("TEX_IMAGE", "SEPARATE_COLOR",
                                                                                      "SEPRGB"):
            return "%s: the material source's %s goes through a %s node (a factor) that is not carried" % (
                slot, name, sock.links[0].from_node.type)
    return None


if SOURCE is not None:
    _have = find_slots(nt, bsdf)
    _src_name = os.path.basename(a.material_source)
    for _slot in ("base_color", "normal"):
        if _slot in SOURCE["slots"]:
            MATERIAL["not_taken"].append("%s: the material source's map %s is not taken (%s)" % (
                _slot, SOURCE["slots"][_slot].image.name,
                "the rig's base colour is the one its UVs carry" if _slot == "base_color"
                else "the exporter bakes its own normal map"))
    # A technical map the rig re-encoded lossily gives way to the source's lossless original.
    for _slot in ("metallic_roughness", "emissive"):
        if (_slot in _have and _slot in SOURCE["slots"] and MATERIAL["sources"].get(_slot) == "the rig's material"
                and lossy_source(_have[_slot].image) and not lossy_source(SOURCE["slots"][_slot].image)):
            MATERIAL["replaced"].append("%s: the rig's lossy map %s, by the material source's %s" % (
                _slot, _have[_slot].image.name, SOURCE["slots"][_slot].image.name))
            del _have[_slot]
    for _slot in ("metallic_roughness", "opacity", "emissive", "occlusion"):
        if _slot in _have or _slot not in SOURCE["slots"]:
            continue
        _note = factor_note(_slot, SOURCE)
        if _note:
            MATERIAL["notes"].append(_note)
        _why = carry(_slot, SOURCE)
        if _why:
            NOT_CARRIED[_slot] = "; ".join(x for x in (NOT_CARRIED.get(_slot), "the map of the material source %s "
                                                       "could not be carried: %s" % (_src_name, _why)) if x)
            MATERIAL["not_carried"].append("%s: %s's map: %s" % (_slot, _src_name, _why))
            MATERIAL["sources"][_slot] = "the material source %s (not carried)" % _src_name
        else:
            MATERIAL["sources"][_slot] = "the material source %s" % _src_name
print("MATERIAL", json.dumps(MATERIAL["sources"], sort_keys=True), flush=True)

if unweighted:
    mesh_report["problems"].append("%d vertices have no weight" % unweighted)
if worst_sum > 1e-3:
    mesh_report["problems"].append("weights are not normalised (worst sum off by %.4f)" % worst_sum)
mesh_report.update({
    "uv_layers": len(body.data.uv_layers), "material_count": len(mats), "base_color_texture": base_node.image.name,
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
mesh_report["facing"] = FACING
mesh_report["problems"] += FACING["problems"]
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
zmin = BODY_FRAME["floor"] if BODY_FRAME else min(c.z for c in coords)
zmax = max(c.z for c in coords)
H = BODY_FRAME["height"] if BODY_FRAME else zmax - zmin
cx, cy = head(CH["hips"]).x, head(CH["hips"]).y
LEGS = [CH["legs"]["left"], CH["legs"]["right"]]
ARMS = [CH["arms"]["left"], CH["arms"]["right"]]
ANKLE = (head(LEGS[0][2]).z - zmin) / H
# The joint each mapped bone points to: the head of the next mapped bone of its chain (its
# own tail for the last one). Rig tools give joints orientations of their own, so a bone's
# axis (head to tail as imported) need not point to its child.
AIM_TO = {}
for _chain in [[CH["hips"]] + CH["spine"] + CH["head"]] + [[c for c in ch if c] for ch in ARMS] + LEGS:
    _chain = [b for b in _chain if b]
    for _b, _c in zip(_chain, _chain[1:]):
        if arm.data.bones[_c].parent is not None and _b in [x.name for x in arm.data.bones[_c].parent_recursive]:
            AIM_TO[_b] = _c
# The last bone of a chain (the head, a hand, a foot without toes, the toes) has no child joint:
# it points along its own axis, unless that axis is more than 45 degrees off the line of its
# parent's segment continued, then along that line (kept in the bone's own frame).
END_AXIS = {}
for _b in AIM_TO.values():
    if _b in AIM_TO:
        continue
    _parent = next((p for p, c in AIM_TO.items() if c == _b), None)
    _bone = arm.data.bones[_b]
    _line = (_bone.head_local - arm.data.bones[_parent].head_local).normalized()
    _own = (_bone.tail_local - _bone.head_local).normalized()
    if _own.angle(_line, 0.0) > math.radians(45):
        END_AXIS[_b] = _bone.matrix_local.to_3x3().inverted() @ _line

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
# Each limb: (upper bone, lower bone, end bone): the IK puts the end bone's joint (wrist,
# ankle) on the target.
LIMBS = {"larm": ARMS[0][1:4], "rarm": ARMS[1][1:4], "lleg": LEGS[0][0:3], "rleg": LEGS[1][0:3]}
SEGMENT = {k: ((head(u) - head(lo)).length, (head(lo) - head(e)).length) for k, (u, lo, e) in LIMBS.items()}
LIMB_LENGTH = {k: sum(v) for k, v in SEGMENT.items()}
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
    """Turns a bone (pose) so that it points along `direction` (rig world): the line from its
    joint to its child's joint (AIM_TO), else its own axis."""
    pb = arm.pose.bones[name]
    m = pb.matrix.copy()
    child = AIM_TO.get(name)
    if child:
        cur = arm.pose.bones[child].head - pb.head
    else:
        cur = m.to_3x3() @ END_AXIS.get(name, Vector((0, 1, 0)))
    if cur.length < 1e-9:
        cur = m.to_3x3() @ Vector((0, 1, 0))
    cur = cur.normalized()
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
    u0 = head(CH["spine"][0]) - head(CH["hips"])
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
    """Two-bone IK, solved exactly: the limb's end joint (wrist, ankle) on `target` (as near as
    the limb reaches), the middle joint (elbow, knee) in the plane of the pole, both bones
    turned by aim_bone (their joints' lines, whatever their own axes)."""
    upper, lower, end = LIMBS[name]
    l1, l2 = SEGMENT[name]
    root = world_of(upper)[0]
    d = target - root
    dist = d.length
    SHORT[name] = max(SHORT.get(name, 0.0), dist - LIMB_LENGTH[name])
    if dist < 1e-9:
        return
    u = d / dist
    dist = min(max(dist, abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
    v = (pole - root) - u * (pole - root).dot(u)
    if v.length < 1e-9:
        v = FRONT - u * FRONT.dot(u)
    v = v.normalized()
    along = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    knee = root + u * along + v * math.sqrt(max(l1 * l1 - along * along, 0.0))
    aim_bone(upper, knee - root)
    aim_bone(lower, root + u * dist - knee)


def pole_of(upper, lower):
    """A pole beyond the middle joint (elbow, knee), on the side it bends to now (FK)."""
    start = world_of(upper)[0]
    mid = world_of(lower)[0]
    child = AIM_TO.get(lower)
    end = world_of(child)[0] if child else world_of(lower)[1]
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
        "ankle_height": ANKLE * H, "front": [round(v, 6) + 0.0 for v in (FRONT.x, FRONT.z, -FRONT.y)], "animations": [],
        "facing": FACING,
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
# What glows is found on the written GLB's base colour after the export (glow_regions.py).
GLOW_COLOURS = [c.strip() for c in a.glow_colours.split(",") if c.strip()]
if not GLOW_COLOURS and a.glow:
    GLOW_COLOURS = ["%s-%s" % tuple(x.strip() for x in a.glow_hues.split(","))]
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
# The images' formats: Blender's glTF exporter ("AUTO") writes an image whose source is a
# JPEG or WebP file as that, every other image as PNG. Lossless: every lossy source becomes a
# PNG. Compact: the base colour is written once as JPEG at quality 92 (PNG when it carries
# the opacity), the technical maps as PNG. The normal map is made here (PNG).
MAPS = os.path.join(OUT, "_maps")


def rewritten(img, fmt):
    """A packed copy of an image as a PNG or JPEG file (same pixels, name and colour space)."""
    os.makedirs(MAPS, exist_ok=True)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    if img.is_float and img.colorspace_settings.name == "sRGB":
        # A float buffer (a 16-bit PNG, say) holds linear values: back to sRGB for 8 bits.
        c = np.clip(px[..., :3], 0.0, 1.0)
        px[..., :3] = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    px = np.clip(np.round(px * 255.0), 0, 255).astype(np.uint8)[::-1]
    alpha = fmt == "png" and img.channels == 4 and bool((px[..., 3] < 255).any())
    pic = PILImage.fromarray(np.ascontiguousarray(px if alpha else px[..., :3]), "RGBA" if alpha else "RGB")
    name = img.name
    path = os.path.join(MAPS, "%d.%s" % (len(os.listdir(MAPS)), "jpg" if fmt == "jpeg" else "png"))
    if fmt == "jpeg":
        pic.save(path, quality=JPEG_QUALITY)
    else:
        pic.save(path)
    img.name = name + "_source"
    new = bpy.data.images.load(path, check_existing=False)
    new.colorspace_settings.name = img.colorspace_settings.name
    new.pack()
    new.name = name
    return new


FINAL_SLOTS = find_slots(nt, bsdf)
_done = {}
for _slot, _node in sorted(FINAL_SLOTS.items()):
    _img = _node.image
    if (_slot == "base_color" and a.image_format == "compact" and "opacity" not in FINAL_SLOTS
            and not bsdf.inputs["Alpha"].is_linked):
        _fmt = "jpeg"
    elif lossy_source(_img):
        _fmt = "png"
    else:
        continue
    _key = (_img.name, _fmt)
    if _key not in _done:
        print("IMAGE", _slot, _img.name, "->", _fmt, flush=True)
        _done[_key] = rewritten(_img, _fmt)
    for _n in nt.nodes:  # every node of that picture (the base colour and its alpha)
        if _n.type == "TEX_IMAGE" and _n.image == _img:
            _n.image = _done[_key]
bpy.ops.export_scene.gltf(filepath=GLB, export_format="GLB", use_selection=True,
                          export_animations=True, export_animation_mode="NLA_TRACKS", export_skins=True,
                          export_def_bones=False, export_frame_range=False, export_force_sampling=False,
                          export_optimize_animation_size=False, export_image_format="AUTO",
                          export_image_quality=JPEG_QUALITY, export_jpeg_quality=JPEG_QUALITY,
                          # No Draco: the GLB stays plain (and the exporter does not log the
                          # missing Draco library as an error).
                          export_draco_mesh_compression_enable=False)
print("EXPORTED", GLB, flush=True)
# Blender's glTF exporter checks for its Draco library whenever it starts and logs "ERROR
# Draco mesh compression is not available" when the module build has none.
print("NOTE the Draco line above is harmless: the GLB is written without Draco compression", flush=True)
if os.path.isdir(MAPS):
    import shutil  # noqa: E402
    shutil.rmtree(MAPS, ignore_errors=True)

# What glows: the written base colour's lights into the GLB's emissive slot and the mask's blue.
import glow_regions  # noqa: E402
if GLOW_COLOURS:
    _mask = os.path.join(OUT, "regions_mask.png")
    GLOW = glow_regions.glow_files(GLB, GLOW_COLOURS, reference=a.glow_reference or None, regions=_mask,
                                   regions_out=_mask, settings=json.loads(a.glow_settings) if a.glow_settings else None,
                                   strength=a.glow_strength)
    regions.update(glow_regions.glow_record(GLOW))
    regions["glow_reference"] = GLOW["reference"]
    if "written" in GLOW["emissive"].values():
        MATERIAL["sources"]["emissive"] = "the exporter's glow (%s)" % ", ".join(GLOW_COLOURS)
    print("GLOW", ",".join(GLOW_COLOURS), GLOW["glow_pixels"], "pixels", json.dumps(GLOW["emissive"]), flush=True)

# The materials as the written GLB holds them (its JSON and image bytes, not this scene): a
# slot whose source map existed but that is missing or lossy is a mesh flag, and so is a
# source map that could not be carried.
import glb_materials  # noqa: E402
_read = glb_materials.material_slots(GLB)
_slots = {}
for _slot in glb_materials.SLOTS:
    _r = _read[_slot]
    _src = MATERIAL["sources"].get(_slot)
    _slots[_slot] = {"present": _r["present"], "format": _r["format"], "size": _r["size"], "lossless": _r["lossless"],
                     "image": _r["image"], "source": _src is not None, "source_from": _src}
_flags = glb_materials.slot_flags(_read, MATERIAL["sources"], a.image_format, NOT_CARRIED)
_slots["opacity"]["alpha_mode"] = _read["opacity"].get("alpha_mode")
mesh_report["materials"] = {"image_format": a.image_format, "read_from": os.path.basename(GLB) + " as written",
                            "slots": _slots, "replaced": MATERIAL["replaced"], "not_carried": MATERIAL["not_carried"],
                            "not_taken": MATERIAL["not_taken"], "notes": MATERIAL["notes"]}
for _slot in sorted(_flags):
    mesh_report["problems"].append("%s: %s" % ("material source" if _slot == "material_source"
                                               else "material slot " + _slot, _flags[_slot]))
mesh_report["valid"] = not mesh_report["problems"]
mesh_report["flags"] = list(mesh_report["problems"])
write_json("mesh_report.json", mesh_report)
print("MATERIALS", json.dumps({k: v["format"] for k, v in _slots.items() if v["present"]}, sort_keys=True),
      len(_flags), "flags", flush=True)
info["mesh"]["image_format"] = a.image_format
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
