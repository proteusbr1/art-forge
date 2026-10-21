"""Smooth normals and the normal map of a GLB, without Blender: NumPy and Pillow only, so that
the exporters (in Blender's Python), the factory's normals step and the refresh command
(python -m forge.normals) share it.

A mesh generator writes no normals (the Hunyuan3D paint stage's GLB has no NORMAL attribute;
glTF viewers then shade every triangle flat), and a mesh exported flat-shaded carries one
normal per triangle. Both look faceted. Here:

- creased_normals(): a normal per triangle corner, the corner-angle weighted mean of the
  normals of the triangles around its vertex that lie within the crease angle of its own
  triangle (vertices at the same place count as one, so a UV seam does not split the
  shading); edges sharper than the crease stay hard.
- smooth_glb(): every triangle primitive of a GLB rewritten with those normals (vertices
  split where a hard edge needs two normals, and vertices equal in every attribute merged);
  every other attribute, skin, animation, node, material and image is kept. A TANGENT
  attribute is dropped (it belonged to the old normals; a client computes MikkTSpace
  tangents from the new ones).
- relief_normals() and blend_normals(): the painted texture's relief as tangent-space
  normal detail and its blend with a baked map (as the humanoid exporter does).
- set_normal_maps(): a tangent-space normal map (PNG, lossless) put into materials'
  normalTexture slots.
- normals_state() and check_glb(): what a written GLB holds: per primitive whether it has
  normals and how many of its triangles are shaded flat; per material whether its normal map
  is present and lossless.
"""
import io
import json
import struct

import numpy as np

CREASE_DEG = 60.0
RELIEF_STRENGTH = 3.0
# The image names this module gives the normal maps it writes (a later run replaces them).
NORMAL_IMAGE = "normal_forge"
COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942
# Two corners' normals at one place are split (shaded apart) when they differ by more than this.
FLAT_DEG = 1.0


# ---- the file --------------------------------------------------------------------------------

def read_glb(path):
    """(the JSON document, the binary chunk as bytes) of a GLB file."""
    with open(path, "rb") as f:
        data = f.read()
    magic, _, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("%s is not a GLB file" % path)
    doc, binary, at = None, b"", 12
    while at + 8 <= min(length, len(data)):
        size, kind = struct.unpack_from("<II", data, at)
        chunk = data[at + 8:at + 8 + size]
        if kind == JSON_CHUNK:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == BIN_CHUNK and not binary:
            binary = bytes(chunk)
        at += 8 + size
    if doc is None:
        raise ValueError("%s has no JSON chunk" % path)
    if len(doc.get("buffers", [])) > 1 or any(bv.get("buffer", 0) != 0 for bv in doc.get("bufferViews", [])):
        raise ValueError("%s: a GLB with more than one buffer is not supported" % path)
    if doc.get("buffers") and doc["buffers"][0].get("uri"):
        raise ValueError("%s: a GLB whose buffer is an outside file is not supported" % path)
    return doc, binary


