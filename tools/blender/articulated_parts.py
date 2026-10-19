"""The parts of an articulated object: the order's part list checked, regions resolved
against the object's geometry, pivots and axes worked out, and the joints written in glTF
axes. No bpy: articulated_export.py (Blender) calls it, and the factory's order check
(forge/tool_specs.py) uses check_parts().

Coordinates
-----------
- Normalised object coordinates (the order): [side, forward, up]. The object is first
  turned so that its front faces glTF +Z (Blender -Y), scaled and stood on the ground with
  its footprint centred on the origin; then side runs from -0.5 (its right) to +0.5 (its
  left), forward from -0.5 (back) to +0.5 (front), up from 0 (ground) to 1 (top), each
  divided by the object's own extent along it.
- Model coordinates (this module, as Blender): metres, z up, front -Y, left +X.
- glTF coordinates (model.json, the GLB): metres, y up, front +Z, left +X:
  glTF (x, y, z) = Blender (x, z, -y).

A part list
-----------
[{"id": "hull"},
 {"id": "turret", "parent": "hull",
  "find": {"box": [[-0.4, -0.35, [0.45, 0.7]], [0.4, 0.4, null]]},
  "joint": {"kind": "turn", "axis": "up", "pivot": "cut", "range": [-180, 180]}}]

The first part without a parent is the root: it keeps whatever the other parts do not take
and has no joint. Every other part names its parent (a part listed before it) and takes the
geometry of its parent that lies inside its region: "box", two corners in normalised
coordinates, or "boxes", a list of them (their union: a sub-assembly spread over several
places). Each coordinate is a number, null (no bound on that side) or [low, high]: the
narrowest section of the parent's geometry between those two values along that axis (the
waist between a turret and its hull). Joints nest through the parents: a barrel that
elevates on a turret that turns on a hull. A thin box takes a thin surface (a door, a ramp,
a hatch, a nacelle at a wing tip): its cut is closed on both sides, so the part becomes a
closed plate and its parent's opening is closed too.

A joint: "kind" turn (rotation within a range of up to 360 degrees), hinge (rotation within
a range), spin (continuous rotation: a rotor; its range, default [-180, 180], only sets the
review's sweep), slide (translation along the axis, range in metres) or fixed (a separate
node that does not move: a sub-assembly's mount; no axis or range); a part without a joint
is fixed. "axis" side, forward or up, with "-" for the opposite way (rotations follow the
right-hand rule), or a vector [side, forward, up]; "pivot" a point [side, forward, up] in
normalised coordinates or one of "cut" (the centre of the part's cut faces, where it meets
its parent), "center", "bottom", "top", "front", "back", "left", "right" (the centre of the
part's box or of one of its faces); "range" [low, high] (degrees, or metres for slide),
containing 0, the rest pose.
"""

import math
import re

import numpy as np

KINDS = ("turn", "hinge", "spin", "slide", "fixed")
AXES = {"side": (1.0, 0.0, 0.0), "forward": (0.0, 1.0, 0.0), "up": (0.0, 0.0, 1.0)}
PIVOTS = ("cut", "center", "bottom", "top", "front", "back", "left", "right")
ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Steps of the search for the narrowest section in a [low, high] range.
NARROW_STEPS = 41


# ---- the order's part list -----------------------------------------------------------------

def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _coord_problem(v):
    if v is None or _is_number(v):
        return None
    if isinstance(v, list) and len(v) == 2 and all(_is_number(x) for x in v) and v[0] < v[1]:
        return None
    return "a number, null or [low, high] with low < high"


def _axis_problem(axis):
    if isinstance(axis, str) and axis.lstrip("-") in AXES and axis.count("-") <= 1:
        return None
    if isinstance(axis, list) and len(axis) == 3 and all(_is_number(x) for x in axis) \
            and sum(x * x for x in axis) > 1e-12:
        return None
    return "side, forward or up (with - for the opposite way) or a vector [side, forward, up]"


