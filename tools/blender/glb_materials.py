"""What the material of a written GLB holds, read from the file itself (its JSON chunk and the
bytes of its images), not from a Blender scene: per texture slot whether it is present, the
image's format, its size and whether it has an alpha channel. No bpy: any Python runs it.

  material_slots(path) -> {slot: {"present", "format", "size", "alpha", "lossless", "image"}}

Slots: base_color, normal, metallic_roughness, occlusion, emissive and opacity (glTF keeps
opacity in the base colour's alpha channel: present when the material's alphaMode is BLEND
or MASK and the base colour image has an alpha channel).
"""
import base64
import json
import os
import struct

SLOTS = ("base_color", "normal", "metallic_roughness", "occlusion", "emissive", "opacity")
# glTF material keys of the slots that are textures of their own.
TEXTURE_KEYS = {"base_color": ("pbrMetallicRoughness", "baseColorTexture"), "normal": (None, "normalTexture"),
                "metallic_roughness": ("pbrMetallicRoughness", "metallicRoughnessTexture"),
                "occlusion": (None, "occlusionTexture"), "emissive": (None, "emissiveTexture")}
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942


def read_glb(path):
    """(the JSON document, the binary chunk or None) of a GLB file."""
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("%s is not a GLB file" % path)
    doc, binary, at = None, None, 12
    while at + 8 <= min(length, len(data)):
        size, kind = struct.unpack_from("<II", data, at)
        chunk = data[at + 8:at + 8 + size]
        if kind == JSON_CHUNK:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == BIN_CHUNK and binary is None:
            binary = chunk
        at += 8 + size  # chunk lengths include their padding
    if doc is None:
        raise ValueError("%s has no JSON chunk" % path)
    return doc, binary


def image_data(doc, binary, index, folder=""):
    """The bytes of image `index` (a buffer view of the binary chunk, a data URI or a file
    next to the GLB), or None."""
    images = doc.get("images") or []
    if not 0 <= index < len(images):
        return None
    im = images[index]
    if "bufferView" in im:
        bv = (doc.get("bufferViews") or [])[im["bufferView"]]
        if bv.get("buffer", 0) != 0 or binary is None:
            return None
        start = bv.get("byteOffset", 0)
        return binary[start:start + bv["byteLength"]]
    uri = im.get("uri") or ""
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    path = os.path.join(folder, uri)
    if uri and os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def image_info(data):
    """{"format": png | jpeg | webp | unknown, "size": [w, h] or None, "alpha": bool or None,
    "lossless": bool} of an image's bytes."""
    if data is None:
        return {"format": None, "size": None, "alpha": None, "lossless": False}
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 26:
        w, h = struct.unpack_from(">II", data, 16)
        # Colour types 4 and 6 carry alpha; a tRNS chunk (before the image data) adds it.
        idat = data.find(b"IDAT")
        alpha = data[25] in (4, 6) or b"tRNS" in (data[:idat] if idat > 0 else data)
        return {"format": "png", "size": [w, h], "alpha": bool(alpha), "lossless": True}
    if data[:3] == b"\xff\xd8\xff":
        at, size = 2, None
        while at + 4 <= len(data):
            if data[at] != 0xFF:
                at += 1
                continue
            marker = data[at + 1]
            if marker == 0xFF:  # fill byte
                at += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                at += 2
                continue
            seg = struct.unpack_from(">H", data, at + 2)[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and at + 9 <= len(data):
                h, w = struct.unpack_from(">HH", data, at + 5)
                size = [w, h]
                break
            at += 2 + seg
        return {"format": "jpeg", "size": size, "alpha": False, "lossless": False}
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        size, alpha, lossless = None, False, kind == b"VP8L"
        if kind == b"VP8X" and len(data) >= 30:
            alpha = bool(data[20] & 0x10)
            size = [1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")]
            lossless = b"VP8L" in data[30:]
        elif kind == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            size = [(bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1]
            alpha = bool((bits >> 28) & 1)
        elif kind == b"VP8 " and len(data) >= 30:
            size = [struct.unpack_from("<H", data, 26)[0] & 0x3FFF, struct.unpack_from("<H", data, 28)[0] & 0x3FFF]
        return {"format": "webp", "size": size, "alpha": alpha, "lossless": lossless}
    return {"format": "unknown", "size": None, "alpha": None, "lossless": False}


def body_material(doc, mesh_name="body"):
    """Index of the material of the mesh node named mesh_name (else of the first material
    a mesh uses, else 0), or None when the GLB has no material."""
    mats = doc.get("materials") or []
    if not mats:
        return None
    meshes = doc.get("meshes") or []
    for node in doc.get("nodes") or []:
        if node.get("name") == mesh_name and "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                if "material" in prim:
                    return prim["material"]
    for m in meshes:
        for prim in m.get("primitives", []):
            if "material" in prim:
                return prim["material"]
    return 0


def material_slots(path, mesh_name="body"):
    """{slot: record} of the body's material in the GLB at path (SLOTS, each present or
    not), plus "_material": its name and alphaMode."""
    doc, binary = read_glb(path)
    folder = os.path.dirname(os.path.abspath(path))
    index = body_material(doc, mesh_name)
    mat = (doc.get("materials") or [])[index] if index is not None else {}
    textures = doc.get("textures") or []
    out = {}
    for slot, (group, key) in TEXTURE_KEYS.items():
        ref = (mat.get(group) or {}).get(key) if group else mat.get(key)
        rec = {"present": False, "format": None, "size": None, "alpha": None, "lossless": False, "image": None}
        if isinstance(ref, dict) and 0 <= ref.get("index", -1) < len(textures):
            tex = textures[ref["index"]]
            src = tex.get("source")
            for ext in (tex.get("extensions") or {}).values():  # EXT_texture_webp and the like
                if isinstance(ext, dict) and "source" in ext:
                    src = ext["source"]
            if src is not None:
                info = image_info(image_data(doc, binary, src, folder))
                rec.update(info, present=info["format"] is not None,
                           image=(doc.get("images") or [])[src].get("name") or "image %d" % src)
        out[slot] = rec
    mode = mat.get("alphaMode", "OPAQUE")
    base = out["base_color"]
    opacity = {"present": False, "format": None, "size": None, "alpha": None, "lossless": False, "image": None,
               "alpha_mode": mode}
    if mode in ("BLEND", "MASK") and base["present"]:
        opacity.update({k: base[k] for k in ("format", "size", "alpha", "lossless", "image")})
        opacity["present"] = bool(base["alpha"])
    out["opacity"] = opacity
    out["_material"] = {"name": mat.get("name"), "alpha_mode": mode, "index": index}
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(material_slots(sys.argv[1]), indent=1))
