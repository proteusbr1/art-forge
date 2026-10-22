"""The views of the paint option `hunyuan_concept` (no GPU, NumPy and Pillow).

Hunyuan3D Paint generates six views of the mesh (azimuths 0, 90, 180, 270, top, bottom) from
the piece's cutout and bakes them. Its views are soft and darker than the cutout, and the view
that shows what the cutout shows gets no more of the cutout's detail than the others. This step
works between the generation and the bake:

1. Each view's silhouette is read from its position map; the side view that shows what the
   cutout shows is the one the cutout's silhouette fits best (a uniform scale and a shift found
   by the bounding box, then a small search on the overlap, IoU), a view and its opposite told
   apart by their colours (their silhouettes are mirror images).
2. When the fit is good enough (`min_fit`, default 0.88: the cutout is an orthographic view of
   the mesh, as a held item's strict side view or a figure's front), the cutout itself replaces
   the view where the surface faces the cutout's camera (fully within about 40 degrees, fading out by 55; the surface direction comes from
   the normal map, the camera's from VIEW_AXES). With `match_colour` every view's colours are
   first brought to the cutout's (mean and spread of L*, a*, b* inside the fitted view, applied
   to every view); off by default: in the comparison that chose this step it bleached saturated
   accents and made the views disagree more at their seams.
3. Every view is enlarged to `size` (Lanczos): the bake samples each view at its own size.

Without a good fit (a 3/4 concept, a mesh that does not match its cutout) the views are only
enlarged: the bake is Hunyuan3D Paint's own.

`make_views` returns the views, the metal-roughness views, the bake's view weights and a record
of what was done (`paint_views.json`)."""

import json

import numpy as np
from PIL import Image, ImageFilter