def check_parts(parts):
    """Problems of an order's part list (empty: valid)."""
    if not isinstance(parts, list) or not parts:
        return ["parts must be a non-empty list of parts"]
    problems, seen, roots = [], set(), []
    for i, p in enumerate(parts):
        where = "parts[%d]" % i
        if not isinstance(p, dict):
            problems.append("%s must be an object" % where)
            continue
        pid = p.get("id")
        if not isinstance(pid, str) or not ID.match(pid):
            problems.append("%s: id must be a lower-case name (letters, digits, _), not %r" % (where, pid))
            continue
        where = "part %s" % pid
        if pid in seen:
            problems.append("%s appears twice" % where)
        unknown = sorted(k for k in p if k not in ("id", "parent", "find", "joint", "note"))
        if unknown:
            problems.append("%s: unknown keys %s (known: id, parent, find, joint, note)" % (where, unknown))
        parent = p.get("parent")
        if parent is None:
            roots.append(pid)
            if "find" in p or "joint" in p:
                problems.append("%s is the root (no parent): it keeps the rest of the object and has no find "
                                "or joint" % where)
        else:
            if parent not in seen:
                problems.append("%s: parent %r must be a part listed before it" % (where, parent))
            problems.extend("%s: %s" % (where, x) for x in _find_problems(p.get("find")))
            problems.extend("%s: %s" % (where, x) for x in _joint_problems(joint_of(p)))
        seen.add(pid)
    if len(roots) != 1:
        problems.append("exactly one part has no parent (the root), not %d (%s)" % (len(roots), ", ".join(roots)))
    return problems


def joint_of(part):
    """A part's joint with its defaults: fixed when it has none; a spin's range [-180, 180]."""
    j = dict(part.get("joint") or {"kind": "fixed"})
    if j.get("kind") == "spin":
        j.setdefault("range", [-180.0, 180.0])
    if j.get("kind") == "fixed":
        j.setdefault("range", [0.0, 0.0])
    j.setdefault("pivot", "cut")
    return j


def boxes_of(find):
    """The boxes of a part's region (find.box or find.boxes)."""
    return [find["box"]] if "box" in find else list(find["boxes"])


def _find_problems(find):
    if not isinstance(find, dict) or ("box" in find) == ("boxes" in find):
        return ["find must be {\"box\": [[side, forward, up], [side, forward, up]]} or {\"boxes\": [box, ...]}"]
    extra = sorted(k for k in find if k not in ("box", "boxes"))
    out = ["find: unknown keys %s" % extra] if extra else []
    if "boxes" in find and (not isinstance(find["boxes"], list) or not find["boxes"]):
        return out + ["find.boxes must be a non-empty list of boxes"]
    for n, box in enumerate(boxes_of(find)):
        name = "find.box" if "box" in find else "find.boxes[%d]" % n
        if not (isinstance(box, list) and len(box) == 2 and all(isinstance(c, list) and len(c) == 3 for c in box)):
            out.append("%s must be two corners [[side, forward, up], [side, forward, up]]" % name)
            continue
        bad = False
        for c, corner in enumerate(box):
            for a, axis in enumerate(("side", "forward", "up")):
                why = _coord_problem(corner[a])
                if why:
                    bad = True
                    out.append("%s corner %d %s must be %s, not %r" % (name, c, axis, why, corner[a]))
        if not bad:
            for a, axis in enumerate(("side", "forward", "up")):
                lo, hi = _span(box[0][a], -math.inf), _span(box[1][a], math.inf)
                if lo[1] > hi[0]:
                    out.append("%s %s: the low corner is above the high corner" % (name, axis))
    return out


def _span(v, unbounded):
    """(lowest, highest) value a box coordinate may take."""
    if v is None:
        return unbounded, unbounded
    if isinstance(v, list):
        return float(v[0]), float(v[1])
    return float(v), float(v)


def _joint_problems(joint):
    if not isinstance(joint, dict):
        return ["joint must be {\"kind\", \"axis\", \"pivot\", \"range\"}"]
    out = []
    extra = sorted(k for k in joint if k not in ("kind", "axis", "pivot", "range", "note"))
    if extra:
        out.append("joint: unknown keys %s" % extra)
    kind = joint.get("kind")
    if kind not in KINDS:
        return out + ["joint.kind must be one of %s, not %r" % (", ".join(KINDS), kind)]
    pivot = joint.get("pivot", "cut")
    if not (pivot in PIVOTS or (isinstance(pivot, list) and len(pivot) == 3 and all(_is_number(x) for x in pivot))):
        out.append("joint.pivot must be [side, forward, up] or one of %s, not %r" % (", ".join(PIVOTS), pivot))
    if kind == "fixed":
        if "axis" in joint or joint.get("range") not in (None, [0.0, 0.0], [0, 0]):
            out.append("a fixed joint has no axis or range")
        return out
    why = _axis_problem(joint.get("axis"))
    if why:
        out.append("joint.axis must be %s, not %r" % (why, joint.get("axis")))
    rng = joint.get("range")
    if not (isinstance(rng, list) and len(rng) == 2 and all(_is_number(x) for x in rng) and rng[0] <= 0 <= rng[1]
            and rng[0] < rng[1]):
        out.append("joint.range must be [low, high] with low <= 0 <= high and low < high, not %r" % (rng,))
    elif kind in ("turn", "hinge", "spin") and (rng[1] - rng[0]) > 360:
        out.append("joint.range spans more than 360 degrees")
    return out


