"""Blender helpers shared by the articulated and creature exporters (articulated_export.py,
creature_export.py): output files, the glTF material slots as Blender's importer lays them
out, the paint stage's metallic-roughness map, lossless images and the check of the written
GLB's materials (glb_materials.py). Imported by scripts that run with a Python that imports
bpy. The humanoid exporter keeps its own copy of these steps."""

import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glb_materials  # noqa: E402

JPEG_QUALITY = 92
LOSSY_EXT = (".jpg", ".jpeg", ".jpe", ".webp")
FRONT_YAW = {"+z": 0.0, "-z": 180.0, "+x": -90.0, "-x": 90.0}


def write_json(out, name, data):
    path = os.path.join(out, name)
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


def import_glb(path):
    """The objects a GLB brings into the scene."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))
    return [o for o in bpy.data.objects if o not in before]


def join_meshes(meshes):
    """One mesh object of several (the first's name), transforms applied."""
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def merge_seams(ob):
    """Vertices split along UV seams merged again (UVs stay per corner): cuts and weights
    then see one surface. Returns how many were merged."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    size = max(ob.dimensions) or 1.0
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-5 * size)
    merged = before - len(bm.verts)
    bm.to_mesh(ob.data)
    bm.free()
    return merged


def turn_to_front(objects, front):
    """Turn objects about the vertical so that a front along the glTF axis `front` faces
    glTF +Z (Blender -Y); returns the turn in degrees."""
    deg = FRONT_YAW[front]
    if deg:
        m = Matrix.Rotation(math.radians(deg), 4, "Z")
        for o in objects:
            if o.parent is None:
                o.matrix_world = m @ o.matrix_world
    return deg


def apply_transforms(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()


def mesh_arrays(ob):
    """(vertices (N, 3) world, triangles (M, 3)) of a mesh object."""
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(ob.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    return co, tri.reshape(-1, 3)


# ---- material slots ------------------------------------------------------------------------

def image_above(socket, depth=4):
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


def find_slots(tree, shader):
    """{slot: image node} of the texture slots a material has."""
    out = {"base_color": image_above(socket_of(shader, "Base Color"), 0)[0]}
    node, output = image_above(socket_of(shader, "Alpha"))
    out["opacity"] = node if output == "Alpha" else None
    out["metallic_roughness"] = (image_above(socket_of(shader, "Roughness"))[0]
                                 or image_above(socket_of(shader, "Metallic"))[0])
    out["normal"] = image_above(socket_of(shader, "Normal"))[0]
    out["emissive"] = image_above(socket_of(shader, "Emission Color", "Emission"))[0]
    grp = next((n for n in tree.nodes if n.type == "GROUP" and n.node_tree is not None and "Occlusion" in n.inputs),
               None)
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


def link_metal_roughness(mat, img):
    """A metallic-roughness map (glTF: roughness green, metallic blue) on a material."""
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    img.colorspace_settings.name = "Non-Color"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])


def lossy_source(img):
    if img.source != "FILE":
        return False
    if img.packed_file is not None:
        data = img.packed_file.data[:12]
        return data[:3] == b"\xff\xd8\xff" or data[8:12] == b"WEBP"
    return img.filepath_raw.lower().endswith(LOSSY_EXT)


def rewritten(img, fmt, folder):
    """A packed copy of an image as a PNG or JPEG file (same pixels, name and colour space)."""
    from PIL import Image as PILImage
    os.makedirs(folder, exist_ok=True)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    if img.is_float and img.colorspace_settings.name == "sRGB":
        c = np.clip(px[..., :3], 0.0, 1.0)
        px[..., :3] = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    px = np.clip(np.round(px * 255.0), 0, 255).astype(np.uint8)[::-1]
    alpha = fmt == "png" and img.channels == 4 and bool((px[..., 3] < 255).any())
    pic = PILImage.fromarray(np.ascontiguousarray(px if alpha else px[..., :3]), "RGBA" if alpha else "RGB")
    name = img.name
    path = os.path.join(folder, "%d.%s" % (len(os.listdir(folder)), "jpg" if fmt == "jpeg" else "png"))
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


def prepare_images(mat, image_format, folder):
    """Every lossy source of the material rewritten as PNG; with "compact", an opaque base
    colour written once as JPEG (quality 92)."""
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    slots = find_slots(nt, bsdf)
    done = {}
    for slot, node in sorted(slots.items()):
        img = node.image
        if slot == "base_color" and image_format == "compact" and "opacity" not in slots \
                and not bsdf.inputs["Alpha"].is_linked:
            fmt = "jpeg"
        elif lossy_source(img):
            fmt = "png"
        else:
            continue
        key = (img.name, fmt)
        if key not in done:
            print("IMAGE", slot, img.name, "->", fmt, flush=True)
            done[key] = rewritten(img, fmt, folder)
        for n in nt.nodes:
            if n.type == "TEX_IMAGE" and n.image == img:
                n.image = done[key]


def material_report(glb, mesh_name, sources, image_format, not_carried=None):
    """(materials record for the mesh report, {slot: flag}) of the written GLB."""
    read = glb_materials.material_slots(glb, mesh_name)
    slots = {}
    for slot in glb_materials.SLOTS:
        r = read[slot]
        src = sources.get(slot)
        slots[slot] = {"present": r["present"], "format": r["format"], "size": r["size"], "lossless": r["lossless"],
                       "image": r["image"], "source": src is not None, "source_from": src}
    slots["opacity"]["alpha_mode"] = read["opacity"].get("alpha_mode")
    flags = glb_materials.slot_flags(read, sources, image_format, not_carried or {})
    return {"image_format": image_format, "read_from": os.path.basename(glb) + " as written", "slots": slots}, flags


def export_glb(path, objects, animations=False):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    kw = {"export_animations": False}
    if animations:
        kw = {"export_animations": True, "export_animation_mode": "NLA_TRACKS", "export_skins": True,
              "export_def_bones": False, "export_frame_range": False, "export_force_sampling": False,
              "export_optimize_animation_size": False}
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_image_format="AUTO",
                              export_image_quality=JPEG_QUALITY, export_jpeg_quality=JPEG_QUALITY,
                              export_draco_mesh_compression_enable=False, **kw)
    print("EXPORTED", path, flush=True)
    print("NOTE a Draco line above is harmless: the GLB is written without Draco compression", flush=True)
