"""A painted object as a held item: the mesh put in item space at its real size, its sockets
proposed from a class template, and the socket file the humanoid exporter reads
(docs/conventions.md, "Held-item socket file").

Runs with a Python that imports bpy (Blender as a module):

  python held_item_export.py --mesh PAINTED.glb --out DIR --spec ITEM.json [--name model]
      [--length 0.9 | --height 0.3] [--front +x] [--top +y] [--snap auto|1|0] [--tris 12000]
      [--tolerance-cm 1.0] [--metal-roughness MAP.png] [--image-format lossless|compact]

Steps (the geometry in held_item_geometry.py):
1. The frame: the generated mesh's front points along the glTF axis --front (default +x: to
   the right of the recipe's side-view concept) and its top along --top (default +y), or as
   <out>/frame.json says when a person marked it "manual": true (front, top, and optionally
   rotate_deg [about x, about y, about z] instead of the squaring). A long, straight item
   (the template's "square", or --snap) is squared to its principal axes when it came out
   turned by 8 to 30 degrees. Scaled so that its length along +x is --length (or its height
   along +z is --height).
2. The sockets: the class template (--spec: the order's item object) proposed on the surface,
   or <out>/sockets.json when a person marked it "manual": true. The grip becomes the origin.
   Flags: a required socket missing, a socket off the mesh, an elongated item whose longest
   axis is far from the front axis.
3. Writes <out>/<name>.glb (item space: front glTF +x, top glTF +y, the grip at the origin),
   <out>/<name>.json (the socket file), <out>/model.json, <out>/frame.json, <out>/sockets.json
   and <out>/mesh_report.json (flags, fit measures, the materials read back from the GLB).
   No UV layer or no base-colour texture stops it (exit code 4).
"""
import argparse
import json
import os
import sys
import time

import bpy  # first: bmesh and mathutils come with it
import numpy as np
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forge_blender as fb  # noqa: E402
import held_item_geometry as hg  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--mesh", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--spec", required=True, help="the order's item object (JSON)")
ap.add_argument("--name", default="model")
ap.add_argument("--length", type=float, default=0.0)
ap.add_argument("--height", type=float, default=0.0)
ap.add_argument("--front", default=hg.DEFAULT_FRONT, choices=tuple(hg.AXES))
ap.add_argument("--top", default=hg.DEFAULT_TOP, choices=tuple(hg.AXES))
ap.add_argument("--snap", default="auto", choices=("auto", "0", "1"))
ap.add_argument("--tris", type=int, default=0, help="triangle budget (reported; the mesh stage reduced it)")
ap.add_argument("--tolerance-cm", type=float, default=1.0, help="how far from the surface a socket may be")
ap.add_argument("--metal-roughness", default="")
ap.add_argument("--image-format", default="lossless", choices=("lossless", "compact"))
a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
print("BPY", bpy.app.version_string, flush=True)
with open(a.spec, encoding="utf-8-sig") as f:
    ITEM = json.load(f)
_problems = hg.check_item(ITEM)
if _problems:
    fb.fail(2, "invalid item: " + "; ".join(_problems))
TEMPLATE = hg.resolve(ITEM)


def read_manual(name):
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("manual") else None


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

report = {"format": "forge-mesh-report", "version": 2, "kind": "held_item", "status": "pending review",
          "problems": []}
mats = [m for m in body.data.materials if m is not None]
base_node = None
_bsdf = None
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

# ---- 1. the frame and 2. the sockets ---------------------------------------------------------
# Blender's axes (z up) are glTF's (y up) turned: glTF = C @ Blender.
C = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
verts_b, tris = fb.mesh_arrays(body)
verts_g = verts_b @ C.T
frame = {"format": "forge-item-frame", "version": 1, "manual": False, "front": a.front, "top": a.top,
         "snap": None if a.snap == "auto" else a.snap == "1"}
manual_frame = read_manual("frame.json")
if manual_frame is not None:
    frame = manual_frame
    print("FRAME manual", json.dumps(frame), flush=True)
front, top = frame.get("front", a.front), frame.get("top", a.top)
if front not in hg.AXES or top not in hg.AXES:
    fb.fail(2, "frame.json: front and top must be one of %s" % ", ".join(hg.AXES))
rotate = frame.get("rotate_deg") if frame.get("manual") else None
if rotate is not None and not (isinstance(rotate, list) and len(rotate) == 3):
    fb.fail(2, "frame.json: rotate_deg must be [about x, about y, about z] in degrees")
manual_sockets = read_manual("sockets.json")
given = None
if manual_sockets is not None:
    given = manual_sockets.get("sockets")
    if not isinstance(given, dict):
        fb.fail(2, "sockets.json: sockets must be an object {name: [x, y, z]}")
    print("SOCKETS manual", json.dumps(given), flush=True)
