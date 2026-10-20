"""What glows on a painted texture: lights, visors, cores, energy veins and thrusters.

No bpy: NumPy and Pillow only, so that the exporters (in Blender's Python), the factory and
the glow command (python -m forge.glow) share it.

The painted base colour of a generated mesh carries its lights as colour only: a cyan seam,
an emerald thruster, a violet sigil are painted bright and saturated, next to skin, cloth
and plates that are not. A texel glows when:

- its hue lies in one of the glow colours (the piece's text names them: "glowing cyan
  seams", "a faint emerald glow"; colours_in_text), with a high enough value, chroma (max -
  min of its sRGB channels: pale skin, pearl-white plates and grey metal have little) and
  saturation, and it stands out: brighter than the texels around it, or much more colourful
  and not darker (the paint stage paints lights dim, but never dimmer than around them);
- it belongs to a small connected region, saturated through: a region of that colour larger
  than max_region of the painted texels is a painted panel, and one whose mean saturation is
  under region_saturation is a pastel patch seeded at its shaded edges, not a light. Regions
  grow by hysteresis: weak texels (value and chroma a little lower) count when they touch a
  strong one, so a light's soft rim comes with its core, and a white-hot core next to a
  coloured light is taken (core_value);
- with a reference picture (the cutout the paint stage projected): the colours the picture
  shows lit (bright and saturated in a glow colour, brighter than what is around them) are
  counted, and a texel whose colour the picture mostly shows lit glows more, one it never
  does less (reference_weight). The picture and the texture share colours, not places: the
  lookup is by colour, as the paint stage projects colour.

The result is a weight per texel (0..1). The emissive map is the texel's own colour at full
value times the weight (emissive_map), the regions mask's blue channel is the weight
(mask_blue), and write_emissive() puts the map into a GLB's emissive slot with a strength
(KHR_materials_emissive_strength) without Blender.
"""
import io
import json
import os
import re
import struct

import numpy as np

# Glow colours: hue ranges in degrees of the sRGB hue (0 red, 60 yellow, 120 green, 180 cyan,
# 240 blue, 300 magenta). Ranges keep apart from the painted colours next to which lights
# usually sit (cobalt and royal blue plates at 210-230, olive skin at 60-100, crimson and
# oxblood at 340-360); emerald reaches 183 because generated "emerald" lights come out teal.
COLOURS = {
    "red": (350.0, 12.0),
    "crimson": (348.0, 8.0),
    "scarlet": (350.0, 12.0),
    "orange": (8.0, 40.0),
    "amber": (28.0, 50.0),
    "gold": (38.0, 55.0),
    "yellow": (45.0, 66.0),
    "lime": (70.0, 105.0),
    "chartreuse": (70.0, 100.0),
    "green": (95.0, 165.0),
    "emerald": (125.0, 183.0),
    "jade": (130.0, 183.0),
    "teal": (160.0, 192.0),
    "turquoise": (163.0, 192.0),
    "aqua": (168.0, 198.0),
    "cyan": (168.0, 202.0),
    "azure": (195.0, 215.0),
    "blue": (200.0, 238.0),
    "indigo": (238.0, 262.0),
    "violet": (255.0, 292.0),
    "purple": (262.0, 300.0),
    "lavender": (255.0, 292.0),
    "magenta": (290.0, 335.0),
    "fuchsia": (295.0, 335.0),
    "pink": (310.0, 345.0),
}
# Words that make a colour next to them a glowing one.
GLOW_WORDS = ("glow", "glows", "glowing", "glowed", "aglow", "luminous", "luminescent", "bioluminescent",
              "bioluminescence", "neon", "emissive", "incandescent", "lights", "lit", "shining", "radiant")
# Words that end the phrase a glow word binds colours in.
STOP_WORDS = ("and", "with", "of", "in", "on", "at", "to", "from", "by", "for", "that", "which", "a", "an", "the",
              "over", "under", "around", "across", "between", "into", "while", "but")
NEGATIONS = ("no", "not", "never", "without", "nor")

