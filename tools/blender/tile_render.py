"""An iso_tile object's mesh rendered as its sprite (run by blender_python; the iso_tile
recipe's form "mesh", forge/local_gpu.py run_tile_render).

The painted mesh is turned about the vertical (eight headings), its height stretched a little
(0.8 to 1.25), scaled and moved on the floor so that its silhouette, seen by the grid's
orthographic camera, fits the game's original sprite best (the overlap of the silhouettes); then Cycles renders it on the CPU into the sprite's
canvas at the art scale: the albedo (the base colour, unlit) and the normal in the sprite's
view (x right, y up, z toward the viewer, as (n + 1) / 2), both with the coverage as alpha.

    blender_python tile_render.py --mesh painted.glb --sprite original.png --spec spec.json --out DIR

spec.json: {"tile": the piece's tile object (forge/tile_jobs.py tile_spec), "grid": the
family's grid, "scale": art pixels per grid pixel, "samples": Cycles samples (default 32)}.
Writes DIR/albedo.png, DIR/normal.png and DIR/fit.json (heading, stretch, size, place and overlap).
The map's axes: X along the grid's x (to the lower right on screen), Y along its y (to the
lower left), Z up; one tile edge is one unit. A glTF mesh imported by Blender (x, y, z) lies
at map (x, -y, z): the map's frame is left-handed, so its y runs south.
"""

import argparse
import json
import math
import os
import sys

import bpy
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forge_blender as fb  # noqa: E402
import iso_grid as G  # noqa: E402

HEADINGS = 8
FIT_SUPERSAMPLE = 4
# The mesh's height over its footprint stretched by these factors (a generated bale or post a
# little squatter or taller than the original's): the best overlap wins.
STRETCHES = (0.8, 0.9, 1.0, 1.12, 1.25)


def args_of(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--mesh", required=True)
    p.add_argument("--sprite", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    return p.parse_args(argv)


def mesh_object(path):
    objs = fb.import_glb(path)
    meshes = [o for o in objs if o.type == "MESH"]
    if not meshes:
        fb.fail(4, "the GLB has no mesh")
    for o in objs:
        if o.type != "MESH":
            o.select_set(False)
    ob = fb.join_meshes(meshes)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    ob.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return ob


def map_vertices(ob):
    """Vertices (N, 3) in map axes (X, Y south, Z up) and triangles (M, 3)."""
    v, t = fb.mesh_arrays(ob)
    return np.stack([v[:, 0], -v[:, 1], v[:, 2]], axis=1), t


def project(g, pts):
    """Grid pixels of map points (Z in tile edges, true length)."""
    o, ex, ey, ez = G.screen_axes(g)
    z = pts[:, 2] / g["wall_aspect"]
    return o[None, :] + pts[:, :1] * ex[None, :] + pts[:, 1:2] * ey[None, :] + z[:, None] * ez[None, :]


def silhouette(g, pts, tris, rect, k=FIT_SUPERSAMPLE):
    """(H, W) bool: the mesh's silhouette on the canvas at k pixels per grid pixel."""
    x0, y0, cw, ch = rect
    w, h = int(round(cw * k)), int(round(ch * k))
    im = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(im)
    p = (project(g, pts) - np.array([x0, y0])) * k
    for a, b, c in tris:
        draw.polygon([tuple(p[a]), tuple(p[b]), tuple(p[c])], fill=255)
    return np.asarray(im) > 127


def original_mask(sprite, rect, k=FIT_SUPERSAMPLE):
    x0, y0, cw, ch = rect
    w, h = int(round(cw * k)), int(round(ch * k))
    xs = np.floor(x0 + (np.arange(w) + 0.5) / k).astype(int)
    ys = np.floor(y0 + (np.arange(h) + 0.5) / k).astype(int)
    a = sprite[..., 3] > 127
    out = np.zeros((h, w), bool)
    okx = (xs >= 0) & (xs < a.shape[1])
    oky = (ys >= 0) & (ys < a.shape[0])
    sub = a[np.clip(ys, 0, a.shape[0] - 1)][:, np.clip(xs, 0, a.shape[1] - 1)]
    out[np.ix_(oky, okx)] = sub[np.ix_(oky, okx)]
    return out


def box(mask):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def fit(g, pts, tris, sprite, rect):
    """The heading, height stretch, size and place whose silhouette overlaps the original's
    best: the mesh turned, its height stretched (STRETCHES), scaled so its silhouette's box fits
    in the original's, its bottom centre on the original's."""
    target = original_mask(sprite, rect)
    tb = box(target)
    if tb is None:
        fb.fail(4, "the original sprite is empty")
    o, ex, ey, ez = G.screen_axes(g)
    solve = np.linalg.inv(np.array([[ex[0], ey[0]], [ex[1], ey[1]]]))
    best = None
    base = pts - np.array([(pts[:, 0].max() + pts[:, 0].min()) / 2, (pts[:, 1].max() + pts[:, 1].min()) / 2,
                           pts[:, 2].min()])
    for i in range(HEADINGS * len(STRETCHES)):
        a = 2 * math.pi * (i // len(STRETCHES)) / HEADINGS
        stretch = STRETCHES[i % len(STRETCHES)]
        c, s = math.cos(a), math.sin(a)
        rot = base @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]]) * np.array([1.0, 1.0, stretch])
        p = project(g, rot)
        w, h = p[:, 0].max() - p[:, 0].min(), p[:, 1].max() - p[:, 1].min()
        k = FIT_SUPERSAMPLE
        tw_, th_ = (tb[2] - tb[0]) / k, (tb[3] - tb[1]) / k
        size = min(tw_ / max(w, 1e-6), th_ / max(h, 1e-6))
        q = rot * size
        p = project(g, q)
        want = np.array([rect[0] + (tb[0] + tb[2]) / 2.0 / k, rect[1] + tb[3] / float(k)])
        have = np.array([(p[:, 0].max() + p[:, 0].min()) / 2.0, p[:, 1].max()])
        mx, my = solve @ (want - have)
        q = q + np.array([mx, my, 0.0])
        sil = silhouette(g, q, tris, rect)
        inter = float((sil & target).sum())
        iou = inter / max(float((sil | target).sum()), 1.0)
        if best is None or iou > best["iou"] + 1e-9:
            best = {"heading_deg": round(math.degrees(a), 1), "stretch": stretch, "size": float(size),
                    "move": [float(mx), float(my)],
                    "iou": round(iou, 4), "cover": round(inter / max(float(target.sum()), 1.0), 4),
                    "overflow": round(float((sil & ~target).sum()) / max(float(sil.sum()), 1.0), 4),
                    "points": q, "centre": [float(x) for x in base.mean(axis=0)]}
    return best


