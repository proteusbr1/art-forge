"""The geometry of a held item, without bpy: the item frame (which way the generated mesh's
front and top point, squared to its principal axes), the class templates of sockets and their
proposal on the mesh, the checks of a socket file and the fit measures that rank variants.
Used by tools/blender/held_item_export.py (in Blender), by the order check
(forge/tool_specs.py) and by the tests.

Axes:
- glTF (the generated mesh as written by the mesh and paint stages): y up. The recipe's
  concept is a strict side view with the item's front to the right, so by default the front
  points along glTF +x and the top along glTF +y.
- Item space (the output; docs/conventions.md): metres, +x the item's front (a muzzle, a
  blade's tip), +z its top, +y = z x x (the item's left), the grip socket at the origin. In
  an item GLB, item x is glTF +x, item z is glTF +y and item y is glTF -z.
- Normalised item coordinates (the templates): [u, v, w], each in 0 to 1 over the item's box
  in item space: u from the back (0) to the front (1), v from the right side (0) to the left
  (1), w from the bottom (0) to the top (1).
"""

import math
import re

import numpy as np

AXES = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0), "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
        "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}
DEFAULT_FRONT, DEFAULT_TOP = "+x", "+y"
SNAPS = ("section", "surface", "none")
CLASS_RE = re.compile(r"^[a-z][a-z0-9]*$")
SOCKET_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Squaring: the longest principal axis is turned onto the front axis when it is within
# SQUARE_MAX_DEG of it and the item is elongated (ratio of the principal spreads); then the
# second axis onto the top axis, the same way.
SQUARE_MAX_DEG = 30.0
# A turn smaller than this is left alone: the principal axes of an item with a dropped stock,
# a grip or a magazine are not exactly its bore's (about 5 degrees off on the tests'
# synthetic carbine), so a mesh that came out straight is not tilted by them; only a clear
# tilt is corrected.
SQUARE_MIN_DEG = 8.0
SQUARE_RATIO = 1.3
ROLL_RATIO = 1.2
# An elongated item whose longest axis is further than SQUARE_MAX_DEG from the front axis is
# flagged (the concept was not a side view, or the mesh came out turned).
FLAG_RATIO = 1.5
# Reference values of the fit measures (ranking only, never gating): excess = sum of
# value / reference.
FIT_REFERENCE = {"socket_shift": 0.15, "turned_deg": 15.0, "loose_share": 0.05}
SAMPLES = 20000


def _s(at, snap="section", band=None, required=False):
    out = {"at": list(at), "snap": snap, "required": required}
    if band is not None:
        out["band"] = list(band)
    return out