# ---- coordinates ---------------------------------------------------------------------------

def to_gltf(v):
    """A model (Blender) vector or point in glTF axes."""
    return [float(v[0]), float(v[2]), -float(v[1])]


def normalised_to_model(p, dims):
    """A normalised point [side, forward, up] in model coordinates (dims: width, length,
    height in metres)."""
    w, ln, h = dims
    return np.array([p[0] * w, -p[1] * ln, p[2] * h], dtype=np.float64)


def axis_vector(axis):
    """A joint axis as a unit model vector."""
    if isinstance(axis, str):
        s = -1.0 if axis.startswith("-") else 1.0
        v = np.array(AXES[axis.lstrip("-")], dtype=np.float64) * s
    else:
        v = np.array(axis, dtype=np.float64)
    v = np.array([v[0], -v[1], v[2]])  # [side, forward, up] -> model (forward is -Y)
    return v / np.linalg.norm(v)


def dims_of(verts):
    """(width, length, height) of an object standing on the ground, centred on the origin."""
    lo, hi = verts.min(0), verts.max(0)
    return tuple(float(x) for x in np.maximum(hi - lo, 1e-9))


# ---- regions -------------------------------------------------------------------------------

# Model axis of each normalised axis and its sign (forward is -Y).
_MODEL_AXIS = ((0, 1.0), (1, -1.0), (2, 1.0))


def section(verts, faces, axis, level):
    """The points where the triangles cross the plane at `level` along `axis`."""
    tri = verts[faces]
    out = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        p, q = tri[:, i], tri[:, j]
        dp, dq = p[:, axis] - level, q[:, axis] - level
        m = dp * dq < 0
        if m.any():
            t = (dp[m] / (dp[m] - dq[m]))[:, None]
            out.append(p[m] + t * (q[m] - p[m]))
    return np.vstack(out) if out else np.zeros((0, 3))


def narrowest(verts, axis, lo, hi, other_lo, other_hi, steps=NARROW_STEPS, faces=None):
    """The level (model coordinate along `axis`, 0 to 2) between lo and hi where the object's
    cross-section has the smallest footprint inside the other two axes' bounds (the area of
    the section's bounding rectangle clipped to the bounds): with faces, the section of the
    triangles; without, a thin slab of vertices. A level where nothing crosses is a gap: the
    best cut. Ties go to the level nearest the middle."""
    others = [a for a in range(3) if a != axis]
    levels = np.linspace(lo, hi, steps)
    half = (hi - lo) / (steps - 1) * 0.75
    mid = (lo + hi) / 2.0
    best, best_key = mid, None

    def footprint(s):
        if len(s) == 0:
            return 0.0
        area = 1.0
        for a in others:
            a0, a1 = max(float(s[:, a].min()), other_lo[a]), min(float(s[:, a].max()), other_hi[a])
            area *= max(0.0, a1 - a0)
        return area
    for z in levels:
        s = verts[np.abs(verts[:, axis] - z) <= half] if faces is None else section(verts, faces, axis, z)
        area = footprint(s)
        key = (round(area, 12), abs(z - mid))
        if best_key is None or key < best_key:
            best, best_key = float(z), key
    return best