def write_glb(path, doc, binary):
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binary = bytes(binary) + b"\0" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(js) + (8 + len(binary) if binary else 0)
    with open(path + ".tmp", "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), JSON_CHUNK))
        f.write(js)
        if binary:
            f.write(struct.pack("<II", len(binary), BIN_CHUNK))
            f.write(binary)
    import os
    os.replace(path + ".tmp", path)


class Glb:
    """A GLB held as its document and one bytes object per buffer view, so that views can be
    added and the binary chunk rebuilt (unused accessors and views dropped)."""

    def __init__(self, path):
        self.doc, binary = read_glb(path)
        self.views = [binary[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
                      for bv in self.doc.get("bufferViews", [])]

    def accessor(self, index):
        """The values of an accessor as an (count, width) array of its component type."""
        acc = self.doc["accessors"][index]
        dtype = np.dtype(COMPONENTS[acc["componentType"]]).newbyteorder("<")
        width = WIDTH[acc["type"]]
        count = acc["count"]
        if "bufferView" in acc:
            bv = self.doc["bufferViews"][acc["bufferView"]]
            data = self.views[acc["bufferView"]]
            start = acc.get("byteOffset", 0)
            item = dtype.itemsize * width
            stride = bv.get("byteStride") or item
            if stride == item:
                out = np.frombuffer(data, dtype, count * width, start).reshape(count, width).copy()
            else:
                rows = np.frombuffer(data, np.uint8, (count - 1) * stride + item, start) if count else \
                    np.zeros(0, np.uint8)
                out = np.lib.stride_tricks.as_strided(rows, (count, item), (stride, 1)).copy().view(dtype)
                out = out.reshape(count, width)
        else:
            out = np.zeros((count, width), dtype)
        sp = acc.get("sparse")
        if sp:
            ii = sp["indices"]
            idt = np.dtype(COMPONENTS[ii["componentType"]]).newbyteorder("<")
            idx = np.frombuffer(self.views[ii["bufferView"]], idt, sp["count"], ii.get("byteOffset", 0))
            vv = sp["values"]
            vals = np.frombuffer(self.views[vv["bufferView"]], dtype, sp["count"] * width, vv.get("byteOffset", 0))
            out[idx.astype(np.int64)] = vals.reshape(-1, width)
        return out

    def add_accessor(self, values, like=None, target=None, kind=None, minmax=False):
        """A new accessor (and view) of values ((count, width) array); like: an accessor whose
        component type, type and normalized flag it takes; kind overrides the type."""
        values = np.ascontiguousarray(values)
        if like is not None:
            old = self.doc["accessors"][like]
            values = values.astype(np.dtype(COMPONENTS[old["componentType"]]).newbyteorder("<"))
            acc = {"componentType": old["componentType"], "type": old["type"]}
            if old.get("normalized"):
                acc["normalized"] = True
        else:
            ct = next(k for k, v in COMPONENTS.items() if np.dtype(v) == values.dtype)
            acc = {"componentType": ct, "type": kind}
        acc["count"] = int(values.shape[0])
        if minmax and len(values):
            acc["min"] = [float(x) if values.dtype.kind == "f" else int(x) for x in values.min(0)]
            acc["max"] = [float(x) if values.dtype.kind == "f" else int(x) for x in values.max(0)]
        view = {"buffer": 0, "byteLength": int(values.nbytes)}
        if target is not None:
            view["target"] = target
        self.doc.setdefault("bufferViews", []).append(view)
        self.views.append(values.tobytes())
        acc["bufferView"] = len(self.views) - 1
        self.doc.setdefault("accessors", []).append(acc)
        return len(self.doc["accessors"]) - 1

    def add_image(self, png, name):
        self.doc.setdefault("bufferViews", []).append({"buffer": 0, "byteLength": len(png)})
        self.views.append(png)
        self.doc.setdefault("images", []).append({"bufferView": len(self.views) - 1, "mimeType": "image/png",
                                                 "name": name})
        return len(self.doc["images"]) - 1

    def image_bytes(self, index):
        img = self.doc["images"][index]
        if "bufferView" in img:
            return self.views[img["bufferView"]]
        uri = img.get("uri", "")
        if uri.startswith("data:") and ";base64," in uri:
            import base64
            return base64.b64decode(uri.split(";base64,", 1)[1])
        raise ValueError("image %d is not inside the GLB" % index)

    def _compact(self):
        doc = self.doc
        used_acc = set()
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                used_acc.update(prim.get("attributes", {}).values())
                if "indices" in prim:
                    used_acc.add(prim["indices"])
                for t in prim.get("targets", []) or []:
                    used_acc.update(t.values())
        for skin in doc.get("skins", []):
            if "inverseBindMatrices" in skin:
                used_acc.add(skin["inverseBindMatrices"])
        for anim in doc.get("animations", []):
            for s in anim.get("samplers", []):
                used_acc.update((s["input"], s["output"]))
        acc_map = {old: new for new, old in enumerate(sorted(used_acc))}
        accessors = [doc["accessors"][old] for old in sorted(used_acc)]
        used_views = set()
        for acc in accessors:
            if "bufferView" in acc:
                used_views.add(acc["bufferView"])
            sp = acc.get("sparse")
            if sp:
                used_views.update((sp["indices"]["bufferView"], sp["values"]["bufferView"]))
        for img in doc.get("images", []):
            if "bufferView" in img:
                used_views.add(img["bufferView"])
        view_map = {old: new for new, old in enumerate(sorted(used_views))}
        for acc in accessors:
            if "bufferView" in acc:
                acc["bufferView"] = view_map[acc["bufferView"]]
            sp = acc.get("sparse")
            if sp:
                sp["indices"]["bufferView"] = view_map[sp["indices"]["bufferView"]]
                sp["values"]["bufferView"] = view_map[sp["values"]["bufferView"]]
        for img in doc.get("images", []):
            if "bufferView" in img:
                img["bufferView"] = view_map[img["bufferView"]]
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                prim["attributes"] = {k: acc_map[v] for k, v in prim.get("attributes", {}).items()}
                if "indices" in prim:
                    prim["indices"] = acc_map[prim["indices"]]
                if prim.get("targets"):
                    prim["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in prim["targets"]]
        for skin in doc.get("skins", []):
            if "inverseBindMatrices" in skin:
                skin["inverseBindMatrices"] = acc_map[skin["inverseBindMatrices"]]
        for anim in doc.get("animations", []):
            for s in anim.get("samplers", []):
                s["input"], s["output"] = acc_map[s["input"]], acc_map[s["output"]]
        if accessors:
            doc["accessors"] = accessors
        else:
            doc.pop("accessors", None)
        views = [doc["bufferViews"][old] for old in sorted(used_views)]
        self.views = [self.views[old] for old in sorted(used_views)]
        if views:
            doc["bufferViews"] = views
        else:
            doc.pop("bufferViews", None)

    def save(self, path):
        self._compact()
        out = bytearray()
        for bv, data in zip(self.doc.get("bufferViews", []), self.views):
            out += b"\0" * ((4 - len(out) % 4) % 4)
            bv["byteOffset"] = len(out)
            bv["byteLength"] = len(data)
            bv["buffer"] = 0
            out += data
        if out:
            self.doc["buffers"] = [{"byteLength": len(out)}]
        else:
            self.doc.pop("buffers", None)
        write_glb(path, self.doc, bytes(out))


# ---- the normals -----------------------------------------------------------------------------

def _weld(pos, rel=1e-6):
    """An id per vertex shared by the vertices at the same place (rel: of the extent)."""
    ext = float(np.ptp(pos, axis=0).max()) if len(pos) else 1.0
    key = np.round(pos / max(ext * rel, 1e-12)).astype(np.int64)
    _, wid = np.unique(key, axis=0, return_inverse=True)
    return wid.reshape(-1)


def face_normals(pos, tris):
    """(unit normals (M, 3), twice the areas (M,)) of triangles."""
    p = pos[tris]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], a


def corner_angles(pos, tris):
    p = pos[tris]
    out = np.zeros(tris.shape, np.float64)
    for k in range(3):
        e1 = p[:, (k + 1) % 3] - p[:, k]
        e2 = p[:, (k + 2) % 3] - p[:, k]
        d = (e1 * e2).sum(1) / np.maximum(np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-30)
        out[:, k] = np.arccos(np.clip(d, -1.0, 1.0))
    return out


def _corner_pairs(pos, tris):
    """(a, b, face normals, corner angles): every pair of triangle corners (indices into the
    flattened (M * 3) corners, a gathering from b, both ways and each with itself) at one place
    (vertices at the same place count as one)."""
    m = len(tris)
    fn, _ = face_normals(pos, tris)
    ang = corner_angles(pos, tris).ravel()
    wid = _weld(pos)[tris].ravel()
    order = np.argsort(wid, kind="stable")
    w_sorted = wid[order]
    starts = np.flatnonzero(np.r_[True, w_sorted[1:] != w_sorted[:-1]])
    sizes = np.diff(np.r_[starts, len(w_sorted)])
    g_start = np.repeat(starts, sizes)  # the group start of each sorted corner
    g_size = np.repeat(sizes, sizes)
    total = int(g_size.sum())
    first = np.repeat(np.arange(len(order)), g_size)
    k = np.arange(total) - np.repeat(np.cumsum(g_size) - g_size, g_size)
    second = g_start[first] + k
    return order[first], order[second], fn, ang, (order, starts, m)


def creased_normals(pos, tris, crease_deg=CREASE_DEG):
    """A unit normal per triangle corner ((M, 3, 3)): the corner-angle weighted mean of the
    normals of the triangles around the corner's vertex (vertices at the same place are one)
    whose normal is within crease_deg of the corner's own triangle's."""
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    m = len(tris)
    if m == 0:
        return np.zeros((0, 3, 3))
    a, b, fn, ang, (order, starts, _) = _corner_pairs(pos, tris)
    face = np.repeat(np.arange(m), 3)
    cos_crease = np.cos(np.radians(crease_deg))
    dots = (fn[face[a]] * fn[face[b]]).sum(1)
    keep = dots >= cos_crease - 1e-9
    a, b = a[keep], b[keep]
    w = ang[b]
    acc = np.zeros((3 * m, 3))
    for c in range(3):
        acc[:, c] = np.bincount(a, weights=fn[face[b], c] * w, minlength=3 * m)
    norm = np.linalg.norm(acc, axis=1)
    bad = norm < 1e-12
    if bad.any():  # a degenerate triangle: the mean of every triangle around its vertex
        allw = np.zeros((len(starts), 3))
        grp = np.searchsorted(starts, np.arange(len(order)), side="right") - 1
        for c in range(3):
            allw[:, c] = np.bincount(grp, weights=fn[face[order], c] * ang[order], minlength=len(starts))
        inv = np.empty(len(order), np.int64)
        inv[order] = grp
        acc[bad] = allw[inv[bad]]
        norm = np.linalg.norm(acc, axis=1)
        still = norm < 1e-12
        acc[still] = [0.0, 0.0, 1.0]
        norm[still] = 1.0
    return (acc / norm[:, None]).reshape(m, 3, 3)


def flat_share(pos, tris, normals):
    """How faceted a mesh's shading is: among the pairs of triangle corners at one place whose
    triangles meet at 2 to 30 degrees (a curved surface, which smooth shading blends), the share
    whose normals differ by more than FLAT_DEG (split: each triangle shaded on its own). A
    smooth mesh gives 0, a mesh shaded flat 1; flat faces and hard edges count for neither."""
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    if not len(tris):
        return 0.0
    a, b, fn, _, _ = _corner_pairs(pos, tris)
    face = np.repeat(np.arange(len(tris)), 3)
    sel = a < b
    a, b = a[sel], b[sel]
    dots = (fn[face[a]] * fn[face[b]]).sum(1)
    curved = (dots <= np.cos(np.radians(2.0))) & (dots >= np.cos(np.radians(30.0)))
    if not curved.any():
        return 0.0
    nn = np.asarray(normals, np.float64)[tris.reshape(-1)]
    nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-30)
    a, b = a[curved], b[curved]
    split = (nn[a] * nn[b]).sum(1) < np.cos(np.radians(FLAT_DEG))
    return float(split.mean())


def _triangles(glb, prim):
    """The triangle list (M, 3) of a primitive (mode 4 or none), or None for other modes."""
    mode = prim.get("mode", 4)
    n = glb.doc["accessors"][prim["attributes"]["POSITION"]]["count"]
    if mode != 4:
        return None
    if "indices" in prim:
        idx = glb.accessor(prim["indices"]).reshape(-1).astype(np.int64)
    else:
        idx = np.arange(n, dtype=np.int64)
    return idx[: len(idx) - len(idx) % 3].reshape(-1, 3)


def normals_state(path_or_glb):
    """[{mesh, primitive, triangles, normals: missing | present, flat_share}] of a GLB."""
    glb = path_or_glb if isinstance(path_or_glb, Glb) else Glb(path_or_glb)
    out = []
    for mi, mesh in enumerate(glb.doc.get("meshes", [])):
        for pi, prim in enumerate(mesh.get("primitives", [])):
            tris = _triangles(glb, prim)
            if tris is None:
                continue
            pos = glb.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
            rec = {"mesh": mesh.get("name", "mesh %d" % mi), "primitive": pi, "triangles": int(len(tris))}
            if "NORMAL" in prim["attributes"]:
                nrm = glb.accessor(prim["attributes"]["NORMAL"]).astype(np.float64)
                rec.update(normals="present", flat_share=round(flat_share(pos, tris, nrm), 4))
            else:
                rec.update(normals="missing", flat_share=1.0)
            out.append(rec)
    return out


def _smooth_primitive(glb, prim, crease_deg):
    tris = _triangles(glb, prim)
    attrs = prim["attributes"]
    pos = glb.accessor(attrs["POSITION"]).astype(np.float64)
    cn = creased_normals(pos, tris, crease_deg).reshape(-1, 3)
    corner_vertex = tris.reshape(-1)
    # One new vertex per (old vertex, normal); vertices equal in every attribute merged after.
    qn = np.round(cn * 2048.0).astype(np.int64)
    key = np.concatenate([corner_vertex[:, None], qn], axis=1)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    src = corner_vertex[first]
    normals = cn[first].astype(np.float32)
    names = [k for k in sorted(attrs) if k not in ("NORMAL", "TANGENT")]
    columns = {k: glb.accessor(attrs[k])[src] for k in names}
    targets = []
    for t in prim.get("targets", []) or []:
        targets.append({k: glb.accessor(v)[src] for k, v in t.items() if k != "TANGENT"})
    # Merge vertices equal in every attribute (a mesh exported flat has one vertex per corner).
    rows = [np.ascontiguousarray(columns[k]).view(np.uint8).reshape(len(src), -1) for k in names]
    rows.append(np.ascontiguousarray(normals).view(np.uint8).reshape(len(src), -1))
    for t in targets:
        rows += [np.ascontiguousarray(v).view(np.uint8).reshape(len(src), -1) for v in t.values()]
    blob = np.ascontiguousarray(np.concatenate(rows, axis=1))
    _, keep, again = np.unique(blob.view(np.dtype((np.void, blob.shape[1]))).reshape(-1), return_index=True,
                               return_inverse=True)
    again = again.reshape(-1)
    new_index = again[inv].reshape(-1, 3)
    n = len(keep)
    new_attrs = {}
    for k in names:
        new_attrs[k] = glb.add_accessor(columns[k][keep], like=attrs[k], target=34962, minmax=(k == "POSITION"))
    new_attrs["NORMAL"] = glb.add_accessor(normals[keep], kind="VEC3", target=34962)
    prim["attributes"] = new_attrs
    if targets:
        new_targets = []
        for t, old in zip(targets, prim["targets"]):
            new_targets.append({k: glb.add_accessor(v[keep], like=old[k], target=34962, minmax=(k == "POSITION"))
                                for k, v in t.items()})
        prim["targets"] = new_targets
    dtype = np.uint16 if n <= 65535 else np.uint32
    prim["indices"] = glb.add_accessor(new_index.astype(dtype).reshape(-1, 1), kind="SCALAR", target=34963)
    prim["mode"] = 4
    return {"vertices_before": int(glb.doc["accessors"][attrs["POSITION"]]["count"]), "vertices_after": int(n)}


def smooth_glb(src, dst, crease_deg=CREASE_DEG, mode="auto"):
    """Write a copy of the GLB src to dst with creased smooth normals. mode: "auto" rewrites a
    triangle primitive without normals or with at least half of its triangles shaded flat,
    "always" every triangle primitive. Returns the report: per primitive the state before,
    whether it was rewritten and its vertex counts, and the state after."""
    glb = Glb(src)
    before = normals_state(glb)
    report = {"crease_deg": crease_deg, "mode": mode, "primitives": []}
    i = 0
    for mesh in glb.doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if _triangles(glb, prim) is None:
                continue
            rec = dict(before[i])
            i += 1
            if mode == "always" or rec["normals"] == "missing" or rec["flat_share"] >= 0.5:
                rec.update(_smooth_primitive(glb, prim, crease_deg), rewritten=True)
            else:
                rec["rewritten"] = False
            report["primitives"].append(rec)
    glb.save(dst)
    after = normals_state(dst)
    for rec, now in zip(report["primitives"], after):
        rec["after"] = {"normals": now["normals"], "flat_share": now["flat_share"]}
    return report


# ---- the normal map --------------------------------------------------------------------------

def box_blur(x, r):
    """A box blur of radius r (edges clamped) of a 2-D array."""
    if r <= 0:
        return x.astype(np.float32)
    x = x.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(x, pad, mode="edge"), axis=axis)
        n = x.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        x = (hi - lo) / (2 * r + 1)
    return x.astype(np.float32)


