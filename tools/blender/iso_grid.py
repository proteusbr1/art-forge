"""The geometry of an isometric tile grid (2:1 dimetric, as 2D games draw it), without bpy:
the faces a tile part covers in its sprite cell, the texture of a face taken out of a
sprite and put back into one, normals in the sprite's view and the measures the iso_tile
recipe's checks use. Imported by the factory's local steps (forge/tiles.py) and by the
Blender renderer of tile objects (tile_render.py).

Coordinates: a sprite cell is `cell` pixels [width, height], x to the right, y down, origin at
its top-left corner (pixel i covers [i, i + 1)). World: X along the grid's x axis (to the lower
right on screen), Y along its y axis (to the lower left), Z up; one tile edge is 1, one level is
`wall_aspect` tile edges high. The floor of a tile is a diamond `tile` pixels [width, height]
whose top corner is at `floor_top`; one level rises `level` pixels.

A face's texture: u from 0 to 1 across the texture (left to right as seen), v from 0 to 1
down it. The floor's texture: u along +X, v along +Y (its top-left texel at the diamond's top
corner). A wall's texture is the face seen from the camera: v = 0 at its top.
"""

import math

import numpy as np

KINDS = ("floor", "west_wall", "north_wall", "object")
FACE_KINDS = ("floor", "west_wall", "north_wall")
DEFAULT_GRID = {"cell": [32, 40], "tile": [32, 16], "level": 24, "floor_top": [16, 24], "wall_thickness": 0.0,
                "normal_y": "up"}
GRID_KEYS = ("cell", "tile", "level", "floor_top", "elevation", "wall_aspect", "wall_thickness", "normal_y")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def grid_problems(grid):
    """Problems of an order's grid object (empty: valid)."""
    if grid is None:
        return []
    if not isinstance(grid, dict):
        return ["grid must be an object"]
    out = []
    for k, v in grid.items():
        if k not in GRID_KEYS:
            out.append("unknown grid key %r (known: %s)" % (k, ", ".join(GRID_KEYS)))
        elif k in ("cell", "tile", "floor_top"):
            if not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v)):
                out.append("grid.%s must be [x, y] numbers" % k)
            elif k != "floor_top" and not all(x > 0 for x in v):
                out.append("grid.%s must be positive" % k)
        elif k == "normal_y":
            if v not in ("up", "down"):
                out.append("grid.normal_y must be up (green up, as OpenGL and Godot) or down (DirectX)")
        elif k == "wall_thickness":
            if not (_num(v) and 0 <= v <= 0.5):
                out.append("grid.wall_thickness must be a number from 0 to 0.5 (a share of a tile edge)")
        elif k == "elevation":
            if not (_num(v) and 5 <= v <= 85):
                out.append("grid.elevation must be a number of degrees from 5 to 85")
        elif not (_num(v) and v > 0):
            out.append("grid.%s must be a number above 0" % k)
    if not out:
        g = grid_of(grid)
        w, h = g["cell"]
        cx, fy = g["floor_top"]
        tw, th = g["tile"]
        if cx - tw / 2.0 < -0.5 or cx + tw / 2.0 > w + 0.5 or fy + th > h + 0.5 or fy - g["level"] < -0.5:
            out.append("grid: the floor diamond and one level of wall above it must lie inside the cell "
                       "(cell %s, tile %s, floor_top %s, level %s)" % (g["cell"], g["tile"], g["floor_top"], g["level"]))
    return out


def grid_of(grid=None):
    """The full grid: the order's keys over the defaults, with the derived ones."""
    g = dict(DEFAULT_GRID)
    g.update({k: v for k, v in (grid or {}).items() if k in GRID_KEYS})
    g["cell"] = [float(x) for x in g["cell"]]
    g["tile"] = [float(x) for x in g["tile"]]
    g["floor_top"] = [float(x) for x in g["floor_top"]]
    g["level"] = float(g["level"])
    tw, th = g["tile"]
    if "elevation" not in g:
        # A 2:1 diamond is the floor seen from sin(elevation) = height / width (30 degrees).
        g["elevation"] = math.degrees(math.asin(max(min(th / tw, 1.0), 0.05)))
    if "wall_aspect" not in g:
        # An orthographic camera: one tile edge is tw / sqrt(2) pixels long on screen (seen along
        # the diagonal) and a world unit up is that times cos(elevation); one level is `level`
        # pixels (the classic 32 x 16 tile with 24 px levels: sqrt(1.5) tile edges).
        g["wall_aspect"] = g["level"] / (tw / math.sqrt(2.0) * math.cos(math.radians(g["elevation"])))
    return g


