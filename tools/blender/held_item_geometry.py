"""The geometry of a held item, without bpy: the item frame (which way the generated mesh's
front and top point, squared to its principal axes), the class templates of sockets and their
proposal on the mesh, the checks of a socket file and the fit measures that rank variants.
Used by tools/blender/held_item_export.py (in Blender), by the order check
(forge/tool_specs.py) and by the tests.

Axes:
- glTF (the generated mesh as written by the mesh and paint stages): y up. The recipe's
  concept is a strict side view with the item's front to the right. The mesh generators keep
  the picture's up on glTF +y, but they lay the picture's left-right on any of glTF +x, -x, +z
  and -z (measured on the first real night: 75 variants, all four), so the frame is found
  (`orient`): the reference picture's silhouette matched from every side, and for guns the
  grip and barrel cues. A frame given by hand (`front` and `top`) skips the search.
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
AUTO = "auto"
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
# The frame search (`orient`). The silhouette of the mesh seen from the side in a candidate
# frame and the reference picture's (its alpha), each on a G x G grid over its own box, are
# compared by their overlap (intersection over union) times the square root of the ratio of
# their aspects (the smaller over the larger). On the first real night (46 gun variants) the
# frame chosen scored 0.55 to 0.88 and the best frame with another front ranked at least 0.13
# lower, but for one mesh of two crossed bodies.
SIL_GRID = 64
# Each candidate is ranked at its best of these turns about its side axis (degrees), less
# SIL_TILT_COST per degree, so that a mesh that came out tilted is still recognised (the
# squaring then straightens it) while a turned-round one is not taken for a tilted one (the
# night's meshes all came out straight: unpenalised, a reversed device scored 0.75 tilted 24
# degrees against 0.69 the right way).
SIL_TILTS = (-24.0, -16.0, -8.0, 0.0, 8.0, 16.0, 24.0)
SIL_TILT_COST = 0.004
# Below this the mesh matches its reference from no side: flagged (too broken to orient).
SIL_MIN = 0.5
# A frame's rank gains SIL_PRIOR times the cosine between its top and glTF +y: the generators
# keep the picture's up there (every one of the night's 46 gun variants, whose up is not in
# doubt, came out so), while a cylinder or a box rolled a quarter turn about its length can
# match its picture a little better (by up to 0.021 on the night's magazines and canisters,
# whose levers and windows then faced sideways).
SIL_PRIOR = 0.03
# With a reference picture the squaring's length turn is the silhouette's (`silhouette_tilt`):
# the principal axes of a gun are not its bore's (8.6 to 11.5 degrees off on three of the
# night's guns, whose silhouettes matched their pictures best untilted: squaring them by their
# principal axes dropped their scores from 0.72 to 0.46, 0.86 to 0.68 and 0.80 to 0.78). A
# turn is made from SIL_TILT_MIN degrees when it raises the score by SIL_TILT_GAIN.
SIL_TILT_MIN = 3.0
SIL_TILT_GAIN = 0.01
# Gun cues, in the frame's item axes (x the length, z up), over GUN_STATIONS stations: `rear`
# (0.5 minus the centre of the profile's height along x: the grip, the magazine and the stock
# make the back half taller) and `ends` (the back end's height minus the front end's, over
# the tallest station: the barrel is the thin end) make the front score
# rear / GUN_REAR + ends / GUN_ENDS; `up` (how much more the bottom edge of the profile varies
# than its top edge, over the tallest station: the grip and the magazine hang below). A cue
# counts when it reaches 1 (front) or GUN_UP (up). Measured on the night's 46 gun variants in
# the frame their silhouette chose: rear 0.021 to 0.155, ends -0.04 to 0.81 (front 0.70 to
# 6.3), up 0.019 to 0.25, never against it.
GUN_STATIONS = 24
GUN_REAR, GUN_ENDS, GUN_UP = 0.05, 0.25, 0.04
# Without a reference picture the long axis of a gun or a long item is taken as its length
# when it is within this of a glTF axis (that axis; a pistol's grip pulls its principal axis
# about 31 degrees off its slide), else the principal axis itself.
SNAP_AXIS_DEG = 35.0
# A long item (a template with "square") whose two largest spreads are this close has no one
# long axis: flagged (a mesh with two crossed bodies came out on the night).
LONG_MIN_RATIO = 1.3
# Section sockets (`propose`): the heights of a station's band points are split where they
# leave a gap of SECTION_GAP of the item's height (the run nearest the socket's height is
# used), and a centre that is not on the mesh moves to the nearest station within
# SECTION_REACH of the length (steps of SECTION_STEP) whose centre is. On the night a
# launcher's pistol grip sat 0.1 to 0.2 of the length behind its template's station, and the
# centre of a band holding the bottom of a tube and the top of a grip fell in the air
# between them. A point is enclosed when the surface is above, below and on both sides of
# it within ENCLOSED_SHARE of the item's largest extent (or the tolerance, when larger).
SECTION_GAP = 0.06
SECTION_REACH = 0.2
SECTION_STEP = 0.01
SECTION_RECENTRE = 4
ENCLOSED_SHARE = 0.02
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
# principal axis is its length), so its frame is squared to its principal axes. "shape": how
# its frame is found (`orient`): "gun" (a grip and a magazine under the back half, the barrel
# the thin end: those cues check the reference's silhouette, or decide without one), "long"
# (its long axis is its length) or "compact" (round or boxy: the reference's silhouette and
# up, never a frame flag of its own). Each socket:
# "at" [u, v, w] (normalised item coordinates), "snap" (section: the centre of the item's
# cross-section at station u, among the surface points whose height w is inside "band";
# surface: the closest surface point to "at"; none: "at" itself), "required" (a variant
# without it is flagged).
_RIFLE = {"square": True, "shape": "gun", "sockets": {
    "butt": _s((0.0, 0.5, 0.55), band=(0.25, 0.85), required=True),
    "grip": _s((0.30, 0.5, 0.2), band=(0.0, 0.45), required=True),
    "guard": _s((0.62, 0.5, 0.45), band=(0.2, 0.65), required=True),
    "muzzle": _s((1.0, 0.5, 0.65), band=(0.0, 1.0), required=True),
    "top": _s((0.45, 0.5, 1.0), snap="surface"),
}}
TEMPLATES = {
    "pistol": {"square": False, "shape": "gun", "sockets": {
        "grip": _s((0.22, 0.5, 0.28), band=(0.0, 0.55), required=True),
        "guard": _s((0.3, 0.5, 0.22), band=(0.0, 0.55)),
        "muzzle": _s((1.0, 0.5, 0.8), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "rifle": _RIFLE,
    "carbine": _RIFLE,
    "heavy": {"square": True, "shape": "gun", "sockets": {
        "butt": _s((0.0, 0.5, 0.5), band=(0.2, 0.9)),
        "grip": _s((0.34, 0.5, 0.2), band=(0.0, 0.45), required=True),
        "guard": _s((0.58, 0.5, 0.35), band=(0.0, 0.6), required=True),
        "muzzle": _s((1.0, 0.5, 0.6), band=(0.0, 1.0), required=True),
        "top": _s((0.45, 0.5, 1.0), snap="surface"),
    }},
    "launcher": {"square": True, "shape": "gun", "sockets": {
        "shoulder": _s((0.42, 0.5, 1.0), snap="surface", required=True),
        "butt": _s((0.0, 0.5, 0.6), band=(0.3, 1.0)),
        "grip": _s((0.3, 0.5, 0.15), band=(0.0, 0.4), required=True),
        "guard": _s((0.62, 0.5, 0.2), band=(0.0, 0.45)),
        "muzzle": _s((1.0, 0.5, 0.7), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "device": {"square": False, "shape": "compact", "sockets": {
        "grip": _s((0.35, 0.5, 0.5), band=(0.0, 1.0), required=True),
        "muzzle": _s((1.0, 0.5, 0.5), band=(0.0, 1.0)),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "thrown": {"square": False, "shape": "compact", "sockets": {
        "grip": _s((0.5, 0.5, 0.5), band=(0.0, 1.0), required=True),
        "top": _s((0.5, 0.5, 1.0), snap="surface"),
    }},
    "melee": {"square": True, "shape": "long", "sockets": {
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
    return {"class": item.get("class"), "template": template, "square": bool(base["square"]),
            "shape": base.get("shape", "compact"), "sockets": sockets}


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


def rotation_about_y(deg):
    """The turn of deg degrees about item y that lifts the front (+x) towards the top (+z)."""
    a = math.radians(deg)
    return np.array([[math.cos(a), 0.0, -math.sin(a)], [0.0, 1.0, 0.0], [math.sin(a), 0.0, math.cos(a)]])


def silhouette_tilt(points_item, reference, limit=SQUARE_MAX_DEG, step=1.0):
    """(tilt, gain): the turn about item y (degrees, front up) that best matches the side
    silhouette of points in item axes to the reference picture's, within -limit to limit, and
    how much it raises the score."""
    base = silhouette_score(points_item, reference)[0]
    best, tilt = base, 0.0
    for d in np.arange(-limit, limit + 0.5 * step, step):
        if abs(d) < 1e-9:
            continue
        sc = silhouette_score(points_item @ rotation_about_y(d).T, reference)[0]
        if sc > best + 1e-9:
            best, tilt = sc, float(d)
    return round(tilt, 3), round(best - base, 4)


def square(points, enabled=True, flag=True, reference=None):
    """(rotation, record) that squares an item already in item axes: its length onto +x, then
    its second principal axis onto +z (a roll about x) when that is within SQUARE_MIN_DEG to
    SQUARE_MAX_DEG of it. The length: with a reference picture's silhouette, the turn about
    item y that matches it best (SIL_TILT_MIN to SQUARE_MAX_DEG, raising the score by
    SIL_TILT_GAIN at least) and no roll (the frame search already put the mesh's own axes on
    the item's; a side view does not show a roll); without one, the longest principal axis
    when within SQUARE_MIN_DEG to SQUARE_MAX_DEG and the item is elongated, then the roll.
    record: turned_deg (the whole
    turn), length_deg (the longest axis from +x before), tilt_deg and tilt_gain (with a
    reference), roll_deg, ratio (longest spread / second) and flag (with `flag`: a text when an
    elongated item's longest axis is too far from +x)."""
    rec = {"turned_deg": 0.0, "length_deg": None, "roll_deg": 0.0, "ratio": None, "squared": False, "flag": None,
           "length_by": "reference" if reference is not None else "principal axes"}
    if len(points) < 4:
        return np.eye(3), rec
    spreads, axes = principal(points)
    ratio = spreads[0] / max(spreads[1], 1e-12)
    e1 = axes[:, 0] * (1.0 if axes[0, 0] >= 0 else -1.0)
    length_deg = _angle(e1, [1.0, 0.0, 0.0])
    rec.update(ratio=round(float(ratio), 4), length_deg=round(length_deg, 3))
    r = np.eye(3)
    if flag and ratio >= FLAG_RATIO and length_deg > SQUARE_MAX_DEG:
        rec["flag"] = ("frame: the longest axis is %.0f degrees from the front axis (not squared): the concept may "
                       "not be a side view, or the mesh came out turned" % length_deg)
    if not enabled:
        return r, rec
    if reference is not None:
        tilt, gain = silhouette_tilt(points, reference)
        rec.update(tilt_deg=tilt, tilt_gain=gain)
        if abs(tilt) >= SIL_TILT_MIN and gain >= SIL_TILT_GAIN:
            r = rotation_about_y(tilt)
    elif ratio >= SQUARE_RATIO and SQUARE_MIN_DEG <= length_deg <= SQUARE_MAX_DEG:
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
    if reference is None and s2 / max(s3, 1e-12) >= ROLL_RATIO and SQUARE_MIN_DEG <= abs(roll) <= SQUARE_MAX_DEG:
        a = math.radians(roll)
        r = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a)], [0.0, math.sin(a), math.cos(a)]]) @ r
        rec["roll_deg"] = round(roll, 3)
    turned = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))))
    rec.update(turned_deg=round(turned, 3), squared=turned > 1e-6)
    return r, rec