# Class templates: generic hold layouts. "square": the item is long and straight (its longest
# principal axis is its length), so its frame is squared to its principal axes. Each socket:
# "at" [u, v, w] (normalised item coordinates), "snap" (section: the centre of the item's
# cross-section at station u, among the surface points whose height w is inside "band";
# surface: the closest surface point to "at"; none: "at" itself), "required" (a variant
# without it is flagged).
_RIFLE = {"square": True, "sockets": {
    "butt": _s((0.0, 0.5, 0.55), band=(0.25, 0.85), required=True),
    "grip": _s((0.30, 0.5, 0.2), band=(0.0, 0.45), required=True),
    "guard": _s((0.62, 0.5, 0.45), band=(0.2, 0.65), required=True),
    "muzzle": _s((1.0, 0.5, 0.65), band=(0.0, 1.0), required=True),
    "top": _s((0.45, 0.5, 1.0), snap="surface"),
}}
TEMPLATES = {
    "pistol": {"square": False, "sockets": {
        "grip": _s((0.22, 0.5, 0.28), band=(0.0, 0.55), required=True),
        "guard": _s((0.3, 0.5, 0.22), band=(0.0, 0.55)),
        "muzzle": _s((1.0, 0.5, 0.8), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "rifle": _RIFLE,
    "carbine": _RIFLE,
    "heavy": {"square": True, "sockets": {
        "butt": _s((0.0, 0.5, 0.5), band=(0.2, 0.9)),
        "grip": _s((0.34, 0.5, 0.2), band=(0.0, 0.45), required=True),
        "guard": _s((0.58, 0.5, 0.35), band=(0.0, 0.6), required=True),
        "muzzle": _s((1.0, 0.5, 0.6), band=(0.0, 1.0), required=True),
        "top": _s((0.45, 0.5, 1.0), snap="surface"),
    }},
    "launcher": {"square": True, "sockets": {
        "shoulder": _s((0.42, 0.5, 1.0), snap="surface", required=True),
        "butt": _s((0.0, 0.5, 0.6), band=(0.3, 1.0)),
        "grip": _s((0.45, 0.5, 0.15), band=(0.0, 0.4), required=True),
        "guard": _s((0.62, 0.5, 0.2), band=(0.0, 0.45)),
        "muzzle": _s((1.0, 0.5, 0.7), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "device": {"square": False, "sockets": {
        "grip": _s((0.35, 0.5, 0.5), band=(0.0, 1.0), required=True),
        "muzzle": _s((1.0, 0.5, 0.5), band=(0.0, 1.0)),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "thrown": {"square": False, "sockets": {
        "grip": _s((0.5, 0.5, 0.5), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "melee": {"square": True, "sockets": {
        "grip": _s((0.15, 0.5, 0.5), band=(0.0, 1.0), required=True),
        "guard": _s((0.32, 0.5, 0.5), band=(0.0, 1.0)),
        "tip": _s((1.0, 0.5, 0.5), band=(0.0, 1.0), required=True),
    }},
}
ITEM_KEYS = ("class", "template", "sockets", "clips", "note")


# ---- the order's item object ------------------------------------------------------------------

def _unit(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0


def check_socket(name, spec):
    """Problems of one socket of a template or of the order's item.sockets (spec may be None:
    the order removes that socket)."""
    out = []
    if not isinstance(name, str) or not SOCKET_RE.match(name):
        out.append("socket name %r: lower-case letters, digits and _" % (name,))
    if spec is None:
        return out
    if not isinstance(spec, dict):
        return out + ["socket %s: an object {\"at\": [u, v, w], \"snap\", \"band\", \"required\"} or null" % name]
    extra = sorted(k for k in spec if k not in ("at", "snap", "band", "required", "note"))
    if extra:
        out.append("socket %s: unknown keys %s" % (name, extra))
    at = spec.get("at")
    if not (isinstance(at, list) and len(at) == 3 and all(_unit(x) for x in at)):
        out.append("socket %s: at must be [u, v, w], each from 0 to 1 (normalised item coordinates)" % name)
    if spec.get("snap", "section") not in SNAPS:
        out.append("socket %s: snap must be one of %s" % (name, ", ".join(SNAPS)))
    band = spec.get("band")
    if band is not None and not (isinstance(band, list) and len(band) == 2 and all(_unit(x) for x in band)
                                 and band[0] < band[1]):
        out.append("socket %s: band must be [low, high], 0 <= low < high <= 1 (heights w)" % name)
    if "required" in spec and not isinstance(spec["required"], bool):
        out.append("socket %s: required must be true or false" % name)
    return out


def check_item(item):
    """Problems of a held-item piece's "item" object: class (a word: the hold class the
    motion library's holds name, no "_"), template (a known class template; default the
    class), sockets ({name: socket or null}), clips (clip names of the motion library)."""
    if not isinstance(item, dict):
        return ["item must be an object {\"class\": ..., \"template\": ..., \"sockets\": {...}}"]
    out = []
    extra = sorted(k for k in item if k not in ITEM_KEYS)
    if extra:
        out.append("item: unknown keys %s (known: %s)" % (extra, ", ".join(ITEM_KEYS)))
    cls = item.get("class")
    if not isinstance(cls, str) or not CLASS_RE.match(cls):
        out.append("item.class must be a lower-case word of letters and digits (the hold class), not %r" % (cls,))
    template = item.get("template", cls)
    if template not in TEMPLATES:
        out.append("item.template %r is not a class template (templates: %s)%s" % (
            template, ", ".join(sorted(TEMPLATES)), "" if "template" in item else "; give item.template"))
    sockets = item.get("sockets", {})
    if not isinstance(sockets, dict):
        out.append("item.sockets must be an object {name: socket or null}")
    else:
        for name, spec in sockets.items():
            out += ["item." + p for p in check_socket(name, spec)]
    clips = item.get("clips")
    if clips is not None and not (isinstance(clips, list) and clips and all(isinstance(c, str) and c for c in clips)):
        out.append("item.clips must be a list of clip names")
    if not out:
        if "grip" not in resolve(item)["sockets"]:
            out.append("item.sockets: the grip socket (the item's origin) cannot be removed")
    return out


def resolve(item):
    """The template of an item with the order's sockets merged in: {"class", "template",
    "square", "sockets": {name: {"at", "snap", "band", "required"}}}."""
    template = item.get("template", item.get("class"))
    base = TEMPLATES[template]
    sockets = {k: dict(v) for k, v in base["sockets"].items()}
    for name, spec in (item.get("sockets") or {}).items():
        if spec is None:
            sockets.pop(name, None)
        else:
            sockets[name] = dict({"snap": "section", "required": False}, **{k: v for k, v in spec.items()
                                                                             if k != "note"})
    for spec in sockets.values():
        if spec.get("snap", "section") == "section":
            spec.setdefault("band", [0.0, 1.0])
    return {"class": item.get("class"), "template": template, "square": bool(base["square"]), "sockets": sockets}


# ---- the frame -----------------------------------------------------------------------------------

def base_rotation(front=DEFAULT_FRONT, top=DEFAULT_TOP):
    """R (3x3): item coordinates = R @ glTF coordinates, for a mesh whose front points along
    the glTF axis `front` and whose top along `top`."""
    if front not in AXES or top not in AXES:
        raise ValueError("front and top must be one of %s" % ", ".join(AXES))
    f, t = np.array(AXES[front]), np.array(AXES[top])
    if abs(float(f @ t)) > 0.5:
        raise ValueError("front %s and top %s must be different axes" % (front, top))
    return np.array([f, np.cross(t, f), t])


def rotation_between(a, b):
    """The rotation (3x3) that turns unit vector a onto unit vector b (the shortest turn)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(a @ b)
    s = float(np.linalg.norm(v))
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + k + k @ k * ((1.0 - c) / (s * s))


def rotation_xyz(deg):
    """The rotation of [about x, about y, about z] degrees (applied in that order)."""
    rx, ry, rz = (math.radians(float(d)) for d in deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def _angle(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))))))


def principal(points):
    """(spreads, axes) of a point cloud: the square roots of the covariance's eigenvalues, the
    largest first, and their axes (columns)."""
    c = points - points.mean(0)
    vals, vecs = np.linalg.eigh(c.T @ c / max(len(points), 1))
    order = np.argsort(vals)[::-1]
    return np.sqrt(np.maximum(vals[order], 0.0)), vecs[:, order]


def square(points, enabled=True):
    """(rotation, record) that squares an item already in item axes to its principal axes:
    the longest axis onto +x when within SQUARE_MAX_DEG and the item is elongated, then the
    second axis onto +z (a roll about x) the same way. record: turned_deg (the whole turn),
    length_deg (the longest axis from +x before), roll_deg, ratio (longest spread / second)
    and flag (a text when an elongated item's longest axis is too far from +x)."""
    rec = {"turned_deg": 0.0, "length_deg": None, "roll_deg": 0.0, "ratio": None, "squared": False, "flag": None}
    if len(points) < 4:
        return np.eye(3), rec
    spreads, axes = principal(points)
    ratio = spreads[0] / max(spreads[1], 1e-12)
    e1 = axes[:, 0] * (1.0 if axes[0, 0] >= 0 else -1.0)
    length_deg = _angle(e1, [1.0, 0.0, 0.0])
    rec.update(ratio=round(float(ratio), 4), length_deg=round(length_deg, 3))
    r = np.eye(3)
    if ratio >= FLAG_RATIO and length_deg > SQUARE_MAX_DEG:
        rec["flag"] = ("frame: the longest axis is %.0f degrees from the front axis (not squared): the concept may "
                       "not be a side view, or the mesh came out turned" % length_deg)
    if not enabled:
        return r, rec
    if ratio >= SQUARE_RATIO and SQUARE_MIN_DEG <= length_deg <= SQUARE_MAX_DEG:
        r = rotation_between(e1, [1.0, 0.0, 0.0])
    p = points @ r.T
    yz = p[:, 1:] - p[:, 1:].mean(0)
    vals, vecs = np.linalg.eigh(yz.T @ yz / max(len(p), 1))
    order = np.argsort(vals)[::-1]
    s2, s3 = math.sqrt(max(vals[order[0]], 0.0)), math.sqrt(max(vals[order[1]], 0.0))
    e2 = vecs[:, order[0]]
    if e2[1] < 0:
        e2 = -e2
    roll = math.degrees(math.atan2(e2[0], e2[1]))  # the second axis's angle from +z, about +x
    if s2 / max(s3, 1e-12) >= ROLL_RATIO and SQUARE_MIN_DEG <= abs(roll) <= SQUARE_MAX_DEG:
        a = math.radians(roll)
        r = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a)], [0.0, math.sin(a), math.cos(a)]]) @ r
        rec["roll_deg"] = round(roll, 3)
    turned = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))))
    rec.update(turned_deg=round(turned, 3), squared=turned > 1e-6)
    return r, rec


# ---- surface samples ------------------------------------------------------------------------------

def sample_surface(verts, tris, n=SAMPLES, seed=0):
    """Points spread over the surface by area (a fixed seed), with the vertices."""
    verts = np.asarray(verts, float)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    if len(tris) == 0:
        return verts.copy()
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(area.sum())
    if total <= 0:
        return verts.copy()
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tris), size=n, p=area / total)
    r1, r2 = rng.random(n), rng.random(n)
    flip = r1 + r2 > 1.0
    r1[flip], r2[flip] = 1.0 - r1[flip], 1.0 - r2[flip]
    pts = a[pick] + (b[pick] - a[pick]) * r1[:, None] + (c[pick] - a[pick]) * r2[:, None]
    return np.vstack([pts, verts])


def loose_share(verts, tris):
    """The share of the surface (by area) outside the largest connected piece (vertices at
    the same place count as one)."""
    verts = np.asarray(verts, float)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    if len(tris) == 0:
        return 0.0
    size = float(np.ptp(verts, axis=0).max()) or 1.0
    keys = np.round(verts / (1e-6 * size)).astype(np.int64)
    _, ids = np.unique(keys, axis=0, return_inverse=True)
    ids = ids.reshape(-1)
    parent = np.arange(ids.max() + 1)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root
    t = ids[tris]
    for a, b, c in t:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    roots = np.array([find(x) for x in t[:, 0]])
    v = verts
    area = 0.5 * np.linalg.norm(np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]]), axis=1)
    total = float(area.sum()) or 1.0
    sums = {}
    for r, ar in zip(roots, area):
        sums[r] = sums.get(r, 0.0) + float(ar)
    return max(0.0, 1.0 - max(sums.values()) / total)