# Defaults of the detection (sRGB values 0..1); the order's stage settings change some.
DEFAULTS = {
    "value": 0.42,        # strong texels: value (max channel) at least this
    "chroma": 0.30,       # strong texels: chroma (max - min) at least this
    "saturation": 0.5,    # strong texels: saturation (chroma / value) at least this (pale skin is not)
    "region_saturation": 0.42,  # a region's mean saturation at least this (a pastel patch is paint)
    "contrast": 0.08,     # strong texels: value at least this above the mean value around them
    "contrast_reach": 6,  # texels: the square around a texel its contrast is measured in
    "chroma_contrast": 0.25,  # or: chroma this far above the mean around it, value not below it (a light on pale skin)
    "weak_value": 0.32,   # weak texels (kept when they touch a strong one)
    "weak_chroma": 0.18,
    "core_value": 0.86,   # a white-hot texel next to a light (any chroma) is part of it
    "core_reach": 2,      # texels from a light where its white core is looked for
    "max_region": 0.006,  # largest region, as a share of the painted texels
    "min_region": 3,      # smallest region in texels (single noisy texels are not lights)
    "max_total": 0.12,    # if more than this share of the texture would glow, keep the brightest
    "strength": 2.0,      # emissive strength written to the GLB
    "reference_weight": 0.6,  # how much the reference picture can raise or lower a texel
}


# ---- the colours a text names as glowing --------------------------------------------------

def _words(text):
    return re.findall(r"[a-z]+", text.lower().replace("-", " "))


def colours_in_text(text):
    """The glow colours a piece's text names: a colour word bound to a glow word in one
    phrase ("glowing cyan seams", "glowing emerald or magenta lights", "a faint emerald glow",
    "orange-red glowing magma"). After a glow word the phrase runs up to four words to the next
    stop word; before it, back to the previous stop word. A glow word right after a negation
    ("no glowing parts") binds nothing. Returns the names in the order found."""
    found = []
    for clause in re.split(r"[.,;:()\[\]!?\n]", text or ""):
        words = _words(clause)
        for i, w in enumerate(words):
            if w not in GLOW_WORDS:
                continue
            if any(x in NEGATIONS for x in words[max(0, i - 2):i]):
                continue
            # forward
            for x in words[i + 1:i + 5]:
                if x in STOP_WORDS:
                    break
                if x in COLOURS and x not in found:
                    found.append(x)
            # backward
            for x in reversed(words[max(0, i - 4):i]):
                if x in STOP_WORDS:
                    break
                if x in COLOURS and x not in found:
                    found.append(x)
    return found


def parse_hues(spec):
    """Hue ranges from a list or a comma-separated text of colour names (COLOURS) and
    "low-high" degree ranges. Raises ValueError on an unknown name."""
    items = spec if isinstance(spec, (list, tuple)) else [s for s in str(spec or "").split(",")]
    out = []
    for item in items:
        item = str(item).strip().lower()
        if not item:
            continue
        if item in COLOURS:
            out.append(COLOURS[item])
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", item)
        if not m:
            raise ValueError("unknown glow colour %r (names: %s, or low-high degrees)" % (item, ", ".join(sorted(COLOURS))))
        out.append((float(m.group(1)) % 360.0, float(m.group(2)) % 360.0))
    return out


def hue_problem(spec):
    """None when spec is a valid glow colour list, else what is wrong."""
    try:
        parse_hues(spec)
    except ValueError as e:
        return str(e)
    return None


# ---- colour measures ----------------------------------------------------------------------

def as_float_rgb(image):
    """An H x W x 3 float32 array of sRGB values 0..1 from a Pillow image or an array (uint8 or
    float 0..1)."""
    if hasattr(image, "convert"):
        image = np.asarray(image.convert("RGB"))
    a = np.asarray(image)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    return np.ascontiguousarray(a[..., :3], dtype=np.float32)


def hue_chroma_value(rgb):
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    c = mx - mn
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.degrees(np.arctan2(np.sqrt(3.0) * (g - b), 2.0 * r - g - b)) % 360.0
    return h.astype(np.float32), c, mx


def in_hues(h, bands):
    out = np.zeros(h.shape, bool)
    for lo, hi in bands:
        out |= ((h >= lo) & (h <= hi)) if lo <= hi else ((h >= lo) | (h <= hi))
    return out


def box_blur(x, r):
    """Mean over a (2r+1)^2 box, edges clamped (integral image)."""
    if r <= 0:
        return x.astype(np.float32)
    p = np.pad(x.astype(np.float64), r + 1, mode="edge")
    s = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    h, w = x.shape
    tot = s[k:k + h, k:k + w] - s[0:h, k:k + w] - s[k:k + h, 0:w] + s[0:h, 0:w]
    return (tot / (k * k)).astype(np.float32)