# ---- finding the frame -----------------------------------------------------------------------------

def frame_matrix(front, top):
    """R (3x3, rows: item x, y, z in glTF) of a front and a top given as vectors; the top is
    made square to the front."""
    f = np.asarray(front, float)
    f = f / np.linalg.norm(f)
    t = np.asarray(top, float)
    t = t - f * float(np.dot(t, f))
    t = t / np.linalg.norm(t)
    return np.array([f, np.cross(t, f), t])


def _axis_name(v):
    for k, a in AXES.items():
        if float(np.dot(v, a)) > 1.0 - 1e-6:
            return k
    return None


def candidate_frames(points):
    """The frames the search tries: the 24 with the front and the top on glTF axes and, when
    the item is elongated (FLAG_RATIO) and its longest principal axis is further than
    SQUARE_MAX_DEG from every glTF axis (a mesh that came out turned in any direction), the 8
    with the front on that axis (either way) and the top on the second or the third (either
    way). The generators write axis-aligned meshes; a smaller turn of a long item is the
    squaring's. [(R, front name or None, top name or None)]."""
    out = []
    for f in AXES:
        for t in AXES:
            if abs(float(np.dot(AXES[f], AXES[t]))) < 0.5:
                out.append((base_rotation(f, t), f, t))
    spreads, axes = principal(points)
    if spreads[0] < FLAG_RATIO * spreads[1] or min(_angle(axes[:, 0], a) for a in AXES.values()) <= SQUARE_MAX_DEG:
        return out
    for sf in (1.0, -1.0):
        for k in (1, 2):
            for st in (1.0, -1.0):
                r = frame_matrix(sf * axes[:, 0], st * axes[:, k])
                if not any(np.allclose(r, c[0], atol=1e-6) for c in out):
                    out.append((r, _axis_name(r[0]), _axis_name(r[2])))
    return out