# ---- sockets -------------------------------------------------------------------------------------

def box_of(points):
    lo, hi = points.min(0), points.max(0)
    return lo, hi, np.maximum(hi - lo, 1e-9)


def to_box(at, lo, ext):
    return lo + np.asarray(at, float) * ext


def section_centre(points, lo, ext, u, band, slab=0.03, least=6):
    """The centre of the cross-section at station u (0 back, 1 front) among the points whose
    height (0 bottom, 1 top) is inside band: the middle of their extent across and up, at
    the station's x. (centre, number of points) or (None, 0) without points there."""
    U = (points[:, 0] - lo[0]) / ext[0]
    W = (points[:, 2] - lo[2]) / ext[2]
    inband = (W >= band[0] - 1e-9) & (W <= band[1] + 1e-9)
    m = None
    for s in (slab, 2 * slab):
        m = inband & (np.abs(U - u) <= s)
        if m.sum() >= least:
            break
    if m is None or m.sum() < 3:
        return None, 0
    sel = points[m]
    y = 0.5 * (sel[:, 1].min() + sel[:, 1].max())
    z = 0.5 * (sel[:, 2].min() + sel[:, 2].max())
    return np.array([lo[0] + u * ext[0], y, z]), int(m.sum())


def nearest(points, p):
    d = np.linalg.norm(points - p, axis=1)
    i = int(np.argmin(d))
    return points[i].copy(), float(d[i])


