"""A painted object as rigid parts on pivots: one GLB with one node per part (each part's
origin at its pivot, the nodes nested as the parts' parents), the joints in model.json, and
the reports that say whether the result can be trusted.

Runs with a Python that imports bpy (Blender as a module):

  python articulated_export.py --mesh PAINTED.glb --out DIR --parts PARTS.json [--name model]
      [--front +z|-z|+x|-x] [--height 2.0 | --length 6.0] [--tris 30000] [--snap 1|0]
      [--min-share 0.002] [--metal-roughness MAP.png] [--image-format lossless|compact]

Steps (formats in docs/conventions.md; the geometry in articulated_parts.py):
1. The frame: the mesh is turned so that its front (--front, the glTF axis the generated
   front points along; or <out>/frame.json marked "manual": true) faces glTF +Z, squared
   when its footprint came out turned by up to 30 degrees (--snap), scaled to --length
   (front to back) or --height, and stood on the ground with its footprint centred.
   <out>/frame.json records it.
2. The parts, parents first: each part takes the geometry of its parent inside its region
   (boxes in normalised object coordinates, [low, high] edges at the parent's narrowest
   section), cut exactly along the boxes' planes; both sides of every cut are closed with
   faces of a plain "interior" material.
3. Each part's origin is put at its pivot; the nodes are nested as the parts' parents.
4. Writes <out>/<name>.glb (no animation: the game moves the parts), <out>/model.json
   (parts, joints in glTF axes) and <out>/mesh_report.json (per part: triangles, cut faces,
   open edges, share of the surface; flags; the materials read back from the GLB).
   No UV layer or no base-colour texture stops it (exit code 4).
"""
import argparse
import json
import math
import os
import sys
import time

import bpy  # first: bmesh and mathutils come with it
import bmesh
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import articulated_parts as ap  # noqa: E402
import forge_blender as fb  # noqa: E402

ap_ = argparse.ArgumentParser()
ap_.add_argument("--mesh", required=True)
ap_.add_argument("--out", required=True)
ap_.add_argument("--parts", required=True, help="the order's part list (JSON)")
ap_.add_argument("--name", default="model")
ap_.add_argument("--front", default="+z", choices=ap.FRONTS)
ap_.add_argument("--height", type=float, default=0.0)
ap_.add_argument("--length", type=float, default=0.0)
ap_.add_argument("--tris", type=int, default=0, help="triangle budget (reported; the mesh stage reduced it)")
ap_.add_argument("--snap", default="1", choices=("0", "1"))
ap_.add_argument("--min-share", type=float, default=0.002)
ap_.add_argument("--metal-roughness", default="")
ap_.add_argument("--image-format", default="lossless", choices=("lossless", "compact"))
a = ap_.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
print("BPY", bpy.app.version_string, flush=True)
with open(a.parts, encoding="utf-8-sig") as f:
    PARTS = json.load(f)
_problems = ap.check_parts(PARTS)
if _problems:
    fb.fail(2, "invalid part list: " + "; ".join(_problems))

# ---- the mesh ------------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
new = fb.import_glb(a.mesh)
meshes = [o for o in new if o.type == "MESH"]
if not meshes:
    fb.fail(2, "the painted mesh has no mesh: %s" % a.mesh)
for o in new:
    if o.type == "MESH":
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
for o in list(bpy.data.objects):
    if o.type != "MESH":
        bpy.data.objects.remove(o)
body = fb.join_meshes(meshes)
fb.apply_transforms([body])
merged = fb.merge_seams(body)
print("SEAMS", merged, "duplicate vertices merged", flush=True)

report = {"format": "forge-mesh-report", "version": 2, "kind": "articulated", "status": "pending review",
          "problems": []}
