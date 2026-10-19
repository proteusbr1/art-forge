"""The approved body a humanoid piece may wear (inputs.body): its check for the order, and the
comparison of an exported figure's skeleton and motions with the body's, read from the GLB
files themselves (no bpy).

An approved body is a GLB the humanoid exporter wrote (a variant's export or a piece's final
pick) with its model.json next to it. Every variant of a piece that wears it is conformed to
it and exported on its skeleton (tools/blender/humanoid_conform.py, humanoid_body_export.py).
"""

import json
import math
import os
import struct

# The humanoid roles a body's model.json must name (tools/blender/humanoid_bones.py REQUIRED).
REQUIRED = (
    "hips", "spine", "chest", "head",
    "left_upper_arm", "left_lower_arm", "left_hand",
    "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot",
    "right_upper_leg", "right_lower_leg", "right_foot",
)
# Params the body decides (the order check refuses them on a piece that wears a body).
BODY_PARAMS = ("height", "head", "hands", "feet", "widen")
# Skeletons are the same when every joint's rest (local translation, rotation, scale) and
# inverse bind matrix match within these (the GLB holds 32-bit floats; Blender's round trips
# leave a few 1e-7).
TOLERANCE_M = 1e-4
TOLERANCE = 1e-4


def model_path(body):
    """The model.json of a body GLB: next to it."""
    return os.path.join(os.path.dirname(os.path.abspath(body)), "model.json")


def read_glb(path):
    """(the glTF JSON, the binary chunk) of a GLB file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("not a GLB file")
    n, kind = struct.unpack("<II", data[12:20])
    if kind != 0x4E4F534A:
        raise ValueError("the GLB's first chunk is not JSON")
    doc = json.loads(data[20:20 + n].decode("utf-8"))
    off = 20 + n
    binary = b""
    if off + 8 <= len(data):
        m, _ = struct.unpack("<II", data[off:off + 8])
        binary = data[off + 8:off + 8 + m]
    return doc, binary


_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def accessor(doc, binary, index):
    """The float values of an accessor, as a list of tuples (float32 components only)."""
    acc = doc["accessors"][index]
    if acc.get("componentType") != 5126:
        raise ValueError("accessor %d is not 32-bit float" % index)
    view = doc["bufferViews"][acc["bufferView"]]
    n = _COUNT[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or 4 * n
    out = []
    for i in range(acc["count"]):
        out.append(struct.unpack_from("<%df" % n, binary, start + i * stride))
    return out


def skeleton(path):
    """The skeleton of a GLB's first skin: {joint name: {"parent", "translation", "rotation",
    "scale", "inverse_bind"}}, plus the nodes parented to a joint that are not joints (the
    item_ref mesh) under the key "attached"."""
    doc, binary = read_glb(path)
    skins = doc.get("skins") or []
    if not skins:
        raise ValueError("the GLB has no skin")
    nodes = doc["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    joints = skins[0]["joints"]
    ibm = accessor(doc, binary, skins[0]["inverseBindMatrices"]) if "inverseBindMatrices" in skins[0] else None
    out, attached = {}, {}

    def rest(n):
        return {"translation": list(n.get("translation", [0.0, 0.0, 0.0])),
                "rotation": list(n.get("rotation", [0.0, 0.0, 0.0, 1.0])),
                "scale": list(n.get("scale", [1.0, 1.0, 1.0]))}
    names = set()
    for k, j in enumerate(joints):
        n = nodes[j]
        name = n.get("name", "joint_%d" % j)
        names.add(j)
        out[name] = dict(rest(n), parent=nodes[parent[j]].get("name") if j in parent else None,
                         inverse_bind=list(ibm[k]) if ibm else None)
    for i, n in enumerate(nodes):
        if i not in names and parent.get(i) in names:
            attached[n.get("name", "node_%d" % i)] = dict(rest(n), parent=nodes[parent[i]].get("name"))
    return {"joints": out, "attached": attached}


def _rotation_deg(q1, q2):
    d = abs(sum(a * b for a, b in zip(q1, q2)))
    n = math.sqrt(sum(a * a for a in q1) * sum(b * b for b in q2)) or 1.0
    # The angle between two rotations, by the sine of half of it (precise near zero).
    c = min(1.0, d / n)
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(max(0.0, 1.0 - c * c)))))


def compare_skeletons(a, b, tolerance_m=TOLERANCE_M, tolerance=TOLERANCE):
    """How a skeleton (skeleton()) differs from another: {"identical", "joints",
    "max_offset_m", "max_turn_deg", "max_scale", "max_inverse_bind", "problems"}."""
    problems = []
    ja, jb = a["joints"], b["joints"]
    for name in sorted(set(ja) - set(jb)):
        problems.append("joint %s is not in the body's skeleton" % name)
    for name in sorted(set(jb) - set(ja)):
        problems.append("the body's joint %s is missing" % name)
    off = turn = scale = ibm = 0.0
    pairs = [(n, ja[n], jb[n]) for n in sorted(set(ja) & set(jb))]
    pairs += [("%s (attached)" % n, a["attached"][n], b["attached"][n])
              for n in sorted(set(a["attached"]) & set(b["attached"]))]
    for name in sorted(set(b["attached"]) - set(a["attached"])):
        problems.append("the node %s attached to the body's %s is missing" % (name, b["attached"][name]["parent"]))
    for name, x, y in pairs:
        if x["parent"] != y["parent"]:
            problems.append("%s hangs from %s, the body's from %s" % (name, x["parent"], y["parent"]))
        off = max(off, max(abs(p - q) for p, q in zip(x["translation"], y["translation"])))
        turn = max(turn, _rotation_deg(x["rotation"], y["rotation"]))
        scale = max(scale, max(abs(p - q) for p, q in zip(x["scale"], y["scale"])))
        if x.get("inverse_bind") and y.get("inverse_bind"):
            ibm = max(ibm, max(abs(p - q) for p, q in zip(x["inverse_bind"], y["inverse_bind"])))
    max_turn = math.degrees(2.0 * tolerance)
    if off > tolerance_m:
        problems.append("a joint's rest position is %.6f m off the body's" % off)
    if turn > max_turn:
        problems.append("a joint's rest rotation is %.4f degrees off the body's" % turn)
    if scale > tolerance:
        problems.append("a joint's rest scale is %.6f off the body's" % scale)
    if ibm > tolerance_m * 10:
        problems.append("an inverse bind matrix is %.6f off the body's" % ibm)
    return {"identical": not problems, "joints": len(ja), "max_offset_m": round(off, 9),
            "max_turn_deg": round(turn, 6), "max_scale": round(scale, 9), "max_inverse_bind": round(ibm, 9),
            "problems": problems}


def animations(path):
    """{(animation, node, path): (times, values)} of a GLB."""
    doc, binary = read_glb(path)
    out = {}
    for an in doc.get("animations") or []:
        for ch in an["channels"]:
            s = an["samplers"][ch["sampler"]]
            key = (an.get("name"), doc["nodes"][ch["target"]["node"]].get("name"), ch["target"]["path"])
            out[key] = (accessor(doc, binary, s["input"]), accessor(doc, binary, s["output"]))
    return out


def compare_animations(a, b):
    """The largest difference between two GLBs' animations (animations()): {"same_channels",
    "max_difference", "worst"}; rotations compared up to the quaternion's sign."""
    worst, where = 0.0, None
    for key in set(a) & set(b):
        (ta, va), (tb, vb) = a[key], b[key]
        if len(va) != len(vb) or len(ta) != len(tb):
            return {"same_channels": set(a) == set(b), "max_difference": float("inf"), "worst": list(key)}
        for p, q in zip(va, vb):
            d = max(abs(x - y) for x, y in zip(p, q))
            if key[2] == "rotation":
                d = min(d, max(abs(x + y) for x, y in zip(p, q)))
            if d > worst:
                worst, where = d, list(key)
        worst = max([worst] + [abs(x[0] - y[0]) for x, y in zip(ta, tb)])
    return {"same_channels": set(a) == set(b), "max_difference": worst, "worst": where}


