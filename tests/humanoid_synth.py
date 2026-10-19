"""Synthetic humanoid figures for the conform's tests (NumPy only): joints by role and a mesh of
closed capsules, one per segment, each skinned to its role.

figure() builds one: its height, its arms' and legs' length factors, how thick its capsules
are (a bulky armour), which way it faces (yaw: 0 faces -y, Blender's front), where it stands,
how far its soles reach below the ankles, and an optional shift of the left arm's capsules off
their joints (a rig whose joints miss the mesh). Coordinates: z up, x the figure's left when it
faces -y.
"""

import math

import numpy as np

# Joints at rest in body heights, the figure facing -y (x is its left).
BASE = {
    "hips": (0.0, 0.0, 0.53), "spine": (0.0, 0.0, 0.60), "chest": (0.0, 0.0, 0.68), "upper_chest": (0.0, 0.0, 0.76),
    "neck": (0.0, 0.0, 0.83), "head": (0.0, 0.0, 0.88),
}
for _s, _x in (("left", 1.0), ("right", -1.0)):
    BASE.update({
        _s + "_shoulder": (0.03 * _x, 0.0, 0.80), _s + "_upper_arm": (0.10 * _x, 0.0, 0.80),
        _s + "_lower_arm": (0.15 * _x, 0.0, 0.62), _s + "_hand": (0.19 * _x, 0.0, 0.45),
        _s + "_upper_leg": (0.06 * _x, 0.0, 0.50), _s + "_lower_leg": (0.07 * _x, 0.0, 0.28),
        _s + "_foot": (0.075 * _x, 0.0, 0.05), _s + "_toes": (0.075 * _x, -0.08, 0.015),
    })
ROLES = list(BASE)
ARM = ("upper_arm", "lower_arm", "hand")
LEG = ("lower_leg", "foot", "toes")
# Capsules: (role whose weights they carry, from joint, to joint or None, radius in body heights).
# A None end: an end part (head, hand, toes) that goes on along its parent's line.
CAPSULES = [("chest", "hips", "neck", 0.085), ("neck", "neck", "head", 0.035), ("head", "head", None, 0.06)]
for _s in ("left", "right"):
    CAPSULES += [(_s + "_shoulder", _s + "_shoulder", _s + "_upper_arm", 0.035),
                 (_s + "_upper_arm", _s + "_upper_arm", _s + "_lower_arm", 0.035),
                 (_s + "_lower_arm", _s + "_lower_arm", _s + "_hand", 0.03),
                 (_s + "_hand", _s + "_hand", None, 0.025),
                 (_s + "_upper_leg", _s + "_upper_leg", _s + "_lower_leg", 0.05),
                 (_s + "_lower_leg", _s + "_lower_leg", _s + "_foot", 0.04),
                 (_s + "_foot", _s + "_foot", _s + "_toes", 0.03),
                 (_s + "_toes", _s + "_toes", None, 0.02)]
END_LENGTH = {"head": 0.12, "hand": 0.07, "toes": 0.03}
PARENT_OF_END = {"head": "neck", "hand": "lower_arm", "toes": "foot"}


def joints(height=1.0, arms=1.0, legs=1.0):
    """{role: (x, y, z)} of a figure facing -y, standing on z = 0 with its hips over the
    origin. arms, legs: length factors of the arms below the shoulder and the legs below the
    hip (the hips keep their height: the figure's legs are scaled about the ankles' floor)."""
    j = {r: np.array(p, float) for r, p in BASE.items()}
    for s in ("left", "right"):
        for part, factor, chain in ((s + "_upper_arm", arms, ARM), (s + "_upper_leg", legs, ("upper_leg",) + LEG)):
            names = [s + "_" + c for c in chain]
            if part not in names:
                names = [part] + names
            root = j[names[0]].copy()
            prev_old, prev_new = root, root
            for n in names[1:]:
                old = j[n].copy()
                j[n] = prev_new + (old - prev_old) * factor
                prev_old, prev_new = old, j[n]
    if legs != 1.0:
        # Stand on the floor again: everything moves so the lowest ankle stays at its rest height.
        dz = BASE["left_foot"][2] - min(j["left_foot"][2], j["right_foot"][2])
        for r in j:
            j[r] = j[r] + np.array([0.0, 0.0, dz])
    return {r: p * height for r, p in j.items()}