def place(ob, best, pts):
    """The object moved, turned and scaled in Blender as the fit says (Blender y = -map Y)."""
    cx = (pts[:, 0].max() + pts[:, 0].min()) / 2
    cy = (pts[:, 1].max() + pts[:, 1].min()) / 2
    z0 = pts[:, 2].min()
    import mathutils
    # fit() turned map points by +a about Z; in Blender's frame (y = -Y) that is a turn by -a.
    a = -math.radians(best["heading_deg"])
    t0 = mathutils.Matrix.Translation((-cx, cy, -z0))
    r = mathutils.Matrix.Rotation(a, 4, "Z")
    k = best["size"]
    s = mathutils.Matrix.Diagonal((k, k, k * best.get("stretch", 1.0), 1.0))
    t1 = mathutils.Matrix.Translation((best["move"][0], -best["move"][1], 0.0))
    ob.matrix_world = t1 @ s @ r @ t0
    return ob


def camera(g, rect, scale):
    """An orthographic camera of the grid on the canvas rect at the art scale."""
    x0, y0, cw, ch = rect
    scene = bpy.context.scene
    scene.render.resolution_x = int(round(cw * scale))
    scene.render.resolution_y = int(round(ch * scale))
    scene.render.resolution_percentage = 100
    cam_data = bpy.data.cameras.new("tile_camera")
    cam_data.type = "ORTHO"
    px_per_unit = g["tile"][0] / math.sqrt(2.0)
    big = max(cw, ch)
    cam_data.ortho_scale = big / px_per_unit
    o = np.array(g["floor_top"])
    cam_data.shift_x = ((x0 + cw / 2.0) - o[0]) / big
    cam_data.shift_y = -((y0 + ch / 2.0) - o[1]) / big
    cam = bpy.data.objects.new("tile_camera", cam_data)
    scene.collection.objects.link(cam)
    e = math.radians(g["elevation"])
    d = 50.0
    # From the map's +X +Y side above: Blender (x, -y) = map (X, Y).
    cam.location = (d * math.cos(e) / math.sqrt(2), -d * math.cos(e) / math.sqrt(2), d * math.sin(e))
    direction = -np.array(cam.location)
    import mathutils
    cam.rotation_euler = mathutils.Vector(direction).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_start, cam_data.clip_end = 0.1, 200.0
    scene.camera = cam
    return cam