def dilate(mask, r):
    """Binary dilation by a (2r+1)^2 square."""
    if r <= 0:
        return mask.copy()
    return box_blur(mask.astype(np.float32), r) > 1e-6


# ---- connected regions (8-connected), without SciPy ---------------------------------------

def label(mask):
    """(labels, sizes): labels 0 for background and 1..n for the 8-connected regions of a
    boolean mask; sizes[k] is the texel count of region k (sizes[0] = 0). Union-find over the
    runs of each row."""
    h, w = mask.shape
    m = np.zeros((h, w + 2), np.int8)
    m[:, 1:-1] = mask
    d = np.diff(m, axis=1)
    rows_s, cols_s = np.nonzero(d == 1)
    rows_e, cols_e = np.nonzero(d == -1)
    n = len(rows_s)
    labels = np.zeros((h, w), np.int32)
    if n == 0:
        return labels, np.zeros(1, np.int64)
    # runs are in row-major order from np.nonzero; start s, end e (exclusive)
    starts, ends, rows = cols_s, cols_e, rows_s
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    row_first = np.searchsorted(rows, np.arange(h + 1))
    for y in range(1, h):
        a0, a1 = row_first[y - 1], row_first[y]
        b0, b1 = row_first[y], row_first[y + 1]
        if a0 == a1 or b0 == b1:
            continue
        i, j = a0, b0
        while i < a1 and j < b1:
            # 8-connected: runs overlap when [s-1, e] intersect
            if starts[i] <= ends[j] and starts[j] <= ends[i]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
            if ends[i] < ends[j]:
                i += 1
            else:
                j += 1
    roots = np.array([find(k) for k in range(n)])
    uniq, run_label = np.unique(roots, return_inverse=True)
    run_label = run_label + 1
    lengths = ends - starts
    sizes = np.zeros(len(uniq) + 1, np.int64)
    np.add.at(sizes, run_label, lengths)
    for k in range(n):
        labels[rows[k], starts[k]:ends[k]] = run_label[k]
    return labels, sizes


# ---- the reference picture ----------------------------------------------------------------

HUE_BINS, SAT_BINS = 36, 8


def _bins(rgb):
    """Colour bins by hue and saturation only: the paint stage takes the light out of the
    picture it projects (the texture's lights are darker than the picture's), so values do
    not match between the two, hues and saturations do."""
    h, c, v = hue_chroma_value(rgb)
    sat = c / np.maximum(v, 1e-4)
    hb = np.clip((h / 360.0 * HUE_BINS).astype(np.int64), 0, HUE_BINS - 1)
    sb = np.clip((sat * SAT_BINS).astype(np.int64), 0, SAT_BINS - 1)
    return hb * SAT_BINS + sb


def _smooth_bins(vol):
    """A mean over each bin and its neighbours (hue wraps, saturation clamped)."""
    vol = vol.reshape(HUE_BINS, SAT_BINS)
    p = np.pad(vol, ((0, 0), (1, 1)), mode="edge")
    out = np.zeros_like(vol)
    for dh in (-1, 0, 1):
        for ds in range(3):
            out += np.roll(p, dh, axis=0)[:, ds:ds + SAT_BINS]
    return (out / 9.0).ravel()


def reference_lit(ref_rgba, bands, settings=None):
    """The texels of a reference picture (RGBA, the figure cut out) that show a light: in a
    glow colour, bright and saturated, and brighter than the figure around them."""
    s = dict(DEFAULTS, **(settings or {}))
    a = np.asarray(ref_rgba.convert("RGBA") if hasattr(ref_rgba, "convert") else ref_rgba)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    rgb, alpha = a[..., :3], (a[..., 3] if a.shape[-1] == 4 else np.ones(a.shape[:2], np.float32))
    solid = alpha > 0.5
    h, c, v = hue_chroma_value(rgb)
    around = box_blur(np.where(solid, v, 0.0), 12) / np.maximum(box_blur(solid.astype(np.float32), 12), 1e-3)
    lit = solid & in_hues(h, bands) & (v >= 0.6) & (c >= 0.3) & (v >= around + 0.08)
    return lit, solid, rgb