def resolve_box(box, verts, dims, faces=None):
    """(low corner, high corner, record) of a part's box in model coordinates, its
    [low, high] edges resolved to the narrowest section of `verts` (the parent's geometry,
    model coordinates). Unbounded edges are -inf / +inf. record: the resolved [low, high]
    edges in normalised coordinates."""
    lo = np.full(3, -np.inf)
    hi = np.full(3, np.inf)
    ranged = []
    for a in range(3):
        m, s = _MODEL_AXIS[a]
        for c in (0, 1):
            v = box[c][a]
            if v is None:
                continue
            if isinstance(v, list):
                ranged.append((a, c, v))
                v = v[0] if c == 0 else v[1]  # the widest box while the ranged edges resolve
            x = float(v) * dims[m] * s
            # the low normalised edge is the low model edge, except along forward (-Y)
            if (c == 0) == (s > 0):
                lo[m] = x
            else:
                hi[m] = x
    record = {}
    for a, c, (r0, r1) in ranged:
        m, s = _MODEL_AXIS[a]
        z0, z1 = sorted((r0 * dims[m] * s, r1 * dims[m] * s))
        level = narrowest(verts, m, z0, z1, lo, hi, faces=faces)
        if (c == 0) == (s > 0):
            lo[m] = level
        else:
            hi[m] = level
        record["%s_%s" % (("side", "forward", "up")[a], "low" if c == 0 else "high")] = round(level * s / dims[m], 4)
    return lo, hi, record


def inside(points, lo, hi, eps=0.0):
    """Mask of the points inside a box (model coordinates)."""
    return np.all((points >= lo - eps) & (points <= hi + eps), axis=1)


# ---- pivots --------------------------------------------------------------------------------

def pivot_point(pivot, part_verts, cap_verts, dims):
    """The pivot of a joint in model coordinates. part_verts: the part's vertices;
    cap_verts: the vertices of its cut faces (may be empty)."""
    if isinstance(pivot, list):
        return normalised_to_model(pivot, dims)
    if pivot == "cut" and len(cap_verts):
        return cap_verts.mean(0)
    if len(part_verts) == 0:
        return np.zeros(3)
    lo, hi = part_verts.min(0), part_verts.max(0)
    c = (lo + hi) / 2.0
    face = {"center": c, "cut": c, "bottom": [c[0], c[1], lo[2]], "top": [c[0], c[1], hi[2]],
            "front": [c[0], lo[1], c[2]], "back": [c[0], hi[1], c[2]],
            "left": [hi[0], c[1], c[2]], "right": [lo[0], c[1], c[2]]}[pivot]
    return np.array(face, dtype=np.float64)


# ---- the review's sweeps -------------------------------------------------------------------

def sweep_values(rng, n):
    """n values of a joint across its range, low to high (both ends included)."""
    lo, hi = float(rng[0]), float(rng[1])
    if n <= 1:
        return [hi]
    return [round(lo + (hi - lo) * i / (n - 1), 4) for i in range(n)]


def joint_record(part, joint, pivot_model, parent_pivot_model):
    """The model.json entry of a joint: glTF axes, the pivot in model space and relative to
    the parent's origin."""
    axis = axis_vector(joint["axis"]) if joint.get("axis") is not None else None
    return {"part": part["id"], "parent": part.get("parent"), "kind": joint["kind"],
            "axis": None if axis is None else [round(x, 6) for x in to_gltf(axis)],
            "range": [float(joint["range"][0]), float(joint["range"][1])],
            "unit": "metres" if joint["kind"] == "slide" else "degrees", "rest": 0.0,
            "pivot_m": [round(x, 5) for x in to_gltf(pivot_model)],
            "offset_m": [round(x, 5) for x in to_gltf(np.asarray(pivot_model) - np.asarray(parent_pivot_model))]}


# ---- the frame: which way the object faces --------------------------------------------------

FRONTS = ("+z", "-z", "+x", "-x")
# The rotation about Blender's z (degrees) that turns a mesh whose front points along a glTF
# axis so that it points along glTF +Z (Blender -Y).
FRONT_YAW = {"+z": 0.0, "-z": 180.0, "+x": -90.0, "-x": 90.0}


def snap_yaw(xy, limit=30.0, step=1.0):
    """The rotation about the vertical (degrees, within +-limit) that makes the footprint's
    bounding rectangle smallest: a generated object that came out slightly turned is squared
    to the axes. xy: (N, 2) footprint points. 0.0 when no turn helps by more than 1 %."""
    if len(xy) < 3:
        return 0.0
    c = xy - xy.mean(0)

    def area(deg):
        t = math.radians(deg)
        r = c @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])
        return float(np.ptp(r[:, 0]) * np.ptp(r[:, 1]))
    base = area(0.0)
    best, best_a = 0.0, base
    d = -limit
    while d <= limit + 1e-9:
        a = area(d)
        if a < best_a - 1e-12:
            best, best_a = d, a
        d += step
    return round(best, 3) if best_a < base * 0.99 else 0.0