def _grid(u, w, g):
    """A G x G occupancy grid (row 0 the top) of points over their own box, closed by one cell,
    and the box's width over its height."""
    du, dw = max(float(np.ptp(u)), 1e-12), max(float(np.ptp(w)), 1e-12)
    iu = np.clip(((u - u.min()) / du * g).astype(int), 0, g - 1)
    iw = np.clip(((w - w.min()) / dw * g).astype(int), 0, g - 1)
    m = np.zeros((g, g), bool)
    m[g - 1 - iw, iu] = True
    d = m.copy()
    d[1:] |= m[:-1]
    d[:-1] |= m[1:]
    d[:, 1:] |= m[:, :-1]
    d[:, :-1] |= m[:, 1:]
    return d, du / dw


def reference_silhouette(alpha, g=SIL_GRID):
    """(grid, aspect) of a reference picture's alpha (2-D, row 0 the top; True, or above 0.5,
    is the item): its box resampled to G x G, and its width over its height. None when the
    picture shows no item."""
    a = np.asarray(alpha)
    a = a if a.dtype == bool else a > 0.5
    ys, xs = np.nonzero(a)
    if len(ys) < 16:
        return None
    crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ry = ((np.arange(g) + 0.5) / g * crop.shape[0]).astype(int)
    rx = ((np.arange(g) + 0.5) / g * crop.shape[1]).astype(int)
    return crop[ry][:, rx], crop.shape[1] / crop.shape[0]