mats = [m for m in body.data.materials if m is not None]
base_node = None
if mats and mats[0].use_nodes:
    _nt = mats[0].node_tree
    _bsdf = next((n for n in _nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if _bsdf is not None:
        base_node = fb.find_slots(_nt, _bsdf).get("base_color")
if not body.data.uv_layers:
    report["problems"].append("the mesh has no UV layer")
if base_node is None:
    report["problems"].append("the material has no base-colour texture")
if report["problems"]:
    report.update(valid=False, flags=list(report["problems"]))
    fb.write_json(OUT, "mesh_report.json", report)
    fb.fail(4, "the mesh cannot be exported: " + "; ".join(report["problems"]))
if len(mats) > 1:
    report["problems"].append("the mesh has %d materials, 1 is expected" % len(mats))
mat = mats[0]
SOURCES = {s: "the painted mesh" for s in fb.find_slots(mat.node_tree, _bsdf)}
NOT_CARRIED = {}
if a.metal_roughness:
    img, why = fb.load_map(a.metal_roughness)
    if img is None:
        NOT_CARRIED["metallic_roughness"] = "the paint stage's map %s could not be carried: %s" % (
            os.path.basename(a.metal_roughness), why)
    else:
        fb.link_metal_roughness(mat, img)
    SOURCES["metallic_roughness"] = "the paint stage's map %s" % os.path.basename(a.metal_roughness)

# ---- 1. the frame ----------------------------------------------------------------------------
frame_path = os.path.join(OUT, "frame.json")
frame = {"format": "forge-frame", "version": 1, "manual": False, "front": a.front, "snap": a.snap == "1"}
if os.path.exists(frame_path):
    try:
        with open(frame_path, encoding="utf-8-sig") as f:
            _old = json.load(f)
        if isinstance(_old, dict) and _old.get("manual"):
            frame = _old
            print("FRAME manual", json.dumps(frame), flush=True)
    except (OSError, ValueError):
        pass
front = frame.get("front", a.front)
if front not in ap.FRONTS:
    fb.fail(2, "frame.json: front must be one of %s, not %r" % (", ".join(ap.FRONTS), front))
turned = fb.turn_to_front([body], front)
fb.apply_transforms([body])
verts, _ = fb.mesh_arrays(body)
if "snap_deg" in frame and frame.get("manual"):
    snap = float(frame["snap_deg"])
else:
    snap = ap.snap_yaw(verts[:, :2]) if frame.get("snap", True) else 0.0
if snap:
    body.matrix_world = Matrix.Rotation(math.radians(snap), 4, "Z") @ body.matrix_world
    fb.apply_transforms([body])
    verts, _ = fb.mesh_arrays(body)
lo, hi = verts.min(0), verts.max(0)
ext = hi - lo
scale = 1.0
if a.length > 0:
    scale = a.length / max(ext[1], 1e-9)
elif a.height > 0:
    scale = a.height / max(ext[2], 1e-9)
centre = Vector((((lo + hi) / 2.0)[0], ((lo + hi) / 2.0)[1], lo[2]))
body.matrix_world = Matrix.Diagonal((scale, scale, scale, 1.0)) @ Matrix.Translation(-centre) @ body.matrix_world
fb.apply_transforms([body])
verts, faces = fb.mesh_arrays(body)
DIMS = ap.dims_of(verts)
frame.update({"turned_deg": turned, "snap_deg": snap, "scale": round(scale, 6),
              "size_m": [round(DIMS[0], 4), round(DIMS[2], 4), round(DIMS[1], 4)]})
if not frame.get("manual"):
    fb.write_json(OUT, "frame.json", frame)
print("FRAME front %s turned %.1f snap %.1f scale %.4f size %s" % (front, turned, snap, scale, DIMS), flush=True)

# The interior material of the cut faces.
interior = bpy.data.materials.new("interior")
interior.use_nodes = True
_ib = next(n for n in interior.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
_ib.inputs["Base Color"].default_value = (0.06, 0.06, 0.07, 1.0)
_ib.inputs["Roughness"].default_value = 0.6
_ib.inputs["Metallic"].default_value = 0.5
body.data.materials.append(interior)
INTERIOR = len(body.data.materials) - 1
tris_in = sum(len(p.vertices) - 2 for p in body.data.polygons)
area_total = sum(p.area for p in body.data.polygons) or 1.0


# ---- 2. the parts ------------------------------------------------------------------------------

def planes_of(boxes):
    out = []
    for lo_, hi_ in boxes:
        for axis in range(3):
            for v in (lo_[axis], hi_[axis]):
                if math.isfinite(v):
                    out.append((axis, float(v)))
    return out


def inside_any(c, boxes, eps):
    return any(all(lo_[i] - eps <= c[i] <= hi_[i] + eps for i in range(3)) for lo_, hi_ in boxes)


def close_cuts(bm, planes, eps):
    """Fill the open loops that lie on the cut planes; returns the new faces."""
    edges = [e for e in bm.edges if e.is_boundary and any(
        all(abs(v.co[axis] - val) <= eps for v in e.verts) for axis, val in planes)]
    if not edges:
        return []
    res = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
    new_faces = list(res.get("faces", []))
    if new_faces:
        tri = bmesh.ops.triangulate(bm, faces=new_faces)
        new_faces = list(tri.get("faces", new_faces))
    for f in new_faces:
        f.material_index = INTERIOR
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    return new_faces


objects = {}
caps = {}
records = {}
root_id = next(p["id"] for p in PARTS if p.get("parent") is None)
body.name = body.data.name = root_id
objects[root_id] = body
size = max(DIMS)
EPS = 1e-5 * size
for part in PARTS:
    pid = part["id"]
    if part.get("parent") is None:
        continue
    parent = objects[part["parent"]]
    pverts, pfaces = fb.mesh_arrays(parent)
    boxes, resolved = [], []
    for box in ap.boxes_of(part["find"]):
        lo_, hi_, rec = ap.resolve_box(box, pverts, DIMS, pfaces if len(pfaces) else None)
        boxes.append((lo_, hi_))
        resolved.append({"low": [None if not math.isfinite(x) else round(float(x), 5) for x in lo_],
                         "high": [None if not math.isfinite(x) else round(float(x), 5) for x in hi_],
                         "narrowest": rec})
    planes = planes_of(boxes)
    bm = bmesh.new()
    bm.from_mesh(parent.data)
    for axis, val in planes:
        co = [0.0, 0.0, 0.0]
        no = [0.0, 0.0, 0.0]
        co[axis], no[axis] = val, 1.0
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), dist=EPS,
                               plane_co=co, plane_no=no, clear_inner=False, clear_outer=False)
    keep = bm.copy()
    take = set(f for f in bm.faces if inside_any(f.calc_center_median(), boxes, EPS))
    rest = [f for f in bm.faces if f not in take]
    bmesh.ops.delete(bm, geom=rest, context="FACES")
    part_caps = close_cuts(bm, planes, EPS * 10)
    # The pivot "cut": the centre of the largest cut (by area) on one plane, where the part
    # meets the body of its parent (not a cut through a thin piece that crosses its box).
    groups = {}
    for f_ in part_caps:
        key = next(((axis, val) for axis, val in planes if all(abs(v.co[axis] - val) <= EPS * 10 for v in f_.verts)),
                   None)
        g = groups.setdefault(key, [0.0, np.zeros(3)])
        g[0] += f_.calc_area()
        g[1] += np.array(f_.calc_center_median()[:]) * f_.calc_area()
    best = max(groups.values(), key=lambda g: g[0]) if groups else None
    cap_co = (best[1] / best[0])[None] if best and best[0] > 0 else np.zeros((0, 3))
    me = bpy.data.meshes.new(pid)
    bm.to_mesh(me)
    bm.free()
    for m_ in parent.data.materials:
        me.materials.append(m_)
    ob = bpy.data.objects.new(pid, me)
    bpy.context.scene.collection.objects.link(ob)
    gone = [f for f in keep.faces if inside_any(f.calc_center_median(), boxes, EPS)]
    bmesh.ops.delete(keep, geom=gone, context="FACES")
    close_cuts(keep, planes, EPS * 10)
    keep.to_mesh(parent.data)
    keep.free()
    parent.data.update()
    objects[pid] = ob
    caps[pid] = cap_co
    records[pid] = {"regions": resolved}
    print("PART", pid, "from", part["parent"], len(me.polygons), "faces", len(part_caps), "cut faces", flush=True)

# ---- 3. pivots, origins, nesting -----------------------------------------------------------------
pivots = {root_id: np.zeros(3)}
part_info = {}
for part in PARTS:
    pid = part["id"]
    ob = objects[pid]
    pverts, _ = fb.mesh_arrays(ob)
    me = ob.data
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    cut_tris = sum(len(p.vertices) - 2 for p in me.polygons if p.material_index == INTERIOR)
    bm = bmesh.new()
    bm.from_mesh(me)
    open_edges = sum(1 for e in bm.edges if e.is_boundary)
    bm.free()
    share = sum(p.area for p in me.polygons if p.material_index != INTERIOR) / area_total
    joint = ap.joint_of(part) if part.get("parent") else None
    if joint is not None:
        pivots[pid] = ap.pivot_point(joint.get("pivot", "cut"), pverts, caps.get(pid, np.zeros((0, 3))), DIMS)
    if len(me.polygons) == 0:
        report["problems"].append("part %s is empty: its region holds no geometry" % pid)
    elif part.get("parent") and share < a.min_share:
        report["problems"].append("part %s is tiny: %.2f %% of the surface (under min_share %.2f %%)" % (
            pid, share * 100, a.min_share * 100))
    if open_edges:
        report["problems"].append("part %s has %d open edges after its cut was closed" % (pid, open_edges))
    lo_, hi_ = (pverts.min(0), pverts.max(0)) if len(pverts) else (np.zeros(3), np.zeros(3))
    part_info[pid] = dict(records.get(pid, {}), parent=part.get("parent"), node=pid, triangles=tris,
                          cut_triangles=cut_tris, open_edges=open_edges, share=round(share, 5),
                          bounds_m=[ap.to_gltf(lo_), ap.to_gltf(hi_)])
for part in PARTS:
    pid = part["id"]
    ob = objects[pid]
    ob.data.transform(Matrix.Translation(Vector((-pivots[pid]).tolist())))
    ob.matrix_world = Matrix.Translation(Vector(pivots[pid].tolist()))
for part in PARTS:
    if part.get("parent"):
        ob, par = objects[part["id"]], objects[part["parent"]]
        ob.parent = par
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.location = Vector((pivots[part["id"]] - pivots[part["parent"]]).tolist())
bpy.context.view_layer.update()
joints = []
for part in PARTS:
    if part.get("parent"):
        joints.append(ap.joint_record(part, ap.joint_of(part), pivots[part["id"]], pivots[part["parent"]]))
        part_info[part["id"]]["origin_m"] = [round(x, 5) for x in ap.to_gltf(pivots[part["id"]])]
part_info[root_id]["origin_m"] = [0.0, 0.0, 0.0]

# ---- 4. export ---------------------------------------------------------------------------------
GLB = os.path.join(OUT, a.name + ".glb")
MAPS = os.path.join(OUT, "_maps")
fb.prepare_images(mat, a.image_format, MAPS)
fb.export_glb(GLB, list(objects.values()))
if os.path.isdir(MAPS):
    import shutil  # noqa: E402
    shutil.rmtree(MAPS, ignore_errors=True)
materials, mflags = fb.material_report(GLB, root_id, SOURCES, a.image_format, NOT_CARRIED)
for slot in sorted(mflags):
    report["problems"].append("material slot %s: %s" % (slot, mflags[slot]))
tris_out = sum(p["triangles"] for p in part_info.values())
report.update({"uv_layers": len(body.data.uv_layers), "material_count": 1, "interior_material": "interior",
               "base_color_texture": base_node.image.name, "texture_size": list(base_node.image.size),
               "triangles_in": tris_in, "triangles": tris_out, "triangle_budget": a.tris or None,
               "seam_vertices_merged": merged, "parts": part_info, "materials": materials})
report["valid"] = not report["problems"]
report["flags"] = list(report["problems"])
fb.write_json(OUT, "mesh_report.json", report)
info = {"format": "forge-articulated", "version": 1, "status": "pending review", "name": a.name,
        "front": [0.0, 0.0, 1.0], "axes": "glTF: y up, front +Z, left +X; metres",
        "size_m": [round(DIMS[0], 4), round(DIMS[2], 4), round(DIMS[1], 4)],
        "center_m": [0.0, round(DIMS[2] / 2.0, 4), 0.0],
        "root": root_id, "parts": part_info, "joints": joints, "interior_material": "interior",
        "frame": {"file": "frame.json", "front_given": front, "source": "manual" if frame.get("manual") else "order",
                  "snap_deg": snap, "scale": round(scale, 6)},
        "mesh": {"triangles": tris_out, "report": "mesh_report.json", "image_format": a.image_format},
        "seconds_export": round(time.time() - T0, 1)}
fb.write_json(OUT, "model.json", info)
print("DONE", len(PARTS), "parts,", len(report["flags"]), "flags", flush=True)
fb.finish(0)
