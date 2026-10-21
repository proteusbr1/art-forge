"""The iso_tile recipe: the grid's geometry (faces, partition of neighbouring floors, the face
taken out of a sprite and put back), the steps' measures on synthetic tiles (seams that join,
painted light, footprint, a block cut back into its tiles), how a piece expands into jobs,
what the order check refuses, the sample order (valid, verified against a fake /object_info,
estimated from the command line) and a night with the fake server and a stand-in for the
Blender render. The render itself is smoke-tested on the CPU when blender_python is
configured (Blender on the CPU, the tests' private lock folder)."""

import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import iso_tile_synth as synth  # noqa: E402
from fake_comfy import FakeComfy  # noqa: E402
from forge import local_gpu, tile_steps  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.readiness import VERIFIED, check_environment  # noqa: E402
from forge.recipes import expand  # noqa: E402
from test_readiness import object_info  # noqa: E402

G = tile_steps.G
SAMPLE = os.path.join(HERE, "fixtures", "order_iso_tile.json")
SPRITES = os.path.join(ROOT, "local", "fixtures", "iso_tile")


def make_sprites():
    return synth.make(SPRITES)


class Geometry(unittest.TestCase):
    def setUp(self):
        self.g = G.grid_of({})

    def test_defaults(self):
        self.assertAlmostEqual(self.g["elevation"], 30.0, places=6)
        self.assertAlmostEqual(self.g["wall_aspect"], math.sqrt(1.5), places=6)
        self.assertEqual(G.grid_problems({"cell": [32, 40], "normal_y": "left"}),
                         ["grid.normal_y must be up (green up, as OpenGL and Godot) or down (DirectX)"])
        self.assertTrue(G.grid_problems({"floor_top": [16, 39]}))
        self.assertEqual(G.grid_problems({"wall_thickness": 0.1}), [])

    def test_neighbouring_floors_cover_each_pixel_once(self):
        tex = {"alpha": np.ones((8, 8, 1))}
        scale = 4
        one = G.project(self.g, "floor", tex, scale, supersample=1)["alpha"][..., 0] >= 0.5
        h, w = one.shape
        count = np.zeros((h * 3, w * 3), int)
        for x in range(2):
            for y in range(2):
                sx, sy = G.screen_position(self.g, (x, y, 0), scale)
                ox, oy = int(sx) + w, int(sy) + h
                count[oy:oy + h, ox:ox + w] += one
        self.assertEqual(int(count.max()), 1)  # no pixel drawn twice
        # The 2 x 2 block is one diamond twice as large: no pixel left out inside it.
        self.assertEqual(int(count.sum()), 4 * int(one.sum()))
        self.assertEqual(int(one.sum()), 32 * 16 // 2 * scale * scale)

    def test_face_out_of_a_sprite_and_back(self):
        spr = synth.sprite("floor", 3)
        pre, a = G.unproject(self.g, "floor", spr, [64, 64])
        rgb = pre / np.maximum(a[..., None], 1e-6)
        self.assertGreater(float((a > 0.99).mean()), 0.95)
        # As a cut, a full face is full to its edges: the tips pixel art leaves out are closed.
        cut = tile_steps.face_alpha(a, 1.5 * 64 / 17.9)
        self.assertGreater(float(cut.min()), 0.99)
        wall = synth.sprite("west_wall", 4, height=0.5)
        _, wa = G.unproject(self.g, "west_wall", wall, [64, 80])
        cut = tile_steps.face_alpha(wa, 1.5 * 64 / 17.9)
        self.assertLess(float(cut[:30].mean()), 0.05)  # a low wall's empty top stays open
        self.assertGreater(float(cut[50:].min()), 0.99)
        back = G.project(self.g, "floor", {"rgb": rgb, "alpha": a[..., None]}, 1)
        inside = (back["alpha"][..., 0] > 0.99) & (spr[..., 3] > 0.5)
        err = np.abs(back["rgb"] - spr[..., :3])[inside].mean()
        self.assertLess(err, 0.08)

    def test_walls(self):
        tex = {"rgb": np.ones((8, 8, 3))}
        a = G.project(self.g, "west_wall", tex, 1, supersample=1)["alpha"][..., 0] > 0.5
        ys, xs = np.nonzero(a)
        self.assertEqual((xs.min(), xs.max(), ys.min(), ys.max()), (0, 15, 0, 31))
        n = G.project(self.g, "north_wall", tex, 1, supersample=1)["alpha"][..., 0] > 0.5
        ys, xs = np.nonzero(n)
        self.assertEqual((xs.min(), xs.max()), (16, 31))
        thick = G.project(self.g, "north_wall", tex, 2, thickness=0.125, supersample=1)["alpha"][..., 0] > 0.5
        self.assertGreater(int(thick.sum()), int(np.kron(n, np.ones((2, 2))).sum()))  # the top and the end show
        low = G.project(self.g, "west_wall", tex, 1, height=0.5, supersample=1)["alpha"][..., 0] > 0.5
        self.assertEqual(int(np.nonzero(low)[0].min()), 12)
        view = G.to_view(self.g, np.array([[0, 0, 1.0], [1, 0, 0], [0, 1, 0]]))
        self.assertAlmostEqual(view[0, 1], math.cos(math.radians(30)))  # a floor faces up and toward us
        self.assertGreater(view[1, 0], 0.5)  # a west wall's face looks to the right
        self.assertLess(view[2, 0], -0.5)  # a north wall's face to the left
        rgb = G.encode_normal(self.g, view)
        self.assertTrue(np.allclose(G.decode_normal(self.g, rgb), view, atol=0.01))
        down = dict(self.g, normal_y="down")
        self.assertEqual(int(G.encode_normal(down, view)[0, 1]), 255 - int(rgb[0, 1]))

    def test_column_and_texture_size(self):
        col = G.column_mask(self.g, (0, 0), None, 1, 1.0)
        self.assertTrue(col[30, 16] and col[5, 16] and not col[5, 2])
        self.assertEqual(G.face_texture_size(self.g, "floor", [4, 4], 384), [1296, 1296])
        w, h = G.face_texture_size(self.g, "west_wall", 1, 384)
        self.assertAlmostEqual(h / float(w), math.sqrt(1.5), delta=0.05)


class Steps(unittest.TestCase):
    def setUp(self):
        self.g = G.grid_of({})

    def spec(self, **kw):
        s = {"kind": "floor", "seam": "g", "grid": [2, 2], "height": 1.0, "thickness": None, "rect": None,
             "cutout": "none", "name": "t", "glow": None, "dome": 0.0, "alpha": "binary"}
        s.update(kw)
        return s

    def test_pieces_of_a_group_join(self):
        size = [128, 128]
        m = synth.seamless(size, 1)
        spec = self.spec()
        sides = []
        for seed in (5, 9):
            pic = synth.seamless(size, seed, base=(0.3, 0.5, 0.2), cells=5)
            b, _ = tile_steps.border_weight(spec, self.g, size, 0.08 * 0.45)
            tex = pic * (1 - b[..., None]) + m * b[..., None]
            self.assertLess(tile_steps.seam_ratio(tex, 1), 2.0)
            sides.append(tex)
        # Piece A's right edge meets piece B's left edge as the group's material meets itself.
        jump = np.abs(sides[0][:, -1] - sides[1][:, 0]).mean()
        inner = np.abs(m[:, 1:] - m[:, :-1]).mean()
        self.assertLess(jump, 2.0 * inner)
        noise = np.random.default_rng(0).random((64, 64, 3))
        ramp = np.linspace(0, 1, 64)[None, :, None] * np.ones((64, 1, 3))
        self.assertGreater(tile_steps.seam_ratio(ramp + 0.01 * noise, 1), 5)

    def test_painted_light(self):
        yy, xx = np.mgrid[0:64, 0:64] / 64.0
        lit = 0.3 + 0.5 * xx
        flat = 0.5 + 0.02 * np.sin(yy * 40)
        mask = np.ones((64, 64), bool)
        self.assertGreater(tile_steps.light_slope(lit, mask), 0.8)
        self.assertLess(tile_steps.light_slope(flat, mask), 0.05)

    def test_footprint(self):
        orig = np.zeros((40, 32))
        orig[10:30, 8:24] = 1
        new = np.zeros((40, 32))
        new[5:30, 8:24] = 1
        over, cover = tile_steps.footprint(new, orig, None, 1, 1)
        self.assertAlmostEqual(over, 4 * 16 / (25 * 16.0), places=3)
        self.assertEqual(cover, 1.0)
        over, cover = tile_steps.footprint(orig * 0, orig, None, 1, 1)
        self.assertEqual((over, cover), (0.0, 0.0))

    def test_a_block_is_cut_back_into_its_tiles(self):
        spec = self.spec(kind="object", grid=[2, 2], rect=[0, -8, 32, 48])
        from forge.tile_jobs import block_canvas
        x0, y0, x1, y1 = block_canvas(self.g, spec)
        scale = 2
        h, w = int((y1 - y0) * scale), int((x1 - x0) * scale)
        rng = np.random.default_rng(3)
        alpha = (rng.random((h, w)) > 0.3).astype(float)
        rgb = rng.random((h, w, 3))
        frames = tile_steps.cut_block(self.g, spec, rgb, alpha, (x0, y0), scale)
        self.assertEqual([f["index"] for f in frames], [0, 1, 2, 3])
        back = np.zeros((h, w))
        for f in sorted(frames, key=lambda f: (f["i"] + f["j"], f["i"])):
            sx, sy = G.screen_position(self.g, (f["i"], f["j"], 0))
            px = int(round((sx + spec["rect"][0] - x0) * scale))
            py = int(round((sy + spec["rect"][1] - y0) * scale))
            fa = f["alpha"][..., 0]
            sub = back[py:py + fa.shape[0], px:px + fa.shape[1]]
            sub[...] = np.maximum(sub, fa[:sub.shape[0], :sub.shape[1]])
        drawn = np.zeros((h, w), bool)
        for f in frames:
            sx, sy = G.screen_position(self.g, (f["i"], f["j"], 0))
            px = int(round((sx + spec["rect"][0] - x0) * scale))
            py = int(round((sy + spec["rect"][1] - y0) * scale))
            drawn[max(py, 0):py + 96, max(px, 0):px + 64] = True
        # Every pixel any tile's canvas holds is kept, by exactly its owner.
        self.assertTrue(np.array_equal((back > 0.5) & drawn, (alpha > 0.5) & drawn))

    def test_sprite_and_layout_problems(self):
        tmp = tempfile.mkdtemp(prefix="forge_tile_")
        try:
            p = os.path.join(tmp, "a.png")
            Image.new("RGB", (32, 40)).save(p)
            self.assertIn("has no transparency", tile_steps.sprite_problems(p, self.g)[0])
            Image.new("RGBA", (16, 16)).save(p)
            self.assertIn("16x16", tile_steps.sprite_problems(p, self.g)[0])
            lay = os.path.join(tmp, "l.json")
            with open(lay, "w") as f:
                json.dump({"format": "forge-tile-layout", "maps": [{"tiles": [{"at": [0, 0]}]}]}, f)
            self.assertTrue(tile_steps.layout_problems(lay, []))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SecondPilot(unittest.TestCase):
    """The fixes after the first real night (2026-10-21): a block's frames keep their columns,
    painted light is measured against the original's own, an object's picture is fitted to the
    original's silhouette, a wall's band only at its ends, a pattern made seamless in phase, the
    material picked by its detail at the sprites' scales, the colour and the delight of a mesh."""

    def setUp(self):
        self.g = G.grid_of({})

    def spec(self, **kw):
        s = {"kind": "object", "seam": None, "grid": [1, 1], "height": 1.0, "thickness": None, "rect": None,
             "cutout": "picture", "name": "t", "glow": None, "dome": 0.6, "alpha": "smooth", "clip": False,
             "relief": 1.0}
        s.update(kw)
        return s

    def test_a_field_block_gives_every_tile_its_column(self):
        # A 4 x 4 field, low (the original rises 0.5 level): every frame holds its own tile's floor
        # and what stands on it, so no frame is empty and a lone frame has no hole.
        spec = self.spec(grid=[4, 4])
        from forge.tile_jobs import block_canvas
        x0, y0, x1, y1 = block_canvas(self.g, spec)
        scale = 2
        h, w = int((y1 - y0) * scale), int((x1 - x0) * scale)
        sprite = synth.object_sprite(2, tall=0.5, wide=1.0)
        block, _ = tile_steps.compose_block(self.g, spec, sprite)
        alpha = tile_steps.resize(block[..., 3], [w, h], Image.NEAREST)
        rgb = np.random.default_rng(1).random((h, w, 3))
        tall = tile_steps.original_top(self.g, sprite) + tile_steps.COLUMN_MARGIN
        self.assertAlmostEqual(tall, 0.5 + 0.1, delta=0.1)
        frames = tile_steps.cut_block(self.g, spec, rgb, alpha, (x0, y0), scale, tall)
        orig = tile_steps.original_in_canvas(self.g, sprite, None, scale)[..., 3] > 0.5
        for f in frames:
            fa = f["alpha"][..., 0] > 0.5
            self.assertGreater(fa.sum(), 0, "frame %d is empty" % f["index"])
            # Each frame covers its own original (the tile alone) almost entirely.
            self.assertGreater((fa & orig).sum() / float(orig.sum()), 0.9, "frame %d" % f["index"])
            over, _ = tile_steps.footprint(fa.astype(float), orig.astype(float), None, scale, 1)
            self.assertLess(over, 0.08, "frame %d" % f["index"])
        # Drawn back to front, the frames give the picture again.
        back = np.zeros((h, w))
        for f in sorted(frames, key=lambda f: (f["i"] + f["j"], f["i"])):
            sx, sy = G.screen_position(self.g, (f["i"], f["j"], 0))
            px, py = int(round((sx - x0) * scale)), int(round((sy - y0) * scale))
            fa = f["alpha"][..., 0]
            ys0, xs0 = max(py, 0), max(px, 0)
            ys1, xs1 = min(py + fa.shape[0], h), min(px + fa.shape[1], w)
            sub = back[ys0:ys1, xs0:xs1]
            sub[...] = np.maximum(sub, fa[ys0 - py:ys1 - py, xs0 - px:xs1 - px])
        self.assertTrue(np.array_equal(back > 0.5, alpha > 0.5))

    def test_painted_light_is_measured_against_the_original(self):
        yy, xx = np.mgrid[0:64, 0:64] / 64.0
        mask = np.ones((64, 64), bool)
        orig = 0.3 + 0.4 * (1 - yy)  # the original is brighter at its top (a crop's tips)
        same = orig + 0.02 * np.sin(xx * 50)
        sun = orig * (0.6 + 0.8 * xx)  # a sun from the right painted over it
        v, v0 = tile_steps.painted_light(same, mask, orig, mask)
        self.assertGreater(v0, 0.7)
        self.assertLess(v, 0.05)
        v, _ = tile_steps.painted_light(sun, mask, orig, mask)
        self.assertGreater(v, 0.5)
        # The planar delight takes most of the painted light out, the original's own slope stays.
        rgb = np.repeat(sun[..., None], 3, -1)
        out, info = tile_steps.planar_delight(rgb, mask, np.repeat(orig[..., None], 3, -1), mask, 1, 1.0)
        self.assertGreater(info["shading_painted"], 0.5)
        self.assertLess(info["shading"], 0.1)
        lum = tile_steps.luminance(out)
        self.assertGreater(lum[:8].mean(), lum[-8:].mean() + 0.2)

    def test_a_picture_is_fitted_to_the_original(self):
        scale = 4
        target = np.zeros((160, 128), bool)
        target[60:140, 30:90] = True
        drawn = np.zeros((160, 128))
        drawn[48:140, 34:103] = 1.0  # 15% larger, off to the right
        fit = tile_steps.fit_silhouette(drawn, target, scale)
        self.assertGreater(fit["iou"], 0.88)
        self.assertLess(fit["iou_before"], 0.7)
        self.assertLess(fit["scale"], 1.0)
        moved, = tile_steps.apply_fit([drawn], fit, scale)
        iou = ((moved > 0.5) & target).sum() / float(((moved > 0.5) | target).sum())
        self.assertGreater(iou, 0.85)
        # Already in place: the identity.
        same = tile_steps.fit_silhouette(target.astype(float), target, scale)
        self.assertEqual((same["scale"], same["move"]), (1.0, [0.0, 0.0]))

    def test_a_wall_joins_only_at_its_ends(self):
        size = [128, 160]
        wall, _ = tile_steps.border_weight(self.spec(kind="west_wall", grid=[1, 2]), self.g, size, 0.08)
        self.assertEqual(float(wall[:4, 64].max()), 0.0)  # its top keeps the picture
        self.assertEqual(float(wall[-4:, 64].max()), 0.0)  # and its foot
        self.assertEqual(float(wall[80, 0]), 1.0)  # its ends are the group's material
        floor, _ = tile_steps.border_weight(self.spec(kind="floor", grid=[2, 2]), self.g, [128, 128], 0.08)
        self.assertEqual(float(floor[0, 64]), 1.0)

    def test_a_pattern_is_made_seamless_in_phase(self):
        h, w = 96, 200
        xx = np.arange(w)[None, :] * np.ones((h, 1))
        ridges = 0.5 + 0.35 * np.sin(2 * np.pi * xx / 23.0)  # a period that does not divide the width
        tex = np.repeat(ridges[..., None], 3, -1) + np.random.default_rng(0).random((h, w, 3)) * 0.03
        self.assertGreater(tile_steps.seam_ratio(tex, 1), 3)
        out, info = tile_steps.make_seamless(tex, "x")
        self.assertEqual(out.shape, tex.shape)
        self.assertLess(tile_steps.seam_ratio(out, 1), 1.5)
        # The ridges keep their contrast (a blended cross would average them to grey).
        self.assertGreater(out[..., 0].mean(axis=0).std(), 0.8 * ridges[0].std())
        noise = tile_steps.box_blur(np.random.default_rng(2).random((128, 128, 3)), 2)
        out, info = tile_steps.make_seamless(noise, "xy")
        self.assertLess(max(tile_steps.seam_ratio(out, 1), tile_steps.seam_ratio(out, 0)), 2.0)
        self.assertLess(info["x"]["stretch"], 1.25)

    def test_the_material_is_picked_by_its_detail_at_the_sprites_scale(self):
        from forge.scoring import score_tile_material
        tmp = tempfile.mkdtemp(prefix="forge_tile_")
        try:
            xx = np.arange(384)[None, :] * np.ones((384, 1))
            paths = {}
            for name, period in (("hairlines", 3.0), ("broad", 32.0)):
                v = 0.5 + 0.3 * np.sin(2 * np.pi * xx / period)
                rgb = np.dstack([v * 0.6, v * 0.7, v])
                paths[name] = os.path.join(tmp, name + ".png")
                Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8)).save(paths[name])
            fine = score_tile_material(paths["hairlines"], 384, 143, 36)[0]
            broad = score_tile_material(paths["broad"], 384, 143, 36)[0]
            self.assertGreater(broad, 2 * fine)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_colour_toward_the_original(self):
        dull = np.full((8, 8, 3), 0.5)
        dull[..., 1] = 0.55
        rich = np.zeros((8, 8, 3))
        rich[..., 1] = 0.7
        ones = np.ones((8, 8))
        gain = tile_steps.saturation_gain([dull], [ones], rich, ones, 0.5)
        self.assertEqual(gain, tile_steps.MAX_SATURATION_GAIN)
        self.assertEqual(tile_steps.saturation_gain([rich], [ones], dull, ones, 0.5), 1.0)  # never duller
        out = tile_steps.saturate(dull, 1.5)
        self.assertAlmostEqual(float(tile_steps.luminance(out).mean()), float(tile_steps.luminance(dull).mean()),
                               places=6)
        self.assertGreater(float(tile_steps.chroma(out).mean()), float(tile_steps.chroma(dull).mean()))
        # A seam group's record: one gain for all its pieces, the originals' brightness per kind
        # (of the pieces that are the material itself when there are any).
        floor = synth.sprite("floor", 1, (0.2, 0.6, 0.1), spread=0.05)
        burnt = synth.sprite("floor", 2, (0.1, 0.1, 0.1), spread=0.05)
        rec = tile_steps.group_colour(self.g, [{"kind": "floor", "material": True}, {"kind": "floor"}],
                                      [floor, burnt], np.full((32, 32, 3), 0.4), 0.5)
        self.assertGreater(rec["saturation_gain"], 1.0)
        self.assertAlmostEqual(rec["original_luminance"]["floor"],
                               float(tile_steps.luminance(floor[..., :3])[floor[..., 3] > 0.5].mean()), delta=0.03)

    def test_a_tree_loses_its_own_ground(self):
        # A tree drawn on its own grass diamond: the grass goes (the map's floor shows there), the
        # crown, the trunk and the darker shadow under the crown stay.
        scale = 2
        spec = self.spec()
        h, w = 40 * scale, 32 * scale
        diamond = G.column_mask(self.g, (0, 0), [0, 0, 32, 40], scale, 0.0)
        rgb = np.zeros((h, w, 3))
        rgb[...] = (0.45, 0.75, 0.25)
        rgb += np.random.default_rng(4).random((h, w, 3)) * 0.04
        alpha = diamond.astype(float)
        crown = np.zeros((h, w), bool)
        crown[8:56, 12:52] = True  # the crown reaches into the diamond's back part
        rgb[crown] = (0.1, 0.3, 0.1)
        shadow = diamond & ~crown
        shadow[:56] = False
        shadow[66:] = False  # under the crown, behind the diamond's front
        shadow[:, :24] = False
        shadow[:, 40:] = False
        rgb[shadow] *= 0.5
        alpha[crown] = 1.0
        out, share = tile_steps.ground_cut(self.g, spec, rgb, alpha, (0.0, 0.0), scale)
        bright_ground = diamond & ~crown & ~shadow
        self.assertLess(out[bright_ground].mean(), 0.15)
        self.assertGreater(out[crown].mean(), 0.95)
        self.assertGreater(out[shadow].mean(), 0.8)
        self.assertGreater(share, 0.2)

    def test_a_picture_on_a_mesh_loses_its_painted_light(self):
        # A box seen from the camera: its top and its two sides, the picture's left side painted
        # bright and its right side dark; the mesh's normals say which side is which.
        h, w = 60, 60
        nv = np.zeros((h, w, 3))
        side = {"up": G.to_view(self.g, np.array([0, 0, 1.0])), "x": G.to_view(self.g, np.array([1.0, 0, 0])),
                "y": G.to_view(self.g, np.array([0, 1.0, 0]))}
        nv[:20] = side["up"]
        nv[20:, :30] = side["y"]
        nv[20:, 30:] = side["x"]
        rgb = np.full((h, w, 3), 0.5)
        rgb[20:, :30] *= 1.3
        rgb[20:, 30:] *= 0.7
        out, gains = tile_steps.delight(rgb, np.ones((h, w)), nv, self.g, 1.0)
        lum = tile_steps.luminance(out)
        self.assertLess(abs(lum[30:, 5:25].mean() - lum[30:, 35:55].mean()), 0.05)
        self.assertEqual(sorted(gains), ["up", "x_side", "y_side"])