def base_colour_socket(mat):
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    return bsdf.inputs["Base Color"] if bsdf else None


def set_pass(ob, mode):
    """Every material of the object made an emission of its base colour (albedo) or of its
    normal in camera space as (n + 1) / 2 (normal)."""
    for slot in ob.material_slots:
        mat = slot.material
        if mat is None:
            continue
        mat.use_nodes = True
        tree = mat.node_tree
        if "tile_base" not in mat:
            sock = base_colour_socket(mat)
            mat["tile_base"] = 1
            src = sock.links[0].from_socket if sock is not None and sock.links else None
            mat["tile_colour"] = list(sock.default_value) if sock is not None else [0.6, 0.6, 0.6, 1.0]
            if src is not None:
                mat["tile_src"] = [src.node.name, src.identifier]
        out = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
        emit = tree.nodes.get("tile_emit") or tree.nodes.new("ShaderNodeEmission")
        emit.name = "tile_emit"
        for link in list(emit.inputs["Color"].links):
            tree.links.remove(link)
        if mode == "albedo":
            if "tile_src" in mat:
                node = tree.nodes[mat["tile_src"][0]]
                sock = next(s for s in node.outputs if s.identifier == mat["tile_src"][1])
                tree.links.new(sock, emit.inputs["Color"])
            else:
                emit.inputs["Color"].default_value = tuple(mat["tile_colour"])
        else:
            geo = tree.nodes.get("tile_geo") or tree.nodes.new("ShaderNodeNewGeometry")
            geo.name = "tile_geo"
            vt = tree.nodes.get("tile_vt") or tree.nodes.new("ShaderNodeVectorTransform")
            vt.name = "tile_vt"
            vt.vector_type, vt.convert_from, vt.convert_to = "NORMAL", "WORLD", "CAMERA"
            ma = tree.nodes.get("tile_ma") or tree.nodes.new("ShaderNodeVectorMath")
            ma.name = "tile_ma"
            ma.operation = "MULTIPLY_ADD"
            # Cycles' camera space looks along +z: the sprite's view has z toward the viewer.
            ma.inputs[1].default_value = (0.5, 0.5, -0.5)
            ma.inputs[2].default_value = (0.5, 0.5, 0.5)
            tree.links.new(geo.outputs["Normal"], vt.inputs["Vector"])
            tree.links.new(vt.outputs["Vector"], ma.inputs[0])
            tree.links.new(ma.outputs["Vector"], emit.inputs["Color"])
        for link in list(out.inputs["Surface"].links):
            tree.links.remove(link)
        tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])


def render(path, view):
    scene = bpy.context.scene
    scene.view_settings.view_transform = view
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main(argv):
    a = args_of(argv)
    a.out = os.path.abspath(a.out)
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    tile = spec["tile"]
    g = G.grid_of(spec.get("grid"))
    scale = int(spec.get("scale", 4))
    rect = G.canvas(g, tile.get("rect"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("BPY", bpy.app.version_string, flush=True)
    ob = mesh_object(a.mesh)
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(50))
    except Exception:
        bpy.ops.object.shade_smooth()
    pts, tris = map_vertices(ob)
    sprite = np.asarray(Image.open(a.sprite).convert("RGBA"))
    best = fit(g, pts, tris, sprite, rect)
    place(ob, best, pts)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(spec.get("samples", 32))
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 0
    scene.cycles.pixel_filter_type = "BOX"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.world = bpy.data.worlds.new("tile_world")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Strength"].default_value = 0.0
    camera(g, rect, scale)
    os.makedirs(a.out, exist_ok=True)
    set_pass(ob, "albedo")
    render(os.path.join(a.out, "albedo.png"), "Standard")
    set_pass(ob, "normal")
    render(os.path.join(a.out, "normal.png"), "Raw")
    rec = {k: v for k, v in best.items() if k not in ("points",)}
    fb.write_json(a.out, "fit.json", {"format": "forge-tile-render", "fit": rec, "rect": rect, "scale": scale,
                                      "samples": scene.cycles.samples, "grid": {k: g[k] for k in G.GRID_KEYS if k in g}})
    print("DONE", json.dumps(rec), flush=True)
    fb.finish(0)


if __name__ == "__main__":
    main(sys.argv[1:])
