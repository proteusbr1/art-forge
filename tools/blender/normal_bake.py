"""Smooth shading and the normal map of a painted mesh in Blender: shared by the held-item
and articulated exporters (imported) and run as a script by the refresh command
(python -m forge.normals) to bake an exported GLB's normal map.

- smooth_by_angle(ob, deg): every face smooth, edges sharper than deg kept hard.
- import_source(path): the raw (pre-reduction) mesh, one object.
- align_source(high, lows, tolerance): the raw mesh brought onto the game mesh: as it is, by
  its bounds (uniform scale and translation: what a re-centring or rescaling tool does), or
  found (a similarity transform searched from the 48 frames of the two meshes' axes and
  principal axes, refined by closest points); used when 90 % of the game mesh's vertices lie
  within tolerance of the raw surface.
- bake(lows, high, size, device): a tangent-space normal map per base-colour image of the
  game meshes' materials (Cycles, selected to active, ray 2 % and cage 0.6 % of the extent,
  as the humanoid exporter), on copies joined into one object (armature and other modifiers
  off: the rest pose).

As a script (Python that imports bpy):

  python normal_bake.py --glb MODEL.glb --out DIR [--raw RAW.glb|.obj|.ply] [--size 0]
      [--max-size 4096] [--device CPU|GPU] [--tolerance 0.01]

imports MODEL.glb with its own normals (glb_normals.smooth_glb wrote them), aligns RAW and
bakes; writes DIR/normal_<k>.png (rows top first, as a picture), DIR/bake.json: per map its
base-colour image's size and signature (a 8 x 8 grid of its mean colours: the refresh command
matches it to the GLB's image), the fit and the device. Without --raw no map is baked
(bake.json says so)."""
import argparse
import json
import math
import os
import sys
import time

import bpy  # first: bmesh and mathutils come with it
import numpy as np
from mathutils import Matrix, Vector

BAKE_SAMPLES = 4000
TOLERANCE = 0.01  # a share of the game mesh's largest extent


def smooth_by_angle(ob, deg):
    """Every face of a mesh object smooth; edges whose faces meet at more than deg sharp
    (custom normals cleared first)."""
    me = ob.data
    if me.has_custom_normals:
        bpy.context.view_layer.objects.active = ob
        with bpy.context.temp_override(object=ob, active_object=ob):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(deg))
    me.update()


def import_source(path):
    """The raw mesh at path (GLB, glTF, OBJ or PLY) as one mesh object with its transforms
    applied, or (None, why)."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))
        elif ext == ".obj":
            bpy.ops.wm.obj_import(filepath=os.path.abspath(path))
        elif ext == ".ply":
            bpy.ops.wm.ply_import(filepath=os.path.abspath(path))
        else:
            return None, "unknown format %s" % ext
    except RuntimeError as ex:
        return None, "cannot be read (%s)" % ex
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:
        if o.type == "MESH":
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    new = [o for o in new if o.name in bpy.data.objects and o.type == "MESH"]
    if not new:
        return None, "has no mesh"
    bpy.ops.object.select_all(action="DESELECT")
    for o in new:
        o.select_set(True)
    bpy.context.view_layer.objects.active = new[0]
    if len(new) > 1:
        bpy.ops.object.join()
    high = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    high.data.materials.clear()
    for m in list(high.modifiers):
        high.modifiers.remove(m)
    high.vertex_groups.clear()
    return high, None


def world_vertices(objects, limit=None):
    """(N, 3) world positions of the vertices of mesh objects (rest pose), every k-th one."""
    out = []
    for ob in objects:
        me = ob.data
        co = np.empty(len(me.vertices) * 3, np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        mw = np.array(ob.matrix_world)
        out.append(co @ mw[:3, :3].T + mw[:3, 3])
    pts = np.concatenate(out) if out else np.zeros((0, 3))
    if limit and len(pts) > limit:
        pts = pts[:: max(1, len(pts) // limit)]
    return pts


def _tree(high):
    """(BVH tree of the raw mesh, its vertices (N, 3)); the object's transforms are applied."""
    from mathutils.bvhtree import BVHTree
    me = high.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    return BVHTree.FromObject(high, bpy.context.evaluated_depsgraph_get()), co.reshape(-1, 3)