def capsule(a, b, r, rings=6, sides=10):
    """A closed capsule-like surface from a to b of radius r: (points, triangles)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    u = b - a
    ln = np.linalg.norm(u)
    u = u / ln
    side = np.cross(u, [0.0, 0.0, 1.0] if abs(u[2]) < 0.9 else [1.0, 0.0, 0.0])
    side /= np.linalg.norm(side)
    up = np.cross(side, u)
    pts = [a - u * r * 0.8]
    for i in range(rings + 1):
        c = a + u * ln * i / rings
        for k in range(sides):
            t = 2 * math.pi * k / sides
            pts.append(c + r * (math.cos(t) * side + math.sin(t) * up))
    pts.append(b + u * r * 0.8)
    tris = []
    last = len(pts) - 1
    for k in range(sides):
        tris.append((0, 1 + (k + 1) % sides, 1 + k))
    for i in range(rings):
        o0, o1 = 1 + i * sides, 1 + (i + 1) * sides
        for k in range(sides):
            k1 = (k + 1) % sides
            tris.append((o0 + k, o0 + k1, o1 + k1))
            tris.append((o0 + k, o1 + k1, o1 + k))
    o = 1 + rings * sides
    for k in range(sides):
        tris.append((o + k, o + (k + 1) % sides, last))
    return np.array(pts), np.array(tris, np.int64)


def figure(height=1.0, arms=1.0, legs=1.0, bulk=1.0, yaw=0.0, offset=(0.0, 0.0, 0.0), sole=0.0, arm_shift=0.0):
    """A synthetic figure: {"joints", "points", "triangles", "roles" (one role per capsule,
    in order), "index", "weight" (one column), "front"}. yaw: degrees about z (90 faces +x);
    sole: how much lower (body heights) the feet's capsules reach; arm_shift: the left arm's
    capsules moved this far (body heights) forward, off their joints."""
    j = joints(1.0, arms, legs)
    pts, tris, idx = [], [], []
    roles = []
    for k, (role, a, b, r) in enumerate(CAPSULES):
        pa = j[a]
        if b is None:
            end = role.split("_")[-1]
            par = role.replace(end, PARENT_OF_END[end]) if "_" in role else PARENT_OF_END[end]
            d = j[a] - j[par if par in j else a]
            d = d / (np.linalg.norm(d) or 1.0)
            pb = pa + d * END_LENGTH[end]
        else:
            pb = j[b]
        if role.endswith("_foot") or role.endswith("_toes"):
            pa = pa - np.array([0.0, 0.0, sole])
            pb = pb - np.array([0.0, 0.0, sole])
        if arm_shift and role.startswith("left_") and ("arm" in role or "hand" in role):
            pa = pa + np.array([0.0, -arm_shift, 0.0])
            pb = pb + np.array([0.0, -arm_shift, 0.0])
        p, t = capsule(pa, pb, r * bulk)
        tris.append(t + sum(len(x) for x in pts))
        pts.append(p)
        idx.append(np.full(len(p), k, np.int64))
        roles.append(role)
    points = np.concatenate(pts) * height
    jj = {r: p * height for r, p in j.items()}
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    off = np.asarray(offset, float)
    points = points @ rot.T + off
    jj = {r: rot @ p + off for r, p in jj.items()}
    front = rot @ np.array([0.0, -1.0, 0.0])
    front = np.round(front, 9)
    return {"joints": jj, "points": points, "triangles": np.concatenate(tris), "roles": roles,
            "index": np.concatenate(idx)[:, None], "weight": np.ones((len(points), 1)), "front": tuple(front)}
