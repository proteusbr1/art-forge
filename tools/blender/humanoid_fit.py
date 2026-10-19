"""A variant's mesh conformed to an approved body: the geometry of humanoid_conform.py, without
bpy (NumPy only), so that it is tested on synthetic skeletons and meshes.

The variant (a rig tool's skeleton and weights on its painted mesh) and the approved body (a
figure the humanoid exporter wrote) each have their joints by role (humanoid_bones.py ROLES).
The conform:

1. A similarity fit (prefit): the variant turned about the vertical so that its front is the
   body's (-y), then turned a little more and scaled uniformly so that its joints, seen from
   the hips, lie as near the body's as they can (least squares), its hips on the body's.
2. One matrix per role (role_matrices): each segment of a limb (shoulder, upper arm, lower arm,
   upper leg, lower leg, foot, neck) is turned onto the body's segment and stretched along its
   own line so that both its joints land on the body's: the bone lengths match, the thickness
   across stays the variant's (a bulky armour stays bulky). The torso (hips, spine, chest,
   upper chest) is one piece from the hips to the neck: the spine joints a rig tool predicts are
   not landmarks of the surface, so they are not forced onto the body's (their remaining
   distance is reported). An end bone (head, hand, toes, a foot without toes) is not stretched:
   it takes the turn of the segment it hangs from and lands on its own joint.
3. Every vertex moved by its own weights (linear blend skinning from the variant's rest to the
   body's rest): its bones' roles, a bone without a role (a finger) counted with its nearest
   mapped ancestor, a role the body lacks with its nearest role the body has.
4. Measures: the joints before and after, each segment's stretch, the edges' stretch, the
   body's joints outside the conformed outfit, how far the outfit's soles are from the body's
   floor and how far the body's surface pokes out of the outfit; flags over the thresholds.

Coordinates are Blender's (z up, the front -y) in the body's units (metres).
"""

import math

import numpy as np

SIDES = ("left", "right")
TORSO = ("hips", "spine", "chest", "upper_chest")
# The parent of each role in a humanoid skeleton (a role the body lacks is counted with its
# nearest ancestor that both skeletons have).
ROLE_PARENT = {"hips": None, "spine": "hips", "chest": "spine", "upper_chest": "chest", "neck": "upper_chest",
               "head": "neck"}
for _s in SIDES:
    ROLE_PARENT.update({_s + "_shoulder": "upper_chest", _s + "_upper_arm": _s + "_shoulder",
                        _s + "_lower_arm": _s + "_upper_arm", _s + "_hand": _s + "_lower_arm",
                        _s + "_upper_leg": "hips", _s + "_lower_leg": _s + "_upper_leg",
                        _s + "_foot": _s + "_lower_leg", _s + "_toes": _s + "_foot"})
# A segment's role -> the roles its far joint may be (the first that both skeletons have).
AIM = {"neck": ("head",)}
for _s in SIDES:
    AIM.update({_s + "_shoulder": (_s + "_upper_arm",), _s + "_upper_arm": (_s + "_lower_arm",),
                _s + "_lower_arm": (_s + "_hand",), _s + "_upper_leg": (_s + "_lower_leg",),
                _s + "_lower_leg": (_s + "_foot",), _s + "_foot": (_s + "_toes",)})
TORSO_AIM = ("neck", "head")

# Default thresholds of the flags (humanoid_conform.py takes them on its command line).
THRESHOLDS = {
    "joint_cm": 4.0,      # a body joint farther than this from the conformed variant's (the torso's)
    "outside_cm": 1.5,    # a body joint farther than this outside the conformed outfit
    "scale": 1.5,         # a segment stretched or shrunk by more than this factor
    "torn_share": 0.002,  # share of edges stretched to more than twice or under half their length
    "floor_cm": 4.0,      # the outfit's soles farther than this below or above the body's floor
}
TORN_RATIO = 2.0
SURFACE_SAMPLE = 20000  # body vertices sampled for the outside measure