def _nearest(tree, pts, normals=False):
    """(nearest points (N, 3), distances (N,)[, their surface normals (N, 3)]) on the tree's
    surface."""
    near = np.empty_like(pts)
    dist = np.empty(len(pts))
    nrm = np.zeros_like(pts)
    for i, p in enumerate(pts):
        hit = tree.find_nearest(Vector(p))
        if hit[0] is None:
            near[i] = p
            dist[i] = np.inf
        else:
            near[i] = hit[0]
            nrm[i] = hit[1]
            dist[i] = hit[3]
    return (near, dist, nrm) if normals else (near, dist)


def _similarity(src, dst):
    """(s, R, t) with dst ~ s R src + t (Umeyama)."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    cov = b.T @ a / len(src)
    u, d, vt = np.linalg.svd(cov)
    e = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        e[2, 2] = -1
    r = u @ e @ vt
    var = (a * a).sum() / len(src)
    s = float(np.trace(np.diag(d) @ e) / max(var, 1e-30))
    return s, r, md - s * r @ ms


def _rotations():
    """The 24 rotations that take axes to axes."""
    out = []
    import itertools
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for i, (p, s) in enumerate(zip(perm, signs)):
                m[i, p] = s
            if np.linalg.det(m) > 0:
                out.append(m)
    return out


def _axes(pts):
    c = pts.mean(0)
    _, vec = np.linalg.eigh(np.cov((pts - c).T))
    vec = vec[:, ::-1]
    if np.linalg.det(vec) < 0:
        vec[:, 2] *= -1
    return c, vec


def _icp(tree, low, s, r, t, steps):
    """Refine (s, R, t) taking the raw mesh onto the low points: closest points of the raw
    surface to the low points brought into its frame."""
    for _ in range(steps):
        back = ((low - t) @ r) / s  # the low points in the raw mesh's frame
        near, dist = _nearest(tree, back)
        ok = np.isfinite(dist)
        if ok.sum() < 8:
            break
        cut = np.percentile(dist[ok], 90)
        ok &= dist <= cut  # the farthest tenth (parts the raw mesh lacks) left out
        s, r, t = _similarity(near[ok], low[ok])
    return s, r, t


def _rotation(w):
    """The rotation of the axis-angle vector w."""
    a = float(np.linalg.norm(w))
    if a < 1e-15:
        return np.eye(3)
    k = w / a
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(a) * kx + (1 - np.cos(a)) * kx @ kx


def _icp_plane(tree, low, s, r, t, steps):
    """Refine (s, R, t) point to plane: a closest point's distance along the raw surface's
    normal is minimised (a linearised small rotation, scale and translation per step), which
    converges where point to point slides (along a long barrel, say)."""
    for _ in range(steps):
        back = ((low - t) @ r) / s
        near, dist, nrm = _nearest(tree, back, normals=True)
        ok = np.isfinite(dist)
        if ok.sum() < 16:
            break
        ok &= dist <= np.percentile(dist[ok], 90)
        y = s * near[ok] @ r.T + t
        n = nrm[ok] @ r.T
        x = low[ok]
        c = y.mean(0)
        yc = y - c
        a_ = np.concatenate([(n * yc).sum(1)[:, None], np.cross(yc, n), n], axis=1)
        b_ = (n * (x - y)).sum(1)
        sol, *_ = np.linalg.lstsq(a_, b_, rcond=None)
        d, w, dt = sol[0], sol[1:4], sol[4:7]
        q = (1.0 + d) * _rotation(w)
        # y' = q (y - c) + c + dt, with y = s R h + t
        r = _rotation(w) @ r
        s = s * (1.0 + d)
        t = q @ (t - c) + c + dt
        if np.linalg.norm(sol) < 1e-7:
            break
    return s, r, t


def _measure(tree, low, s, r, t):
    back = ((low - t) @ r) / s
    _, dist = _nearest(tree, back)
    dist = dist * s
    return {"median": round(float(np.median(dist)), 5), "p90": round(float(np.percentile(dist, 90)), 5)}


def align_source(high, lows, tolerance=TOLERANCE, search=True):
    """Bring the raw mesh high onto the game mesh objects lows (world space). Returns the
    record for the reports: tolerance_m, how (as_is, bounds, found), distance_m (median and
    90th percentile from the game mesh's vertices to the raw surface), scale, and used (False
    when it still does not line up: the raw mesh is then left out of the bake)."""
    low = world_vertices(lows)
    ext = float(np.ptp(low, axis=0).max()) if len(low) else 1.0
    tol = tolerance * ext
    sample = low[:: max(1, len(low) // BAKE_SAMPLES)]
    tree, hv = _tree(high)
    rec = {"tolerance_m": round(tol, 5), "vertices": int(len(hv))}
    eye = (1.0, np.eye(3), np.zeros(3))
    d0 = _measure(tree, sample, *eye)
    rec["distance_as_is_m"] = d0
    best, best_d, how = eye, d0, "as_is"
    if d0["p90"] > tol:
        lo_h, hi_h = hv.min(0), hv.max(0)
        lo_b, hi_b = low.min(0), low.max(0)
        eh, eb = hi_h - lo_h, hi_b - lo_b
        s = float((eh * eb).sum() / max(float((eh * eh).sum()), 1e-12))
        t = (lo_b + hi_b) / 2.0 - s * (lo_h + hi_h) / 2.0
        d1 = _measure(tree, sample, s, np.eye(3), t)
        rec["distance_bounds_m"] = d1
        if d1["p90"] < best_d["p90"]:
            best, best_d, how = (s, np.eye(3), t), d1, "bounds"
    if best_d["p90"] > tol and search:
        coarse = sample[:: max(1, len(sample) // 300)]
        hs = hv[:: max(1, len(hv) // 20000)]
        cl, al = _axes(low)
        ch, ah = _axes(hs)
        rms_l = float(np.sqrt(((low - cl) ** 2).sum(1).mean()))
        rms_h = float(np.sqrt(((hs - ch) ** 2).sum(1).mean()))
        s0 = rms_l / max(rms_h, 1e-12)
        tried = []
        for frame in ("axes", "principal"):
            for p in _rotations():
                r0 = p if frame == "axes" else al @ p @ ah.T
                t0 = cl - s0 * r0 @ ch
                s, r, t = _icp(tree, coarse, s0, r0, t0, 4)
                tried.append((_measure(tree, coarse, s, r, t)["p90"], s, r, t))
        tried.sort(key=lambda x: x[0])
        for _, s, r, t in tried[:3]:
            fine = sample[:: max(1, len(sample) // 2000)]
            s, r, t = _icp(tree, fine, s, r, t, 10)
            s, r, t = _icp_plane(tree, fine, s, r, t, 30)
            d = _measure(tree, sample, s, r, t)
            if d["p90"] < best_d["p90"]:
                best, best_d, how = (s, r, t), d, "found"
        rec["candidates"] = len(tried)
    s, r, t = best
    m = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            m[i][j] = s * r[i, j]
        m[i][3] = t[i]
    if how != "as_is":
        high.data.transform(m)
        high.data.update()
    rec.update(how=how, distance_m=best_d, scale=round(s, 6), used=best_d["p90"] <= tol,
               matrix=[[float(m[i][j]) for j in range(4)] for i in range(4)],
               rotation_deg=round(math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1) / 2)))), 3))
    return rec


def _image_pixels(img):
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def signature(rgb_top_first):
    """A 8 x 8 grid of an image's mean colours (0..255), to match one image across tools."""
    a = np.asarray(rgb_top_first, np.float64)[..., :3]
    h, w = a.shape[:2]
    ys = np.linspace(0, h, 9).astype(int)
    xs = np.linspace(0, w, 9).astype(int)
    return [[[round(float(a[ys[i]:ys[i + 1], xs[j]:xs[j + 1], c].mean()), 2) for c in range(3)] for j in range(8)]
            for i in range(8)]