def silhouette_score(points_item, reference, g=SIL_GRID):
    """(score, overlap, aspect) of points in item axes seen from the side (x to the right, z
    up) against a reference_silhouette: the overlap times the square root of the aspects'
    ratio."""
    grid, asp = _grid(points_item[:, 0], points_item[:, 2], g)
    ref, ref_asp = reference
    iou = float((grid & ref).sum()) / max(float((grid | ref).sum()), 1.0)
    match = min(asp, ref_asp) / max(asp, ref_asp)
    return iou * math.sqrt(match), iou, asp


def gun_cues(points_item, n=GUN_STATIONS):
    """{"rear", "ends", "front", "up"} of points in item axes (x the length, z up), see GUN_*:
    front > 0 says the front is +x, up > 0 that the top is +z."""
    x, z = points_item[:, 0], points_item[:, 2]
    u = (x - x.min()) / max(float(np.ptp(x)), 1e-12)
    idx = np.minimum((u * n).astype(int), n - 1)
    lo, hi = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        m = idx == i
        if m.any():
            lo[i], hi[i] = z[m].min(), z[m].max()
    h = hi - lo
    ok = ~np.isnan(h)
    tallest = float(np.nanmax(h)) if ok.any() else 0.0
    if ok.sum() < n // 2 or tallest <= 0:
        return {"rear": 0.0, "ends": 0.0, "front": 0.0, "up": 0.0}
    uc = (np.arange(n) + 0.5) / n
    rear = 0.5 - float((uc[ok] * h[ok]).sum() / h[ok].sum())
    k = max(2, n // 7)
    ends = (float(np.nanmean(h[:k])) - float(np.nanmean(h[-k:]))) / tallest
    mid = slice(k, n - k)
    up = (float(np.nanstd(lo[mid])) - float(np.nanstd(hi[mid]))) / tallest
    return {"rear": round(rear, 4), "ends": round(ends, 4), "front": round(rear / GUN_REAR + ends / GUN_ENDS, 4),
            "up": round(up, 4)}


TURN_ABOUT_Z = np.diag([-1.0, -1.0, 1.0])  # the front reversed, the top kept
TURN_ABOUT_X = np.diag([1.0, -1.0, -1.0])  # the top reversed, the front kept


def orient(points, template, reference=None):
    """(R, record): the frame of a generated mesh (item coordinates = R @ glTF), found from its
    surface points (glTF axes) and the reference picture's silhouette (reference_silhouette, or
    None). record: method, front and top (glTF vectors, and their axis names when on an axis),
    with a reference the score, overlap and aspects and the best other front's score and the
    margin, a gun's cues, the spreads, length_deg (the longest principal axis from the front)
    and flags (texts: the cues disagree, a mesh too broken to orient).

    With a reference: the candidate frame whose side silhouette matches best (its rank raised
    by SIL_PRIOR towards a top on glTF +y, lowered by SIL_TILT_COST for a tilt); a gun's cues,
    where they are clear, must agree with it. Without one: a gun or a long item has its long axis turned to +x with the
    generators' up (glTF +y) kept as near as it can be, then a gun's cues pick its top and its
    front (unclear: a flag); a compact item keeps the default frame (front glTF +x, top +y)."""
    shape = template.get("shape", "compact")
    spreads, axes = principal(points)
    e1 = axes[:, 0]
    ratio = float(spreads[0] / max(spreads[1], 1e-12))
    rec = {"method": None, "shape": shape, "spreads": [round(float(x), 6) for x in spreads],
           "ratio": round(ratio, 4), "flags": []}
    if template.get("square") and ratio < LONG_MIN_RATIO:
        rec["flags"].append("frame: a long item with no one long axis (its two largest spreads %.2f to 1): the "
                            "mesh may be broken" % (1.0 / ratio))
    yup = np.array([0.0, 1.0, 0.0])
    if reference is not None:
        rec["method"] = "reference"
        scored = []
        for r, fname, tname in candidate_frames(points):
            p = points @ r.T
            rank, sc, iou, asp = max((s[0] - SIL_TILT_COST * abs(d),) + tuple(s) for d in SIL_TILTS
                                     for s in [silhouette_score(p @ rotation_about_y(d).T, reference)])
            scored.append((rank + SIL_PRIOR * float(r[2] @ yup), sc, iou, asp, r, fname, tname))
        scored.sort(key=lambda c: -c[0])
        best = scored[0]
        r = best[4]
        other = next((c for c in scored[1:] if float(c[4][0] @ r[0]) < 0.7), None)
        rec.update(score=round(best[1], 4), overlap=round(best[2], 4), aspect=round(best[3], 4),
                   reference_aspect=round(float(reference[1]), 4),
                   other_front=None if other is None else {
                       "score": round(other[1], 4), "front": [round(float(x), 4) for x in other[4][0]],
                       "front_axis": other[5]},
                   margin=None if other is None else round(best[0] - other[0], 4))
        if best[1] < SIL_MIN:
            rec["flags"].append("frame: the mesh matches its reference picture from no side (best %.2f, below "
                                "%.2f): the mesh may be broken" % (best[1], SIL_MIN))
        if shape == "gun":
            cues = gun_cues(points @ r.T)
            rec["cues"] = cues
            if cues["front"] <= -1.0:
                rec["flags"].append("frame: the cues disagree: the reference picture puts the front where the grip "
                                    "and the barrel put the back (front cue %.2f)" % cues["front"])
            if cues["up"] <= -GUN_UP:
                rec["flags"].append("frame: the cues disagree: the reference picture puts the top where the grip "
                                    "hangs (up cue %.2f)" % cues["up"])
    elif shape in ("gun", "long"):
        rec["method"] = "shape"
        near = max(AXES, key=lambda k: float(np.dot(AXES[k], e1)))
        length = np.array(AXES[near]) if _angle(e1, AXES[near]) <= SNAP_AXIS_DEG else e1
        if float(length @ [1.0, 0.0, 0.0]) - 0.5 * float(length @ [0.0, 0.0, 1.0]) < 0:
            length = -length  # the default front's side (+x, then -z)
        up = yup - length * float(yup @ length)
        if np.linalg.norm(up) < 0.5:  # the long axis is the generators' up: the second axis is the top
            up = axes[:, 1] - length * float(axes[:, 1] @ length)
            if float(up @ [1.0, 0.0, 0.0]) < 0:
                up = -up
        r = frame_matrix(length, up)
        if shape == "gun":
            cues = gun_cues(points @ r.T)
            if cues["up"] <= -GUN_UP:
                r = TURN_ABOUT_X @ r
                cues = gun_cues(points @ r.T)
            if cues["front"] <= -1.0:
                r = TURN_ABOUT_Z @ r
            elif cues["front"] < 1.0:
                rec["flags"].append("frame: the front cannot be told: no reference picture, and the grip and "
                                    "barrel cues are unclear (front cue %.2f)" % cues["front"])
            rec["cues"] = gun_cues(points @ r.T)
    else:
        rec["method"] = "default"
        r = base_rotation(DEFAULT_FRONT, DEFAULT_TOP)
    off = math.degrees(math.acos(min(1.0, abs(float(r[0] @ e1)))))
    rec.update(front=[round(float(x), 6) for x in r[0]], top=[round(float(x), 6) for x in r[2]],
               front_axis=_axis_name(r[0]), top_axis=_axis_name(r[2]), length_deg=round(off, 3))
    if template.get("square") and ratio >= FLAG_RATIO and off > SQUARE_MAX_DEG:
        rec["flags"].append("frame: the longest axis is %.0f degrees from the front found: the mesh is not the "
                            "side view's object" % off)
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


def section_centre(points, lo, ext, u, band, slab=0.03, least=6, near_w=None, centre_x=False):
    """The centre of the cross-section at station u (0 back, 1 front) among the points whose
    height (0 bottom, 1 top) is inside band: the middle of their extent across and up, at the
    station's x (brought onto their extent along x when the station's slab only grazes them;
    with centre_x, the middle of that extent). With near_w (a height, 0 to 1), the points are first split where their
    heights leave a gap of SECTION_GAP (a grip under a body, a magazine beside a grip) and
    only the run nearest near_w is used. (centre, number of points) or (None, 0) without
    points there."""
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
    if near_w is not None:
        w = W[m]
        ws = np.sort(w)
        runs = np.split(ws, np.nonzero(np.diff(ws) > SECTION_GAP)[0] + 1)
        run = min(runs, key=lambda r: 0.0 if r[0] <= near_w <= r[-1] else min(abs(r[0] - near_w),
                                                                               abs(r[-1] - near_w)))
        keep = (w >= run[0]) & (w <= run[-1])
        if keep.sum() >= 3:
            sel = sel[keep]
    if centre_x:
        x = 0.5 * (float(sel[:, 0].min()) + float(sel[:, 0].max()))
    else:
        x = min(max(lo[0] + u * ext[0], float(sel[:, 0].min())), float(sel[:, 0].max()))
    y = 0.5 * (sel[:, 1].min() + sel[:, 1].max())
    z = 0.5 * (sel[:, 2].min() + sel[:, 2].max())
    return np.array([x, y, z]), int(len(sel))


def enclosed(points, p, r):
    """Whether p is inside the surface points' shell at its station: within r along x, there
    are points above and below it (within r across) and on both sides of it (within r up)."""
    q = points[np.abs(points[:, 0] - p[0]) <= r]
    if len(q) < 4:
        return False
    col = q[np.abs(q[:, 1] - p[1]) <= r]
    row = q[np.abs(q[:, 2] - p[2]) <= r]
    return bool((col[:, 2] > p[2]).any() and (col[:, 2] < p[2]).any() and (row[:, 1] > p[1]).any()
                and (row[:, 1] < p[1]).any())


def holds(points, p, tolerance, r):
    """Whether a proposed socket is on the mesh: within tolerance of the surface, or enclosed
    (within r)."""
    _, d = nearest(points, p)
    return d <= tolerance or enclosed(points, p, r)


def nearest(points, p):
    d = np.linalg.norm(points - p, axis=1)
    i = int(np.argmin(d))
    return points[i].copy(), float(d[i])


def section_stations(u, reach=None, step=None):
    """The stations a section socket tries, its own first: u, then u -/+ step, ... up to reach,
    inside 0 to 1."""
    reach = SECTION_REACH if reach is None else reach
    step = SECTION_STEP if step is None else step
    out = [u]
    k = 1
    while k * step <= reach + 1e-9:
        for x in (u - k * step, u + k * step):
            if -1e-9 <= x <= 1.0 + 1e-9:
                out.append(min(1.0, max(0.0, x)))
        k += 1
    return out


def propose(points, template, tolerance=0.01):
    """{socket: record} of a resolved template on an item's surface points (item axes, at
    real size, before the grip is moved to the origin): "at" (metres, or None when missing),
    "template_at" (the template's position in the box), "snap", "required", "found",
    "shift_m" (how far the snap moved it from the template's position), "points" and, for a
    section, "station" (the station used). A section socket takes the run of its band's
    points nearest its height at its station; when that centre is not on the mesh (within
    tolerance of the surface, or enclosed by it) or the band is empty there, the stations
    around it are tried, nearest first, up to SECTION_REACH of the length; with none on the
    mesh it is missing."""
    lo, hi, ext = box_of(points)
    r = max(tolerance, ENCLOSED_SHARE * float(ext.max()))
    out = {}
    for name, spec in template["sockets"].items():
        target = to_box(spec["at"], lo, ext)
        snap = spec.get("snap", "section")
        rec = {"template_at": [round(float(x), 6) for x in target], "snap": snap,
               "required": bool(spec.get("required")), "found": True, "points": None}
        if snap == "section":
            p = None
            band = spec.get("band", [0.0, 1.0])
            for st in section_stations(spec["at"][0]):
                c, n = section_centre(points, lo, ext, st, band, near_w=spec["at"][2])
                if c is None or not holds(points, c, tolerance, r):
                    continue
                if st != spec["at"][0]:
                    # Found off its station: centred on the piece it found (a grip's middle,
                    # not the face the slab grazed), a few steps at most.
                    for _ in range(SECTION_RECENTRE):
                        u2 = float(np.clip((c[0] - lo[0]) / ext[0], 0.0, 1.0))
                        c2, n2 = section_centre(points, lo, ext, u2, band, near_w=spec["at"][2], centre_x=True)
                        if c2 is None or not holds(points, c2, tolerance, r):
                            break
                        c, n, st = c2, n2, u2
                p = c
                rec.update(points=n, station=round(float(st), 4))
                break
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


def place_item(verts_gltf, tris, template, front=None, top=None, snap=None, rotate_deg=None,
               length=None, height=None, manual_sockets=None, tolerance=0.01, samples=SAMPLES, reference=None):
    """The whole frame and socket proposal of one item. verts_gltf: the mesh's vertices in
    glTF axes. Returns a dictionary:
      matrix (3x3) and translation (3): item coordinates = matrix @ glTF + translation
      scale, frame (front, top, orient record, square record), box ([lo, hi] item space),
      length_m, sockets ({name: [x, y, z]} item space, grip at the origin), proposal (per
      socket records), flags (socket and frame flags), fit, required.
    front and top: the glTF axes the mesh's front and top point along, given by hand; None
    (or "auto") for both: the frame is found (`orient`, with the reference picture's alpha
    `reference`, 2-D, row 0 the top, when given); one given: the other is the default's.
    snap: square to the principal axes (default: the template's "square"); rotate_deg:
    [about x, about y, about z] given by hand instead of the squaring (and the frame given by
    front and top, else the default's); length or height: the real size (the extent along
    item x, or z); manual_sockets: {name: [x, y, z]} in the item space of this frame, which
    win over the proposal (the grip given there becomes the origin)."""
    front = None if front == AUTO else front
    top = None if top == AUTO else top
    verts_gltf = np.asarray(verts_gltf, float)
    pts_g = sample_surface(verts_gltf, tris, samples)
    flags = []
    found = ref = None
    if front is None and top is None and rotate_deg is None:
        ref = reference_silhouette(reference) if reference is not None else None
        r0, found = orient(pts_g, template, ref)
        if reference is not None and ref is None:
            found["flags"].append("frame: the reference picture shows no item (its alpha is empty)")
        flags += found["flags"]
    else:
        r0 = base_rotation(front or DEFAULT_FRONT, top or DEFAULT_TOP)
    v = verts_gltf @ r0.T
    pts = pts_g @ r0.T
    if rotate_deg is not None:
        r1 = rotation_xyz(rotate_deg)
        sq = {"turned_deg": round(_angle_of(r1), 3), "manual": True, "rotate_deg": list(rotate_deg),
              "squared": False, "flag": None}
    else:
        r1, sq = square(pts, template["square"] if snap is None else bool(snap),
                        flag=found is None and template["square"],
                        reference=ref if found is not None else None)
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
    proposal = propose(pts, template, tolerance)
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
            "frame": {"front": front or (DEFAULT_FRONT if found is None else AUTO),
                      "top": top or (DEFAULT_TOP if found is None else AUTO), "orient": found, "square": sq},
            "box": [[round(float(x), 6) for x in lo2], [round(float(x), 6) for x in hi2]],
            "length_m": round(float((hi2 - lo2).max()), 6), "size_m": [round(float(x), 6) for x in hi2 - lo2],
            "sockets": sockets, "proposal": proposal, "flags": flags, "required": required,
            "fit": fit(proposal, float((hi2 - lo2)[0]), sq.get("turned_deg", 0.0), loose_share(v_final, tris))}


def _angle_of(r):
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))))