def common_roles(variant_roles, body_roles):
    """The roles both skeletons have (dictionaries {role: bone})."""
    return [r for r in ROLE_PARENT if variant_roles.get(r) and body_roles.get(r)]


def shared_role(role, common):
    """The role a variant's role counts as: itself when the body has it, else its nearest
    ancestor the body has."""
    while role is not None and role not in common:
        role = ROLE_PARENT[role]
    return role or "hips"


def bone_roles(tree_parents, roles, common):
    """{bone: role} for every bone of the variant: a mapped bone its (shared) role, an unmapped
    one (a finger, an extra root) its nearest mapped ancestor's, else the hips.
    tree_parents: {bone: parent or None}."""
    by_bone = {b: r for r, b in roles.items() if b}
    out = {}
    for b in tree_parents:
        x, seen = b, set()
        while x is not None and x not in by_bone and x not in seen:
            seen.add(x)
            x = tree_parents.get(x)
        out[b] = shared_role(by_bone[x], common) if x in by_bone else "hips"
    return out


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def rotation_between(a, b):
    """The smallest rotation (3x3) taking the direction a to the direction b."""
    a, b = _unit(np.asarray(a, float)), _unit(np.asarray(b, float))
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = np.linalg.norm(v)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        # Opposite: half a turn about any axis across a.
        axis = _unit(np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]))
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k * ((1.0 - c) / (s * s))


def rot_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def affine(linear, offset):
    m = np.eye(4)
    m[:3, :3] = linear
    m[:3, 3] = offset
    return m


def apply(m, pts):
    pts = np.asarray(pts, float)
    return pts @ m[:3, :3].T + m[:3, 3]


def prefit(vj, bj, common, front):
    """The similarity (4x4) that takes the variant's joints toward the body's: turned so that
    its front (a horizontal axis, from its bone map) is -y, its hips on the body's hips, then
    turned about the vertical and scaled uniformly by least squares over the other joints.
    Returns (matrix, {"turn_deg", "refine_deg", "scale"})."""
    f = np.array(front, float)
    yaw0 = -math.atan2(f[0], -f[1])  # as the exporter: the front onto -y
    h_v, h_b = np.asarray(vj["hips"], float), np.asarray(bj["hips"], float)
    roles = [r for r in common if r != "hips"]
    x = np.array([rot_z(yaw0) @ (np.asarray(vj[r], float) - h_v) for r in roles])
    y = np.array([np.asarray(bj[r], float) - h_b for r in roles])
    cross = float(np.sum(x[:, 0] * y[:, 1] - x[:, 1] * y[:, 0]))
    dot = float(np.sum(x[:, 0] * y[:, 0] + x[:, 1] * y[:, 1]))
    refine = math.atan2(cross, dot) if (abs(cross) + abs(dot)) > 1e-12 else 0.0
    r = rot_z(yaw0 + refine)
    xr = x @ rot_z(refine).T
    scale = float(np.sum(xr * y) / max(np.sum(xr * xr), 1e-12))
    m = affine(scale * r, h_b - scale * (r @ h_v))
    return m, {"turn_deg": math.degrees(yaw0 + refine), "refine_deg": math.degrees(refine), "scale": scale}


def _segment(hv, tv, hb, tb):
    """The matrix taking the segment hv -> tv onto hb -> tb (turned, stretched along its line)
    and its stretch factor."""
    dv, db = np.asarray(tv, float) - hv, np.asarray(tb, float) - hb
    lv, lb = np.linalg.norm(dv), np.linalg.norm(db)
    if lv < 1e-9 or lb < 1e-9:
        return affine(np.eye(3), hb - hv), np.eye(3), 1.0
    s = lb / lv
    u = dv / lv
    rot = rotation_between(dv, db)
    lin = rot @ (np.eye(3) + (s - 1.0) * np.outer(u, u))
    return affine(lin, hb - lin @ hv), rot, s


