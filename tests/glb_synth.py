"""Small GLB files written without Blender for the tests of forge/body.py and the order check:
a skin (joints with their rest and inverse bind matrices), an attached node and animations,
and a humanoid exporter's model.json next to it (stub_body)."""

import json
import os
import struct

# A humanoid skeleton by role: (Godot name, parent, local translation).
BONES = [
    ("Hips", None, (0.0, 1.0, 0.0)), ("Spine", "Hips", (0.0, 0.1, 0.0)), ("Chest", "Spine", (0.0, 0.15, 0.0)),
    ("Neck", "Chest", (0.0, 0.25, 0.0)), ("Head", "Neck", (0.0, 0.1, 0.0)),
]
for _s, _x in (("Left", 1.0), ("Right", -1.0)):
    BONES += [(_s + "UpperArm", "Chest", (0.18 * _x, 0.2, 0.0)), (_s + "LowerArm", _s + "UpperArm", (0.1 * _x, -0.25, 0.0)),
              (_s + "Hand", _s + "LowerArm", (0.05 * _x, -0.25, 0.0)),
              (_s + "UpperLeg", "Hips", (0.1 * _x, -0.05, 0.0)), (_s + "LowerLeg", _s + "UpperLeg", (0.0, -0.42, 0.0)),
              (_s + "Foot", _s + "LowerLeg", (0.0, -0.42, 0.0))]
ROLE_NAMES = {"hips": "Hips", "spine": "Spine", "chest": "Chest", "neck": "Neck", "head": "Head"}
for _s in ("left", "right"):
    for _p in ("upper_arm", "lower_arm", "hand", "upper_leg", "lower_leg", "foot"):
        ROLE_NAMES["%s_%s" % (_s, _p)] = _s.capitalize() + "".join(w.capitalize() for w in _p.split("_"))


def write_glb(path, doc, binary=b""):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    js = json.dumps(doc).encode("utf-8")
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    with open(path, "wb") as f:
        f.write(b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks)


def skeleton_glb(path, bones=None, moved=None, attached=True, animation=None):
    """A GLB with one skin over bones [(name, parent, translation)]; moved: {name: offset} added
    to a bone's translation; attached: an item_ref node under the hips; animation: {"name",
    "node", "times", "rotations"} (one rotation channel)."""
    bones = list(bones or BONES)
    moved = moved or {}
    nodes = [{"name": "Armature", "children": []}]
    index = {}
    for name, parent, t in bones:
        t = [a + b for a, b in zip(t, moved.get(name, (0.0, 0.0, 0.0)))]
        index[name] = len(nodes)
        nodes.append({"name": name, "translation": t, "rotation": [0.0, 0.0, 0.0, 1.0]})
        (nodes[index[parent]] if parent else nodes[0]).setdefault("children", []).append(index[name])
    if attached:
        nodes.append({"name": "item_ref", "translation": [0.0, 0.0, 0.0]})
        nodes[index["Hips"]].setdefault("children", []).append(len(nodes) - 1)
    # Inverse bind matrices: translations only (the joints' world positions negated).
    world = {}
    for name, parent, t in bones:
        t = [a + b for a, b in zip(t, moved.get(name, (0.0, 0.0, 0.0)))]
        base = world[parent] if parent else (0.0, 0.0, 0.0)
        world[name] = tuple(a + b for a, b in zip(base, t))
    binary = b""
    for name, _, _ in bones:
        x, y, z = world[name]
        binary += struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -x, -y, -z, 1)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": len(bones), "type": "MAT4"}]
    doc = {"asset": {"version": "2.0"}, "nodes": nodes, "scenes": [{"nodes": [0]}], "scene": 0,
           "skins": [{"joints": [index[b[0]] for b in bones], "inverseBindMatrices": 0}],
           "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": 0}]}
    if animation:
        t = b"".join(struct.pack("<f", x) for x in animation["times"])
        r = b"".join(struct.pack("<4f", *q) for q in animation["rotations"])
        views += [{"buffer": 0, "byteOffset": len(binary), "byteLength": len(t)},
                  {"buffer": 0, "byteOffset": len(binary) + len(t), "byteLength": len(r)}]
        accessors += [{"bufferView": 1, "componentType": 5126, "count": len(animation["times"]), "type": "SCALAR"},
                      {"bufferView": 2, "componentType": 5126, "count": len(animation["rotations"]), "type": "VEC4"}]
        binary += t + r
        doc["animations"] = [{"name": animation["name"], "samplers": [{"input": 1, "output": 2}],
                              "channels": [{"sampler": 0, "target": {"node": index[animation["node"]],
                                                                    "path": "rotation"}}]}]
    doc["buffers"][0]["byteLength"] = len(binary)
    write_glb(path, doc, binary)
    return path


def stub_body(folder, name="body", height=1.8, roles=None):
    """A body as the order check reads it: <folder>/<name>.glb (a skeleton) and model.json."""
    glb = skeleton_glb(os.path.join(folder, name + ".glb"))
    with open(os.path.join(folder, "model.json"), "w", encoding="utf-8") as f:
        json.dump({"format": "forge-humanoid", "version": 1, "name": name, "height": height,
                   "bones": dict(ROLE_NAMES if roles is None else roles)}, f)
    return glb