def screen_axes(g):
    """(origin, ex, ey, ez): the floor's top corner and the screen vectors (pixels) of one
    tile edge along X and Y and of one level up."""
    tw, th = g["tile"]
    return (np.array(g["floor_top"], float), np.array([tw / 2.0, th / 2.0]), np.array([-tw / 2.0, th / 2.0]),
            np.array([0.0, -g["level"]]))


def world_to_screen(g, p):
    """The cell pixel of a world point [X, Y, Z] (Z in levels)."""
    o, ex, ey, ez = screen_axes(g)
    return o + p[0] * ex + p[1] * ey + p[2] * ez


def view_basis(g):
    """(right, up, toward the viewer) as world unit vectors (X, Y, Z with Z up and true
    lengths): the camera looks down at `elevation` degrees from the +X +Y side."""
    e = math.radians(g["elevation"])
    b = 1.0 / math.sqrt(2.0)
    right = np.array([b, -b, 0.0])
    up = np.array([-math.sin(e) * b, -math.sin(e) * b, math.cos(e)])
    toward = np.array([math.cos(e) * b, math.cos(e) * b, math.sin(e)])
    return right, up, toward


def to_view(g, n):
    """World normals (..., 3) in the sprite's view: x right, y up, z toward the viewer."""
    r, u, t = view_basis(g)
    return np.stack([n @ r, n @ u, n @ t], axis=-1)


def encode_normal(g, nv):
    """A view normal (..., 3) as 8-bit RGB: (n + 1) / 2, green up or down as the grid says."""
    n = np.array(nv, dtype=np.float64)
    if g.get("normal_y", "up") == "down":
        n[..., 1] = -n[..., 1]
    return np.clip(np.round((n + 1.0) * 127.5), 0, 255).astype(np.uint8)


def decode_normal(g, rgb):
    """The view normal of an encoded normal map (uint8 RGB)."""
    n = np.asarray(rgb, dtype=np.float64)[..., :3] / 127.5 - 1.0
    if g.get("normal_y", "up") == "down":
        n[..., 1] = -n[..., 1]
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)


def face_texture_size(g, kind, tiles=1, texels=384, max_pixels=1700000):
    """[width, height] of a face texture (multiples of 16) whose texels are square in the
    world: `texels` per tile edge, `tiles` [along u, along v] for a floor or the tiles along a
    wall; scaled down to at most max_pixels in all."""
    if kind == "floor":
        tu, tv = tiles if isinstance(tiles, (list, tuple)) else (tiles, tiles)
        w, h = tu * texels, tv * texels
    else:
        n = tiles if not isinstance(tiles, (list, tuple)) else max(tiles)
        w, h = n * texels, texels * g["wall_aspect"]
    f = min(1.0, math.sqrt(max_pixels / float(w * h)))
    return [max(16, int(round(w * f / 16.0)) * 16), max(16, int(round(h * f / 16.0)) * 16)]