try:
    res = hg.place_item(verts_g, tris, TEMPLATE, front=front, top=top, snap=frame.get("snap"), rotate_deg=rotate,
                        length=a.length or None, height=a.height or None, manual_sockets=given,
                        tolerance=a.tolerance_cm / 100.0)
except ValueError as ex:
    fb.fail(2, str(ex))
# Item space is Blender's space of the written GLB: item = M @ (C @ blender) + t.
m3 = res["matrix"] @ C
m4 = Matrix(((m3[0, 0], m3[0, 1], m3[0, 2], res["translation"][0]),
             (m3[1, 0], m3[1, 1], m3[1, 2], res["translation"][1]),
             (m3[2, 0], m3[2, 1], m3[2, 2], res["translation"][2]),
             (0.0, 0.0, 0.0, 1.0)))
body.data.transform(m4)
body.data.update()
body.name = body.data.name = a.name
check, _ = fb.mesh_arrays(body)
print("FRAME front %s top %s turned %.1f scale %.4f size %s" % (
    front, top, res["frame"]["square"].get("turned_deg", 0.0), res["scale"], res["size_m"]), flush=True)
frame.update({"turned_deg": res["frame"]["square"].get("turned_deg", 0.0), "square": res["frame"]["square"],
              "scale": round(res["scale"], 6), "size_m": res["size_m"]})
if manual_frame is None:
    fb.write_json(OUT, "frame.json", frame)
if manual_sockets is None:
    fb.write_json(OUT, "sockets.json", {
        "format": "forge-item-sockets", "version": 1, "manual": False, "source": "proposed",
        "template": TEMPLATE["template"], "sockets": res["sockets"],
        "how_to_change": "set manual to true and edit sockets ({name: [x, y, z]} metres in item space, as in the "
                         "socket file); a grip other than [0, 0, 0] moves the origin there; the next factory run "
                         "redoes this variant's item"})

# ---- 3. export ---------------------------------------------------------------------------------
GLB = os.path.join(OUT, a.name + ".glb")
MAPS = os.path.join(OUT, "_maps")
fb.prepare_images(mat, a.image_format, MAPS)
fb.export_glb(GLB, [body])
if os.path.isdir(MAPS):
    import shutil  # noqa: E402
    shutil.rmtree(MAPS, ignore_errors=True)
materials, mflags = fb.material_report(GLB, a.name, SOURCES, a.image_format, NOT_CARRIED)
for slot in sorted(mflags):
    report["problems"].append("material slot %s: %s" % (slot, mflags[slot]))
report["problems"] += res["flags"]
tris_n = sum(len(p.vertices) - 2 for p in body.data.polygons)
report.update({"uv_layers": len(body.data.uv_layers), "material_count": len(mats),
               "base_color_texture": base_node.image.name, "texture_size": list(base_node.image.size),
               "triangles": tris_n, "triangle_budget": a.tris or None, "seam_vertices_merged": merged,
               "fit": res["fit"], "materials": materials})
report["valid"] = not report["problems"]
report["flags"] = list(report["problems"])
fb.write_json(OUT, "mesh_report.json", report)
sockets = {k: v for k, v in res["sockets"].items() if v is not None}
socket_file = {"format": "forge-held-item", "version": 1, "status": "pending review", "name": a.name,
               "class": TEMPLATE["class"], "sockets": sockets, "box": res["box"], "length_m": res["length_m"]}
fb.write_json(OUT, a.name + ".json", socket_file)
records = {}
for k, rec in res["proposal"].items():
    records[k] = dict(rec, used=sockets.get(k), source="manual" if manual_sockets is not None else "proposed")
for k in sockets:
    records.setdefault(k, {"used": sockets[k], "source": "manual"})
info = {"format": "forge-held-item-model", "version": 1, "status": "pending review", "name": a.name,
        "hold_class": TEMPLATE["class"], "template": TEMPLATE["template"],
        "axes": "item space: metres, +x front, +z top, +y left, the grip at the origin; in the GLB item x is "
                "glTF +x, item z glTF +y, item y glTF -z",
        "size_m": res["size_m"], "box_m": res["box"], "length_m": res["length_m"],
        "socket_file": a.name + ".json", "socket_records": records, "required": res["required"],
        "frame": {"file": "frame.json", "source": "manual" if manual_frame is not None else "order",
                  "front_given": front, "top_given": top, "square": res["frame"]["square"],
                  "scale": round(res["scale"], 6)},
        "sockets_source": "manual" if manual_sockets is not None else "proposed",
        "fit": res["fit"], "mesh": {"triangles": tris_n, "report": "mesh_report.json", "image_format": a.image_format},
        "seconds_export": round(time.time() - T0, 1)}
fb.write_json(OUT, "model.json", info)
print("DONE item", a.name, "class", TEMPLATE["class"], len(report["flags"]), "flags", flush=True)
fb.finish(0)