def reference_chance(ref_rgba, bands, settings=None):
    """A lookup from colour (hue and saturation) to the share of the reference's texels of that
    colour that are lit (0..1), and the number of lit texels: (function(rgb) -> chance,
    lit_count)."""
    lit, solid, rgb = reference_lit(ref_rgba, bands, settings)
    bins = _bins(rgb)
    n = HUE_BINS * SAT_BINS
    total = _smooth_bins(np.bincount(bins[solid], minlength=n).astype(np.float64))
    lights = _smooth_bins(np.bincount(bins[lit], minlength=n).astype(np.float64))
    chance = np.where(total > 1e-3, lights / np.maximum(total, 1e-9), 0.0)

    def lookup(texture_rgb):
        return chance[_bins(texture_rgb)].astype(np.float32)
    return lookup, int(lit.sum())


# ---- the detection ------------------------------------------------------------------------

def _smoothstep(lo, hi, x):
    t = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def detect(albedo, hues, exclude=None, coverage=None, reference=None, settings=None):
    """(weight, report): the glow weight per texel (float32 0..1) of a painted texture and what
    was found. albedo: H x W x 3 sRGB (uint8 or float 0..1, or a Pillow image); hues: colour
    names or ranges (parse_hues); exclude: texels that never glow (skin, hair); coverage: the
    painted texels (default: every texel that is not pure black); reference: the reference
    picture (Pillow RGBA or an array) or None; settings: DEFAULTS overrides."""
    s = dict(DEFAULTS, **{k: v for k, v in (settings or {}).items() if v is not None})
    bands = parse_hues(hues)
    rgb = as_float_rgb(albedo)
    hgt, wid = rgb.shape[:2]
    report = {"hues": [list(b) for b in bands], "settings": {k: s[k] for k in sorted(DEFAULTS)},
              "glow_pixels": 0, "glow_share": 0.0, "regions": 0, "regions_too_large": 0, "reference": None}
    weight = np.zeros((hgt, wid), np.float32)
    if not bands:
        report["note"] = "no glow colour"
        return weight, report
    h, c, v = hue_chroma_value(rgb)
    if coverage is None:
        coverage = v > 0.02
    painted = max(int(coverage.sum()), 1)
    allowed = coverage if exclude is None else (coverage & ~exclude)
    band = in_hues(h, bands)
    around = box_blur(v, int(s["contrast_reach"]))
    around_c = box_blur(c, int(s["contrast_reach"]))
    # Brighter than around it, or much more colourful and not darker (a light on pale skin).
    stands_out = (v - around >= s["contrast"]) | ((c - around_c >= s["chroma_contrast"]) & (v >= around - 0.02))
    strong = (allowed & band & (v >= s["value"]) & (c >= s["chroma"]) & (c >= s["saturation"] * v) & stands_out)
    weak = allowed & band & (v >= s["weak_value"]) & (c >= s["weak_chroma"])
    weak |= strong
    labels, sizes = label(weak)
    has_strong = np.zeros(len(sizes), bool)
    has_strong[np.unique(labels[strong])] = True
    has_strong[0] = False
    max_px = max(int(s["max_region"] * painted), s["min_region"])
    # A region's mean saturation: a light is saturated through, a pastel patch (lavender skin,
    # a pale plate) seeded only at its edges is not.
    sat = c / np.maximum(v, 1e-4)
    mean_sat = np.bincount(labels.ravel(), weights=sat.ravel(), minlength=len(sizes)) / np.maximum(sizes, 1)
    keep = has_strong & (sizes >= s["min_region"]) & (sizes <= max_px) & (mean_sat >= s["region_saturation"])
    report["regions_pale"] = int((has_strong & (sizes <= max_px) & (mean_sat < s["region_saturation"])).sum())
    report["regions"] = int(keep.sum())
    report["regions_too_large"] = int((has_strong & (sizes > max_px)).sum())
    report["largest_region_share"] = round(float(sizes[keep].max()) / painted, 6) if keep.any() else 0.0
    lights = keep[labels]
    # The light's white-hot core: very bright texels right next to it.
    if s["core_reach"] > 0 and lights.any():
        near = dilate(lights, int(s["core_reach"]))
        lights |= near & allowed & (v >= s["core_value"])
    # Soft weight: how far over the weak thresholds the texel is.
    soft = np.minimum(_smoothstep(s["weak_value"], s["value"] + 0.08, v),
                      _smoothstep(s["weak_chroma"], s["chroma"] + 0.06, c))
    soft = np.where(v >= s["core_value"], 1.0, soft)
    # A texel of a light glows at least at half its strength (a pale line is a light too); the
    # texels well over the thresholds glow fully.
    weight = np.where(lights, 0.55 + 0.45 * soft, 0.0).astype(np.float32)
    if reference is not None:
        lookup, lit = reference_chance(reference, bands, s)
        report["reference"] = {"lit_pixels": lit}
        if lit > 0:
            chance = lookup(rgb)
            # chance 0: the weight times (1 - reference_weight); chance 0.5 or more: times
            # (1 + reference_weight), at most 1.
            rw = float(s["reference_weight"])
            factor = 1.0 - rw + 2.0 * rw * np.clip(chance * 2.0, 0.0, 1.0)
            weight = np.clip(weight * factor, 0.0, 1.0).astype(np.float32)
            report["reference"]["supported"] = float(((chance >= 0.25) & lights).sum()) / max(int(lights.sum()), 1)
    total = float((weight > 0.5).sum()) / painted
    if total > s["max_total"]:
        # Too much would glow: keep the brightest texels up to max_total.
        cut = np.quantile(weight[weight > 0], 1.0 - s["max_total"] / max(total, 1e-9))
        weight = np.where(weight >= cut, weight, 0.0).astype(np.float32)
        report["capped"] = True
    report["glow_pixels"] = int((weight > 0.5).sum())
    report["glow_share"] = round(report["glow_pixels"] / painted, 6)
    return weight, report