def check_body(path, where):
    """Problems of an approved body given as inputs.body: a GLB the humanoid exporter wrote,
    its model.json next to it naming its bones by role."""
    problems = []
    if not path.lower().endswith(".glb"):
        return ["%s: inputs.body must be a GLB the humanoid exporter wrote (a variant's export or a final "
                "pick): %s" % (where, path)]
    if not os.path.isfile(path):
        return []  # the generic input check names it
    mp = model_path(path)
    try:
        with open(mp, encoding="utf-8-sig") as f:
            model = json.load(f)
    except (OSError, ValueError) as ex:
        return ["%s: inputs.body: its model.json cannot be read (%s): the body must be a humanoid exporter's "
                "output with its model.json next to it" % (where, ex)]
    if not isinstance(model, dict) or model.get("format") != "forge-humanoid" or not isinstance(
            model.get("bones"), dict):
        return ["%s: inputs.body: %s is not a humanoid exporter's model.json" % (where, mp)]
    missing = [r for r in REQUIRED if not model["bones"].get(r)]
    if missing:
        problems.append("%s: inputs.body: its model.json names no bone for %s" % (where, ", ".join(missing)))
    if not (isinstance(model.get("height"), (int, float)) and model["height"] > 0):
        problems.append("%s: inputs.body: its model.json has no height" % where)
    try:
        sk = skeleton(path)
    except (OSError, ValueError, KeyError, IndexError, struct.error) as ex:
        problems.append("%s: inputs.body cannot be read as a skinned GLB (%s): %s" % (where, ex, path))
        return problems
    absent = sorted(n for n in model["bones"].values() if n not in sk["joints"])
    if absent:
        problems.append("%s: inputs.body: the skeleton lacks the bones its model.json names: %s" % (
            where, ", ".join(absent)))
    return problems


def wears_own_output(path, family, piece):
    """True when a body path lies in this piece's own output folder (<family>/<piece>/...): the
    piece would be conformed to its own pick, which changes with every run."""
    parts = [p.lower() for p in os.path.normpath(os.path.abspath(path)).split(os.sep)]
    fam, pid = family.lower(), piece.lower()
    return any(parts[i] == fam and parts[i + 1] == pid for i in range(len(parts) - 2))