def propose(points, template):
    """{socket: record} of a resolved template on an item's surface points (item axes, at
    real size, before the grip is moved to the origin): "at" (metres, or None when missing),
    "template_at" (the template's position in the box), "snap", "required", "found",
    "shift_m" (how far the snap moved it from the template's position), "points"."""
    lo, hi, ext = box_of(points)
    out = {}
    for name, spec in template["sockets"].items():
        target = to_box(spec["at"], lo, ext)
        snap = spec.get("snap", "section")
        rec = {"template_at": [round(float(x), 6) for x in target], "snap": snap,
               "required": bool(spec.get("required")), "found": True, "points": None}
        if snap == "section":
            p, n = section_centre(points, lo, ext, spec["at"][0], spec.get("band", [0.0, 1.0]))
            rec["points"] = n
        elif snap == "surface":
            p, _ = nearest(points, target)
        else:
            p = target
        if p is None:
            rec.update(found=False, at=None, shift_m=None)
        else:
            rec.update(at=[float(x) for x in p], shift_m=round(float(np.linalg.norm(p - target)), 6))
        out[name] = rec
    return out


def on_mesh(points, p, tolerance):
    """(on the mesh, distance to the surface in metres): a socket is on the mesh when it is
    within `tolerance` of the surface or inside the extent (across and up, widened by the
    tolerance) of the surface points of its own station."""
    p = np.asarray(p, float)
    _, d = nearest(points, p)
    if d <= tolerance:
        return True, d
    lo, hi, ext = box_of(points)
    slab = max(0.03 * ext[0], tolerance)
    m = np.abs(points[:, 0] - p[0]) <= slab
    if m.sum() < 3:
        return False, d
    sel = points[m]
    inside = all(sel[:, i].min() - tolerance <= p[i] <= sel[:, i].max() + tolerance for i in (1, 2))
    return bool(inside), d


