"""Synthetic human motion for the motion tests: a walk with exactly planted feet (metres, y up,
+z forward, +x the figure's left), as canonical joints, as HY-Motion's SMPL-ordered output
(joints without the root translation, plus the translation) and as a BVH capture."""

import math

import numpy as np

from forge.motion_clips import CANON, IDX, SMPL_BODY

PELVIS_Y, HIP_X, THIGH, SHIN, ANKLE_Y = 0.95, 0.09, 0.43, 0.42, 0.08


def _ik(hip, ankle, l1, l2):
    d = ankle - hip
    dist = min(np.linalg.norm(d), l1 + l2 - 1e-6)
    u = d / np.linalg.norm(d)
    v = np.array([0.0, 0.0, 1.0]) - u * u[2]
    v /= np.linalg.norm(v)
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    return hip + u * a + v * math.sqrt(max(l1 * l1 - a * a, 0.0))


def walk(seconds=3.0, fps=30.0, speed=1.2, cycle=1.0, duty=0.6, lift=0.1, still=False):
    """(frames, 24, 3) canonical positions. still: the same figure standing (an idle)."""
    n = int(round(seconds * fps))
    stride = speed * cycle
    out = np.zeros((n, len(CANON), 3))
    for i in range(n):
        t = i / fps
        pz = 0.0 if still else speed * t
        bob = 0.0 if still else 0.015 * math.cos(4 * math.pi * t / cycle)
        P = {"pelvis": np.array([0.0, PELVIS_Y + bob, pz])}
        for s, sign, off in (("l", 1.0, 0.0), ("r", -1.0, 0.5)):
            hip = P["pelvis"] + np.array([sign * HIP_X, -0.05, 0.0])
            if still:
                ank = np.array([sign * HIP_X, ANKLE_Y, 0.0])
            else:
                ph = t / cycle + off
                c, phi = math.floor(ph), ph - math.floor(ph)
                if phi < duty:
                    sw, h = 0.0, 0.0
                else:
                    x = (phi - duty) / (1 - duty)
                    sw, h = 0.5 - 0.5 * math.cos(math.pi * x), lift * math.sin(math.pi * x)
                # planted under the hip at mid-stance, then swung one stride forward
                z = stride * (c + sw - off + duty / 2)
                ank = np.array([sign * HIP_X, ANKLE_Y + h, z])
            P[s + "_hip"] = hip
            P[s + "_ankle"] = ank
            P[s + "_knee"] = _ik(hip, ank, THIGH, SHIN)
            P[s + "_toe"] = ank + np.array([sign * 0.02, -0.06, 0.14])
        base = P["pelvis"]
        P["spine"] = base + [0, 0.12, 0]
        P["chest"] = base + [0, 0.30, 0]
        P["neck"] = base + [0, 0.50, 0]
        P["head"] = base + [0, 0.60, 0.02]
        P["head_top"] = base + [0, 0.78, 0.02]
        for s, sign, off in (("l", 1.0, 0.5), ("r", -1.0, 0.0)):
            swing = 0.0 if still else 0.12 * math.sin(2 * math.pi * (t / cycle + off))
            P[s + "_collar"] = base + [sign * 0.04, 0.45, 0]
            P[s + "_shoulder"] = base + [sign * 0.18, 0.45, 0]
            P[s + "_elbow"] = P[s + "_shoulder"] + [sign * 0.02, -0.28, swing]
            P[s + "_wrist"] = P[s + "_elbow"] + [0, -0.25, swing * 0.6]
            P[s + "_hand"] = P[s + "_wrist"] + [0, -0.08, 0.01]
        for j in CANON:
            out[i, IDX[j]] = P[j]
    return out