def role_matrices(vj, bj, common):
    """{role: 4x4} taking the (prefitted) variant's rest to the body's rest, and per role
    {"scale", "aim", "kind"} (kind: "torso", "segment" or "end")."""
    vj = {r: np.asarray(p, float) for r, p in vj.items()}
    bj = {r: np.asarray(p, float) for r, p in bj.items()}
    mats, rots, info = {}, {}, {}
    torso_aim = next(r for r in TORSO_AIM if r in common)
    m, rot, s = _segment(vj["hips"], vj[torso_aim], bj["hips"], bj[torso_aim])
    for r in TORSO:
        if r in common:
            mats[r], rots[r] = m, rot
            info[r] = {"scale": s, "aim": torso_aim, "kind": "torso"}
    # Parents before children (ROLE_PARENT's order).
    for r in ROLE_PARENT:
        if r not in common or r in mats:
            continue
        aim = next((x for x in AIM.get(r, ()) if x in common), None)
        if aim is not None:
            m, rot, s = _segment(vj[r], vj[aim], bj[r], bj[aim])
            mats[r], rots[r] = m, rot
            info[r] = {"scale": s, "aim": aim, "kind": "segment"}
        else:
            src = shared_role(ROLE_PARENT[r], common)
            rot = rots[src]
            mats[r], rots[r] = affine(rot, bj[r] - rot @ vj[r]), rot
            info[r] = {"scale": 1.0, "aim": None, "kind": "end", "turn_of": src}
    return mats, info


def skin(points, index, weight, mats):
    """Linear blend skinning: points (N, 3), index (N, K) of the matrices in mats (M, 4, 4) and
    their weights (N, K), normalised here."""
    points = np.asarray(points, float)
    w = np.asarray(weight, float)
    tot = w.sum(axis=1, keepdims=True)
    w = np.where(tot > 0, w / np.where(tot > 0, tot, 1.0), 0.0)
    out = np.zeros_like(points)
    for k in range(index.shape[1]):
        m = mats[index[:, k]]
        out += w[:, k:k + 1] * (np.einsum("nij,nj->ni", m[:, :3, :3], points) + m[:, :3, 3])
    lost = tot[:, 0] <= 0
    out[lost] = points[lost]
    return out


def edges_of(triangles):
    t = np.asarray(triangles, np.int64)
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


def edge_stretch(rest, now, edges):
    """|length now / length at rest - 1| of every edge longer than zero at rest."""
    a = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    b = np.linalg.norm(now[edges[:, 0]] - now[edges[:, 1]], axis=1)
    keep = a > 1e-9
    return b[keep] / a[keep]


def ray_directions(n=26):
    """n directions spread evenly over the sphere (a Fibonacci lattice)."""
    i = np.arange(n) + 0.5
    z = 1.0 - 2.0 * i / n
    r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    phi = i * math.pi * (3.0 - math.sqrt(5.0))
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)


