"""Local steps of the iso_tile recipe (forge/tile_jobs.py), without the card: the guide of a
piece's picture, the border blend of a face, the sprite (the frames at the order's scale with
their checks), the review sheet of a piece and the set sheet of a family. Run by the factory
between passes (forge/local_steps.py); every formula is NumPy and Pillow, the geometry is
tools/blender/iso_grid.py.

Frames of a sprite (docs/conventions.md, "Iso tiles"): `<name>.png` (shaded: the albedo lit by
the order's light from the normals, the picture the game draws), `<name>_albedo.png` (unlit),
`<name>_normal.png` (the sprite's view: x right, y up or down as the grid says, z toward the
viewer) and `<name>_emissive.png` (what glows, when anything does); a piece with a variant grid
has `<name>_v<NN>*` per frame, NN = (y % gy) * gx + (x % gx).
"""

import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .factory_util import sha_file, sha_file_cached, slug, write_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools", "blender")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import glow_regions  # noqa: E402
import iso_grid as G  # noqa: E402

KINDS = ("tile_guide", "tile_blend", "tile_sprite", "tile_review", "tile_sheet", "tile_seamless")
CODE = [os.path.abspath(__file__), os.path.join(TOOLS, "iso_grid.py"), os.path.join(TOOLS, "glow_regions.py")]
GREY = 0.55
LAYOUT_FORMAT = "forge-tile-layout"
PARTS = ("floor", "west_wall", "north_wall", "object")
# The sprite's settings (forge/stages.py TILE_SPRITE_SETTINGS) and their defaults; a face's
# and an object's footprint limits differ.
DEFAULTS = {"max_overflow": None, "min_cover": None, "tolerance": 1.0, "max_seam": 2.5, "max_shading": 0.35,
            "normal_strength": 1.0, "exposure": "original", "light_key": 0.55, "light_fill": 0.15,
            "light_ambient": 0.5, "cavity": 0.6, "base_ao": 0.18, "glow_strength": 1.0,
            "saturation": 0.5, "sharpen": 0.4, "fit": "silhouette", "min_render_iou": 0.72,
            "mesh_albedo": "picture", "delight": 0.7}
FACE_LIMITS = {"max_overflow": 0.04, "min_cover": 0.85, "max_shading": 0.35}
# A face cut by its picture (a fence's rails) loses a little of its thin parts to the cut.
CUT_FACE_LIMITS = {"min_cover": 0.75}
# An object's picture has more light in it by nature (a canopy's darker underside): its limit is wider.
OBJECT_LIMITS = {"max_overflow": 0.08, "min_cover": 0.6, "max_shading": 0.6}
# The picture of an object fitted to the original's silhouette: the scales tried and the
# largest move (grid pixels).
FIT_SCALES = (0.8, 0.87, 0.93, 1.0, 1.07, 1.15, 1.25)
FIT_MOVE = 4.0
# The saturation gain's ceiling (the albedo's colours brought toward the original's).
MAX_SATURATION_GAIN = 1.4
# The levels an object's columns rise above the original's own top (a picture a little taller).
COLUMN_MARGIN = 0.1
# The texels a seamless material's cut is feathered over.
FEATHER = 6
# The shaded sprite's lights in the sprite's view (x right, y up, z toward the viewer): the key
# from the upper left and in front, the fill from the right.
KEY_LIGHT = np.array([-0.55, 0.62, 0.56])
FILL_LIGHT = np.array([0.75, 0.05, 0.66])
# The relief a face's detail gives its normals, in tile edges at normal_strength 1.
RELIEF = 0.012


def code_fingerprint():
    """{file: sha256} of the code of these steps: a changed formula redoes their jobs."""
    return {os.path.basename(p): sha_file_cached(p) for p in CODE}


# ---- images

def read_rgba(path):
    im = Image.open(path)
    return np.asarray(im.convert("RGBA")).astype(np.float64) / 255.0


def read_grey(path):
    return np.asarray(Image.open(path).convert("L")).astype(np.float64) / 255.0