class _Files:
    """The factory's side the sprite step needs: references resolve to files."""

    def __init__(self, out):
        self.out = out

    def resolve(self, ref):
        return ref["file"], None

    def input_record(self, ref):
        return {}


class FirstNightLessons(unittest.TestCase):
    """What a real night of tiles taught: frames that overlap, small shapes strewn instead of one
    large shape per tile, a busy picture calmed to its floor, a face that keeps its layout."""

    def setUp(self):
        self.g = G.grid_of({})
        self.tmp = tempfile.mkdtemp(prefix="forge_tile_lessons_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spec(self, **kw):
        s = {"kind": "floor", "seam": None, "grid": [4, 4], "height": 1.0, "thickness": None, "rect": None,
             "cutout": "none", "name": "t", "glow": None, "dome": 0.0, "alpha": "binary", "scatter": 0,
             "trim": False, "match": None}
        s.update(kw)
        return s

    def one_shape_sprite(self):
        """A floor sprite of brown ground with one large green shape in the middle (a cabbage)."""
        spr = synth.sprite("floor", seed=4, base=(0.45, 0.3, 0.18), spread=0.05)
        yy, xx = np.mgrid[0:40, 0:32]
        blob = ((xx - 16) / 7.0) ** 2 + ((yy - 32) / 3.5) ** 2 < 1
        spr[blob & (spr[..., 3] > 0), :3] = (0.2, 0.6, 0.25)
        return spr

    def test_a_scattered_floor_strews_small_shapes_instead_of_a_grid(self):
        spr = self.one_shape_sprite()
        size = [512, 512]
        plain, _ = tile_steps.face_guide(self.g, self.spec(), spr, size, 0.6, None, 0)
        strewn, lay = tile_steps.face_guide(self.g, self.spec(scatter=3), spr, size, 0.6, None, 0, 7)
        self.assertEqual(lay["scatter"], 3)
        self.assertGreater(tile_steps.tile_repeat(plain, [4, 4], 128 / 24.0), 0.9)  # the original, tile by tile
        self.assertLess(tile_steps.tile_repeat(strewn, [4, 4], 128 / 24.0), 0.15)
        # Small shapes: the green covers about as much, in many patches far smaller than a tile.
        green = strewn[..., 1] - strewn[..., 0] > 0.15
        plain_green = plain[..., 1] - plain[..., 0] > 0.15
        self.assertGreater(green.mean(), 0.3 * plain_green.mean())
        rows = np.abs(np.diff(green.astype(int), axis=1)).sum(1).mean()
        plain_rows = np.abs(np.diff(plain_green.astype(int), axis=1)).sum(1).mean()
        self.assertGreater(rows, 1.5 * plain_rows)  # more, smaller patches along every row
        # The same seed gives the same guide (the guide is the piece's, shared by its variants).
        again, _ = tile_steps.face_guide(self.g, self.spec(scatter=3), spr, size, 0.6, None, 0, 7)
        self.assertTrue(np.array_equal(strewn, again))

    def test_repeat_of_identical_and_unrelated_tiles(self):
        rng = np.random.default_rng(3)
        one = rng.random((32, 32, 3))
        self.assertAlmostEqual(tile_steps.tile_repeat(np.tile(one, (4, 4, 1)), [4, 4], 1), 1.0, places=3)
        self.assertLess(abs(tile_steps.tile_repeat(rng.random((128, 128, 3)), [4, 4], 1)), 0.05)
        self.assertIsNone(tile_steps.tile_repeat(one, [1, 1], 1))

    def test_a_busy_picture_is_calmed_to_its_floor(self):
        rng = np.random.default_rng(5)
        base = np.full((128, 128, 3), 0.4)
        material = np.clip(base + rng.normal(0, 0.04, (128, 128, 3)), 0, 1)
        loud = np.clip(base + rng.normal(0, 0.12, (128, 128, 3)), 0, 1)  # loud flowers
        mask = np.ones((128, 128), bool)
        ref = tile_steps.detail_contrast(material, mask, 4, True)
        s = dict(tile_steps.DEFAULTS, max_busy=1.4, calm=0.5)
        out, before, after = tile_steps.calm(loud, mask, 4, ref, s, wrap=True)
        self.assertGreater(before, 2.5)
        self.assertGreater(after, 1.4)  # calmed by at most half: still over, the sprite flags it
        self.assertLess(after, before * 0.55)
        mild = np.clip(base + rng.normal(0, 0.07, (128, 128, 3)), 0, 1)
        out, before, after = tile_steps.calm(mild, mask, 4, ref, s, wrap=True)
        self.assertGreater(before, 1.5)
        self.assertLessEqual(after, 1.4)
        self.assertLess(abs(out.mean() - mild.mean()), 0.01)  # the broad colours are kept
        # calm 1: never calmed.
        same, _, after = tile_steps.calm(mild, mask, 4, ref, dict(s, calm=1.0), wrap=True)
        self.assertTrue(np.array_equal(same, mild))
        self.assertGreater(after, 1.4)

    def test_a_face_keeps_its_guides_layout(self):
        xx = np.mgrid[0:128, 0:128][1] / 128.0
        guide = np.dstack([0.15 + 0 * xx, 0.25 + 0 * xx, 0.6 + 0 * xx])  # a blue roof
        guide[:, :12] = (0.9, 0.85, 0.3)  # a yellow flashing along its edge
        mask = np.ones((128, 128), bool)
        rng = np.random.default_rng(2)
        kept = np.clip(guide * 0.9 + rng.normal(0, 0.05, guide.shape), 0, 1)
        scene = guide.copy()
        scene[30:100, 30:100] = (0.3, 0.55, 0.2)  # a field and a building painted over the strip
        scene[50:80, 45:85] = (0.5, 0.35, 0.25)
        self.assertLess(tile_steps.layout_difference(kept, guide, mask, 10), 0.05)
        self.assertGreater(tile_steps.layout_difference(scene, guide, mask, 10), 0.12)

    def test_trim_and_cut_limits(self):
        face = tile_steps._settings({}, self.spec())
        self.assertEqual((face["overlap"], face["max_layout"]), (0.75, 0.22))
        self.assertEqual(tile_steps._settings({}, self.spec(trim=True))["max_layout"], 0.12)
        self.assertEqual(tile_steps._settings({}, self.spec(kind="west_wall", cutout="picture"))["overlap"], 0.0)
        obj = tile_steps._settings({}, self.spec(kind="object", alpha="smooth"))
        self.assertEqual(obj["overlap"], 0.0)
        self.assertEqual(tile_steps._settings({}, self.spec(kind="object", trim=True))["max_overflow"], 0.04)
        self.assertEqual(tile_steps._settings({"args": {"overlap": 0.5}}, self.spec())["overlap"], 0.5)

    def sprite_of(self, spec, overlap):
        """The frames of a floor that is its group's material (no picture), at scale 4."""
        spr = synth.sprite("floor", seed=6)
        sp = os.path.join(self.tmp, "floor.png")
        Image.fromarray(np.round(spr * 255).astype(np.uint8), "RGBA").save(sp)
        rng = np.random.default_rng(8)
        mat = np.clip(0.4 + rng.normal(0, 0.05, (128, 128, 3)) * np.array([1.0, 1.4, 0.6]), 0, 1)
        mp = tile_steps.save_rgba(os.path.join(self.tmp, "mat.png"), mat)
        d = os.path.join(self.tmp, "o%s" % overlap)
        os.makedirs(d)
        job = {"id": "f/p/v1/sprite", "args": {"tile": spec, "grid": {}, "scale": 4, "size": [128, 128],
                                              "overlap": overlap},
               "inputs": {"sprite": {"file": sp}, "texture": {"file": mp}, "material": {"file": mp}}}
        tile_steps.step_sprite(_Files(d), job, d, {})
        from forge.factory_util import slug
        out = os.path.join(d, slug(job["id"]))
        with open(os.path.join(out, "tile.json"), encoding="utf-8") as fh:
            return json.load(fh), out

    def test_a_face_frame_overlaps_its_neighbours(self):
        spec = self.spec(seam="g", grid=[2, 2])
        exact, d0 = self.sprite_of(spec, 0.0)
        grown, d1 = self.sprite_of(spec, 0.75)
        self.assertEqual((exact["overlap"], grown["overlap"]), (0, 3))
        f = exact["frames"][0]["file"]
        a0 = np.asarray(Image.open(os.path.join(d0, f))).astype(float)
        a1 = np.asarray(Image.open(os.path.join(d1, f))).astype(float)
        self.assertEqual(sorted(set(a1[..., 3].ravel().tolist())), [0, 255])  # still binary
        inside, more = a0[..., 3] > 0, a1[..., 3] > 0
        self.assertTrue(np.array_equal(inside & more, inside))  # the exact face is kept
        ring = more & ~inside
        # About 3 art pixels all around the diamond (its outline is some 4 x 72 pixels long).
        self.assertGreater(ring.sum(), 2 * 4 * 72)
        self.assertLess(ring.sum(), 4.5 * 4 * 72)
        # The ring takes the colour of the face beside it, not the transparent black.
        self.assertGreater(a1[ring][:, :3].mean(), 0.6 * a0[inside][:, :3].mean())
        n1 = np.asarray(Image.open(os.path.join(d1, exact["frames"][0]["normal"])))
        self.assertTrue((n1[ring][:, 3] == 255).all())


STYLE = {"global": "Plain test style.", "view_tile_floor": "A flat texture.", "view_tile_wall": "A flat wall face.",
         "view_tile_object": "The object alone on grey.", "material_top": "A seamless texture of",
         "material_front": "A seamless wall of", "material_negative": "text"}


def order_of(pieces, family=None):
    fam = {"id": "f", "recipe": "iso_tile", "variants": 2, "seams": {
        "ground": {"kind": "floor", "brief": "gravel", "span": [2, 2]},
        "wood": {"kind": "wall", "brief": "boards", "span": 2}}, "assets": pieces}
    fam.update(family or {})
    return {"version": 1, "style": dict(STYLE), "families": [fam]}


def piece(pid, kind, sprite, **kw):
    p = {"id": pid, "brief": "A test %s." % kind, "tile": dict({"kind": kind}, **kw.pop("tile", {})),
         "inputs": {"sprite": sprite}}
    p.update(kw)
    return p


class Recipe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = make_sprites()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_tile_")
        self.config = load_config(None)
        self.config["blender_python"] = sys.executable
        self.order_path = os.path.join(self.tmp, "order.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        with open(self.order_path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(self.order_path)

    def problems(self, *args, **kw):
        return check_order(self.load(order_of(*args, **kw)), self.config)

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    def test_expansion(self):
        f = self.files
        jobs = {j["id"]: j for j in expand(self.load(order_of([
            piece("a", "floor", f["stone"], tile={"seam": "ground"}),
            piece("w", "west_wall", f["plank_w"], tile={"seam": "wood"}),
            piece("o", "object", f["bush"], tile={"grid": [2, 2], "rect": [0, -8, 32, 48]}),
            piece("m", "object", f["crate"], stages={"form": "mesh"}),
            piece("b", "floor", f["grass"], tile={"seam": "ground"}, skip=["picture"])])))}
        s = seeds_for("f/a", 2)
        self.assertEqual([j for j in jobs if j.startswith("f/a/")],
                         ["f/a/guide"] + ["f/a/v%d/%s" % (x, st) for x in s for st in ("picture", "blend", "edges",
                                                                                          "sprite")] +
                         ["f/a/review", "f/a/final"])
        sp = jobs["f/a/v%d/sprite" % s[0]]
        self.assertEqual(sp["inputs"]["texture"], {"ref": "f/a/v%d/edges" % s[0], "role": "rgb"})
        self.assertEqual(sp["inputs"]["material"], {"ref": "f/_seam_ground/seamless", "role": "rgb"})
        self.assertEqual(sp["args"]["tile"]["grid"], [2, 2])  # the group's span
        self.assertEqual(jobs["f/w/v%d/sprite" % seeds_for("f/w", 1)[0]]["args"]["tile"]["grid"], [1, 2])
        self.assertEqual(jobs["f/a/v%d/picture" % s[0]]["values"]["$IMAGE1$"], {"ref": "f/a/guide", "role": "guide"})
        self.assertIn("A flat texture.", jobs["f/a/v%d/picture" % s[0]]["values"]["$PROMPT$"])
        # A floor that is its group's material: one variant, no picture.
        self.assertEqual([j for j in jobs if j.startswith("f/b/")],
                         ["f/b/v%d/sprite" % seeds_for("f/b", 1)[0], "f/b/review", "f/b/final"])
        mv = seeds_for("f/m", 1)[0]
        self.assertEqual([j for j in jobs if j.startswith("f/m/v%d/" % mv)],
                         ["f/m/v%d/%s" % (mv, st) for st in ("picture", "cutout", "mesh", "paint", "render", "sprite")])
        self.assertEqual(jobs["f/m/v%d/render" % mv]["tool"], "tile_render")
        ob = jobs["f/o/v%d/sprite" % seeds_for("f/o", 1)[0]]
        self.assertEqual(ob["inputs"]["guide_layout"], {"ref": "f/o/guide", "role": "layout"})
        for g in ("ground", "wood"):
            self.assertIn("f/_seam_%s/seamless" % g, jobs)
        self.assertEqual(jobs["f/_seam_wood/raw_s%d" % seeds_for("f/_seam_wood", 1)[0]]["values"]["$WIDTH$"], 768)
        sheet = jobs["f/_set/sheet"]
        self.assertEqual(sheet["from"], ["f/%s/final" % x for x in ("a", "w", "o", "m", "b")])
        self.assertEqual(jobs["f/a/final"]["accept"], {"mesh_flags": 0})
        # A seam group's material: picked by its detail at the sprites' scales, made seamless by
        # the local cut (no inpaint), which also writes the group's colour record.
        self.assertEqual(jobs["f/_seam_ground/pick"]["metric"], "tile_material")
        self.assertEqual(jobs["f/_seam_ground/seamless"]["kind"], "tile_seamless")
        self.assertEqual(jobs["f/_seam_ground/seamless"]["args"]["axes"], "xy")
        self.assertEqual(jobs["f/_seam_wood/seamless"]["args"]["axes"], "x")
        self.assertFalse(any(j.startswith("f/_seam_ground/rolled") for j in jobs))
        self.assertEqual(sp["inputs"]["group_colour"], {"ref": "f/_seam_ground/seamless", "role": "colour"})
        # A meshed object's sprite also gets its picture (laid on the render) and the guide's layout.
        ms = jobs["f/m/v%d/sprite" % mv]
        self.assertEqual(ms["inputs"]["guide_layout"], {"ref": "f/m/guide", "role": "layout"})
        self.assertEqual(ms["inputs"]["mask"], {"ref": "f/m/v%d/picture" % mv, "role": "mask"})
        # A repainted face's sprite checks its layout against the piece's guide.
        self.assertEqual(sp["inputs"]["guide"], {"ref": "f/a/guide", "role": "guide"})
        self.assertNotIn("guide", jobs["f/b/v%d/sprite" % seeds_for("f/b", 1)[0]]["inputs"])
        # The inpainted cross is still an option of a seam group.
        old = {j["id"]: j for j in expand(self.load(order_of([
            piece("a", "floor", f["stone"], tile={"seam": "ground"})],
            {"seams": {"ground": {"kind": "floor", "brief": "gravel", "span": [2, 2], "seamless": "inpaint"}}})))}
        self.assertEqual(old["f/_seam_ground/seamless"]["kind"], "unroll")
        self.assertNotIn("group_colour", old["f/a/v%d/sprite" % s[0]]["inputs"])

    def test_expansion_of_the_first_nights_lessons(self):
        from forge import tile_jobs
        f = self.files
        seams = {"ground": {"kind": "floor", "brief": "gravel", "span": [2, 2]},
                 "wood": {"kind": "wall", "brief": "boards", "span": 2},
                 "meadow": {"kind": "floor", "brief": "short grass", "span": [4, 4]}}
        pieces = [piece("c", "floor", f["stone"], tile={"seam": "ground", "scatter": 3}),
                  piece("e", "floor", f["stone"], tile={"trim": True}),
                  piece("m", "object", f["bush"], tile={"grid": [2, 2], "match": "meadow", "scatter": 2})]
        order = order_of(pieces, {"seams": seams})
        jobs = {j["id"]: j for j in expand(self.load(order))}
        c = seeds_for("f/c", 1)[0]
        self.assertIn(tile_jobs.SCATTER_TEXT, jobs["f/c/v%d/picture" % c]["values"]["$PROMPT$"])
        self.assertEqual(jobs["f/c/guide"]["args"]["scatter_seed"], seeds_for("f/c/scatter", 1)[0])
        e = seeds_for("f/e", 1)[0]
        self.assertIn(tile_jobs.TRIM_TEXT, jobs["f/e/v%d/picture" % e]["values"]["$PROMPT$"])
        self.assertNotIn("scatter_seed", jobs["f/e/guide"]["args"])
        mv = seeds_for("f/m", 1)[0]
        m = jobs["f/m/v%d/sprite" % mv]
        self.assertEqual(m["inputs"]["match"], {"ref": "f/_seam_meadow/seamless", "role": "rgb"})
        self.assertEqual(m["args"]["match_span"], [4, 4])
        self.assertIn(tile_jobs.SCATTER_TEXT, jobs["f/m/v%d/picture" % mv]["values"]["$PROMPT$"])
        self.assertIn("f/_seam_meadow/seamless", jobs)  # made for the object alone
        # The family's style may word the sentences itself.
        order["style"]["view_tile_trim"] = "Just the strip."
        jobs = {j["id"]: j for j in expand(self.load(order))}
        self.assertTrue(jobs["f/e/v%d/picture" % e]["values"]["$PROMPT$"].endswith("Just the strip."))
        self.assertEqual(self.problems(pieces, {"seams": seams}), [])

    def test_problems(self):
        f = self.files
        self.assertEqual(self.problems([piece("a", "floor", f["stone"], tile={"seam": "ground"})]), [])
        self.assertProblem(self.problems([piece("a", "roof", f["stone"])]), "tile.kind must be one of")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"seam": "wood"})]), "is a wall group")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"seam": "x"})]), "no seam group")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"seam": "ground", "grid": [1, 1]})]),
                           "differs from its seam group's span")
        self.assertProblem(self.problems([piece("a", "west_wall", f["plank_w"], tile={"grid": [2, 1]})]),
                           "tile.grid must be [1, n]")
        self.assertProblem(self.problems([piece("a", "object", f["crate"], skip=["picture"])]),
                           "cannot skip the picture")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], skip=["picture"])]), "needs tile.seam")
        self.assertProblem(self.problems([piece("a", "object", f["crate"], tile={"grid": [2, 1]},
                                                stages={"form": "mesh"})]), "form mesh makes one object")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], stages={"form": "mesh"})]),
                           "is for objects")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"wobble": 1})]), "unknown tile key")
        self.assertProblem(self.problems([piece("a", "floor", f["layout"])]), "inputs.sprite: cannot be read")
        self.assertProblem(self.problems([{"id": "a", "brief": "x", "tile": {"kind": "floor"}}]),
                           "inputs.sprite is required")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], params={"scale": 0})]), "params.scale")
        self.assertProblem(self.problems([piece("_a", "floor", f["stone"])]), "cannot start with _")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"])], {"seams": {"g": {"kind": "roof"}}}),
                           "kind must be floor or wall")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"])], {"layout": "missing.json"}),
                           "layout not found")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], stages={
            "sprite": {"settings": {"max_overflow": 3}}})]), "max_overflow")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"glow": "plaid"})]), "tile.glow")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"clip": True})]),
                           "tile.clip is for objects")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"relief": 9})]), "tile.relief")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"ground": "cut"})]),
                           "tile.ground cut is for objects")
        self.assertProblem(self.problems([piece("a", "object", f["bush"], tile={"ground": "dig"})]),
                           "tile.ground must be keep or cut")
        self.assertEqual(self.problems([piece("a", "object", f["crate"], tile={"clip": True})]), [])
        self.assertProblem(self.problems([piece("a", "west_wall", f["plank_w"], tile={"scatter": 2})]),
                           "tile.scatter is for floors and objects")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"scatter": 9})]),
                           "tile.scatter must be 0 or")
        self.assertProblem(self.problems([piece("a", "object", f["crate"], tile={"scatter": 2},
                                                stages={"form": "mesh"})]), "tile.scatter is for floors")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"trim": "yes"})]),
                           "tile.trim must be true or false")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"seam": "ground", "trim": True},
                                                skip=["picture"])]), "tile.trim needs the picture stage")
        self.assertProblem(self.problems([piece("a", "object", f["bush"], tile={"match": "x"})]),
                           "tile.match 'x' is no seam group")
        self.assertProblem(self.problems([piece("a", "object", f["bush"], tile={"match": "wood"})]),
                           "is a wall group")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"], tile={"match": "ground"})]),
                           "tile.match is for objects")
        self.assertProblem(self.problems([piece("a", "floor", f["stone"])], {"seams": {"g": {
            "kind": "floor", "brief": "x", "seamless": "glue"}}}), "seamless must be one of")
        order = order_of([piece("a", "floor", f["stone"], tile={"seam": "ground"})])
        del order["style"]["view_tile_floor"]
        self.assertProblem(check_order(self.load(order), self.config), "style.view_tile_floor is required")
        bad = order_of([dict(piece("a", "floor", f["stone"]), recipe="material")], {"recipe": "material"})
        self.assertProblem(check_order(self.load(bad), self.config), "does not use tile")

    def sample(self):
        return load_order(SAMPLE)

    def test_sample_order(self):
        order = self.sample()
        self.assertEqual(check_order(order, self.config), [])
        env = check_environment(order, self.config, object_info())
        self.assertEqual((env.status, env.problems), (VERIFIED, []))

    def test_check_and_estimate_from_the_command_line(self):
        fake = FakeComfy()
        try:
            fake.object_info = object_info()
            config = os.path.join(self.tmp, "forge.local.json")
            with open(config, "w", encoding="utf-8") as fh:
                json.dump({"blender_python": sys.executable}, fh)
            base = [sys.executable, "-m", "forge", "--order", SAMPLE, "--out", os.path.join(self.tmp, "out"),
                    "--config", config, "--server", fake.url]
            res = subprocess.run(base + ["--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("order: valid", res.stdout)
            self.assertIn("environment: verified", res.stdout)
            res = subprocess.run(base + ["--estimate"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("total 84 jobs", res.stdout)
            self.assertFalse(any(r[1] == "POST" for r in fake.requests))
        finally:
            fake.close()


def stand_in_render(mesh, sprite, out, config, tile, grid=None, scale=4, samples=None):
    """The render's stand-in: the original sprite upscaled, a flat normal facing the viewer."""
    os.makedirs(out, exist_ok=True)
    g = G.grid_of(grid)
    x0, y0, cw, ch = G.canvas(g, tile.get("rect"))
    im = Image.open(sprite).convert("RGBA").resize((int(cw * scale), int(ch * scale)), Image.NEAREST)
    im.save(os.path.join(out, "albedo.png"))
    a = np.asarray(im)[..., 3]
    n = np.zeros(a.shape + (4,), np.uint8)
    n[..., :3] = (128, 128, 255)
    n[..., 3] = a
    Image.fromarray(n, "RGBA").save(os.path.join(out, "normal.png"))
    with open(os.path.join(out, "fit.json"), "w") as f:
        json.dump({"fit": {"iou": 1.0}}, f)
    return {"albedo": os.path.join(out, "albedo.png"), "normal": os.path.join(out, "normal.png"),
            "fit": os.path.join(out, "fit.json")}


class Night(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_sprites()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_tile_night_")
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_night(self, order_path):
        a = SimpleNamespace(order=order_path, order_base="", out=self.out, only="", retry_failed=False, limit=6,
                            one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.config = load_config(None)
        f.config["blender_python"] = sys.executable
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        calls = []

        def render(*args, **kw):
            calls.append(args[2])
            return stand_in_render(*args, **kw)
        with patch.object(local_gpu, "run_tile_render", render), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh), calls

    def test_sample_night(self):
        m, calls = self.run_night(SAMPLE)
        self.assertEqual(sorted(k for k, v in m.items() if v.get("status") != "done"), [])
        self.assertEqual(len(calls), 2)  # the crate's two variants
        yard = os.path.join(self.out, "yard")
        with open(os.path.join(yard, "stone", "pick_final.json"), encoding="utf-8") as fh:
            pick = json.load(fh)
        self.assertEqual((pick["outcome"], pick["review"]), ("picked", "pending"))
        final = sorted(os.listdir(os.path.join(yard, "stone", "final")))
        self.assertIn("stone_v03_normal.png", final)  # the group's 2 x 2 span: four frames
        with open(os.path.join(yard, "stone", "final", "tile.json"), encoding="utf-8") as fh:
            tile = json.load(fh)
        self.assertEqual((tile["scale"], tile["size"], tile["variant_grid"]), (4, [128, 160], [2, 2]))
        im = Image.open(os.path.join(yard, "stone", "final", tile["frames"][0]["file"]))
        self.assertEqual((im.size, im.mode), ((128, 160), "RGBA"))
        self.assertEqual(sorted(set(np.asarray(im)[..., 3].ravel().tolist())), [0, 255])  # binary alpha: floors join
        # The fake pictures are blobs on a light ground: the fence's cut from its picture overflows
        # the original's silhouette and is flagged, no variant of it passes; the bush's blob is fitted
        # (scaled and moved) onto the original's silhouette and passes.
        with open(os.path.join(yard, "fence", "pick_final.json"), encoding="utf-8") as fh:
            fence = json.load(fh)
        self.assertEqual(fence["outcome"], "no_variant_passed")
        self.assertTrue(any(r.startswith("mesh: footprint:") for r in fence["candidates"][0]["reasons"]))
        self.assertEqual(fence["responsible_stage"], "picture")
        sheet = os.path.join(yard, "_set", "sheet")
        with open(os.path.join(sheet, "set_sheet.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        # The fake pictures of the north plank wall are blobs far from its guide's planks: its layout
        # is flagged on both variants (the west twin's blobs land close enough).
        self.assertEqual((info["maps"], info["not_picked"]), (["yard"], ["fence", "plank_n"]))
        with open(os.path.join(yard, "plank_n", "pick_final.json"), encoding="utf-8") as fh:
            plank = json.load(fh)
        self.assertTrue(all(any(r.startswith("mesh: layout:") for r in c["reasons"]) for c in plank["candidates"]))
        # A floor's frames are grown past the exact face (overlap, 0.75 grid pixel: 3 art pixels here).
        self.assertEqual(tile["overlap"], 3)
        self.assertTrue(os.path.isfile(os.path.join(sheet, "set_sheet.png")))
        self.assertTrue(os.path.isfile(os.path.join(yard, "stone", "review", "sheet.png")))
        # A second run changes nothing.
        m2, calls = self.run_night(SAMPLE)
        self.assertEqual(calls, [])
        self.assertEqual({k: v["spec"] for k, v in m.items()}, {k: v["spec"] for k, v in m2.items()})


class RenderSmoke(unittest.TestCase):
    """The Blender render of a mesh object on the CPU (skipped without blender_python)."""

    def setUp(self):
        cfg = os.path.join(ROOT, "forge.local.json")
        self.config = load_config(cfg if os.path.isfile(cfg) else None)
        if not self.config.get("blender_python") or not os.path.isfile(self.config["blender_python"]):
            self.skipTest("blender_python is not configured")
        self.folder = os.path.join(ROOT, "local", "smoke", "iso_tile")
        os.makedirs(self.folder, exist_ok=True)

    def test_render_fits_the_original(self):
        glb = os.path.join(self.folder, "box.glb")
        script = os.path.join(HERE, "fixtures", "iso_tile", "make_box.py")
        if not os.path.isfile(glb):
            subprocess.run([self.config["blender_python"], script, glb], check=True, capture_output=True, timeout=300)
        sprite = synth.save(synth.object_sprite(6, tall=0.7, wide=0.6), os.path.join(self.folder, "box.png"))
        out = os.path.join(self.folder, "render")
        files = local_gpu.run_tile_render(glb, sprite, out, self.config, {"kind": "object", "grid": [1, 1]}, scale=4,
                                          samples=4)
        with open(files["fit"], encoding="utf-8") as fh:
            fit = json.load(fh)["fit"]
        self.assertGreater(fit["iou"], 0.6)
        albedo = np.asarray(Image.open(files["albedo"]).convert("RGBA")).astype(float)
        self.assertEqual(albedo.shape[:2], (160, 128))
        opaque = albedo[..., 3] > 127
        self.assertGreater(opaque.sum(), 500)
        self.assertGreater(albedo[opaque][:, 0].mean(), albedo[opaque][:, 2].mean())  # the box's orange paint
        normal = G.decode_normal(G.grid_of({}), np.asarray(Image.open(files["normal"]).convert("RGB")))
        up = G.to_view(G.grid_of({}), np.array([0, 0, 1.0]))
        top = (normal @ up > 0.98) & opaque
        self.assertGreater(top.sum(), 50)  # the lid faces up, in the sprite's view
        self.assertGreater((normal[..., 2][opaque] > 0).mean(), 0.95)  # everything seen faces the viewer


if __name__ == "__main__":
    unittest.main()
