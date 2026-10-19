"""The layout of the held-item recipe's review sheet, worked out here from each variant's
model.json and reports (so it is tested without Godot); Godot (tools/review_godot/
item_sheet.gd) only renders the cells, marks and texts it is given.

Per variant: a header (its label and "pending review", or FAILED with a red cross), its
verdict line; the item alone in every direction of the review camera, at the scale a figure
of REFERENCE_HEIGHT metres has at figure_height pixels (as in the game); a large side view
(front to the right, top up) with every socket marked and labelled and a line listing their
positions; then, when the hold stage ran, the reference figure holding the item in each of
its clips from HOLD_YAWS; then the flag lines (the item's mesh flags, the hold's motion
flags) and the fit line. Nothing on the sheet marks a candidate approved.

Axes: an item GLB is in item space (+x front, +z top, +y left; in glTF, x, then y up, then
z = -item y). The side view looks along glTF -z (yaw 0, pitch 0), so item +x is to the
right and item +z up, and the item's right side faces the camera.
"""

import math

from .sheet_layout import LINE, PAD, read_json, text_width  # noqa: F401  (read_json for the runner)

DEFAULTS = {"pitch": 60.0, "directions": 8, "figure_height": 96, "front": True, "side_width": 640,
            "hold_height": None}
REFERENCE_HEIGHT = 1.8
HOLD_YAWS = (0.0, 90.0, 180.0, 270.0)
MIN_CELL = 32
MARK_RADIUS = 5


def to_gltf(p):
    """An item-space point in the GLB's glTF axes."""
    return [float(p[0]), float(p[2]), -float(p[1])]


def _box(model):
    """(low, high) of an item model's box in glTF axes."""
    box = (model or {}).get("box_m")
    if not (isinstance(box, list) and len(box) == 2):
        return [-0.1, -0.05, -0.05], [0.1, 0.05, 0.05]
    lo, hi = to_gltf(box[0]), to_gltf(box[1])
    return [min(lo[i], hi[i]) for i in range(3)], [max(lo[i], hi[i]) for i in range(3)]


def item_flags(v):
    """The flag lines of a variant: its item's mesh flags, its hold's motion flags or
    failure, its fit."""
    out = []
    mesh = v.get("mesh_report")
    if isinstance(mesh, dict):
        out += ["mesh: " + (f if isinstance(f, str) else str(f)) for f in mesh.get("flags", [])]
    else:
        out.append("mesh: no report")
    hold = v.get("hold")
    if isinstance(hold, dict) and not hold.get("failed"):
        motion = hold.get("motion_report")
        if isinstance(motion, dict):
            for f in motion.get("flags", []):
                if isinstance(f, dict):
                    val = f.get("value")
                    out.append("hold: %s %s%s" % (f.get("clip"), f.get("measure"), "" if val is None else
                                                  " %.2f over %s" % (float(val), f.get("threshold"))))
        else:
            out.append("hold: no motion report")
    return out or ["no flags"]


def fit_line(v):
    fit = (v.get("mesh_report") or {}).get("fit") or (v.get("model") or {}).get("fit")
    if not isinstance(fit, dict):
        return None
    return "fit (ranking only): socket shift %.3f, turned %.1f deg, loose %.1f %%, excess %.2f" % (
        float(fit.get("socket_shift", 0.0)), float(fit.get("turned_deg", 0.0)),
        100.0 * float(fit.get("loose_share", 0.0)), float(fit.get("excess", 0.0)))


def socket_line(model):
    recs = (model or {}).get("socket_records") or {}
    parts = []
    for name in sorted(recs):
        p = recs[name].get("used")
        parts.append("%s %s" % (name, "missing" if p is None else "(%.3f, %.3f, %.3f)" % tuple(p)))
    return ("sockets (m, item space): " + ", ".join(parts)) if parts else "sockets: none"