def socket_flags(points, sockets, required, tolerance, manual=False):
    """The flags of a socket set (item axes, metres): a required socket missing, a socket off
    the mesh. sockets: {name: [x, y, z] or None}."""
    flags = []
    for name in sorted(required):
        if sockets.get(name) is None:
            flags.append("socket %s is missing: %s" % (
                name, "not in the manual sockets file" if manual else "no geometry at its station"))
    for name in sorted(sockets):
        p = sockets[name]
        if p is None:
            continue
        ok, d = on_mesh(points, p, tolerance)
        if not ok:
            flags.append("socket %s is off the mesh: %.1f cm from the surface and outside its cross-section" % (
                name, d * 100.0))
    return flags


def fit(sockets, length, turned_deg, loose):
    """The fit measures of a variant (ranking only): socket_shift (the largest move of a
    snapped template socket from its template position, as a share of the length),
    turned_deg (the squaring turn), loose_share and excess (sum of value / FIT_REFERENCE)."""
    shifts = [r["shift_m"] for r in sockets.values() if r.get("found") and r.get("snap") != "none"
              and r.get("shift_m") is not None]
    out = {"socket_shift": round(max(shifts) / max(length, 1e-9), 4) if shifts else 0.0,
           "turned_deg": round(float(turned_deg), 3), "loose_share": round(float(loose), 5)}
    out["excess"] = round(sum(out[k] / FIT_REFERENCE[k] for k in FIT_REFERENCE), 4)
    out["reference"] = dict(FIT_REFERENCE)
    return out