def relief_normals(rgb, strength=RELIEF_STRENGTH):
    """The painted texture's relief as tangent-space normals (H x W x 3 floats in -1..1, rows
    top first as the picture): the slopes of its luminance high-passed (radii 1 and 12 pixels
    at 2048, scaled with the size), times strength. glTF's normal map convention (+y is the
    texture's v, up the picture)."""
    rgb = np.asarray(rgb, np.float32)[..., :3]
    if rgb.max() > 1.0:
        rgb = rgb / 255.0
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    k = max(lum.shape) / 2048.0
    hgt = box_blur(lum, max(1, int(round(1 * k)))) - box_blur(lum, max(2, int(round(12 * k))))
    gy, gx = np.gradient(hgt[::-1])  # rows bottom first: y up the picture
    n = np.dstack([-gx * strength, -gy * strength, np.ones_like(gx)])[::-1]
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def blend_normals(base, detail):
    """A detail map on a base map (both -1..1 tangent normals): the humanoid exporter's blend."""
    m = np.dstack([base[..., 0] + detail[..., 0], base[..., 1] + detail[..., 1], base[..., 2] * detail[..., 2]])
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12)


def encode_normals(n):
    """-1..1 normals to RGB bytes."""
    return np.clip(np.round((np.asarray(n) * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)


def decode_normals(rgb):
    return np.asarray(rgb, np.float32)[..., :3] / 255.0 * 2.0 - 1.0


def png_bytes(rgb):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb[..., :3]), "RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def base_colour_image(glb, material):
    """The image index of a material's base-colour texture, or None."""
    tex = (glb.doc["materials"][material].get("pbrMetallicRoughness") or {}).get("baseColorTexture")
    if tex is None:
        return None
    return glb.doc["textures"][tex["index"]].get("source")


def set_normal_maps(glb, maps, scale=1.0, replace="ours"):
    """Put normal maps into materials' normalTexture: maps {material index: H x W x 3 uint8}.
    A material whose slot holds a map this module did not write keeps it unless replace is
    "all". Materials that share one base-colour image share one normal image. Returns
    {material index: written | kept}."""
    doc = glb.doc
    textures = doc.setdefault("textures", [])
    done, by_bytes = {}, {}
    for mi, rgb in sorted(maps.items()):
        mat = doc["materials"][mi]
        old = mat.get("normalTexture")
        if old is not None and replace != "all":
            src = textures[old["index"]].get("source")
            name = doc["images"][src].get("name", "") if src is not None else ""
            if not name.startswith(NORMAL_IMAGE):
                done[mi] = "kept"
                continue
        png = png_bytes(rgb)
        if png not in by_bytes:
            ii = glb.add_image(png, "%s_%d" % (NORMAL_IMAGE, mi))
            base = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            tex = {"source": ii}
            if base is not None and "sampler" in textures[base["index"]]:
                tex["sampler"] = textures[base["index"]]["sampler"]
            textures.append(tex)
            by_bytes[png] = len(textures) - 1
        entry = {"index": by_bytes[png]}
        base = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if base is not None and "texCoord" in base:
            entry["texCoord"] = base["texCoord"]
        if abs(scale - 1.0) > 1e-6:
            entry["scale"] = float(scale)
        mat["normalTexture"] = entry
        done[mi] = "written"
    _drop_unused_images(glb)
    return done


def _drop_unused_images(glb):
    """Textures and images no material uses any more (a replaced normal map) removed."""
    doc = glb.doc
    used_tex = set()
    for mat in doc.get("materials", []):
        for key, sub in (("baseColorTexture", "pbrMetallicRoughness"), ("metallicRoughnessTexture", "pbrMetallicRoughness"),
                         ("normalTexture", None), ("occlusionTexture", None), ("emissiveTexture", None)):
            t = (mat.get(sub) or {}).get(key) if sub else mat.get(key)
            if t is not None:
                used_tex.add(t["index"])
        for ext in (mat.get("extensions") or {}).values():
            for v in (ext.values() if isinstance(ext, dict) else []):
                if isinstance(v, dict) and "index" in v:
                    used_tex.add(v["index"])
    textures = doc.get("textures", [])
    if len(used_tex) == len(textures):
        return
    tex_map = {old: new for new, old in enumerate(sorted(used_tex))}

    def remap(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith("Texture") and isinstance(v, dict) and "index" in v:
                    v["index"] = tex_map[v["index"]]
                else:
                    remap(v)
        elif isinstance(obj, list):
            for v in obj:
                remap(v)
    for mat in doc.get("materials", []):
        remap(mat)
    doc["textures"] = [textures[old] for old in sorted(used_tex)]
    used_img = set()
    for t in doc["textures"]:
        if "source" in t:
            used_img.add(t["source"])
        for ext in (t.get("extensions") or {}).values():
            if isinstance(ext, dict) and "source" in ext:
                used_img.add(ext["source"])
    img_map = {old: new for new, old in enumerate(sorted(used_img))}
    for t in doc["textures"]:
        if "source" in t:
            t["source"] = img_map[t["source"]]
        for ext in (t.get("extensions") or {}).values():
            if isinstance(ext, dict) and "source" in ext:
                ext["source"] = img_map[ext["source"]]
    doc["images"] = [doc["images"][old] for old in sorted(used_img)]


def check_glb(path, require_maps=True):
    """What a written GLB holds and its flags: every triangle primitive has normals and is not
    mostly shaded flat; every material with a base colour has a normal map, and it is a
    lossless image (PNG or lossless WebP). Returns {"primitives", "materials", "flags"}."""
    import glb_materials
    glb = Glb(path)
    state = normals_state(glb)
    flags = []
    for rec in state:
        if rec["normals"] == "missing":
            flags.append("mesh %s primitive %d has no normals" % (rec["mesh"], rec["primitive"]))
        elif rec["flat_share"] >= 0.5:
            flags.append("mesh %s primitive %d is shaded flat (%.0f %% of its curved joins split)" % (
                rec["mesh"], rec["primitive"], rec["flat_share"] * 100))
    mats = []
    for mi, mat in enumerate(glb.doc.get("materials", [])):
        rec = {"material": mat.get("name", "material %d" % mi), "base_colour": base_colour_image(glb, mi) is not None}
        nt = mat.get("normalTexture")
        if nt is None:
            rec.update(normal_map=False)
            if require_maps and rec["base_colour"]:
                flags.append("material %s has no normal map" % rec["material"])
        else:
            src = glb.doc["textures"][nt["index"]].get("source")
            info = glb_materials.image_info(glb.image_bytes(src)) if src is not None else {
                "format": None, "size": None, "lossless": False}
            rec.update(normal_map=True, format=info["format"], size=info["size"], lossless=info["lossless"],
                       image=glb.doc["images"][src].get("name") if src is not None else None)
            if not info["lossless"]:
                flags.append("material %s: its normal map is %s, which is lossy" % (rec["material"], info["format"]))
        mats.append(rec)
    return {"primitives": state, "materials": mats, "flags": flags}