class MeshSurface:
    """The conformed outfit as a surface the measures ask (NumPy, brute force: the tests'
    meshes are small; humanoid_conform.py gives Blender's BVH tree the same two questions).

    escape(points, dirs) -> (P, D) True where the ray from a point along a direction leaves
    without crossing the surface. distance(points) -> distance to the nearest vertex."""

    def __init__(self, co, triangles):
        self.co = np.asarray(co, float)
        t = np.asarray(triangles, np.int64)
        self.a = self.co[t[:, 0]]
        self.e1 = self.co[t[:, 1]] - self.a
        self.e2 = self.co[t[:, 2]] - self.a

    def escape(self, points, dirs):
        out = np.zeros((len(points), len(dirs)), bool)
        for i, p in enumerate(np.asarray(points, float)):
            for j, d in enumerate(dirs):
                pv = np.cross(d, self.e2)
                det = np.einsum("ij,ij->i", self.e1, pv)
                ok = np.abs(det) > 1e-12
                inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
                tv = p - self.a
                u = np.einsum("ij,ij->i", tv, pv) * inv
                qv = np.cross(tv, self.e1)
                v = (qv @ d) * inv
                t = np.einsum("ij,ij->i", self.e2, qv) * inv
                hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
                out[i, j] = not hit.any()
        return out

    def distance(self, points):
        out = []
        for chunk in np.array_split(np.asarray(points, float), max(1, len(points) // 256)):
            d = ((chunk[:, None, :] - self.co[None, :, :]) ** 2).sum(-1)
            out.append(np.sqrt(d.min(axis=1)))
        return np.concatenate(out) if out else np.zeros(0)


# A point is outside the outfit when at least this share of the rays from it leave without
# crossing its surface: robust to the inner shells, open seams and mixed winding of generated
# meshes, which defeat a normal's side or a ray's parity. A point just outside a surface sees
# about half the directions leave; one inside sees none (or a few, through a small hole).
ESCAPE_SHARE = 0.25


def outside_depth(points, surface, dirs=None):
    """How far each point lies outside the outfit (0 inside): its distance to the surface when
    at least ESCAPE_SHARE of its rays escape."""
    points = np.asarray(points, float)
    if not len(points):
        return np.zeros(0)
    dirs = ray_directions() if dirs is None else dirs
    share = surface.escape(points, dirs).mean(axis=1)
    return np.where(share >= ESCAPE_SHARE, surface.distance(points), 0.0)


def _round(v, n=4):
    return None if v is None else round(float(v), n)


def conform(vj, bj, variant_roles, body_roles, front, points, index, weight, vertex_roles, triangles,
            body_points=None, body_floor=0.0, surface_of=MeshSurface, body_sample=SURFACE_SAMPLE, thresholds=None):
    """The whole conform of a variant's mesh to the body.

    vj, bj: {role: joint} of the variant (its rig's world) and of the body; variant_roles,
    body_roles: their bone maps {role: bone}; front: the variant's front (bone map); points
    (N, 3): the variant's mesh; index (N, K) and weight (N, K): its skin, index into
    vertex_roles (the shared role of each column's bone group); triangles (T, 3); body_points
    (M, 3): the body's surface, for the outside measure (at most body_sample of them, evenly
    taken); body_floor: the body's floor height; surface_of(points, triangles): the conformed
    outfit as a surface (MeshSurface, or Blender's BVH tree in humanoid_conform.py).
    Returns {"points": conformed (N, 3), "prefit": 4x4, "mats": {role: 4x4}, "report": {...}}.
    """
    th = dict(THRESHOLDS, **(thresholds or {}))
    common = common_roles(variant_roles, body_roles)
    missing = [r for r in ROLE_PARENT if body_roles.get(r) and not variant_roles.get(r)]
    pre, fit = prefit(vj, bj, common, front)
    vjp = {r: apply(pre, [vj[r]])[0] for r in common}
    mats, info = role_matrices(vjp, bj, common)
    order = list(mats)
    stack = np.array([mats[r] for r in order])
    col = np.array([order.index(shared_role(r, common)) for r in vertex_roles], np.int64)
    rest = apply(pre, points)
    out = skin(rest, col[np.asarray(index, np.int64)], weight, stack)
    joints = {}
    for r in common:
        before = float(np.linalg.norm(vjp[r] - np.asarray(bj[r], float)))
        after = float(np.linalg.norm(apply(mats[r], [vjp[r]])[0] - np.asarray(bj[r], float)))
        joints[r] = {"before_cm": _round(before * 100, 3), "after_cm": _round(after * 100, 3),
                     "scale": _round(info[r]["scale"]), "kind": info[r]["kind"]}
    flags = []
    worst_joint = max(joints, key=lambda r: joints[r]["after_cm"])
    joint_cm = joints[worst_joint]["after_cm"]
    if joint_cm > th["joint_cm"]:
        flags.append("joints: the conformed %s joint misses the body's by %.1f cm (over %s)" % (
            worst_joint, joint_cm, th["joint_cm"]))
    scales = {r: v["scale"] for r, v in joints.items() if v["kind"] != "end"}
    for r in sorted(scales, key=lambda x: -abs(math.log(max(scales[x], 1e-9)))):
        s = scales[r]
        if s > th["scale"] or s < 1.0 / th["scale"]:
            flags.append("scale: the %s segment is stretched by %.2f to fit the body (limit %s)" % (r, s, th["scale"]))
    ratio = edge_stretch(rest, out, edges_of(triangles)) if len(triangles) else np.ones(1)
    stretch = np.abs(ratio - 1.0)
    torn = float(np.mean((ratio > TORN_RATIO) | (ratio < 1.0 / TORN_RATIO))) if len(ratio) else 0.0
    if torn > th["torn_share"]:
        flags.append("tear: %.2f%% of the edges are stretched over twice or under half their length (limit %.2f%%)"
                     % (torn * 100, th["torn_share"] * 100))
    surface = surface_of(out, triangles)
    roles_b = [r for r in common]
    depth = outside_depth(np.array([bj[r] for r in roles_b], float), surface) * 100.0
    outside = {r: _round(d, 3) for r, d in zip(roles_b, depth)}
    for r, d in sorted(outside.items(), key=lambda x: -x[1]):
        if d > th["outside_cm"]:
            flags.append("outside: the body's %s joint lies %.1f cm outside the outfit (limit %s)" % (
                r, d, th["outside_cm"]))
    # The soles: the lowest point of each foot's part against the body's floor.
    dom = col[np.asarray(index, np.int64)[np.arange(len(out)), np.argmax(np.asarray(weight), axis=1)]]
    floor = {}
    for side in SIDES:
        ids = [order.index(r) for r in (side + "_foot", side + "_toes") if r in order]
        sel = np.isin(dom, ids)
        floor[side] = _round((body_floor - float(out[sel, 2].min())) * 100.0, 3) if sel.any() else None
    worst_floor = max((abs(v) for v in floor.values() if v is not None), default=0.0)
    if worst_floor > th["floor_cm"]:
        side = max((s for s in SIDES if floor[s] is not None), key=lambda s: abs(floor[s]))
        flags.append("floor: the %s sole lies %.1f cm %s the body's floor (limit %s)" % (
            side, abs(floor[side]), "below" if floor[side] > 0 else "above", th["floor_cm"]))
    body_out = None
    if body_points is not None and len(body_points):
        bp = np.asarray(body_points, float)
        if len(bp) > body_sample:
            bp = bp[np.linspace(0, len(bp) - 1, body_sample).astype(np.int64)]
        d = outside_depth(bp, surface) * 100.0
        body_out = {"p95_cm": _round(np.percentile(d, 95), 3), "p99_cm": _round(np.percentile(d, 99), 3),
                    "max_cm": _round(d.max(), 3), "share_over_1cm": _round(np.mean(d > 1.0)),
                    "sampled": int(len(bp))}
    report = {
        "roles": common, "missing_roles": missing,
        "prefit": {k: _round(v, 5) for k, v in fit.items()},
        "joints": joints, "joint_error_cm": joint_cm, "joint_error_role": worst_joint,
        "joint_outside_cm": outside, "joint_outside_max_cm": _round(max(depth) if len(depth) else 0.0, 3),
        "segment_scale": {"min": _round(min(scales.values())), "max": _round(max(scales.values()))},
        "stretch_p95": _round(np.percentile(stretch, 95)), "stretch_max": _round(stretch.max()),
        "torn_share": _round(torn, 6), "floor_offset_cm": floor, "body_outside": body_out,
        "thresholds": th, "flags": flags,
    }
    return {"points": out, "prefit": pre, "mats": mats, "report": report}