SIDE = (0, 1, 2, 3)
DEFAULT_WEIGHTS = [1, 0.5, 1, 0.5, 1, 1]   # Hunyuan3D Paint's own (forge/workflows/hunyuan21_paint_mesh.json)
CONCEPT_WEIGHTS = [1, 1, 1, 1, 0.5, 0.5]   # the four sides alike once the cutout is in one of them
# The direction toward each view's camera in Hunyuan3D Paint's normal maps (stored as (n + 1) / 2):
# the mean normal of a view's surface points this way on every mesh measured.
VIEW_AXES = ((0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
FACING = (0.57, 0.77)                      # cosines: no cutout below, all of it above
GAIN = (0.67, 1.5)                         # limits of the spread's change per Lab channel


# --- images

def load(path, mode="RGB"):
    return np.asarray(Image.open(path).convert(mode)).astype(np.float64) / 255.0


def to_image(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def resize(a, size, method=Image.LANCZOS):
    if a.shape[0] == size and a.shape[1] == size:
        return a
    return np.asarray(to_image(a).resize((size, size), method)).astype(np.float64) / 255.0


def resize_mask(m, size):
    if m.shape[0] == size:
        return m
    return np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((size, size), Image.NEAREST)) > 127


def erode(m, px):
    if px <= 0:
        return m
    im = Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(2 * px + 1))
    return np.asarray(im) > 127


def blur(a, radius):
    """Gaussian blur of a float image (H, W) or (H, W, C)."""
    if radius <= 0:
        return a
    r = max(1, int(3 * radius + 0.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / float(radius)) ** 2)
    k /= k.sum()
    out = a.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        n = out.shape[axis]
        out = sum(w * np.take(p, np.arange(i, i + n), axis=axis) for i, w in enumerate(k))
    return out


def silhouette(pos):
    """The view's foreground from its position map: pixels unlike the corners' colour."""
    corners = np.concatenate([pos[:4, :4].reshape(-1, 3), pos[-4:, -4:].reshape(-1, 3),
                              pos[:4, -4:].reshape(-1, 3), pos[-4:, :4].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    m = np.abs(pos - bg).max(-1) > 2.5 / 255
    im = Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return np.asarray(im) > 127


def bbox(m):
    ys, xs = np.nonzero(m)
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def iou(a, b):
    return float((a & b).sum() / max(1, (a | b).sum()))


# --- the cutout on a view

def warp(img, t, size):
    """img (H, W, C) in cutout pixels -> a size x size view: view = cutout * scale + (tx, ty)."""
    s, tx, ty = t["scale"], t["tx"], t["ty"]
    data = (1.0 / s, 0.0, -tx / s, 0.0, 1.0 / s, -ty / s)
    chans = []
    for c in range(img.shape[-1]):
        im = Image.fromarray(img[..., c].astype(np.float32), "F")
        chans.append(np.asarray(im.transform((size, size), Image.AFFINE, data, resample=Image.BILINEAR)))
    return np.stack(chans, -1).astype(np.float64)


def warp_mask(m, t, size):
    return warp(m.astype(np.float64)[..., None], t, size)[..., 0] > 0.5


def fit(cutout_mask, view_mask):
    """The scale and shift that lay the cutout's silhouette on the view's (no mirror: a picture
    from a camera is not mirrored), with its IoU."""
    size = view_mask.shape[0]
    vy0, vy1, vx0, vx1 = bbox(view_mask)
    cy0, cy1, cx0, cx1 = bbox(cutout_mask)
    best = None
    for s in sorted({np.sqrt(((vx1 - vx0) * (vy1 - vy0)) / max(1, (cx1 - cx0) * (cy1 - cy0))),
                     (vx1 - vx0) / (cx1 - cx0), (vy1 - vy0) / (cy1 - cy0)}):
        t = {"scale": float(s), "tx": (vx0 + vx1) / 2 - s * (cx0 + cx1) / 2,
             "ty": (vy0 + vy1) / 2 - s * (cy0 + cy1) / 2}
        t["iou"] = iou(warp_mask(cutout_mask, t, size), view_mask)
        if best is None or t["iou"] > best["iou"]:
            best = t
    step = {"scale": best["scale"] * 0.01, "tx": 2.0, "ty": 2.0}
    for _ in range(3):
        improved = True
        while improved:
            improved = False
            for k in ("scale", "tx", "ty"):
                for sign in (-1, 1):
                    c = dict(best)
                    c[k] += sign * step[k]
                    c["iou"] = iou(warp_mask(cutout_mask, c, size), view_mask)
                    if c["iou"] > best["iou"] + 1e-5:
                        best, improved = c, True
        step = {k: v / 2 for k, v in step.items()}
    return best


# --- colour

def srgb_to_lab(rgb):
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = (c @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab_to_srgb(lab):
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    d = 6 / 29
    f = lambda t: np.where(t > d, t ** 3, 3 * d * d * (t - 4 / 29))
    xyz = np.stack([f(fx) * 0.95047, f(fy), f(fz) * 1.08883], -1)
    m = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                  [0.0556434, -0.2040259, 1.0572252]])
    c = np.clip(xyz @ m.T, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def colour_fit(src, dst, mask):
    a, b = srgb_to_lab(src[mask]), srgb_to_lab(dst[mask])
    return {"src_mean": a.mean(0).tolist(), "src_std": a.std(0).tolist(),
            "dst_mean": b.mean(0).tolist(), "dst_std": b.std(0).tolist()}


def colour_apply(img, t, mask):
    lab = srgb_to_lab(img)
    gain = np.clip(np.array(t["dst_std"]) / (np.array(t["src_std"]) + 1e-6), *GAIN)
    out = lab_to_srgb((lab - np.array(t["src_mean"])) * gain + np.array(t["dst_mean"]))
    return np.where(mask[..., None], out, img)


def mean_de(a, b, mask):
    """Mean CIE76 colour difference of two blurred pictures inside mask."""
    la, lb = srgb_to_lab(blur(a, 4)[mask]), srgb_to_lab(blur(b, 4)[mask])
    return float(np.linalg.norm(la - lb, axis=1).mean())


# --- the step

def make_views(views, mrs, positions, normals, cutout, size=2048, min_fit=0.88, match_colour=False):
    """views, mrs, positions, normals: six float arrays each (H, W, 3) in [0, 1] (normals as
    stored, 0.5 = 0); cutout: float (h, w, 4). Returns (views, mrs, weights, record)."""
    n = views[0].shape[0]
    positions = [resize(p, n, Image.NEAREST) for p in positions]
    normals = [resize(p, n, Image.NEAREST) * 2 - 1 for p in normals]
    masks = [silhouette(p) for p in positions]
    cmask = cutout[..., 3] > 0.5
    record = {"view_size": n, "size": size, "min_fit": min_fit, "match_colour": match_colour}
    first = max(SIDE, key=lambda k: fit(cmask, masks[k])["iou"] if masks[k].any() else -1)
    fits = {k: fit(cmask, masks[k]) for k in (first, (first + 2) % 4) if masks[k].any()}
    des = {}
    for k, t in fits.items():
        cw = warp(cutout, t, n)
        m = erode(masks[k] & (cw[..., 3] > 0.5), 3)
        des[k] = mean_de(views[k], cw[..., :3], m) if m.any() else float("inf")
    k = min(des, key=des.get)
    if fits[k]["iou"] < fits[first]["iou"] - 0.02:
        k = first
    t = fits[k]
    record.update({"match_view": k, "fit": t, "side_colour_difference": des})
    big_masks = [resize_mask(m, size) for m in masks]
    out_views = [resize(v, size) for v in views]
    out_mrs = [resize(v, size) for v in mrs]
    if t["iou"] < min_fit:
        record.update({"applied": False, "weights": DEFAULT_WEIGHTS,
                       "reason": "the cutout's silhouette fits no view well enough (IoU %.3f below %.2f): "
                                 "Hunyuan3D Paint's views, enlarged" % (t["iou"], min_fit)})
        return out_views, out_mrs, list(DEFAULT_WEIGHTS), record
    cw = warp(cutout, t, n)
    joint = erode(masks[k] & (cw[..., 3] > 0.5), 3)
    colour = colour_fit(views[k], cw[..., :3], joint)
    if match_colour:
        out_views = [colour_apply(v, colour, m) for v, m in zip(out_views, big_masks)]
    # the cutout where the surface faces its camera
    nk = normals[k]
    d = np.array(VIEW_AXES[k], np.float64)
    facing = np.clip(((nk * d).sum(-1) - FACING[0]) / (FACING[1] - FACING[0]), 0, 1) * masks[k]
    big = {"scale": t["scale"] * size / n, "tx": (t["tx"] + 0.5) * size / n - 0.5,
           "ty": (t["ty"] + 0.5) * size / n - 0.5}
    cb = warp(cutout, big, size)
    a = erode((cb[..., 3] > 0.5) & big_masks[k], max(1, size // 512)).astype(np.float64)
    a = blur(a, size / 1024.0) * resize(np.repeat(facing[..., None], 3, -1), size)[..., 0]
    out_views[k] = out_views[k] * (1 - a[..., None]) + cb[..., :3] * a[..., None]
    record.update({"applied": True, "weights": CONCEPT_WEIGHTS, "colour": colour if match_colour else None,
                   "colour_measured": colour,
                   "cutout_share": float((a > 0.5).sum() / max(1, big_masks[k].sum()))})
    return out_views, out_mrs, list(CONCEPT_WEIGHTS), record


def run(view_paths, mr_paths, position_paths, normal_paths, cutout_path, out_dir, size=2048, min_fit=0.88,
        match_colour=False):
    """make_views on files; writes albedo_<i>.png, mr_<i>.png and paint_views.json in out_dir and
    returns {"albedo": [paths], "mr": [paths], "record": path, "weights": [...]}."""
    import os
    views = [load(p) for p in view_paths]
    mrs = [load(p) for p in mr_paths]
    pos = [load(p) for p in position_paths]
    nrm = [load(p) for p in normal_paths]
    cutout = load(cutout_path, "RGBA")
    v, m, weights, record = make_views(views, mrs, pos, nrm, cutout, size=size, min_fit=min_fit,
                                       match_colour=match_colour)
    os.makedirs(out_dir, exist_ok=True)
    out = {"albedo": [], "mr": [], "weights": weights}
    for i, (a, b) in enumerate(zip(v, m)):
        pa = os.path.join(out_dir, "albedo_%d.png" % i)
        pm = os.path.join(out_dir, "mr_%d.png" % i)
        to_image(a).save(pa)
        to_image(b).save(pm)
        out["albedo"].append(pa)
        out["mr"].append(pm)
    out["record"] = os.path.join(out_dir, "paint_views.json")
    with open(out["record"], "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    return out


def code_fingerprint():
    """The SHA-256 of this step's code (the factory's specification of a paint_views job), line
    endings normalised."""
    import hashlib
    with open(__file__, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()