def quads(g, kind, height=1.0, thickness=None):
    """The faces a tile part covers: [{"name", "main", "corners": world points of the texture
    corners (u, v) = (0, 0), (1, 0), (1, 1), (0, 1) (X, Y in tile edges, Z in levels), "normal":
    world, "source": [u0, v0, u1, v1] of the face texture it shows}], in drawing order (back to
    front). The face texture of a wall always covers one level; a wall `height` levels high
    (0 to 1) shows its lower part. A wall `thickness` tile edges thick (default the grid's) also
    shows its top and its open end, whose texture is the face's own top band and end band as
    deep as the wall is thick (the face's texture is one tile edge wide and one level high)."""
    t = float(g.get("wall_thickness") or 0.0) if thickness is None else float(thickness)
    a = float(height)
    if kind == "floor":
        return [{"name": "floor", "main": True, "corners": [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
                 "normal": (0, 0, 1), "source": [0, 0, 1, 1]}]
    out = []
    v0 = 1.0 - a
    # The caps show the face's texture undistorted: a band as deep as the wall is thick.
    top_band = min(t / g["wall_aspect"], a) if t > 0 else 0.0
    if kind == "west_wall":
        if t > 0:
            out.append({"name": "end", "main": False, "corners": [(0, 1, a), (t, 1, a), (t, 1, 0), (0, 1, 0)],
                        "normal": (0, 1, 0), "source": [0, v0, t, 1]})
        out.append({"name": "face", "main": True, "corners": [(t, 1, a), (t, 0, a), (t, 0, 0), (t, 1, 0)],
                    "normal": (1, 0, 0), "source": [0, v0, 1, 1]})
        if t > 0:
            out.append({"name": "top", "main": False, "corners": [(0, 1, a), (0, 0, a), (t, 0, a), (t, 1, a)],
                        "normal": (0, 0, 1), "source": [0, v0, 1, v0 + top_band]})
        return out
    if kind == "north_wall":
        if t > 0:
            out.append({"name": "end", "main": False, "corners": [(1, t, a), (1, 0, a), (1, 0, 0), (1, t, 0)],
                        "normal": (1, 0, 0), "source": [1 - t, v0, 1, 1]})
        out.append({"name": "face", "main": True, "corners": [(0, t, a), (1, t, a), (1, t, 0), (0, t, 0)],
                    "normal": (0, 1, 0), "source": [0, v0, 1, 1]})
        if t > 0:
            out.append({"name": "top", "main": False, "corners": [(0, 0, a), (1, 0, a), (1, t, a), (0, t, a)],
                        "normal": (0, 0, 1), "source": [0, v0, 1, v0 + top_band]})
        return out
    raise ValueError("no faces for kind %r" % kind)


def face_quad(g, kind, thickness=None):
    """The main face of a floor or wall at its full level (the quad the whole texture covers)."""
    return next(q for q in quads(g, kind, 1.0, thickness) if q["main"])


def _affine(g, q):
    """(p00, du, dv): the screen point of the texture's corner (0, 0) and the screen vectors of
    a full step in u and in v (a quad of a grid is a parallelogram)."""
    c = [world_to_screen(g, p) for p in q["corners"]]
    return c[0], c[1] - c[0], c[3] - c[0]


def tangents(g, q):
    """World unit vectors (true lengths, Z up) of +u and +v on a quad."""
    c = np.array(q["corners"], float)
    c[:, 2] *= g["wall_aspect"]
    du, dv = c[1] - c[0], c[3] - c[0]
    return du / max(np.linalg.norm(du), 1e-9), dv / max(np.linalg.norm(dv), 1e-9)


def bilinear(img, x, y):
    """Samples of an image (H, W, C) float at pixel coordinates x, y (pixel centres at i + 0.5),
    clamped at the edges."""
    h, w = img.shape[:2]
    x = np.clip(x - 0.5, 0, w - 1)
    y = np.clip(y - 0.5, 0, h - 1)
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def unproject(g, kind, sprite, size, thickness=None, inset=0.75):
    """The main face of a floor or wall as a texture of size [width, height] (one tile, one
    level), taken out of a sprite (H, W, 4 float 0..1, straight alpha, the cell's size):
    (premultiplied RGB, alpha). inset: the sprite pixels the samples keep away from the face's
    border (pixel art rarely covers a face's slanted edge exactly), so a full face comes out
    full to its edges."""
    w, h = size
    p00, du, dv = _affine(g, face_quad(g, kind, thickness))
    iu = min(inset / max(np.linalg.norm(du), 1e-9), 0.25)
    iv = min(inset / max(np.linalg.norm(dv), 1e-9), 0.25)
    uu, vv = np.meshgrid(iu + (np.arange(w) + 0.5) / w * (1 - 2 * iu), iv + (np.arange(h) + 0.5) / h * (1 - 2 * iv))
    px = p00[0] + uu * du[0] + vv * dv[0]
    py = p00[1] + uu * du[1] + vv * dv[1]
    pre = np.concatenate([sprite[..., :3] * sprite[..., 3:4], sprite[..., 3:4]], axis=-1)
    s = bilinear(pre, px, py)
    return s[..., :3], s[..., 3]


def canvas(g, rect=None):
    """[x, y, width, height] of a sprite's canvas in grid pixels (default: the cell)."""
    return [float(v) for v in rect] if rect else [0.0, 0.0, g["cell"][0], g["cell"][1]]


def project(g, kind, layers, scale, rect=None, height=1.0, thickness=None, supersample=4):
    """A floor or wall drawn into a sprite canvas (rect, grid pixels; default the cell) at
    `scale` art pixels per grid pixel.

    layers: {name: (H, W, C) float texture of the face, one tile and one level}; "rgb" (straight
    colour), "alpha" (C = 1), "normal" (world normals: a cap shows the face's colours with its own
    normal) and any other per-texel data. Returns {name: (h, w, C)}: "alpha" the coverage
    (antialiased by `supersample` samples per pixel and axis), every other layer straight."""
    x0, y0, cw, ch = canvas(g, rect)
    w, h = int(round(cw * scale)), int(round(ch * scale))
    ss = int(supersample)
    xs = x0 + (np.arange(w * ss) + 0.5) / (ss * scale)
    ys = y0 + (np.arange(h * ss) + 0.5) / (ss * scale)
    px, py = np.meshgrid(xs, ys)
    acc = {k: np.zeros((h * ss, w * ss, v.shape[-1]), np.float64) for k, v in layers.items() if k != "alpha"}
    cover = np.zeros((h * ss, w * ss, 1), np.float64)
    for q in quads(g, kind, height, thickness):
        p00, du, dv = _affine(g, q)
        inv = np.linalg.inv(np.array([[du[0], dv[0]], [du[1], dv[1]]]))
        rx, ry = px - p00[0], py - p00[1]
        u = inv[0, 0] * rx + inv[0, 1] * ry
        v = inv[1, 0] * rx + inv[1, 1] * ry
        inside = (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        if not inside.any():
            continue
        u0, v0, u1, v1 = q["source"]
        su = u0 + np.clip(u, 0, 1) * (u1 - u0)
        sv = v0 + np.clip(v, 0, 1) * (v1 - v0)
        a_tex = layers.get("alpha")
        if a_tex is not None:
            th_, tw_ = a_tex.shape[:2]
            a = bilinear(a_tex, su * tw_, sv * th_)
        else:
            a = np.ones(px.shape + (1,))
        a = a * inside[..., None]
        for k, tex in layers.items():
            if k == "alpha":
                continue
            th_, tw_ = tex.shape[:2]
            val = bilinear(tex, su * tw_, sv * th_)
            if k == "normal" and not q["main"]:
                val = np.broadcast_to(np.array(q["normal"], float), val.shape)
            acc[k] = acc[k] * (1 - a) + val * a
        cover = cover * (1 - a) + a
    out = {}
    pooled_a = cover.reshape(h, ss, w, ss, 1).mean(axis=(1, 3))
    for k, v in acc.items():
        pre = (v * cover).reshape(h, ss, w, ss, v.shape[-1]).mean(axis=(1, 3))
        out[k] = pre / np.maximum(pooled_a, 1e-6)
    out["alpha"] = pooled_a
    return out


def detail_normals(g, kind, height, amplitude, texels_per_unit, thickness=None):
    """World normals (H, W, 3) of a face texture whose relief is `height` (H, W) times
    `amplitude` tile edges (larger is nearer; the texture's texels are square, texels_per_unit
    to a tile edge): the face normal tilted by the relief's slope (the texture wraps)."""
    q = face_quad(g, kind, thickness)
    n0 = np.array(q["normal"], float)
    tu, tv = tangents(g, q)
    k = amplitude * texels_per_unit
    gx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) / 2.0 * k
    gy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) / 2.0 * k
    n = n0[None, None, :] - gx[..., None] * tu[None, None, :] - gy[..., None] * tv[None, None, :]
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


