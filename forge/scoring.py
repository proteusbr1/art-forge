"""Automatic pick scores (sharpness and clean background for concepts; detail and evenness
of light for materials; detail for illustrations) and the image helpers of the local steps."""

import os

import numpy as np
from PIL import Image, ImageFilter


def load_rgba(path):
    im = Image.open(path)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return np.asarray(im).astype(np.float32) / 255.0


def laplacian_var(lum, mask=None):
    lap = (np.roll(lum, 1, 0) + np.roll(lum, -1, 0) + np.roll(lum, 1, 1) + np.roll(lum, -1, 1) - 4 * lum)[1:-1, 1:-1]
    if mask is not None:
        m = mask[1:-1, 1:-1] > 0.5
        if m.sum() < 100:
            return 0.0
        return float(lap[m].var())
    return float(lap.var())


def score_object(path):
    x = load_rgba(path)
    a = x[..., 3]
    rgb = x[..., :3]
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    cover = float((a > 0.5).mean())
    border = np.concatenate([a[:4].ravel(), a[-4:].ravel(), a[:, :4].ravel(), a[:, -4:].ravel()])
    touches = float((border > 0.5).mean())
    # Fragments: share of the alpha outside the bounding box of its main mass (2-98 %).
    ys, xs = np.nonzero(a > 0.5)
    frag = 0.0
    if len(xs):
        x0, x1 = np.percentile(xs, [2, 98])
        y0, y1 = np.percentile(ys, [2, 98])
        inside = ((xs >= x0) & (xs <= x1) & (ys >= y0) & (ys <= y1)).mean()
        frag = float(1 - inside)
    # Background: the RGB picture outside the object should be one flat light colour.
    bg = (a < 0.05)
    bg_std = float(rgb[bg].std()) if bg.sum() > 1000 else 0.5
    bg_mean = float(rgb[bg].mean()) if bg.sum() > 1000 else 0.0
    sharp = laplacian_var(lum, a)
    s = np.sqrt(sharp) * 100
    s *= 1.0 if 0.06 <= cover <= 0.7 else 0.4
    s *= max(0.1, 1 - 6 * touches)
    s *= max(0.2, 1 - 4 * frag)
    s *= max(0.2, 1 - 4 * bg_std)
    s *= 1.0 if bg_mean > 0.75 else 0.7
    return float(s), {"sharpness": round(sharp * 1e4, 3), "coverage": round(cover, 3), "touches_border": round(touches, 3),
                      "fragments": round(frag, 3), "background_std": round(bg_std, 3),
                      "background_mean": round(bg_mean, 3)}


def blur(a, r):
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r))).astype(np.float32) / 255


def score_material(path):
    x = load_rgba(path)[..., :3]
    lum = x @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    n = lum.shape[0]
    low = blur(lum, n / 8)
    even = float(low.std())
    detail = float(np.abs(lum - blur(lum, 2)).mean())
    sat = float((x.max(-1) - x.min(-1)).mean())
    s = detail * 100 * max(0.1, 1 - 5 * even) * (0.7 + sat)
    return float(s), {"detail": round(detail, 4), "uneven_light": round(even, 4), "saturation": round(sat, 3)}


def score_image(path):
    x = load_rgba(path)[..., :3]
    lum = x @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    detail = float(np.abs(lum - blur(lum, 2)).mean())
    sat = float((x.max(-1) - x.min(-1)).mean())
    contrast = float(lum.std())
    return float(detail * 100 * (0.7 + sat) * (0.5 + contrast)), {"detail": round(detail, 4), "saturation": round(sat, 3),
                                                                 "contrast": round(contrast, 3)}


def score_tile_material(path, texels_per_tile=384, frame_px_per_tile=143.0, map_px_per_tile=36.0):
    """A seam group's material for tile sprites: its detail where it will be seen, at the
    frame's scale and at a map's (a pattern finer than a few texels there, a corrugation of
    hairlines, averages to a flat grey and scores low), times the evenness of its light and its
    saturation, as score_material."""
    x = load_rgba(path)[..., :3]
    lum = x @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    n = lum.shape[0]
    even = float(blur(lum, n / 8).std())
    details = {}
    for name, px in (("frame", frame_px_per_tile), ("map", map_px_per_tile)):
        f = min(1.0, float(px) / float(texels_per_tile))
        im = Image.fromarray(np.clip(lum * 255, 0, 255).astype(np.uint8))
        small = im.resize((max(8, int(round(im.width * f))), max(8, int(round(im.height * f)))), Image.BOX)
        a = np.asarray(small).astype(np.float32) / 255
        details[name] = float(np.abs(a - blur(a, 1)).mean())
    detail = float(np.sqrt(max(details["frame"], 0.0) * max(details["map"], 0.0)))
    sat = float((x.max(-1) - x.min(-1)).mean())
    s = detail * 100 * max(0.1, 1 - 5 * even) * (0.7 + sat)
    return float(s), {"detail_frame": round(details["frame"], 4), "detail_map": round(details["map"], 4),
                      "uneven_light": round(even, 4), "saturation": round(sat, 3)}


METRICS = {"object": score_object, "material": score_material, "image": score_image,
           "tile_material": score_tile_material}


def cross_mask(h, w, band, feather, axes="xy"):
    yy = np.abs(np.arange(h) - (h - 1) / 2.0)[:, None] / h
    xx = np.abs(np.arange(w) - (w - 1) / 2.0)[None, :] / w

    def ramp(d):
        t = np.clip((band - d) / feather, 0.0, 1.0)
        return t * t * (3 - 2 * t)
    bar = ramp(xx) * np.ones((h, 1))
    return bar if axes == "x" else np.maximum(bar, ramp(yy) * np.ones((1, w)))


def save_rgb(path, arr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(np.clip(arr * 255 + 0.5, 0, 255).astype(np.uint8)).save(path)