def place_item(verts_gltf, tris, template, front=DEFAULT_FRONT, top=DEFAULT_TOP, snap=None, rotate_deg=None,
               length=None, height=None, manual_sockets=None, tolerance=0.01, samples=SAMPLES):
    """The whole frame and socket proposal of one item. verts_gltf: the mesh's vertices in
    glTF axes. Returns a dictionary:
      matrix (3x3) and translation (3): item coordinates = matrix @ glTF + translation
      scale, frame (front, top, square record), box ([lo, hi] item space), length_m
      sockets ({name: [x, y, z]} item space, grip at the origin), proposal (per socket
      records), flags (socket and frame flags), fit, required.
    snap: square to the principal axes (default: the template's "square"); rotate_deg:
    [about x, about y, about z] given by hand instead of the squaring; length or height:
    the real size (the extent along item x, or z); manual_sockets: {name: [x, y, z]} in the
    item space of this frame, which win over the proposal (the grip given there becomes the
    origin)."""
    r0 = base_rotation(front, top)
    v = np.asarray(verts_gltf, float) @ r0.T
    pts = sample_surface(v, tris, samples)
    flags = []
    if rotate_deg is not None:
        r1 = rotation_xyz(rotate_deg)
        sq = {"turned_deg": round(_angle_of(r1), 3), "manual": True, "rotate_deg": list(rotate_deg),
              "squared": False, "flag": None}
    else:
        r1, sq = square(pts, template["square"] if snap is None else bool(snap))
        if sq.get("flag"):
            flags.append(sq["flag"])
    v1 = v @ r1.T
    pts = pts @ r1.T
    lo, hi, ext = box_of(v1)
    if length:
        s = float(length) / ext[0]
    elif height:
        s = float(height) / ext[2]
    else:
        s = 1.0
    v1, pts = v1 * s, pts * s
    proposal = propose(pts, template)
    required = [k for k, r in proposal.items() if r["required"]]
    if manual_sockets is not None:
        grip_proposed = np.array(proposal["grip"]["at"]) if proposal["grip"]["found"] else box_of(pts)[0]
        given = {k: (None if p is None else np.asarray(p, float) + grip_proposed) for k, p in manual_sockets.items()}
        sockets_pre = given
    else:
        sockets_pre = {k: (None if r["at"] is None else np.asarray(r["at"], float)) for k, r in proposal.items()}
    grip = sockets_pre.get("grip")
    flags += socket_flags(pts, sockets_pre, required if manual_sockets is None else
                          sorted(set(required) | {"grip"}), tolerance, manual=manual_sockets is not None)
    origin = grip if grip is not None else box_of(v1)[0]
    t = -origin
    v_final = v1 + t
    sockets = {k: (None if p is None else [round(float(x), 6) for x in (p + t)]) for k, p in sockets_pre.items()}
    for rec in proposal.values():
        if rec["at"] is not None:
            rec["at"] = [round(float(x), 6) for x in np.asarray(rec["at"]) + t]
        rec["template_at"] = [round(float(x), 6) for x in np.asarray(rec["template_at"]) + t]
    lo2, hi2 = v_final.min(0), v_final.max(0)
    matrix = s * (r1 @ r0)
    return {"matrix": matrix, "translation": t, "scale": s, "rotation": r1 @ r0,
            "frame": {"front": front, "top": top, "square": sq},
            "box": [[round(float(x), 6) for x in lo2], [round(float(x), 6) for x in hi2]],
            "length_m": round(float((hi2 - lo2).max()), 6), "size_m": [round(float(x), 6) for x in hi2 - lo2],
            "sockets": sockets, "proposal": proposal, "flags": flags, "required": required,
            "fit": fit(proposal, float((hi2 - lo2)[0]), sq.get("turned_deg", 0.0), loose_share(v_final, tris))}


def _angle_of(r):
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))))