def column_mask(g, at, rect, scale, height=1.0):
    """(h, w) bool: the pixels of a canvas (rect, grid pixels, at `scale`) that lie in the column
    of the tile at map offset at = (dx, dy) from the canvas's own tile: its floor diamond swept up
    `height` levels."""
    x0, y0, cw, ch = canvas(g, rect)
    w, h = int(round(cw * scale)), int(round(ch * scale))
    o, ex, ey, ez = screen_axes(g)
    base = o + at[0] * ex + at[1] * ey
    xs = x0 + (np.arange(w) + 0.5) / scale - base[0]
    ys = y0 + (np.arange(h) + 0.5) / scale - base[1]
    px, py = np.meshgrid(xs, ys)
    tw, th = g["tile"]
    # The diamond: |x| / (tw / 2) + |y - th / 2| / (th / 2) <= 1, swept up by 0..height levels.
    lift = np.clip(th / 2.0 - py, 0.0, height * g["level"])
    yy = py + lift
    return (np.abs(px) / (tw / 2.0) + np.abs(yy - th / 2.0) / (th / 2.0)) <= 1.0


def screen_position(g, at, scale=1.0):
    """The top-left pixel where the cell of the tile at [x, y, z] (z in levels) is drawn on a
    map whose tile [0, 0, 0] cell starts at (0, 0)."""
    tw, th = g["tile"]
    x, y, z = at
    return ((x - y) * tw / 2.0 * scale, ((x + y) * th / 2.0 - z * g["level"]) * scale)


def silhouette_box(alpha, threshold=0.5):
    """(x0, y0, x1, y1) of the pixels whose alpha is over the threshold (x1, y1 exclusive), or
    None when there is none."""
    ys, xs = np.nonzero(alpha > threshold)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