def emissive_map(albedo, weight):
    """The emissive colour per texel (uint8 RGB): the texel's own colour brought to full value,
    times its weight (black where nothing glows)."""
    rgb = as_float_rgb(albedo)
    mx = np.maximum(rgb.max(-1, keepdims=True), 1e-4)
    # A white-hot core stays white, a dim teal line becomes a teal light.
    out = rgb / mx * weight[..., None]
    return np.clip(np.round(out * 255.0), 0, 255).astype(np.uint8)


def mask_blue(weight):
    return np.clip(np.round(weight * 255.0), 0, 255).astype(np.uint8)


# ---- the emissive slot of a GLB, without Blender ------------------------------------------

def _read_glb(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("%s is not a GLB file" % path)
    off, doc, binary = 12, None, b""
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8:off + 8 + clen]
        if ctype == 0x4E4F534A:
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            binary = chunk
        off += 8 + clen
    return doc, binary


def _write_glb(path, doc, binary):
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(js) + (8 + len(binary) if binary else 0)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        if binary:
            f.write(struct.pack("<II", len(binary), 0x004E4942))
            f.write(binary)


def glb_image_bytes(doc, binary, image_index):
    img = doc["images"][image_index]
    uri = img.get("uri", "")
    if "bufferView" not in img and uri.startswith("data:") and ";base64," in uri:
        import base64
        return base64.b64decode(uri.split(";base64,", 1)[1])
    if "bufferView" not in img:
        raise ValueError("image %d is not inside the GLB" % image_index)
    bv = doc["bufferViews"][img["bufferView"]]
    start = bv.get("byteOffset", 0)
    return binary[start:start + bv["byteLength"]]


def base_colour_images(doc):
    """{material index: image index} of the materials with a base-colour texture."""
    out = {}
    for mi, mat in enumerate(doc.get("materials", [])):
        tex = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if tex is None:
            continue
        src = doc["textures"][tex["index"]].get("source")
        if src is not None:
            out[mi] = src
    return out


def read_glb_base_colours(path):
    """{material index: Pillow image} of a GLB's base-colour textures."""
    from PIL import Image
    doc, binary = _read_glb(path)
    images = {}
    for mi, ii in base_colour_images(doc).items():
        images[mi] = Image.open(io.BytesIO(glb_image_bytes(doc, binary, ii)))
    return images