def base_images(objects):
    """{material: base-colour image or None} of the objects' materials."""
    out = {}
    for ob in objects:
        for m in ob.data.materials:
            if m is None or m in out:
                continue
            img = None
            if m.use_nodes:
                bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf is not None and bsdf.inputs["Base Color"].is_linked:
                    node = bsdf.inputs["Base Color"].links[0].from_node
                    if node.type == "TEX_IMAGE":
                        img = node.image
            out[m] = img
    return out


def use_device(device):
    """Cycles on the GPU when asked and one is found, else the CPU. Returns what is used."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    used = "CPU"
    if device == "GPU":
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
                used = "GPU " + kind
                break
        if used == "CPU":
            print("WARNING no GPU device for Cycles; baking on the CPU", flush=True)
    scene.cycles.device = "GPU" if used != "CPU" else "CPU"
    scene.cycles.samples = 1
    return used


def bake(lows, high, size=0, device="CPU", max_size=4096):
    """{base-colour image: baked normal image (Blender, Non-Color)} of the game mesh objects
    lows from the raw mesh high (None: the maps stay flat). size: the maps' size (0: each
    base-colour image's size, at most max_size). Returns (maps, device used)."""
    bases = base_images(lows)
    targets = {}
    for mat, img in bases.items():
        if img is None or img in targets:
            continue
        w = size or min(max(img.size[0], img.size[1]), max_size)
        t = bpy.data.images.new(img.name + "_normal", w, w, alpha=False)
        t.colorspace_settings.name = "Non-Color"
        t.generated_color = (0.5, 0.5, 1.0, 1.0)
        targets[img] = t
    if high is None or not targets:
        return targets, None
    # Copies of the game meshes joined into one object in world space, modifiers off.
    copies = []
    for ob in lows:
        c = ob.copy()
        c.data = ob.data.copy()
        for m in list(c.modifiers):
            c.modifiers.remove(m)
        c.parent = None
        c.matrix_world = ob.matrix_world.copy()
        bpy.context.scene.collection.objects.link(c)
        copies.append(c)
    bpy.ops.object.select_all(action="DESELECT")
    for c in copies:
        c.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    low = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # A bake target in every material: its normal image, or a scratch one (no base colour).
    scratch = bpy.data.images.new("_scratch_normal", 64, 64, alpha=False)
    added = []
    for m in low.data.materials:
        if m is None:
            continue
        m.use_nodes = True
        node = m.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = targets.get(bases.get(m), scratch)
        m.node_tree.nodes.active = node
        added.append((m, node))
    if not low.data.materials:
        fail_mat = bpy.data.materials.new("_bake")
        low.data.materials.append(fail_mat)
    if not high.data.materials:
        high.data.materials.append(bpy.data.materials.new("_high"))
    used = use_device(device)
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    ext = max(low.dimensions)
    bpy.ops.object.bake(type="NORMAL", use_selected_to_active=True, cage_extrusion=0.006 * ext,
                        max_ray_distance=0.02 * ext, normal_space="TANGENT", margin=8, use_clear=True)
    for m, node in added:
        m.node_tree.nodes.remove(node)
    bpy.data.objects.remove(low)
    bpy.data.images.remove(scratch)
    return targets, used