def layout(variants, camera, banner=None):
    """The sheet's layout. variants: [{"label", "failed", "reason", "glb" (the item),
    "verdict" ({"class", "reasons"} or None), "model", "mesh_report", "hold" (None, or
    {"failed", "reason"} or {"glb", "model", "motion_report"})}]. camera: pitch, directions or
    yaws, figure_height, front (the side view), side_width, hold_height."""
    cam = dict(DEFAULTS, **{k: v for k, v in camera.items() if v is not None})
    yaws = cam.get("yaws")
    if yaws is None:
        n = int(cam["directions"])
        yaws = [round(360.0 * i / n, 3) for i in range(n)]
    pitch = float(cam["pitch"])
    fig = int(cam["figure_height"])
    hold_h = int(cam.get("hold_height") or 2 * fig)
    side_w = int(cam["side_width"])
    ppm_game = fig / REFERENCE_HEIGHT
    p = math.radians(pitch)
    cells, texts, marks, failures, out_variants = [], [], [], [], []
    width, y = 64, PAD
    if banner:
        texts.append({"at": [PAD, y], "text": banner.upper(), "color": "fail", "size": 15})
        width = max(width, PAD * 2 + text_width(banner.upper(), 15))
        y += LINE + 6
    for index, v in enumerate(variants):
        rec = {"label": v["label"], "failed": bool(v.get("failed")), "reason": v.get("reason", ""),
               "glb": v.get("glb"), "figure": None, "verdict": None, "verdict_reasons": [], "flags": [],
               "cells": [], "sockets": {}}
        model = v.get("model") or {}
        header = "%s   %s" % (v["label"], ("FAILED: " + str(v.get("reason", ""))) if v.get("failed") else
                               "pending review   class %s, template %s, %.2f m" % (
                                   model.get("hold_class"), model.get("template"), float(model.get("length_m") or 0)))
        texts.append({"at": [PAD, y], "text": header, "color": "fail" if v.get("failed") else "text", "size": 14})
        width = max(width, PAD * 2 + text_width(header, 14))
        y += LINE + 2
        if v.get("verdict"):
            vd = v["verdict"]
            rec["verdict"], rec["verdict_reasons"] = vd["class"], list(vd.get("reasons") or [])
            line = vd["class"] + (": " + "; ".join(vd["reasons"]) if vd.get("reasons") else "")
            texts.append({"at": [PAD, y], "text": line, "color": "pass" if vd["class"] == "acceptable" else "fail",
                          "size": 13})
            width = max(width, PAD * 2 + text_width(line, 13))
            y += LINE
        if v.get("failed"):
            r = [PAD, y, max(len(yaws) * (MIN_CELL * 2 + PAD) - PAD, MIN_CELL * 2), MIN_CELL * 2]
            failures.append(r)
            rec["cells"].append({"kind": "failure", "rect": r})
            width = max(width, r[0] + r[2] + PAD)
            y += r[3] + PAD * 3
            out_variants.append(rec)
            continue
        lo, hi = _box(model)
        centre = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
        ext = [max(hi[i] - lo[i], 1e-3) for i in range(3)]
        # 1. the item alone, as in the game (the review camera, a figure's scale)
        horiz = math.hypot(ext[0], ext[2])
        cw = max(MIN_CELL, int(math.ceil(horiz * ppm_game * 1.2)) + 6)
        ch = max(MIN_CELL, int(math.ceil((ext[1] * abs(math.cos(p)) + horiz * abs(math.sin(p))) * ppm_game * 1.2)) + 6)
        title = "the item from the review camera (pitch %g, a %.1f m figure %d px tall)" % (pitch, REFERENCE_HEIGHT, fig)
        texts.append({"at": [PAD, y], "text": title, "color": "text", "size": 12})
        width = max(width, PAD * 2 + text_width(title, 12))
        y += LINE
        x = PAD
        for yaw in yaws:
            c = {"variant": index, "subject": "item", "kind": "small", "rect": [x, y, cw, ch], "yaw": yaw,
                 "pitch": pitch, "ppm": round(ppm_game, 4), "target": centre, "anim": "", "time": 0.0, "pose": []}
            cells.append(c)
            rec["cells"].append(c)
            x += cw + PAD
        width = max(width, x)
        y += ch + PAD
        # 2. the side view with the sockets
        if cam["front"]:
            ppm = side_w / (ext[0] * 1.15)
            sh = max(MIN_CELL * 2, int(math.ceil(ext[1] * ppm * 1.25)) + 24)
            title = "side view, front to the right, top up (%.0f px per metre)" % ppm
            texts.append({"at": [PAD, y], "text": title, "color": "text", "size": 12})
            width = max(width, PAD * 2 + text_width(title, 12))
            y += LINE
            r = [PAD, y, side_w, sh]
            c = {"variant": index, "subject": "item", "kind": "side", "rect": r, "yaw": 0.0, "pitch": 0.0,
                 "ppm": round(ppm, 4), "target": centre, "anim": "", "time": 0.0, "pose": []}
            cells.append(c)
            rec["cells"].append(c)
            cx, cy = r[0] + r[2] / 2.0, r[1] + r[3] / 2.0
            placed = []
            recs = model.get("socket_records") or {}
            for name in sorted(recs, key=lambda k: (recs[k].get("used") or [0, 0, 0])[0]):
                pos = recs[name].get("used")
                if pos is None:
                    continue
                g = to_gltf(pos)
                px = cx + (g[0] - centre[0]) * ppm
                py = cy - (g[1] - centre[1]) * ppm
                at = [int(round(px)), int(round(py))]
                marks.append({"at": at, "r": MARK_RADIUS, "color": "socket", "label": name})
                rec["sockets"][name] = at
                # The label beside its mark, moved down while it would cover an earlier one.
                lw = text_width(name, 12)
                lx, ly = at[0] + MARK_RADIUS + 3, at[1] - LINE // 2
                while any(abs(ly - qy) < LINE and lx < qx + qw and qx < lx + lw for qx, qy, qw in placed):
                    ly += LINE
                placed.append((lx, ly, lw))
                texts.append({"at": [lx, ly], "text": name, "color": "socket", "size": 12})
                width = max(width, lx + lw + PAD)
            width = max(width, r[0] + r[2] + PAD)
            y += sh + PAD
            line = socket_line(model)
            texts.append({"at": [PAD, y], "text": line, "color": "text", "size": 12})
            width = max(width, PAD * 2 + text_width(line, 12))
            y += LINE
        # 3. the reference figure holding the item
        hold = v.get("hold")
        if isinstance(hold, dict) and hold.get("failed"):
            line = "hold: FAILED: %s" % hold.get("reason", "")
            texts.append({"at": [PAD, y], "text": line, "color": "fail", "size": 12})
            width = max(width, PAD * 2 + text_width(line, 12))
            y += LINE
        elif isinstance(hold, dict):
            rec["figure"] = hold.get("glb")
            hm = hold.get("model") or {}
            fh = float(hm.get("height") or REFERENCE_HEIGHT)
            ppm = hold_h / (fh * 1.25)
            hw = max(MIN_CELL, int(math.ceil(fh * 0.95 * ppm)) + 6)
            hh = max(MIN_CELL, int(math.ceil((fh * abs(math.cos(p)) + 0.8 * abs(math.sin(p))) * ppm * 1.2)) + 6)
            for anim in hm.get("animations") or []:
                fps = float(anim.get("fps") or 24.0) or 24.0
                frame = anim.get("pose_frame")
                t = round(float(frame) / fps, 5) if isinstance(frame, (int, float)) else 0.0
                title = "held: %s (%s), frame %s" % (anim.get("name"), anim.get("holds"),
                                                    frame if isinstance(frame, (int, float)) else 0)
                texts.append({"at": [PAD, y], "text": title, "color": "text", "size": 12})
                width = max(width, PAD * 2 + text_width(title, 12))
                y += LINE
                x = PAD
                for yaw in HOLD_YAWS:
                    c = {"variant": index, "subject": "figure", "kind": "hold", "rect": [x, y, hw, hh], "yaw": yaw,
                         "pitch": pitch, "ppm": round(ppm, 4), "target": [0.0, fh / 2.0, 0.0],
                         "anim": anim.get("name"), "time": t, "pose": []}
                    cells.append(c)
                    rec["cells"].append(c)
                    x += hw + PAD
                width = max(width, x)
                y += hh + PAD
        for f in item_flags(v):
            texts.append({"at": [PAD, y], "text": f, "color": "flag", "size": 12})
            rec["flags"].append(f)
            width = max(width, PAD * 2 + text_width(f, 12))
            y += LINE
        line = fit_line(v)
        if line:
            texts.append({"at": [PAD, y], "text": line, "color": "text", "size": 12})
            width = max(width, PAD * 2 + text_width(line, 12))
            y += LINE
        y += PAD * 2
        out_variants.append(rec)
    cam_out = {"pitch": pitch, "yaws": yaws, "figure_height": fig, "front": bool(cam["front"]),
               "side_width": side_w, "hold_height": hold_h, "hold_yaws": list(HOLD_YAWS),
               "reference_height": REFERENCE_HEIGHT}
    return {"format": "forge-item-sheet-layout", "version": 1, "status": "pending review", "size": [width, y],
            "camera": cam_out, "banner": banner or None, "variants": out_variants, "cells": cells, "marks": marks,
            "texts": texts, "failures": failures}