def smpl_output(pos):
    """HY-Motion's arrays of canonical positions: keypoints3d (1, frames, 52, 3) without the
    root translation and transl (1, frames, 3), the hands' middle fingers at 25 and 40."""
    n = len(pos)
    kp = np.zeros((n, 52, 3))
    for c, i in SMPL_BODY.items():
        kp[:, i] = pos[:, IDX[c]]
    kp[:, 25] = pos[:, IDX["l_hand"]]
    kp[:, 40] = pos[:, IDX["r_hand"]]
    kp[:, 6] = (pos[:, IDX["spine"]] + pos[:, IDX["chest"]]) / 2
    transl = pos[:, IDX["pelvis"]].copy()
    transl[:, 1] = 0.3  # a constant height offset, as the model's ground shift leaves
    kp = kp - transl[:, None, :]
    return kp[None], transl[None]


BVH_TREE = [("Hips", None, "pelvis"), ("Spine", "Hips", "spine"), ("Spine1", "Spine", "chest"),
            ("Neck1", "Spine1", "neck"), ("Head", "Neck1", "head"), ("Head/end", "Head", "head_top"),
            ("LeftShoulder", "Spine1", "l_collar"), ("LeftArm", "LeftShoulder", "l_shoulder"),
            ("LeftForeArm", "LeftArm", "l_elbow"), ("LeftHand", "LeftForeArm", "l_wrist"),
            ("LeftFingerBase", "LeftHand", "l_hand"),
            ("RightShoulder", "Spine1", "r_collar"), ("RightArm", "RightShoulder", "r_shoulder"),
            ("RightForeArm", "RightArm", "r_elbow"), ("RightHand", "RightForeArm", "r_wrist"),
            ("RightFingerBase", "RightHand", "r_hand"),
            ("LeftUpLeg", "Hips", "l_hip"), ("LeftLeg", "LeftUpLeg", "l_knee"), ("LeftFoot", "LeftLeg", "l_ankle"),
            ("LeftToeBase", "LeftFoot", "l_toe"),
            ("RightUpLeg", "Hips", "r_hip"), ("RightLeg", "RightUpLeg", "r_knee"),
            ("RightFoot", "RightLeg", "r_ankle"), ("RightToeBase", "RightFoot", "r_toe")]


def write_bvh(path, pos, fps, scale=100.0, prefix=""):
    """A BVH of canonical positions (in centimetres by default): every joint has position
    channels holding its offset from its parent and no rotation, so forward kinematics gives
    the positions back. The top of the head is an end site."""
    children = {}
    for name, parent, _ in BVH_TREE:
        children.setdefault(parent, []).append(name)
    canon = {name: c for name, _, c in BVH_TREE}
    parent_of = {name: p for name, p, _ in BVH_TREE}
    order = []
    lines = ["HIERARCHY"]

    def emit(name, depth):
        pad = "  " * depth
        if name.endswith("/end"):
            lines.append(pad + "End Site")
            lines.append(pad + "{")
            off = (pos[0, IDX[canon[name]]] - pos[0, IDX[canon[parent_of[name]]]]) * scale
            lines.append(pad + "  OFFSET %.5f %.5f %.5f" % tuple(off))
            lines.append(pad + "}")
            return
        lines.append(pad + ("ROOT " if parent_of[name] is None else "JOINT ") + prefix + name)
        lines.append(pad + "{")
        lines.append(pad + "  OFFSET 0 0 0")
        lines.append(pad + "  CHANNELS 3 Xposition Yposition Zposition")
        order.append(name)
        for c in children.get(name, []):
            emit(c, depth + 1)
        lines.append(pad + "}")
    emit("Hips", 0)
    lines += ["MOTION", "Frames: %d" % len(pos), "Frame Time: %.6f" % (1.0 / fps)]
    for f in range(len(pos)):
        vals = []
        for name in order:
            p = pos[f, IDX[canon[name]]] * scale
            if parent_of[name] is not None:
                p = p - pos[f, IDX[canon[parent_of[name]]]] * scale
            vals += ["%.5f" % x for x in p]
        lines.append(" ".join(vals))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(lines) + "\n")