def pixels_top_first(img):
    """An image's RGB as floats 0..1, rows top first (as a picture)."""
    return _image_pixels(img)[::-1, :, :3].copy()


def set_pixels_top_first(img, rgb):
    h, w = rgb.shape[:2]
    px = np.ones((h, w, 4), np.float32)
    px[..., :3] = rgb[::-1]
    img.pixels.foreach_set(px.ravel())
    img.update()


def link_normal_map(mat, img):
    """A tangent-space normal map on a material's Principled BSDF (an earlier one unlinked)."""
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    for lk in list(bsdf.inputs["Normal"].links):
        nt.links.remove(lk)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])


def normal_maps(lows, high, size=0, device="CPU", relief=None):
    """The exporters' normal maps: baked from the raw mesh high (None: flat), the painted
    texture's relief blended in (relief: its strength, 0 or None for none), linked into every
    material of lows that has that base colour. Returns (maps {base image: normal image},
    device used or None)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import glb_normals as gn
    maps, used = bake(lows, high, size, device)
    for base, img in maps.items():
        n = gn.decode_normals(np.round(pixels_top_first(img) * 255.0))
        if relief:
            rgb = pixels_top_first(base)
            if rgb.shape[:2] != n.shape[:2]:
                from PIL import Image
                rgb = np.asarray(Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8)).resize(
                    (n.shape[1], n.shape[0]), Image.BILINEAR)).astype(np.float32) / 255.0
            n = gn.blend_normals(n, gn.relief_normals(rgb, relief))
        set_pixels_top_first(img, gn.encode_normals(n).astype(np.float32) / 255.0)
    for mat, base in base_images(lows).items():
        if base in maps:
            link_normal_map(mat, maps[base])
    return maps, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw", default="")
    ap.add_argument("--via", default="", help="a mesh in the raw mesh's frame with the model's own surface (the "
                                             "painted mesh the model was exported from): the model is found on it")
    ap.add_argument("--size", type=int, default=0)
    ap.add_argument("--max-size", type=int, default=4096)
    ap.add_argument("--device", default="CPU", choices=("CPU", "GPU"))
    ap.add_argument("--tolerance", type=float, default=TOLERANCE)
    a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
    t0 = time.time()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    print("BPY", bpy.app.version_string, flush=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(a.glb), import_shading="NORMALS")
    lows = [o for o in bpy.data.objects if o.type == "MESH"]
    for o in bpy.data.objects:  # the rest pose
        if o.type == "ARMATURE":
            o.data.pose_position = "REST"
    bpy.context.view_layer.update()
    rec = {"format": "forge-normal-bake", "version": 1, "glb": os.path.basename(a.glb), "maps": [],
           "raw": os.path.basename(a.raw) if a.raw else None}
    high = None
    via = None
    if a.raw and a.via:
        via, why = import_source(a.via)
        if via is None:
            rec["via_problem"] = "the mesh %s %s" % (os.path.basename(a.via), why)
    if a.raw:
        high, why = import_source(a.raw)
        if high is None:
            rec["raw_problem"] = "the raw mesh %s %s" % (os.path.basename(a.raw), why)
        elif via is not None:
            # The raw mesh onto the via mesh (same frame: as it is or by its bounds), the via
            # mesh found on the model, the raw mesh moved with it, then measured.
            rec["raw_on_via"] = align_source(high, [via], a.tolerance, search=False)
            rec["via"] = align_source(via, lows, a.tolerance)
            print("VIA", json.dumps({k: v for k, v in rec["via"].items() if k != "matrix"}), flush=True)
            high.data.transform(Matrix(rec["via"]["matrix"]))
            high.data.update()
            rec["fit"] = align_source(high, lows, a.tolerance, search=False)
            rec["fit"]["how"] = "via " + rec["via"]["how"]
            bpy.data.objects.remove(via)
        else:
            rec["fit"] = align_source(high, lows, a.tolerance)
            print("FIT", json.dumps(rec["fit"]), flush=True)
            if not rec["fit"]["used"]:
                rec["raw_problem"] = ("the raw mesh does not line up with the model (90th percentile distance %.4f m, "
                                      "tolerance %.4f m): not baked" % (rec["fit"]["distance_m"]["p90"],
                                                                       rec["fit"]["tolerance_m"]))
                bpy.data.objects.remove(high)
                high = None
    if high is not None:
        maps, used = bake(lows, high, a.size, a.device, max_size=a.max_size)
        rec["device"] = used
        from PIL import Image
        for k, (base, img) in enumerate(maps.items()):
            name = "normal_%d.png" % k
            rgb = pixels_top_first(img)
            Image.fromarray(np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8), "RGB").save(
                os.path.join(out, name))
            base_rgb = pixels_top_first(base)
            if base.colorspace_settings.name == "sRGB" and base.is_float:
                c = np.clip(base_rgb, 0.0, 1.0)
                base_rgb = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
            rec["maps"].append({"file": name, "base_image": base.name, "base_size": list(base.size),
                                "size": list(img.size), "signature": signature(base_rgb * 255.0)})
            print("BAKED", name, "for", base.name, list(img.size), flush=True)
    rec["baked"] = bool(rec["maps"])
    rec["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(out, "bake.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("DONE", len(rec["maps"]), "maps", flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
