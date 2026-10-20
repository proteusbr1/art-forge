"""Synthetic sprites of an isometric tile grid for the iso_tile tests (NumPy and Pillow): small
pixel-art sprites in the classic 32 x 40 cell (a 32 x 16 floor diamond whose top corner is at
(16, 24), 24 px levels): a stone floor, a grass floor, a plank west wall, its north twin, a
low fence (see-through), a crate object and a two-level tree; and a map layout of them."""

import json
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools", "blender"))
import iso_grid as G  # noqa: E402

GRID = G.grid_of({})


def _poly_mask(points, size=(32, 40), sub=1):
    from PIL import ImageDraw
    w, h = size
    im = Image.new("L", (w * sub, h * sub), 0)
    ImageDraw.Draw(im).polygon([(x * sub, y * sub) for x, y in points], fill=255)
    return np.asarray(im.resize((w, h), Image.NEAREST)) > 127


def _quad(kind, height=1.0, thickness=0.0):
    pts = []
    for q in G.quads(GRID, kind, height, thickness):
        pts.append([tuple(G.world_to_screen(GRID, c) - 0.0) for c in q["corners"]])
    return pts


def _texture(seed, base, spread, size=(32, 40)):
    rng = np.random.default_rng(seed)
    noise = rng.random(size[::-1] + (1,)) * spread
    return np.clip(np.array(base)[None, None, :] + noise - spread / 2, 0, 1)


def sprite(kind, seed=1, base=(0.45, 0.42, 0.38), spread=0.2, height=1.0, holes=False):
    """An RGBA float sprite (40, 32, 4) of one tile part."""
    mask = np.zeros((40, 32), bool)
    for poly in _quad(kind, height):
        mask |= _poly_mask(poly)
    rgb = _texture(seed, base, spread)
    if holes:  # a fence: rails only
        yy = np.arange(40)[:, None] * np.ones((1, 32))
        mask &= (yy % 6) < 2
    return np.dstack([rgb, mask.astype(float)])


def object_sprite(seed=3, tall=1.0, wide=0.6, base=(0.55, 0.38, 0.2)):
    """A box-like object standing on the floor's middle, `tall` levels high."""
    a, b = 0.5 - wide / 2, 0.5 + wide / 2

    def p(x, y, z):
        return tuple(G.world_to_screen(GRID, (x, y, z)))
    hull = [p(a, b, 0), p(b, b, 0), p(b, a, 0), p(b, a, tall), p(a, a, tall), p(a, b, tall)]
    mask = _poly_mask(hull)
    rgb = _texture(seed, base, 0.15)
    return np.dstack([rgb, mask.astype(float)])


def save(arr, path):
    rgb = np.clip(np.round(arr[..., :3] * 255), 0, 255).astype(np.uint8)
    a = (arr[..., 3] > 0.5).astype(np.uint8) * 255
    Image.fromarray(np.dstack([rgb, a]), "RGBA").save(path)
    return path


def make(folder):
    """The synthetic sprites and a layout in folder; returns {name: path}."""
    os.makedirs(folder, exist_ok=True)
    out = {
        "stone": save(sprite("floor", 1, (0.5, 0.48, 0.45)), os.path.join(folder, "stone.png")),
        "grass": save(sprite("floor", 2, (0.25, 0.45, 0.2)), os.path.join(folder, "grass.png")),
        "plank_w": save(sprite("west_wall", 3, (0.5, 0.35, 0.2)), os.path.join(folder, "plank_w.png")),
        "plank_n": save(sprite("north_wall", 4, (0.5, 0.35, 0.2)), os.path.join(folder, "plank_n.png")),
        "fence": save(sprite("west_wall", 5, (0.6, 0.5, 0.3), height=0.6, holes=True),
                      os.path.join(folder, "fence.png")),
        "crate": save(object_sprite(6), os.path.join(folder, "crate.png")),
        "bush": save(object_sprite(7, tall=0.4, wide=0.9, base=(0.2, 0.5, 0.15)), os.path.join(folder, "bush.png")),
    }
    tiles = []
    for x in range(4):
        for y in range(4):
            t = {"at": [x, y, 0], "floor": "stone" if x < 2 else "grass"}
            if x == 0:
                t["west_wall"] = "plank_w"
            if y == 0 and x < 2:
                t["north_wall"] = "plank_n"
            if (x, y) == (1, 2):
                t["object"] = "crate"
            if x == 2 and y < 2:
                t["object"] = "bush"
            tiles.append(t)
    layout = {"format": "forge-tile-layout", "maps": [{"name": "yard", "tiles": tiles}]}
    out["layout"] = os.path.join(folder, "layout.json")
    with open(out["layout"], "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=1)
    return out


def seamless(size, seed=0, base=(0.5, 0.45, 0.4), cells=8):
    """A seamless (periodic) texture (H, W, 3) float: a sum of sines of whole periods."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    out = np.zeros((h, w))
    for _ in range(12):
        kx, ky = rng.integers(1, cells, 2)
        ph = rng.random() * 2 * np.pi
        out += np.sin(2 * np.pi * (kx * xx / w + ky * yy / h) + ph)
    out = out / np.abs(out).max() * 0.15
    return np.clip(np.array(base)[None, None, :] + out[..., None], 0, 1)