def write_emissive(src, dst, emissive, strength=DEFAULTS["strength"], name="emissive_glow"):
    """Write a copy of the GLB src to dst with each given material's emissive slot set to its
    map. emissive: {material index: H x W x 3 uint8 array, or None to clear the slot}. The map
    goes in as a PNG; emissiveFactor is white and the strength is the material's
    KHR_materials_emissive_strength (left out at 1). The binary chunk is rebuilt view by view
    (every view kept in its place and index; a replaced emissive image's view takes the new
    bytes when nothing else uses that image). Returns {material index: image index}."""
    from PIL import Image
    doc, binary = _read_glb(src)
    if len(doc.get("buffers", [])) > 1 or any(bv.get("buffer", 0) != 0 for bv in doc.get("bufferViews", [])):
        raise ValueError("%s: a GLB with more than one buffer is not supported" % src)
    views = doc.setdefault("bufferViews", [])
    images = doc.setdefault("images", [])
    textures = doc.setdefault("textures", [])
    contents = [binary[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]] for bv in views]
    used = {}
    for mat in doc.get("materials", []):
        for key, sub in (("baseColorTexture", "pbrMetallicRoughness"), ("metallicRoughnessTexture", "pbrMetallicRoughness"),
                         ("normalTexture", None), ("occlusionTexture", None), ("emissiveTexture", None)):
            t = (mat.get(sub) or {}).get(key) if sub else mat.get(key)
            if t is not None:
                ii = textures[t["index"]].get("source")
                used[ii] = used.get(ii, 0) + 1
    written = {}
    for mi, emap in sorted(emissive.items()):
        mat = doc["materials"][mi]
        if emap is None:
            mat.pop("emissiveTexture", None)
            mat.pop("emissiveFactor", None)
            (mat.get("extensions") or {}).pop("KHR_materials_emissive_strength", None)
            continue
        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(emap[..., :3]), "RGB").save(buf, format="PNG", optimize=True)
        png = buf.getvalue()
        old = mat.get("emissiveTexture")
        old_image = textures[old["index"]].get("source") if old else None
        if old is not None and old_image is not None and used.get(old_image) == 1 and "bufferView" in images[old_image]:
            contents[images[old_image]["bufferView"]] = png
            images[old_image].update({"mimeType": "image/png", "name": "%s_%d" % (name, mi)})
            images[old_image].pop("uri", None)
            ii = old_image
        else:
            views.append({"buffer": 0, "byteLength": len(png)})
            contents.append(png)
            images.append({"bufferView": len(views) - 1, "mimeType": "image/png", "name": "%s_%d" % (name, mi)})
            ii = len(images) - 1
            base = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            tex = {"source": ii}
            if base is not None and "sampler" in textures[base["index"]]:
                tex["sampler"] = textures[base["index"]]["sampler"]
            textures.append(tex)
            entry = {"index": len(textures) - 1}
            if base is not None and "texCoord" in base:
                entry["texCoord"] = base["texCoord"]
            mat["emissiveTexture"] = entry
        mat["emissiveFactor"] = [1.0, 1.0, 1.0]
        ext = mat.setdefault("extensions", {})
        if abs(float(strength) - 1.0) > 1e-6:
            ext["KHR_materials_emissive_strength"] = {"emissiveStrength": float(strength)}
        else:
            ext.pop("KHR_materials_emissive_strength", None)
        if not ext:
            mat.pop("extensions", None)
        written[mi] = ii
    if any("KHR_materials_emissive_strength" in (m.get("extensions") or {}) for m in doc.get("materials", [])):
        eu = doc.setdefault("extensionsUsed", [])
        if "KHR_materials_emissive_strength" not in eu:
            eu.append("KHR_materials_emissive_strength")
    elif "KHR_materials_emissive_strength" in doc.get("extensionsUsed", []):
        doc["extensionsUsed"].remove("KHR_materials_emissive_strength")
        if not doc["extensionsUsed"]:
            doc.pop("extensionsUsed")
    out = bytearray()
    for bv, data in zip(views, contents):
        out += b"\0" * ((4 - len(out) % 4) % 4)
        bv["byteOffset"] = len(out)
        bv["byteLength"] = len(data)
        bv["buffer"] = 0
        out += data
    if doc.get("buffers"):
        doc["buffers"][0]["byteLength"] = len(out)
        doc["buffers"][0].pop("uri", None)
    elif out:
        doc["buffers"] = [{"byteLength": len(out)}]
    _write_glb(dst, doc, bytes(out))
    return written


GLOW_IMAGE = "emissive_glow"