def save_rgba(path, rgb, alpha=None):
    rgb = np.clip(np.round(np.asarray(rgb)[..., :3] * 255.0), 0, 255).astype(np.uint8)
    if alpha is None:
        Image.fromarray(rgb, "RGB").save(path)
        return path
    a = np.clip(np.round(np.asarray(alpha).reshape(rgb.shape[:2]) * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([rgb, a]), "RGBA").save(path)
    return path


def resize(arr, size, resample=Image.BICUBIC):
    """An array (H, W) or (H, W, C) float 0..1 resized to size [width, height]."""
    w, h = int(size[0]), int(size[1])
    if arr.shape[1] == w and arr.shape[0] == h:
        return arr
    if arr.ndim == 2:
        return np.asarray(Image.fromarray(arr.astype(np.float32), "F").resize((w, h), resample)).astype(np.float64)
    return np.stack([resize(arr[..., c], size, resample) for c in range(arr.shape[2])], axis=-1)


def box_blur(x, r, wrap=False):
    """A box blur of radius r (pixels) along both axes, three times (close to a Gaussian of
    sigma about r); wrap: the image repeats (a seamless texture)."""
    r = int(round(r))
    if r < 1:
        return x
    out = np.asarray(x, dtype=np.float64)
    for _ in range(3):
        for axis in (0, 1):
            n = out.shape[axis]
            if wrap:
                pad = np.concatenate([np.take(out, range(n - r, n), axis=axis), out,
                                      np.take(out, range(0, r), axis=axis)], axis=axis)
            else:
                pad = np.concatenate([np.repeat(np.take(out, [0], axis=axis), r, axis=axis), out,
                                      np.repeat(np.take(out, [n - 1], axis=axis), r, axis=axis)], axis=axis)
            c = np.cumsum(pad, axis=axis)
            zero = np.zeros_like(np.take(c, [0], axis=axis))
            c = np.concatenate([zero, c], axis=axis)
            hi = np.take(c, range(2 * r + 1, 2 * r + 1 + n), axis=axis)
            lo = np.take(c, range(0, n), axis=axis)
            out = (hi - lo) / (2 * r + 1)
    return out


def luminance(rgb):
    return rgb[..., :3] @ np.array([0.299, 0.587, 0.114])


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def bleed(rgb, alpha, passes=12):
    """Colours of the pixels a cut leaves partly or wholly transparent replaced by those of the
    opaque pixels nearest them (a picture's background never shows in a soft edge)."""
    solid = alpha >= 0.9
    if not solid.any():
        return rgb
    out = np.where(solid[..., None], rgb, 0.0)
    w = solid.astype(np.float64)
    for _ in range(passes):
        acc = box_blur(out * w[..., None], 1)
        cnt = box_blur(w, 1)
        fill = (w < 0.5) & (cnt > 1e-3)
        out = np.where(fill[..., None], acc / np.maximum(cnt[..., None], 1e-6), out)
        w = np.where(fill, 1.0, w)
    return np.where(solid[..., None], rgb, out)


def dilate(mask, r):
    """A boolean mask grown by r pixels (a square)."""
    r = int(round(r))
    if r < 1:
        return mask
    im = Image.fromarray((mask * 255).astype(np.uint8), "L")
    for _ in range(r):
        im = im.filter(ImageFilter.MaxFilter(3))
    return np.asarray(im) > 127


def inner_distance(mask, limit):
    """Per pixel of a boolean mask, how many pixels (up to limit) it lies inside."""
    im = Image.fromarray((mask * 255).astype(np.uint8), "L")
    out = np.zeros(mask.shape, np.float64)
    for _ in range(int(limit)):
        cur = np.asarray(im) > 127
        if not cur.any():
            break
        out += cur
        im = im.filter(ImageFilter.MinFilter(3))
    return out


# ---- inputs the order supplies

def sprite_problems(path, g):
    """Problems of a game's sprite: readable, the cell's size, with a transparent background
    and something opaque."""
    try:
        im = Image.open(path)
        im.load()
    except Exception as ex:
        return ["cannot be read as a picture (%s)" % ex]
    w, h = g["cell"]
    if im.size != (int(w), int(h)):
        return ["is %dx%d; the grid's cell is %dx%d (the sprite at its own size)" % (im.size + (int(w), int(h)))]
    a = np.asarray(im.convert("RGBA"))[..., 3]
    if a.max() == 0:
        return ["is fully transparent"]
    if "A" not in im.getbands() and "transparency" not in im.info:
        return ["has no transparency: the sprite's transparent pixels give its footprint"]
    return []


def read_layout(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def layout_problems(path, ids):
    """Problems of a set's map layout ({"format": "forge-tile-layout", "maps": [{"name",
    "tiles": [{"at": [x, y, z], "floor": piece, ...}]}]}); pieces it names that the family
    does not have are skipped (the sheet lists them), not problems."""
    try:
        doc = read_layout(path)
    except (OSError, ValueError) as ex:
        return ["cannot be read (%s)" % ex]
    if not isinstance(doc, dict) or doc.get("format") != LAYOUT_FORMAT:
        return ["format must be %s" % LAYOUT_FORMAT]
    maps = doc.get("maps")
    if not isinstance(maps, list) or not maps:
        return ["maps must be a non-empty list"]
    out = []
    for i, m in enumerate(maps):
        tiles = m.get("tiles") if isinstance(m, dict) else None
        if not isinstance(tiles, list):
            out.append("maps[%d]: tiles must be a list" % i)
            continue
        for t in tiles:
            at = t.get("at") if isinstance(t, dict) else None
            if not (isinstance(at, list) and len(at) == 3 and all(isinstance(v, int) for v in at)):
                out.append("maps[%d]: every tile needs at [x, y, z] whole numbers" % i)
                break
            bad = [k for k in t if k not in PARTS + ("at",)]
            if bad:
                out.append("maps[%d]: unknown tile keys %s (known: at, %s)" % (i, bad, ", ".join(PARTS)))
                break
    return out


# ---- the factory's entry point

def run_local(factory, job, d, files):
    """Run one local step of the recipe; returns its input records (provenance)."""
    kind = job["kind"]
    inputs = []
    for name, ref in sorted((job.get("inputs") or {}).items()):
        if isinstance(ref, dict) and ("ref" in ref or "file" in ref) and (ref.get("file") is not None or "ref" in ref):
            inputs.append(dict(factory.input_record(ref), name=name))
    fn = {"tile_guide": step_guide, "tile_blend": step_blend, "tile_sprite": step_sprite,
          "tile_review": step_review, "tile_sheet": step_sheet, "tile_seamless": step_seamless}[kind]
    fn(factory, job, d, files)
    return inputs


def _path(factory, ref):
    return factory.resolve(ref)[0]


def _record(factory, files, role, path):
    files[role] = [os.path.relpath(path, factory.out), sha_file(path)]


def _settings(job, spec):
    s = dict(DEFAULTS)
    s.update(FACE_LIMITS if spec["kind"] != "object" else OBJECT_LIMITS)
    if spec["kind"] != "object" and spec.get("cutout") == "picture":
        s.update(CUT_FACE_LIMITS)
    s.update({k: v for k, v in (job.get("args") or {}).items() if k in DEFAULTS and v is not None})
    return s


# ---- guide

def face_tile_size(size, spec):
    """[width, height] texels of one tile of a face texture of size `size`."""
    along = _along(spec)
    return [int(round(size[0] / float(along[0]))), int(round(size[1] / float(along[1])))]


def _along(spec):
    gx, gy = spec["grid"]
    if spec["kind"] == "floor":
        return [gx, gy]
    return [gy if spec["kind"] == "west_wall" else gx, 1]


def face_alpha(a, radius):
    """The original face's alpha as a cut: transparent parts narrower than twice `radius`
    texels (a face's slanted edges and corner tips, which pixel art seldom covers) closed, the
    larger ones (the top of a low wall, a hole) kept, with a soft edge."""
    r = int(round(radius))
    clear = Image.fromarray(((a < 0.5) * 255).astype(np.uint8), "L")
    for _ in range(r):
        clear = clear.filter(ImageFilter.MinFilter(3))
    for _ in range(r):
        clear = clear.filter(ImageFilter.MaxFilter(3))
    keep = 1.0 - np.asarray(clear.filter(ImageFilter.GaussianBlur(max(radius / 3.0, 0.5)))).astype(np.float64) / 255
    return smoothstep(0.35, 0.65, keep)


def original_face(g, spec, sprite, size):
    """The original's face of one tile at size [w, h]: (straight RGB on grey where it is
    transparent, alpha)."""
    pre, a = G.unproject(g, spec["kind"], sprite, size, spec.get("thickness"))
    rgb = np.where(a[..., None] > 1e-3, pre / np.maximum(a[..., None], 1e-3), GREY)
    return rgb, a


def step_guide(factory, job, d, files):
    """The picture's guide (`<slug>_guide.png`) and its layout (`<slug>_layout.json`)."""
    args = job["args"]
    spec = args["tile"]
    g = G.grid_of(args.get("grid"))
    sprite = read_rgba(_path(factory, job["inputs"]["sprite"]))
    size = args["size"]
    blur_px = float(args.get("blur", 0.6))
    if spec["kind"] == "object":
        guide, layout = object_guide(g, spec, sprite, size, blur_px)
    else:
        material = read_rgba(_path(factory, job["inputs"]["material"]))[..., :3] if "material" in job["inputs"] else None
        guide, layout = face_guide(g, spec, sprite, size, blur_px, material, float(args.get("detail", 1.0)))
    p = os.path.join(d, slug(job["id"]) + "_guide.png")
    save_rgba(p, guide)
    _record(factory, files, "guide", p)
    lp = os.path.join(d, slug(job["id"]) + "_layout.json")
    write_json(lp, layout)
    _record(factory, files, "layout", lp)


def face_guide(g, spec, sprite, size, blur_px, material, detail):
    """A face's guide: the original's face (one tile, repeated over the piece's variant grid),
    upscaled and blurred, with the seam group's material detail over it, grey where the
    original's face is transparent."""
    tile = face_tile_size(size, spec)
    rgb, a = original_face(g, spec, sprite, tile)
    along = _along(spec)
    rgb = np.tile(rgb, (along[1], along[0], 1))
    a = np.tile(a, (along[1], along[0]))
    rgb = resize(rgb, size)
    a = resize(a, size)
    q = G.face_quad(g, spec["kind"], spec.get("thickness"))
    p00, du, dv = G._affine(g, q)
    texels_per_px = tile[0] / max(np.linalg.norm(du), 1e-6)
    low = box_blur(rgb, max(blur_px * texels_per_px, 1), wrap=True)
    if material is not None and detail > 0:
        m = resize(material, size)
        low = low + detail * (m - box_blur(m, tile[0] / 10.0, wrap=True))
    cover = smoothstep(0.2, 0.6, a)[..., None] if spec["cutout"] != "none" else 1.0
    guide = np.clip(low * cover + GREY * (1 - cover), 0, 1)
    return guide, {"kind": spec["kind"], "size": list(size), "tile": tile, "along": along,
                   "texels_per_pixel": round(float(texels_per_px), 3), "material": material is not None}


def compose_block(g, spec, sprite):
    """The original sprite drawn at every tile of the piece's variant grid on the block's
    canvas (grid pixels): (RGBA array, (x0, y0) of the canvas in tile (0, 0)'s cell)."""
    from .tile_jobs import block_canvas
    x0, y0, x1, y1 = block_canvas(g, spec)
    w, h = int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im = Image.fromarray(np.clip(np.round(sprite * 255), 0, 255).astype(np.uint8), "RGBA")
    gx, gy = spec["grid"]
    for i, j in sorted(((i, j) for i in range(gx) for j in range(gy)), key=lambda t: (t[0] + t[1], t[0])):
        sx, sy = G.screen_position(g, (i, j, 0))
        canvas.alpha_composite(im, (int(round(sx - x0)), int(round(sy - y0))))
    return np.asarray(canvas).astype(np.float64) / 255.0, (x0, y0)


def object_guide(g, spec, sprite, size, blur_px):
    """An object's guide: the original (a block of it in the variant grid) upscaled onto grey,
    centred with a margin; the layout says where the block's canvas lies in the picture."""
    block, (x0, y0) = compose_block(g, spec, sprite)
    h, w = block.shape[:2]
    pw, ph = size
    f = min(pw / (w * 1.2), ph / (h * 1.2))
    ox, oy = (pw - w * f) / 2.0, (ph - h * f) / 2.0
    up = Image.fromarray(np.clip(np.round(block * 255), 0, 255).astype(np.uint8), "RGBA").resize(
        (max(1, int(round(w * f))), max(1, int(round(h * f)))), Image.NEAREST)
    bg = Image.new("RGBA", (pw, ph), tuple(int(GREY * 255) for _ in range(3)) + (255,))
    bg.alpha_composite(up, (int(round(ox)), int(round(oy))))
    out = bg.convert("RGB")
    if blur_px > 0:
        out = out.filter(ImageFilter.GaussianBlur(blur_px * f))
    guide = np.asarray(out).astype(np.float64) / 255.0
    return guide, {"kind": "object", "size": [pw, ph], "canvas": [x0, y0, x0 + w, y0 + h], "f": f,
                   "offset": [ox, oy], "grid": list(spec["grid"])}


# ---- a seam group's seamless material

def _min_cut(cost):
    """Per row of a cost array (H, B), the column of the cheapest top-to-bottom path that
    moves at most one column per row: (columns (H,), mean cost along it)."""
    h, b = cost.shape
    acc = cost.copy()
    back = np.zeros((h, b), int)
    idx = np.arange(b)
    for y in range(1, h):
        prev = acc[y - 1]
        left = np.concatenate([[np.inf], prev[:-1]])
        right = np.concatenate([prev[1:], [np.inf]])
        stack = np.stack([left, prev, right])
        k = np.argmin(stack, axis=0)
        acc[y] += stack[k, idx]
        back[y] = idx + k - 1
    path = np.zeros(h, int)
    path[-1] = int(np.argmin(acc[-1]))
    for y in range(h - 1, 0, -1):
        path[y - 1] = back[y, path[y]]
    return path, float(acc[-1, path[-1]]) / h


def wrap_axis(tex, band=0.1, search=0.06, step=2):
    """A texture made to wrap along its columns (its right edge flowing into its left) without a
    painted cross: the texture is cut at a length L (its last `band` + up to `search` of its
    width searched for the L whose continuation, the columns from L on, matches its first columns
    best: a periodic pattern's phase), the first columns replaced by that continuation left of the
    cheapest seam through the overlap (a feathered min-error cut), and the result stretched back
    to the full width. A pattern (corrugation, courses, planks) keeps its ridges in phase; a
    natural texture gets an irregular seam instead of a blended band. Returns (tex, info)."""
    h, n = tex.shape[:2]
    b = max(4, int(round(band * n)))
    best = None
    lo = max(b, int(round(n - b - search * n)))
    for L in range(n - b, lo - 1, -step):
        cost = ((tex[:, L:L + b] - tex[:, 0:b]) ** 2).sum(-1)
        cost[:, -(FEATHER + 2):] = 1e3  # the seam stays off the overlap's far end (the feather reaches past it)
        path, c = _min_cut(cost)
        if best is None or c < best[1]:
            best = (L, c, path)
    L, c, path = best
    out = tex[:, :L].copy()
    cols = np.arange(b)[None, :]
    # Left of the seam: the continuation (so column 0 follows column L - 1); right of it: the
    # original. Feathered over FEATHER texels (a pattern in phase blends without cancelling).
    w = np.clip((path[:, None] - cols) / float(FEATHER) + 1.0, 0.0, 1.0)[..., None]
    out[:, :b] = tex[:, L:L + b] * w + tex[:, :b] * (1 - w)
    out = resize(out, [n, h], Image.LANCZOS)
    return np.clip(out, 0, 1), {"length": int(L), "stretch": round(n / float(L), 4), "cut_cost": round(c, 5)}


def make_seamless(tex, axes):
    """A texture wrapped along x (a wall's material, end to end) or along x and y (a floor's)."""
    info = {}
    out, info["x"] = wrap_axis(tex)
    if axes == "xy":
        t, info["y"] = wrap_axis(np.transpose(out, (1, 0, 2)))
        out = np.transpose(t, (1, 0, 2))
    return out, info


def group_colour(g, pieces, sprites, material, share):
    """A seam group's colour record: the saturation gain its pieces share (the material's mean
    saturation brought `share` of the way to the group's originals', only up) and the originals'
    mean brightness per kind (of the pieces that are the material itself, when there are any:
    the exposure every piece of that kind takes, so their shaded borders join)."""
    chromas, lums = [], {}
    for p, spr in zip(pieces, sprites):
        spec = {"kind": p["kind"], "thickness": p.get("thickness")}
        w = 64
        hgt = 64 if p["kind"] == "floor" else int(round(64 * g["wall_aspect"]))
        rgb, a = original_face(g, spec, spr, [w, hgt])
        m = a > 0.5
        if not m.any():
            continue
        chromas.append(saturation(rgb)[m])
        lums.setdefault(p["kind"], []).append((bool(p.get("material")), float(luminance(rgb)[m].mean())))
    ratio = 1.0
    if chromas:
        ratio = float(np.concatenate(chromas).mean()) / max(float(saturation(material).mean()), 1e-3)
    gain = float(np.clip(1.0 + share * (ratio - 1.0), 1.0, MAX_SATURATION_GAIN)) if share > 0 else 1.0
    target = {}
    for kind, vals in lums.items():
        own = [v for is_mat, v in vals if is_mat] or [v for _, v in vals]
        target[kind] = round(float(np.mean(own)), 5)
    return {"format": "forge-tile-group-colour", "saturation_gain": round(gain, 4), "original_luminance": target}


def step_seamless(factory, job, d, files):
    """A seam group's seamless material from its picked texture (wrap_axis per axis, no
    inpaint), a 2 x 2 preview and the group's colour record (`colour`)."""
    args = job["args"]
    g = G.grid_of(args.get("grid"))
    tex = read_rgba(_path(factory, job["inputs"]["material"]))[..., :3]
    size = args.get("size") or [tex.shape[1], tex.shape[0]]
    tex = resize(tex, size)
    out, info = make_seamless(tex, args.get("axes", "xy"))
    p = save_rgba(os.path.join(d, "%s.png" % job["asset"]), out)
    _record(factory, files, "rgb", p)
    pv = os.path.join(d, "%s_tile2x2.png" % job["asset"])
    prev = np.tile(out, (2, 2, 1))
    Image.fromarray(np.clip(np.round(prev * 255), 0, 255).astype(np.uint8)).resize(
        (1536, int(round(1536 * prev.shape[0] / float(prev.shape[1])))), Image.LANCZOS).save(pv)
    _record(factory, files, "preview", pv)
    pieces = args.get("pieces") or []
    sprites = [read_rgba(_path(factory, job["inputs"]["sprite_%02d" % k])) for k in range(len(pieces))]
    colour = group_colour(g, pieces, sprites, out, float(args.get("saturation", 0.5)))
    colour["wrap"] = info
    colour["seam"] = {"x": round(seam_ratio(out, 1), 3)}
    if args.get("axes", "xy") == "xy":
        colour["seam"]["y"] = round(seam_ratio(out, 0), 3)
    cp = os.path.join(d, "%s_colour.json" % job["asset"])
    write_json(cp, colour)
    _record(factory, files, "colour", cp)


# ---- the border blend of a face

def border_weight(spec, g, size, band):
    """(H, W) weight of the seam group's material at a face texture's border: 1 within 0.4 of
    the band (a share of a tile edge) from an edge, 0 past the band. A floor joins its
    neighbours on all four edges; a wall only at its ends (left and right): its top and foot
    keep the picture (a window near the top, the hedge's crown, the cap drawn from the top band)."""
    w, h = size
    along = _along(spec)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    du = np.minimum(u, 1 - u) * along[0]
    if spec["kind"] == "floor":
        dv = np.minimum(v, 1 - v) * along[1]
    else:
        dv = np.full(h, np.inf)
    dist = np.minimum(du[None, :], dv[:, None])
    return smoothstep(band, band * 0.4, dist), dist


def step_blend(factory, job, d, files):
    """The picture with its border band replaced by the seam group's material (`image`) and
    the ring the inpaint repaints (`mask`, white)."""
    args = job["args"]
    spec = args["tile"]
    g = G.grid_of(args.get("grid"))
    material = read_rgba(_path(factory, job["inputs"]["material"]))[..., :3]
    size = [material.shape[1], material.shape[0]]
    picture = resize(read_rgba(_path(factory, job["inputs"]["picture"]))[..., :3], size)
    band = float(args.get("band", 0.08))
    b, dist = border_weight(spec, g, size, band)
    image = picture * (1 - b[..., None]) + material * b[..., None]
    ring = smoothstep(band * 0.15, band * 0.3, dist) * smoothstep(band * 1.6, band * 1.2, dist)
    p1 = save_rgba(os.path.join(d, slug(job["id"]) + "_image.png"), image)
    p2 = save_rgba(os.path.join(d, slug(job["id"]) + "_mask.png"), np.repeat(ring[..., None], 3, -1))
    _record(factory, files, "image", p1)
    _record(factory, files, "mask", p2)


# ---- the sprite

def view_light(g, nv, s):
    """The shaded sprite's light per pixel of view normals nv (..., 3), 1 on a flat floor."""
    def lit(n):
        k = np.maximum(n @ (KEY_LIGHT / np.linalg.norm(KEY_LIGHT)), 0)
        f = np.maximum(n @ (FILL_LIGHT / np.linalg.norm(FILL_LIGHT)), 0)
        return s["light_ambient"] + s["light_key"] * k + s["light_fill"] * f
    floor = G.to_view(g, np.array([0.0, 0.0, 1.0]))
    return lit(nv) / max(float(lit(floor)), 1e-6)


def frames_of(spec):
    """[(index, i, j)] of a piece's variant grid: index (j * gx + i)."""
    gx, gy = spec["grid"]
    return [(j * gx + i, i, j) for j in range(gy) for i in range(gx)]


def frame_name(spec, index):
    gx, gy = spec["grid"]
    return spec["name"] if gx * gy == 1 else "%s_v%02d" % (spec["name"], index)


def texture_part(spec, arr, i, j):
    """The part of a face texture one frame of the variant grid shows (one tile)."""
    along = _along(spec)
    h, w = arr.shape[:2]
    tw, th = w // along[0], h // along[1]
    if spec["kind"] == "floor":
        col, row = i, j
    elif spec["kind"] == "west_wall":
        col, row = along[0] - 1 - j, 0  # a west wall's texture runs from the far (+y) tile to the near one
    else:
        col, row = i, 0
    return arr[row * th:(row + 1) * th, col * tw:(col + 1) * tw]


def original_in_canvas(g, sprite, rect, scale):
    """The original sprite's alpha and colour on a canvas (rect, grid pixels) at the art scale,
    nearest (H, W, 4)."""
    x0, y0, cw, ch = G.canvas(g, rect)
    w, h = int(round(cw * scale)), int(round(ch * scale))
    xs = np.floor(x0 + (np.arange(w) + 0.5) / scale).astype(int)
    ys = np.floor(y0 + (np.arange(h) + 0.5) / scale).astype(int)
    out = np.zeros((h, w, 4))
    sh, sw = sprite.shape[:2]
    okx, oky = (xs >= 0) & (xs < sw), (ys >= 0) & (ys < sh)
    sub = sprite[np.clip(ys, 0, sh - 1)][:, np.clip(xs, 0, sw - 1)]
    out[np.ix_(oky, okx)] = sub[np.ix_(oky, okx)]
    return out


def footprint(new_alpha, orig_alpha, extra_alpha, scale, tolerance):
    """(overflow, cover): the share of the new sprite's opaque pixels outside the original's
    silhouette (and the order's silhouette) widened by tolerance grid pixels, and the share of
    the original's silhouette the new sprite covers."""
    new = new_alpha > 0.5
    orig = orig_alpha > 0.5
    allowed = orig | (extra_alpha > 0.5 if extra_alpha is not None else False)
    allowed = dilate(allowed, tolerance * scale)
    n_new = int(new.sum())
    overflow = float((new & ~allowed).sum()) / n_new if n_new else 0.0
    n_orig = int(orig.sum())
    cover = float((new & orig).sum()) / n_orig if n_orig else 1.0
    return overflow, cover


def light_vector(lum, mask):
    """How the brightness rises across a region, as a share of its mean: the slopes [x, y] of
    a plane fitted to the luminance over the region (x and y from -0.5 to 0.5 across its box)."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 16:
        return np.zeros(2)
    x = (xs - xs.min()) / max(xs.max() - xs.min(), 1) - 0.5
    y = (ys - ys.min()) / max(ys.max() - ys.min(), 1) - 0.5
    a = np.stack([np.ones_like(x), x, y], axis=1)
    coef, *_ = np.linalg.lstsq(a, lum[ys, xs], rcond=None)
    return np.array([coef[1], coef[2]]) / max(coef[0], 1e-3)


def light_slope(lum, mask):
    """The size of light_vector: how much the brightness rises across a region."""
    return float(np.linalg.norm(light_vector(lum, mask)))


def painted_light(lum, mask, orig_lum, orig_mask):
    """(painted, original): the brightness slope of a new albedo that the original's own does
    not have (the size of their difference), and the original's slope. A tree's darker trunk, a
    crop's shaded foot or a yellow flashing along one edge are in the original too and are not
    painted light; a sun the picture added is."""
    new = light_vector(lum, mask)
    if orig_lum is None or orig_mask is None or int(orig_mask.sum()) < 16:
        return float(np.linalg.norm(new)), None
    old = light_vector(orig_lum, orig_mask)
    return float(np.linalg.norm(new - old)), float(np.linalg.norm(old))


def seam_ratio(tex, axis):
    """How many times more a texture's opposite edges (along axis 1: left and right) differ
    than neighbouring texel columns inside it."""
    if axis == 1:
        edge = np.abs(tex[:, 0] - tex[:, -1]).mean()
        inner = np.abs(tex[:, 1:] - tex[:, :-1]).mean()
    else:
        edge = np.abs(tex[0] - tex[-1]).mean()
        inner = np.abs(tex[1:] - tex[:-1]).mean()
    return float(edge / max(inner, 1e-4))


def step_sprite(factory, job, d, files):
    """The frames of one variant at the order's scale, its report (`mesh_report.json`: flags and
    measures) and its record (`tile.json`), in `<slug>/`."""
    args = job["args"]
    spec = args["tile"]
    g = G.grid_of(args.get("grid"))
    s = _settings(job, spec)
    out = os.path.join(d, slug(job["id"]))
    os.makedirs(out, exist_ok=True)
    for fn in os.listdir(out):
        if fn.endswith(".png") or fn.endswith(".json"):
            os.remove(os.path.join(out, fn))
    ins = job["inputs"]
    sprite = read_rgba(_path(factory, ins["sprite"]))
    silhouette = read_rgba(_path(factory, ins["silhouette"])) if "silhouette" in ins else None
    scale = int(args.get("scale", 4))
    group = None
    if "group_colour" in ins:
        with open(_path(factory, ins["group_colour"]), encoding="utf-8") as f:
            group = json.load(f)
    if spec["kind"] != "object":
        frames, measures, flags = face_frames(factory, job, g, spec, s, sprite, scale, out, files, group)
    else:
        frames, measures, flags = object_frames(factory, job, g, spec, s, sprite, scale, silhouette)
    material_lum = measures.pop("_material", None)
    original_lum = measures.pop("_original_lum", None)
    # Footprint: every frame against the original in its canvas (a block of objects: the whole
    # picture against the original's block, measured by object_frames).
    over, cover = [], []
    orig = original_in_canvas(g, sprite, spec.get("rect"), scale)
    extra = original_in_canvas(g, silhouette, spec.get("rect"), scale)[..., 3] if silhouette is not None else None
    for fr in frames:
        o, c = footprint(fr["alpha"][..., 0], orig[..., 3], extra, scale, s["tolerance"])
        over.append(o)
        cover.append(c)
        fr["original_lum"] = luminance(orig[..., :3])[orig[..., 3] > 0.5]
    if "overflow" not in measures:
        measures["overflow"] = round(max(over), 4)
        measures["cover"] = round(min(cover), 4)
    if measures["overflow"] > s["max_overflow"]:
        flags.append("footprint: %.1f%% of the sprite lies outside the original's silhouette (over %.1f%%)" % (
            100 * measures["overflow"], 100 * s["max_overflow"]))
    if measures["cover"] < s["min_cover"]:
        flags.append("footprint: the sprite covers %.0f%% of the original's silhouette (under %.0f%%)" % (
            100 * measures["cover"], 100 * s["min_cover"]))
    # Colour: the albedo's saturation brought part of the way toward the original's (a seam
    # group's pieces share their group's gain, so their borders still join), then its detail
    # sharpened at the frame's own scale.
    if group is not None:
        sat_gain = float(group.get("saturation_gain", 1.0))
    elif spec["kind"] != "object" and "material" in ins:
        sat_gain = 1.0  # a face of a group without the group's colour: a gain of its own would break the join
    else:
        sat_gain = saturation_gain([fr["albedo"] for fr in frames], [fr["alpha"][..., 0] for fr in frames],
                                   orig[..., :3], orig[..., 3], s["saturation"])
    measures["saturation_gain"] = round(sat_gain, 4)
    for fr in frames:
        fr["albedo"] = sharpen(saturate(fr["albedo"], sat_gain), fr["alpha"][..., 0], scale, s["sharpen"])
    # The shaded frames: the albedo lit from the normals; one exposure for the piece (a seam
    # group's faces of one kind: the group's, from its material and its pieces' originals).
    for fr in frames:
        fr["shaded"] = fr["albedo"] * (view_light(g, fr["view_normal"], s) * fr["shade"])[..., None]
    gain = 1.0
    if s["exposure"] == "original":
        group_gain = group_exposure(g, spec, s, group, material_lum)
        if group_gain is not None:
            gain = group_gain
        else:
            orig_lum = original_lum if original_lum is not None else np.concatenate(
                [fr["original_lum"] for fr in frames])
            new = np.concatenate([luminance(fr["shaded"])[fr["alpha"][..., 0] > 0.5] for fr in frames])
            if len(orig_lum) and len(new):
                gain = float(np.clip(orig_lum.mean() / max(new.mean(), 1e-3), 0.6, 1.6))
    measures["exposure_gain"] = round(gain, 4)
    # What glows: the colours the piece names (tile.glow, else its text's glowing colours).
    hues = spec.get("glow") or glow_regions.colours_in_text(args.get("text", "")) or None
    records = []
    roles = {}
    for fr in frames:
        name = frame_name(spec, fr["index"])
        alpha = fr["alpha"][..., 0]
        if spec.get("alpha", "binary") == "binary":
            alpha = (fr["centre"][..., 0] if "centre" in fr else alpha) >= 0.5
            alpha = alpha.astype(np.float64)
        shaded = np.clip(fr["shaded"] * gain, 0, 1) * (alpha[..., None] > 0)
        albedo = np.clip(fr["albedo"], 0, 1) * (alpha[..., None] > 0)
        rec = {"index": fr["index"], "at": [fr["i"], fr["j"]], "file": name + ".png", "albedo": name + "_albedo.png",
               "normal": name + "_normal.png", "emissive": None}
        save_rgba(os.path.join(out, rec["file"]), shaded, alpha)
        save_rgba(os.path.join(out, rec["albedo"]), albedo, alpha)
        save_rgba(os.path.join(out, rec["normal"]), G.encode_normal(g, fr["view_normal"]) / 255.0, alpha)
        if hues:
            weight, rep = glow_regions.detect(np.clip(np.round(albedo * 255), 0, 255).astype(np.uint8), hues,
                                              coverage=alpha > 0.5)
            if weight.max() > 0:
                em = glow_regions.emissive_map(albedo, weight).astype(np.float64) / 255.0 * s["glow_strength"]
                rec["emissive"] = name + "_emissive.png"
                save_rgba(os.path.join(out, rec["emissive"]), em, weight * (alpha > 0.5))
            rec["glow"] = {"pixels": rep["glow_pixels"], "regions": rep["regions"]}
        records.append(rec)
        key = "frame%02d" % fr["index"]
        for suffix, f in (("", rec["file"]), ("_albedo", rec["albedo"]), ("_normal", rec["normal"]),
                          ("_emissive", rec["emissive"])):
            if f:
                roles[key + suffix] = os.path.join(out, f)
    limits = {"overflow": s["max_overflow"], "seam": s["max_seam"], "shading": s["max_shading"]}
    excess = sum(measures[k] / limits[k] for k in limits if k in measures and limits[k]) + \
        max(0.0, 1.0 - measures["cover"]) / max(1.0 - s["min_cover"], 0.05)
    x0, y0, cw, ch = G.canvas(g, spec.get("rect"))
    tile = {"format": "forge-tile", "name": spec["name"], "kind": spec["kind"], "variant_grid": list(spec["grid"]),
            "scale": scale, "anchor": [x0, y0], "size": [int(round(cw * scale)), int(round(ch * scale))],
            "frames": records, "grid": {k: g[k] for k in G.GRID_KEYS if k in g}, "measures": measures,
            "settings": {k: s[k] for k in sorted(s)}, "form": args.get("form")}
    report = {"format": "forge-tile-report", "flags": flags, "measures": measures,
              "fit": {"excess": round(float(excess), 4)}}
    for role, p in sorted(roles.items()):
        _record(factory, files, role, p)
    _record(factory, files, "tile", write_json(os.path.join(out, "tile.json"), tile) or os.path.join(out, "tile.json"))
    _record(factory, files, "mesh_report", write_json(os.path.join(out, "mesh_report.json"), report) or
            os.path.join(out, "mesh_report.json"))


# ---- colour

def chroma(rgb):
    """Per pixel: the spread of its channels (0 for a grey)."""
    return rgb[..., :3].max(-1) - rgb[..., :3].min(-1)


def saturation(rgb):
    """Per pixel: its saturation (the spread of its channels over the largest; 0 for a grey),
    which does not change with the brightness (a darker original is not a duller one)."""
    return chroma(rgb) / np.maximum(rgb[..., :3].max(-1), 0.04)


def saturation_gain(albedos, alphas, orig_rgb, orig_alpha, share):
    """How much the albedo's colours are strengthened: `share` of the way from its mean
    saturation to the original's, only up, at most MAX_SATURATION_GAIN times (painterly realism
    toward the original's rich palette, not its flat colours)."""
    new = np.concatenate([saturation(a)[m > 0.5] for a, m in zip(albedos, alphas)]) if albedos else np.zeros(0)
    old = saturation(orig_rgb)[orig_alpha > 0.5]
    if not len(new) or not len(old) or share <= 0:
        return 1.0
    ratio = float(old.mean()) / max(float(new.mean()), 1e-3)
    return float(np.clip(1.0 + share * (ratio - 1.0), 1.0, MAX_SATURATION_GAIN))


def saturate(rgb, gain):
    """Colours spread from their own grey by `gain` (their brightness kept)."""
    if abs(gain - 1.0) < 1e-6:
        return rgb
    lum = luminance(rgb)[..., None]
    return np.clip(lum + (rgb[..., :3] - lum) * gain, 0.0, 1.0)


def sharpen(rgb, alpha, scale, amount):
    """An unsharp mask at the frame's own scale (radius a quarter of a grid pixel, at least one
    art pixel), weighted by the coverage so the background never bleeds in."""
    if amount <= 0:
        return rgb
    r = max(1.0, scale / 4.0)
    w = (alpha > 0.05).astype(np.float64)
    blur = box_blur(rgb * w[..., None], r) / np.maximum(box_blur(w, r)[..., None], 1e-6)
    return np.clip(rgb + amount * (rgb - blur) * w[..., None], 0.0, 1.0)


def face_light(g, spec, s):
    """The mean light of a flat face of this kind (relative to a flat floor) times a wall's
    mean foot darkening: the shaded sprite's mean brightness over its albedo's."""
    n = G.to_view(g, np.array(G.face_quad(g, spec["kind"], spec.get("thickness"))["normal"], float))
    light = float(view_light(g, n, s))
    if spec["kind"] != "floor":
        v = (np.arange(64) + 0.5) / 64
        light *= float((1 - s["base_ao"] * smoothstep(0.7, 1.0, v)).mean())
    return light


def group_exposure(g, spec, s, group, material_lum):
    """A seam group's face exposure: the group's originals of this side (floor or wall) against
    the group's material lit as this face, so every piece of the group and kind gets the same
    and their shaded borders join; None without the group's colour record."""
    if group is None or material_lum is None:
        return None
    side = "floor" if spec["kind"] == "floor" else "wall"
    target = (group.get("original_luminance") or {}).get(side)
    if not target:
        return None
    lit = material_lum * face_light(g, spec, s)
    return float(np.clip(target / max(lit, 1e-3), 0.6, 1.6))


# ---- faces

def relief_height(lum, tile_px, strength=1.0):
    """A face's relief from its albedo (-1 to 1): the fine detail (a grain, a leaf) plus the
    medium forms (a plank, a brick, a corrugation, a clump of leaves) at twice the weight, so a
    pattern larger than a few texels still reads in the normal map at the frame's scale."""
    fine = lum - box_blur(lum, tile_px / 24.0, wrap=True)
    medium = box_blur(lum, tile_px / 48.0, wrap=True) - box_blur(lum, tile_px / 8.0, wrap=True)
    fine = fine / max(3 * fine.std(), 1e-4)
    medium = medium / max(3 * medium.std(), 1e-4)
    return np.clip((fine + 2.0 * medium) / 3.0 * 1.5, -1, 1) * strength


def face_frames(factory, job, g, spec, s, sprite, scale, out, files, group=None):
    """The frames of a floor or wall: (frames, measures, flags)."""
    args = job["args"]
    ins = job["inputs"]
    tex = read_rgba(_path(factory, ins["texture"]))[..., :3]
    material = read_rgba(_path(factory, ins["material"]))[..., :3] if "material" in ins else None
    size = [material.shape[1], material.shape[0]] if material is not None else list(args["size"])
    tex = resize(tex, size)
    measures, flags = {}, []
    tile = face_tile_size(size, spec)
    along = _along(spec)
    relief = float(spec.get("relief", 1.0))
    b = None
    if material is not None:
        # The group's material owns the outer band exactly: every piece of the group joins there.
        b, _ = border_weight(spec, g, size, float(args.get("band") or 0.08) * 0.45)
        tex = tex * (1 - b[..., None]) + material * b[..., None]
        measures["border_difference"] = round(float(np.abs(tex[:, :1] - material[:, :1]).mean()), 5)
        # A floor joins on both axes; a wall only end to end.
        ratio = seam_ratio(tex, 1) if spec["kind"] != "floor" else max(seam_ratio(tex, 1), seam_ratio(tex, 0))
        measures["seam"] = round(ratio, 3)
        if ratio > s["max_seam"]:
            flags.append("seam: the texture's opposite edges differ %.1f times more than neighbouring texels "
                         "(over %.1f)" % (ratio, s["max_seam"]))
        if group is not None:
            measures["_material"] = float(luminance(saturate(material, float(group.get("saturation_gain", 1.0))))
                                          .mean())
    save_rgba(os.path.join(out, "texture.png"), tex)
    _record(factory, files, "texture", os.path.join(out, "texture.png"))
    # The face's alpha: the original's face, the picture's own cut, or the whole face.
    if spec["cutout"] == "original":
        _, a = original_face(g, spec, sprite, tile)
        q = G.face_quad(g, spec["kind"], spec.get("thickness"))
        per_px = tile[0] / max(np.linalg.norm(G._affine(g, q)[1]), 1e-6)
        alpha = face_alpha(resize(np.tile(a, (along[1], along[0])), size), 1.5 * per_px)
    elif spec["cutout"] == "picture" and "mask" in ins:
        alpha = smoothstep(0.35, 0.65, resize(read_grey(_path(factory, ins["mask"])), size))
    else:
        alpha = np.ones((size[1], size[0]))
    lum = luminance(tex)
    opaque = alpha > 0.5
    # Painted light: the face's brightness slope that the original's face does not have.
    orig_rgb, orig_a = original_face(g, spec, sprite, tile)
    orig_lum = resize(luminance(np.tile(orig_rgb, (along[1], along[0], 1))), size)
    orig_mask = resize(np.tile(orig_a, (along[1], along[0])), size) > 0.5
    blur_r = size[0] / 12.0
    slope, slope0 = painted_light(box_blur(lum, blur_r, wrap=True), opaque,
                                  box_blur(orig_lum, blur_r, wrap=True), orig_mask & opaque)
    measures["shading"] = round(slope, 4)
    if slope0 is not None:
        measures["shading_original"] = round(slope0, 4)
    if slope > s["max_shading"]:
        flags.append("shading: the albedo brightens by %.0f%% across the face (painted light, over %.0f%%)" % (
            100 * slope, 100 * s["max_shading"]))
    # Relief from the albedo's detail: normals, hollows (cavity) and a wall's darker foot. In a
    # group's band the relief is the material's own, so the borders' light joins too.
    height = relief_height(lum, tile[0], relief)
    if b is not None:
        height = height * (1 - b) + relief_height(luminance(material), tile[0], relief) * b
    normals = G.detail_normals(g, spec["kind"], height, RELIEF * s["normal_strength"], tile[0],
                               spec.get("thickness"))
    hollow = np.clip(box_blur(height, tile[0] / 64.0, wrap=True) - height, 0, 1)
    shade = 1 - s["cavity"] * 0.5 * hollow
    if spec["kind"] != "floor":
        v = (np.arange(size[1]) + 0.5) / size[1]
        shade = shade * (1 - s["base_ao"] * smoothstep(0.7, 1.0, v))[:, None]
    frames = []
    for index, i, j in frames_of(spec):
        layers = {"rgb": texture_part(spec, tex, i, j), "alpha": texture_part(spec, alpha, i, j)[..., None],
                  "normal": texture_part(spec, normals, i, j), "shade": texture_part(spec, shade, i, j)[..., None]}
        kw = dict(rect=spec.get("rect"), height=spec.get("height", 1.0), thickness=spec.get("thickness"))
        pr = G.project(g, spec["kind"], layers, scale, **kw)
        centre = G.project(g, spec["kind"], {"alpha": layers["alpha"]}, scale, supersample=1, **kw)["alpha"]
        nw = pr["normal"]
        nw = nw / np.maximum(np.linalg.norm(nw, axis=-1, keepdims=True), 1e-9)
        nv = G.to_view(g, nw)
        frames.append({"index": index, "i": i, "j": j, "albedo": pr["rgb"], "alpha": pr["alpha"], "centre": centre,
                       "view_normal": nv, "shade": pr["shade"][..., 0]})
    return frames, measures, flags


# ---- objects

def picture_block(factory, ins, scale):
    """An object's picture and its cut on the block's canvas at the art scale, placed as the
    guide's layout says: (rgb, alpha, (x0, y0) of the canvas in grid pixels)."""
    with open(_path(factory, ins["guide_layout"]), encoding="utf-8") as f:
        lay = json.load(f)
    pic = Image.open(_path(factory, ins["texture"])).convert("RGB")
    mask = Image.open(_path(factory, ins["mask"])).convert("L")
    if mask.size != pic.size:
        mask = mask.resize(pic.size, Image.BICUBIC)
    sx = pic.size[0] / float(lay["size"][0])
    sy = pic.size[1] / float(lay["size"][1])
    bx0, by0, bx1, by1 = lay["canvas"]
    f, (ox, oy) = lay["f"], lay["offset"]
    box = (ox * sx, oy * sy, (ox + (bx1 - bx0) * f) * sx, (oy + (by1 - by0) * f) * sy)
    size = (int(round((bx1 - bx0) * scale)), int(round((by1 - by0) * scale)))
    rgb = np.asarray(pic.resize(size, Image.LANCZOS, box=box)).astype(np.float64) / 255.0
    a = np.asarray(mask.resize(size, Image.LANCZOS, box=box)).astype(np.float64) / 255.0
    return rgb, np.clip(a, 0, 1), (bx0, by0)


def _bottom_centre(mask):
    ys, xs = np.nonzero(mask)
    return np.array([(xs.min() + xs.max() + 1) / 2.0, ys.max() + 1.0])


def _shifted(mask, dx, dy):
    """A boolean mask moved by whole pixels (zeros come in)."""
    out = np.zeros_like(mask)
    h, w = mask.shape
    ys0, ys1 = max(dy, 0), min(h, h + dy)
    xs0, xs1 = max(dx, 0), min(w, w + dx)
    if ys1 > ys0 and xs1 > xs0:
        out[ys0:ys1, xs0:xs1] = mask[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return out


def affine_about(arr, s, centre, move):
    """An array (H, W) or (H, W, C) scaled by s about `centre` and moved by `move` (pixels),
    bilinear, zeros outside."""
    if arr.ndim == 3:
        return np.stack([affine_about(arr[..., c], s, centre, move) for c in range(arr.shape[2])], axis=-1)
    cx, cy = centre
    tx, ty = move
    im = Image.fromarray(arr.astype(np.float32), "F")
    coef = (1.0 / s, 0.0, cx - (cx + tx) / s, 0.0, 1.0 / s, cy - (cy + ty) / s)
    return np.asarray(im.transform(im.size, Image.AFFINE, coef, resample=Image.BILINEAR)).astype(np.float64)


def fit_silhouette(alpha, target, scale, scales=FIT_SCALES, max_move=FIT_MOVE, k=2):
    """The scale (about the cut's bottom centre) and move (grid pixels) that make a picture's
    cut overlap the original's silhouette best, searched at k pixels per grid pixel (the edit
    draws an object a little larger, smaller or off its place): {"scale", "move",
    "iou_before", "iou", "centre"}; the identity unless it gains at least 0.02 of overlap."""
    h, w = alpha.shape
    lw, lh = max(1, int(round(w * k / float(scale)))), max(1, int(round(h * k / float(scale))))
    a = resize(alpha, (lw, lh), Image.BOX) > 0.5
    t = resize(target.astype(np.float64), (lw, lh), Image.BOX) > 0.5
    none = {"scale": 1.0, "move": [0.0, 0.0], "iou_before": 0.0, "iou": 0.0, "centre": [0.0, 0.0]}
    if not a.any() or not t.any():
        return none

    def iou(m):
        return float((m & t).sum()) / max(float((m | t).sum()), 1.0)
    base = iou(a)
    best = dict(none, iou_before=round(base, 4), iou=round(base, 4))
    ca, ct = _bottom_centre(a), _bottom_centre(t)
    lim = int(round(max_move * k))
    for sc in scales:
        m = affine_about(a.astype(np.float64), sc, ca, (0.0, 0.0)) > 0.5
        if not m.any():
            continue
        t0 = np.round(ct - _bottom_centre(m)).astype(int)
        for dy in range(-2 * k, 2 * k + 1):
            for dx in range(-2 * k, 2 * k + 1):
                mx, my = int(np.clip(t0[0] + dx, -lim, lim)), int(np.clip(t0[1] + dy, -lim, lim))
                v = iou(_shifted(m, mx, my))
                if v > best["iou"] + 1e-9:
                    best = {"scale": sc, "move": [mx / float(k), my / float(k)], "iou_before": round(base, 4),
                            "iou": round(v, 4), "centre": [float(ca[0] / k), float(ca[1] / k)]}
    if best["iou"] < base + 0.02:
        return dict(none, iou_before=round(base, 4), iou=round(base, 4))
    return best


def apply_fit(arrs, fit, scale):
    """Arrays at the art scale moved as fit_silhouette() says."""
    if fit["scale"] == 1.0 and fit["move"] == [0.0, 0.0]:
        return arrs
    c = (fit["centre"][0] * scale, fit["centre"][1] * scale)
    mv = (fit["move"][0] * scale, fit["move"][1] * scale)
    return [affine_about(x, fit["scale"], c, mv) for x in arrs]


def original_top(g, sprite):
    """Levels the original object rises above its floor diamond's top corner (what the columns
    of a block are swept up by)."""
    ys = np.nonzero(sprite[..., 3] > 0.5)[0]
    if not len(ys):
        return 1.0
    return max(0.0, (g["floor_top"][1] - float(ys.min())) / g["level"])


def object_normals(lum, opaque, scale, dome, strength):
    """An object's view normals from its silhouette's dome and its albedo's detail: the fine
    grain and the medium forms (a clump of leaves, a stone, a plank) at about twice the weight:
    (normals, the fine height used for the hollows)."""
    fine = lum - box_blur(lum, 2 * scale)
    medium = box_blur(lum, scale) - box_blur(lum, 6 * scale)
    nf = max(3 * fine[opaque].std(), 1e-4) if opaque.any() else 1.0
    nm = max(3 * medium[opaque].std(), 1e-4) if opaque.any() else 1.0
    h = np.clip(fine / nf, -1, 1) * opaque
    hm = np.clip(medium / nm, -1, 1) * opaque
    radius = 4 * scale
    dome_h = np.sqrt(np.clip(inner_distance(opaque, radius) / radius, 0, 1)) * dome
    hh = dome_h * radius + (h * 0.6 + hm * 1.6) * scale * strength
    gx = (np.roll(hh, -1, 1) - np.roll(hh, 1, 1)) / 2.0
    gy = (np.roll(hh, -1, 0) - np.roll(hh, 1, 0)) / 2.0
    nv = np.stack([-gx, gy, np.ones_like(gx)], axis=-1)
    return nv / np.linalg.norm(nv, axis=-1, keepdims=True), h


def delight(rgb, alpha, nv, g, strength):
    """A picture laid on a mesh with its painted light taken out: the pixels grouped by the
    side of a box they face (up, the +X side, the +Y side), each group's mean brightness brought
    `strength` of the way to the whole's; the mesh's normals give the light back. Returns (rgb,
    {group: gain})."""
    if strength <= 0:
        return rgb, {}
    names = ("up", "x_side", "y_side")
    dirs = [G.to_view(g, np.array(v, float)) for v in ((0, 0, 1), (1, 0, 0), (0, 1, 0))]
    which = np.argmax(np.stack([nv @ d for d in dirs], axis=-1), axis=-1)
    opaque = alpha > 0.5
    if opaque.sum() < 32:
        return rgb, {}
    lum = luminance(rgb)
    whole = float(lum[opaque].mean())
    gain_map = np.ones(lum.shape)
    gains = {}
    for k, n in enumerate(names):
        m = opaque & (which == k)
        if m.sum() < 0.05 * opaque.sum():
            continue
        gk = float(np.clip((whole / max(float(lum[m].mean()), 1e-3)) ** strength, 0.6, 1.6))
        gains[n] = round(gk, 3)
        gain_map[which == k] = gk
    w = opaque.astype(np.float64)
    smooth = box_blur(gain_map * w, 1) / np.maximum(box_blur(w, 1), 1e-6)
    return np.clip(rgb * smooth[..., None], 0, 1), gains


def ground_cut(g, spec, rgb, alpha, origin, scale):
    """An object's own ground taken out of its cut: inside the floor diamonds of its tiles, the
    pixels whose colour is the ground's (the median and spread of the diamonds' front part, where
    nothing stands) and not darker than it (a shadow under a crown stays, as a contact shadow).
    Returns (alpha, the share of the opaque pixels removed)."""
    h, w = alpha.shape
    bx0, by0 = origin
    rect = [bx0, by0, w / float(scale), h / float(scale)]
    o, ex, ey, ez = G.screen_axes(g)
    ys = by0 + (np.arange(h) + 0.5) / scale
    diamond = np.zeros((h, w), bool)
    front = np.zeros((h, w), bool)
    th = g["tile"][1]
    for _, i, j in frames_of(spec):
        d = G.column_mask(g, (i, j), rect, scale, 0.0)
        base = o + i * ex + j * ey
        diamond |= d
        front |= d & ((ys - base[1]) > 0.7 * th)[:, None]
    opaque = alpha > 0.5
    sample = rgb[front & opaque]
    if len(sample) < 32:
        return alpha, 0.0
    med = np.median(sample, axis=0)
    spread = np.median(np.abs(sample - med), axis=0) * 1.4826 + 0.03
    dist = (np.abs(rgb - med) / (2.5 * spread)).max(-1)
    lum = luminance(rgb)
    ground = diamond & (dist < 1.0) & (lum > 0.8 * float(luminance(med[None, :])[0]))
    soft = smoothstep(0.3, 0.7, box_blur(ground.astype(np.float64), max(scale / 4.0, 1)))
    out = alpha * (1 - soft * diamond)
    n = float(opaque.sum())
    return out, (float((opaque & (out <= 0.5)).sum()) / n if n else 0.0)


def _ramp(v, mask):
    """The plane 1 + v . (x, y) over a region's box (x and y from -0.5 to 0.5), on the whole
    array."""
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    x = (np.arange(w) - xs.min()) / max(xs.max() - xs.min(), 1) - 0.5
    y = (np.arange(h) - ys.min()) / max(ys.max() - ys.min(), 1) - 0.5
    return 1.0 + v[0] * x[None, :] + v[1] * y[:, None]


def planar_delight(rgb, mask, orig_rgb, orig_mask, scale, strength):
    """A picture's painted light (its brightness slope that the original's does not have,
    painted_light) measured, and `strength` of it taken out by dividing by the ramp of the
    difference (the picture's detail and local form kept): (rgb, {"shading_painted", "shading",
    "shading_original"}); "shading" is what is left."""
    lum = box_blur(luminance(rgb), scale)
    olum = box_blur(luminance(orig_rgb), scale)
    v, v0 = painted_light(lum, mask, olum, orig_mask)
    out = {"shading_painted": round(v, 4), "shading": round(v, 4)}
    if v0 is not None:
        out["shading_original"] = round(v0, 4)
    if strength <= 0 or v0 is None or mask.sum() < 16:
        return rgb, out
    diff = light_vector(lum, mask) - light_vector(olum, orig_mask)
    ramp = np.clip(_ramp(diff, mask), 0.4, 2.5)
    rgb = np.clip(rgb / (ramp ** strength)[..., None], 0, 1)
    v2, _ = painted_light(box_blur(luminance(rgb), scale), mask, olum, orig_mask)
    out["shading"] = round(v2, 4)
    return rgb, out


def object_frames(factory, job, g, spec, s, sprite, scale, silhouette=None):
    """The frames of an object: its picture cut back into the grid's columns, or its render."""
    ins = job["inputs"]
    measures, flags = {}, []
    if "render" in ins:
        render = read_rgba(_path(factory, ins["render"]))
        nrm = read_rgba(_path(factory, ins["render_normal"]))
        x0, y0, cw, ch = G.canvas(g, spec.get("rect"))
        size = [int(round(cw * scale)), int(round(ch * scale))]
        render, nrm = resize(render, size), resize(nrm, size)
        with open(_path(factory, ins["render_fit"]), encoding="utf-8") as f:
            measures["render_fit"] = json.load(f).get("fit")
        rfit = measures["render_fit"] or {}
        if rfit.get("iou") is not None and rfit["iou"] < s["min_render_iou"]:
            flags.append("render: the mesh's silhouette overlaps the original's by %.0f%% (under %.0f%%)" % (
                100 * rfit["iou"], 100 * s["min_render_iou"]))
        nv = G.decode_normal({"normal_y": "up"}, np.clip(np.round(nrm[..., :3] * 255), 0, 255))
        ra = render[..., 3]
        albedo = render[..., :3]
        paint = albedo.copy()
        if s["mesh_albedo"] == "picture" and all(k in ins for k in ("texture", "mask", "guide_layout")):
            # The picture's own colours on the mesh (the paint loses its detail): its cut fitted
            # to the render's silhouette, the paint kept where the picture does not reach.
            prgb, pa, _ = picture_block(factory, ins, scale)
            if pa.shape == ra.shape:
                pa = smoothstep(0.3, 0.7, pa)
                pf = fit_silhouette(pa, ra > 0.5, scale)
                prgb, pa = apply_fit([bleed(prgb, pa), pa], pf, scale)
                on = (pa > 0.5) & (ra > 0.5)
                iou = float(on.sum()) / max(float(((pa > 0.5) | (ra > 0.5)).sum()), 1.0)
                measures["picture_on_mesh"] = {"iou": round(iou, 4), "scale": pf["scale"], "move": pf["move"]}
                if iou >= 0.7:
                    use = smoothstep(0.5, 0.9, pa)[..., None] * (ra[..., None] > 0)
                    albedo = bleed(prgb, pa) * use + albedo * (1 - use)
                    albedo, gains = delight(albedo, ra, nv, g, s["delight"])
                    measures["delight"] = gains
        # Painted light: the slope the albedo has that the paint's own (unlit) albedo lacks,
        # `delight` of it taken out by a planar ramp (after the sides were evened).
        albedo, light = planar_delight(albedo, ra > 0.5, paint, ra > 0.5, scale, s["delight"])
        measures.update({k.replace("_original", "_paint"): v for k, v in light.items()})
        blocks = [{"index": 0, "i": 0, "j": 0, "albedo": albedo, "alpha": render[..., 3:4], "normal": nv}]
    else:
        rgb, a, (bx0, by0) = picture_block(factory, ins, scale)
        block, _ = compose_block(g, spec, sprite)
        size = [rgb.shape[1], rgb.shape[0]]
        orig_block = resize(block, size, Image.NEAREST)  # the original's block at the art scale
        if spec["cutout"] == "original":
            a = smoothstep(0.35, 0.65, resize(block[..., 3], size, Image.BILINEAR))
        a = smoothstep(0.3, 0.7, a)
        rgb = bleed(rgb, a)
        if s["fit"] == "silhouette" and spec["cutout"] != "original":
            fit = fit_silhouette(a, orig_block[..., 3] > 0.5, scale)
            rgb, a = apply_fit([rgb, a], fit, scale)
            measures["fit"] = {k: fit[k] for k in ("scale", "move", "iou_before", "iou")}
        if spec.get("ground") == "cut":
            # The object's own ground (the grass a tree's picture stands on) cut away inside the
            # floor diamonds, so the map's floor shows there; the original's too, for the checks.
            a, share = ground_cut(g, spec, rgb, a, (bx0, by0), scale)
            measures["ground_cut"] = round(share, 4)
            oa, _ = ground_cut(g, spec, orig_block[..., :3], orig_block[..., 3], (bx0, by0), scale)
            orig_block = np.concatenate([orig_block[..., :3], (oa > 0.5)[..., None].astype(np.float64)], axis=-1)
        if spec.get("clip"):
            # The cut kept to the original's silhouette, widened as the footprint check allows.
            allowed = dilate(orig_block[..., 3] > 0.5, s["tolerance"] * scale).astype(np.float64)
            a = np.minimum(a, smoothstep(0.5, 1.0, box_blur(allowed, max(scale / 3.0, 1))))
        # Painted light, measured on the whole picture against the original's block (a crop
        # field's inner tiles show no side, the original's single sprite does): the slope the
        # original lacks, then `delight` of it taken out by a planar ramp.
        rgb, light = planar_delight(rgb, a > 0.5, orig_block[..., :3], orig_block[..., 3] > 0.5, scale,
                                    s["delight"])
        measures.update(light)
        # The footprint, on the whole picture against the original's block (a block's frames
        # overlap, and a back tile's frame holds the tips above the block's back edge).
        extra = None
        if silhouette is not None:
            sb, _ = compose_block(g, spec, silhouette)
            extra = resize(sb[..., 3], size, Image.NEAREST)
        o, c = footprint(a, orig_block[..., 3], extra, scale, s["tolerance"])
        measures["overflow"], measures["cover"] = round(o, 4), round(c, 4)
        measures["_original_lum"] = luminance(orig_block[..., :3])[orig_block[..., 3] > 0.5]
        tall = original_top(g, sprite) + COLUMN_MARGIN
        blocks = cut_block(g, spec, rgb, a, (bx0, by0), scale, tall)
    frames = []
    for b in blocks:
        alpha = b["alpha"][..., 0]
        opaque = alpha > 0.5
        lum = luminance(b["albedo"])
        nv, h = object_normals(lum, opaque, scale, float(spec.get("dome", 0.6)), s["normal_strength"])
        if "normal" in b:
            nv = b["normal"]
        hollow = np.clip(box_blur(h, scale) - h, 0, 1)
        b.update(view_normal=nv, shade=1 - s["cavity"] * 0.5 * hollow)
        frames.append(b)
    worst = measures["shading"]
    if worst > s["max_shading"]:
        flags.append("shading: the albedo brightens by %.0f%% toward one side of the object (painted light, over "
                     "%.0f%%)" % (100 * worst, 100 * s["max_shading"]))
    return frames, measures, flags


def cut_block(g, spec, rgb, alpha, origin, scale, tall=None):
    """A block picture (the variant grid's tiles drawn as one picture, canvas origin in tile
    (0, 0)'s cell) cut back into its frames. Each frame keeps its tile's whole column (its floor
    diamond swept up `tall` levels, the original's own height: what stands on that tile), even
    where a nearer tile's column overlaps it, so a frame drawn alone (at the block's edge, beside
    a tile of another kind) has no hole; a pixel in no column (a tip above the block's back edge)
    goes to the back-most tile whose column, swept without limit, holds it, else to the
    front-most whose canvas does. Drawing the frames back to front gives the picture again."""
    rx, ry, rw, rh = G.canvas(g, spec.get("rect"))
    bh, bw = alpha.shape
    bx0, by0 = origin
    block_rect = [bx0, by0, bw / float(scale), bh / float(scale)]
    if tall is None:
        tall = (g["floor_top"][1] - ry) / g["level"] + 1.0
    order = sorted(frames_of(spec), key=lambda t: (t[1] + t[2], t[1]))
    inside, cols = {}, {}
    for index, i, j in order:
        sx, sy = G.screen_position(g, (i, j, 0))
        px0, py0 = int(round((sx + rx - bx0) * scale)), int(round((sy + ry - by0) * scale))
        canvas = np.zeros((bh, bw), bool)
        canvas[max(py0, 0):py0 + int(round(rh * scale)), max(px0, 0):px0 + int(round(rw * scale))] = True
        inside[index] = (canvas, px0, py0)
        cols[index] = G.column_mask(g, (i, j), block_rect, scale, tall) & canvas
    in_any = np.zeros((bh, bw), bool)
    for c in cols.values():
        in_any |= c
    rest = (~in_any) & (alpha > 0)
    owner = -np.ones((bh, bw), int)
    for index, i, j in reversed(order):  # front to back: the back-most column is written last
        up = G.column_mask(g, (i, j), block_rect, scale, 1000.0) & inside[index][0]
        owner[rest & up] = index
    for index, i, j in order:
        owner[(owner < 0) & rest & inside[index][0]] = index
    out = []
    w, h = int(round(rw * scale)), int(round(rh * scale))
    for index, i, j in frames_of(spec):
        _, px0, py0 = inside[index]
        mine = cols[index] | (owner == index)
        fr_rgb = np.zeros((h, w, 3))
        fr_a = np.zeros((h, w, 1))
        ys0, xs0 = max(py0, 0), max(px0, 0)
        ys1, xs1 = min(py0 + h, bh), min(px0 + w, bw)
        if ys1 > ys0 and xs1 > xs0:
            sub = (slice(ys0 - py0, ys1 - py0), slice(xs0 - px0, xs1 - px0))
            fr_rgb[sub] = rgb[ys0:ys1, xs0:xs1]
            fr_a[sub] = (alpha * mine)[ys0:ys1, xs0:xs1, None]
        out.append({"index": index, "i": i, "j": j, "albedo": fr_rgb, "alpha": fr_a})
    return out


# ---- review sheets

def _font():
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=14)
    except TypeError:
        return ImageFont.load_default()


def _thumb(im, height):
    w = max(1, int(round(im.width * height / float(im.height))))
    return im.resize((w, height), Image.LANCZOS)


def _checker(size, cell=8):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    c = ((xx // cell + yy // cell) % 2) * 30 + 200
    return Image.fromarray(np.dstack([c, c, c, np.full_like(c, 255)]).astype(np.uint8), "RGBA")


def _on_checker(im):
    bg = _checker(im.size)
    bg.alpha_composite(im.convert("RGBA"))
    return bg


def lit_preview(albedo, normal_rgb, g, light=(0.6, 0.5, 0.62)):
    """An albedo lit by one light from its normal map (the game's dynamic light, roughly)."""
    n = G.decode_normal(g, normal_rgb)
    lv = np.array(light) / np.linalg.norm(light)
    return np.clip(albedo * (0.25 + 0.9 * np.maximum(n @ lv, 0))[..., None], 0, 1)


def step_review(factory, job, d, files):
    """One sheet of a piece's variants: per variant the original, the first frame shaded, its
    albedo, its normal map and the albedo lit by a moving light, then the frames assembled as the
    game draws them (twice the variant grid), and the verdict and flags."""
    from . import acceptance
    args = job["args"]
    spec = args["tile"]
    g = G.grid_of(args.get("grid"))
    sprite = read_rgba(_path(factory, job["inputs"]["sprite"]))
    scale = int(args.get("scale", 4))
    verdicts, summary = factory.decision(job)
    font = _font()
    rows = []
    height = 200
    orig = original_in_canvas(g, sprite, spec.get("rect"), scale)
    orig_im = Image.fromarray(np.clip(np.round(orig * 255), 0, 255).astype(np.uint8), "RGBA")
    for v, verdict in zip(job["variants"], verdicts):
        label = v["variant"]
        cells = [_on_checker(_thumb(orig_im, height))]
        lines = ["%s: %s" % (label, verdict["class"])] + ["  " + r for r in verdict["reasons"][:4]]
        if factory.done_ok(v["job"]):
            tile = _read_tile(factory, v["job"])
            folder = os.path.dirname(os.path.join(factory.out, factory.entry(v["job"])["files"]["tile"][0]))
            fr = tile["frames"][0]
            shaded = Image.open(os.path.join(folder, fr["file"])).convert("RGBA")
            albedo = Image.open(os.path.join(folder, fr["albedo"])).convert("RGBA")
            normal = Image.open(os.path.join(folder, fr["normal"])).convert("RGBA")
            a = np.asarray(albedo).astype(np.float64) / 255.0
            lit = lit_preview(a[..., :3], np.asarray(normal)[..., :3], g)
            lit_im = Image.fromarray(np.dstack([np.round(lit * 255), np.asarray(albedo)[..., 3]]).astype(np.uint8),
                                     "RGBA")
            cells += [_on_checker(_thumb(x, height)) for x in (shaded, albedo, normal, lit_im)]
            assembled = assemble(g, repeat_tiles(spec, tile["variant_grid"]), {"piece": (tile, folder)}, scale, "file")
            cells.append(_on_checker(_thumb(crop_to([assembled])[0], height)))
            m = tile.get("measures") or {}
            lines.append("  " + ", ".join("%s %s" % (k, m[k]) for k in sorted(m) if not isinstance(m[k], dict)))
        else:
            lines.append("  not finished")
        rows.append((cells, lines))
    width = max(sum(c.width + 8 for c in cells) for cells, _ in rows) + 16 if rows else 400
    total = sum(height + 24 + 18 * len(lines) for _, lines in rows) + 40
    sheet = Image.new("RGBA", (max(width, 600), total), (32, 34, 38, 255))
    draw = ImageDraw.Draw(sheet)
    banner = "%s (%s): %d acceptable of %d" % (spec["name"], spec["kind"], len(summary["acceptable"]), len(verdicts))
    draw.text((8, 8), banner, fill=(255, 255, 255, 255), font=font)
    y = 32
    for cells, lines in rows:
        x = 8
        for c in cells:
            sheet.alpha_composite(c, (x, y))
            x += c.width + 8
        y += height + 6
        for ln in lines:
            draw.text((8, y), ln, fill=(220, 220, 220, 255) if not ln.startswith("  ") else (255, 190, 120, 255),
                      font=font)
            y += 18
        y += 18
    out = os.path.join(d, "review")
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "sheet%s.png" % ("_reserve" if job["id"].endswith("_reserve") else ""))
    sheet.convert("RGB").save(p)
    _record(factory, files, "sheet", p)
    _ = acceptance


def repeat_tiles(spec, grid):
    """The tiles of a piece repeated as the game would lay it: a floor or object over twice
    its variant grid, a wall along its line (at least three tiles)."""
    gx, gy = grid
    if spec["kind"] == "west_wall":
        return [{"at": [0, y, 0], "west_wall": "piece"} for y in range(max(2 * gy, 3))]
    if spec["kind"] == "north_wall":
        return [{"at": [x, 0, 0], "north_wall": "piece"} for x in range(max(2 * gx, 3))]
    return [{"at": [x, y, 0], spec["kind"]: "piece"} for y in range(2 * gy) for x in range(2 * gx)]


def crop_to(images):
    """The images (one size) cropped to the box of what any of them shows, with a margin."""
    boxes = [im.getbbox() for im in images if im.getbbox()]
    if not boxes:
        return images
    x0, y0 = max(min(b[0] for b in boxes) - 8, 0), max(min(b[1] for b in boxes) - 8, 0)
    x1, y1 = max(b[2] for b in boxes) + 8, max(b[3] for b in boxes) + 8
    return [im.crop((x0, y0, min(x1, im.width), min(y1, im.height))) for im in images]


def _read_tile(factory, jid):
    with open(os.path.join(factory.out, factory.entry(jid)["files"]["tile"][0]), encoding="utf-8") as f:
        return json.load(f)


def assemble(g, tiles, pieces, scale, which="file", originals=None):
    """A small map drawn as the game draws it: tiles [{"at": [x, y, z], part: piece id}] back to
    front (by level, then x + y, then x), each part's frame chosen by the variant grid. pieces:
    {id: (tile record, folder)}; originals: {id: sprite RGBA array} drawn (nearest) where a piece
    has none, or instead of every piece when which is "original"."""
    tw, th = g["tile"]
    pos = [G.screen_position(g, t["at"], scale) for t in tiles]
    if not tiles:
        return Image.new("RGBA", (8, 8))
    cw, ch = g["cell"]
    pad_top = 3 * g["level"] * scale
    xs = [p[0] for p in pos]
    ys = [p[1] for p in pos]
    x0, y0 = min(xs) - 8 * scale, min(ys) - pad_top
    w = int(max(xs) - x0 + (cw + 16) * scale)
    h = int(max(ys) - y0 + (ch + 8) * scale)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cache = {}
    order = sorted(range(len(tiles)), key=lambda k: (tiles[k]["at"][2], tiles[k]["at"][0] + tiles[k]["at"][1],
                                                     tiles[k]["at"][0]))
    for k in order:
        t = tiles[k]
        for part in PARTS:
            pid = t.get(part)
            if not pid:
                continue
            im, anchor = None, (0.0, 0.0)
            if which != "original" and pid in pieces:
                tile, folder = pieces[pid]
                gx, gy = tile["variant_grid"]
                x, y = t["at"][0], t["at"][1]
                fr = next((f for f in tile["frames"] if f["index"] == (y % gy) * gx + (x % gx)), tile["frames"][0])
                key = (pid, fr["index"], which)
                if key not in cache:
                    name = fr["file"] if which == "file" else fr["albedo"]
                    src = Image.open(os.path.join(folder, name)).convert("RGBA")
                    f = scale / float(tile["scale"])
                    if f != 1:
                        src = src.resize((max(1, int(round(src.width * f))), max(1, int(round(src.height * f)))),
                                         Image.LANCZOS)
                    cache[key] = src
                im, anchor = cache[key], tile.get("anchor", [0, 0])
            elif originals and pid in originals:
                key = (pid, "original")
                if key not in cache:
                    arr = originals[pid]
                    src = Image.fromarray(np.clip(np.round(arr * 255), 0, 255).astype(np.uint8), "RGBA")
                    cache[key] = src.resize((int(src.width * scale), int(src.height * scale)), Image.NEAREST)
                im = cache[key]
            if im is None:
                continue
            canvas.alpha_composite(im, (int(round(pos[k][0] - x0 + anchor[0] * scale)),
                                        int(round(pos[k][1] - y0 + anchor[1] * scale))))
    return canvas


def test_layout(pieces):
    """A small map that shows every piece (no layout given): each floor on a patch twice its
    variant grid, each wall along a patch's edge over the first floor, each object on its patch."""
    floors = [p for p in pieces if p["tile"]["kind"] == "floor"]
    base = floors[0]["id"] if floors else None
    tiles = []
    x = 0
    for p in pieces:
        spec = p["tile"]
        gx, gy = spec["grid"]
        w, h = max(2 * gx, 2), max(2 * gy, 2)
        for i in range(w):
            for j in range(h):
                t = {"at": [x + i, j, 0]}
                if spec["kind"] == "floor":
                    t["floor"] = p["id"]
                else:
                    if base:
                        t["floor"] = base
                    if spec["kind"] == "object":
                        t["object"] = p["id"]
                    elif spec["kind"] == "west_wall" and i == 0:
                        t["west_wall"] = p["id"]
                    elif spec["kind"] == "north_wall" and j == 0:
                        t["north_wall"] = p["id"]
                tiles.append(t)
        x += w + 1
    return [{"name": "test arrangement", "tiles": tiles}]


def step_sheet(factory, job, d, files):
    """The set sheet: each map of the layout (else a test arrangement) drawn three times: the
    originals, the picked sprites shaded, and their albedo lit by one light from their normal
    maps; pieces without a pick are drawn as their originals."""
    args = job["args"]
    g = G.grid_of(args.get("grid"))
    scale = 2
    pieces, originals, missing = {}, {}, []
    for p in job["pieces"]:
        originals[p["id"]] = read_rgba(p["sprite"]["file"]) if p["sprite"].get("file") and os.path.isfile(
            p["sprite"]["file"]) else None
        e = factory.entry(p["final"])
        if e.get("status") == "done" and "tile" in (e.get("files") or {}):
            path = os.path.join(factory.out, e["files"]["tile"][0])
            with open(path, encoding="utf-8") as f:
                pieces[p["id"]] = (json.load(f), os.path.dirname(path))
        else:
            missing.append(p["id"])
    originals = {k: v for k, v in originals.items() if v is not None}
    if "layout" in (job.get("inputs") or {}):
        maps = read_layout(_path(factory, job["inputs"]["layout"]))["maps"]
    else:
        maps = test_layout(job["pieces"])
    font = _font()
    panels, unknown = [], set()
    for m in maps:
        tiles = m["tiles"]
        for t in tiles:
            for part in PARTS:
                if t.get(part) and t[part] not in originals:
                    unknown.add(t[part])
        a = assemble(g, tiles, pieces, scale, "original", originals)
        b = assemble(g, tiles, pieces, scale, "file", originals)
        c = assemble(g, tiles, pieces, scale, "albedo", originals)
        normals = assemble_normals(g, tiles, pieces, scale, originals)
        ca = np.asarray(c).astype(np.float64) / 255.0
        lit = lit_preview(ca[..., :3], normals, g, light=(0.7, 0.3, 0.65))
        c = Image.fromarray(np.dstack([np.round(lit * 255), np.asarray(c)[..., 3]]).astype(np.uint8), "RGBA")
        panels.append((m.get("name", "map"), crop_to([a, b, c])))
    width = max(sum(im.width for im in ims) + 40 for _, ims in panels)
    height = sum(max(im.height for im in ims) + 40 for _, ims in panels) + 30
    sheet = Image.new("RGBA", (width, height), (24, 26, 30, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 6), "originals | shaded | albedo lit by one light from the normal maps   (%d pieces picked, %d not: "
                      "drawn as their originals)" % (len(pieces), len(missing)), fill=(255, 255, 255, 255), font=font)
    y = 30
    for name, ims in panels:
        draw.text((8, y), name, fill=(220, 220, 220, 255), font=font)
        x = 8
        for im in ims:
            sheet.alpha_composite(im, (x, y + 20))
            x += im.width + 12
        y += max(im.height for im in ims) + 40
    out = os.path.join(d, "sheet")
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "set_sheet.png")
    sheet.convert("RGB").save(p)
    _record(factory, files, "sheet", p)
    lp = os.path.join(out, "set_sheet.json")
    write_json(lp, {"format": "forge-tile-set-sheet", "maps": [n for n, _ in panels], "picked": sorted(pieces),
                    "not_picked": sorted(missing), "skipped_ids": sorted(unknown)})
    _record(factory, files, "layout", lp)


def assemble_normals(g, tiles, pieces, scale, originals):
    """The normal maps of a map's picked sprites assembled as assemble() draws them (a flat
    floor's normal where a piece has none), as uint8 RGB."""
    canvas = assemble(g, tiles, {k: ({**t, "frames": [dict(f, file=f["normal"]) for f in t["frames"]]}, folder)
                                 for k, (t, folder) in pieces.items()}, scale, "file", None)
    arr = np.asarray(canvas).astype(np.float64)
    flat = G.encode_normal(g, G.to_view(g, np.array([0.0, 0.0, 1.0]))).astype(np.float64)
    a = arr[..., 3:4] / 255.0
    rgb = arr[..., :3] * a + flat * (1 - a)
    base = assemble(g, tiles, pieces, scale, "albedo", originals)
    out = np.zeros((base.height, base.width, 3))
    hh, ww = min(out.shape[0], rgb.shape[0]), min(out.shape[1], rgb.shape[1])
    out[...] = flat
    out[:hh, :ww] = rgb[:hh, :ww]
    return np.clip(np.round(out), 0, 255).astype(np.uint8)