def glow_files(glb, colours, out_glb=None, reference=None, regions=None, regions_out=None, mask_out=None,
               emissive_out=None, settings=None, strength=None, keep_other_emissive=True):
    """The glow of a GLB's base-colour texture written to files: the GLB (out_glb, default in
    place) with the emissive map in each material's emissive slot, the regions mask with the
    weight in its blue channel (regions_out: a copy of regions, whose red skin and green hair
    never glow; without regions a new mask, red and green 0), a grey mask (mask_out) and the
    map (emissive_out). colours: names or ranges ([] clears the glow); reference: the
    reference picture's path or None. A material whose emissive slot holds a map this module
    did not write keeps it when keep_other_emissive (its mask still gets the weight).
    Returns the report: per material the detection's report, and glow_pixels, glow_share,
    colours, strength, emissive (written, kept or none)."""
    from PIL import Image
    strength = float(DEFAULTS["strength"] if strength is None else strength)
    doc, binary = _read_glb(glb)
    bases = base_colour_images(doc)
    if not bases:
        raise ValueError("%s: no material with a base-colour texture" % glb)
    ref = Image.open(reference).convert("RGBA") if reference else None
    mask_src = np.asarray(Image.open(regions).convert("RGB")) if regions else None
    report = {"colours": list(colours or []), "strength": strength, "materials": {}, "emissive": {},
              "reference": os.path.basename(reference) if reference else None}
    emissive, first, cache = {}, None, {}
    for mi, ii in sorted(bases.items()):
        if ii not in cache:
            albedo = Image.open(io.BytesIO(glb_image_bytes(doc, binary, ii))).convert("RGB")
            exclude = None
            if mask_src is not None:
                m = mask_src
                if m.shape[:2] != (albedo.size[1], albedo.size[0]):
                    m = np.asarray(Image.fromarray(m).resize(albedo.size, Image.NEAREST))
                exclude = (m[..., 0] > 127) | (m[..., 1] > 127)
            weight, rep = detect(albedo, colours or [], exclude=exclude, reference=ref, settings=settings)
            cache[ii] = (albedo, weight, rep)
        albedo, weight, rep = cache[ii]
        report["materials"][str(mi)] = rep
        if first is None:
            first = cache[ii]
        mat = doc["materials"][mi]
        old = mat.get("emissiveTexture")
        old_name = doc["images"][doc["textures"][old["index"]]["source"]].get("name", "") if old else ""
        if old is not None and not old_name.startswith(GLOW_IMAGE) and keep_other_emissive:
            report["emissive"][str(mi)] = "kept (the material's own emissive map)"
            continue
        if rep["glow_pixels"]:
            emissive[mi] = emissive_map(albedo, weight)
            report["emissive"][str(mi)] = "written"
        else:
            emissive[mi] = None
            report["emissive"][str(mi)] = "none"
    earlier = any(
        doc["images"][doc["textures"][m["emissiveTexture"]["index"]]["source"]].get("name", "").startswith(GLOW_IMAGE)
        for m in doc.get("materials", []) if m.get("emissiveTexture"))
    if any(e is not None for e in emissive.values()) or earlier:
        write_emissive(glb, out_glb or glb, emissive, strength, name=GLOW_IMAGE)
    elif out_glb and os.path.abspath(out_glb) != os.path.abspath(glb):
        import shutil
        shutil.copyfile(glb, out_glb)
    albedo, weight, rep = first
    report["glow_pixels"] = int(sum(c[2]["glow_pixels"] for c in cache.values()))
    report["glow_share"] = rep["glow_share"]
    if emissive_out:
        Image.fromarray(emissive_map(albedo, weight), "RGB").save(emissive_out)
    blue = mask_blue(weight)
    if regions_out:
        if mask_src is not None:
            m = mask_src.copy()
            if blue.shape != m.shape[:2]:
                blue = np.asarray(Image.fromarray(blue).resize((m.shape[1], m.shape[0]), Image.BILINEAR))
        else:
            m = np.zeros(blue.shape + (3,), np.uint8)
        m[..., 2] = blue
        Image.fromarray(m, "RGB").save(regions_out)
    if mask_out:
        Image.fromarray(mask_blue(weight), "L").save(mask_out)
    return report


def glow_record(report):
    """The keys of a model record's "regions" a glow report gives."""
    return {"glow_pixels": int(report["glow_pixels"]), "glow_share": report["glow_share"],
            "glow_colours": list(report["colours"]), "glow_hues": [list(b) for b in parse_hues(report["colours"])],
            "emissive_strength": report["strength"] if report["glow_pixels"] else None}
