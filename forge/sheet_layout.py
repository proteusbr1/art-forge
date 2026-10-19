"""The layout of the review sheet of the articulated and creature recipes, worked out here
from each variant's model.json and reports, so it is tested without Godot; Godot
(tools/review_godot/sheet.gd) only renders the cells and texts it is given.

Per variant: a header (its label and "pending review", or FAILED with a red cross in place of
its figures), its verdict line, the subject at rest (articulated) or in its idle clip
(creature) in every direction of the review camera and one large front view, then a strip
per joint sweeping its range (articulated) or per clip (creature), then one line per mesh
flag and per motion measure over its threshold. Nothing on the sheet marks a candidate
approved.
"""

import json
import math
import os

PAD = 6
LINE = 18
DEFAULTS = {"pitch": 60.0, "directions": 8, "figure_height": 96, "front": True, "strip_frames": 8}
IDLE_KINDS = ("breathe", "hover")


def read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def text_width(s, size):
    """About how wide Godot's fallback font draws a line (a little wide on purpose)."""
    return int(math.ceil(len(s) * size * 0.62 * 1.1)) + 8


def _size(model):
    """(width, height, length, centre) of a model in glTF metres."""
    if isinstance(model, dict) and isinstance(model.get("size_m"), list) and len(model["size_m"]) == 3:
        w, h, ln = (max(float(x), 1e-3) for x in model["size_m"])
        c = model.get("center_m") or [0.0, h / 2.0, 0.0]
        return w, h, ln, [float(x) for x in c]
    return 1.0, 1.8, 1.0, [0.0, 0.9, 0.0]


def flags(v):
    """The flag lines of a variant (as the humanoid sheet: mesh flags, then one line per
    motion measure with how many clips are over its threshold and the worst one)."""
    out = []
    mesh = v.get("mesh_report")
    if isinstance(mesh, dict):
        out += ["mesh: " + (f if isinstance(f, str) else json.dumps(f, sort_keys=True)) for f in mesh.get("flags", [])]
    else:
        out.append("mesh: no report")
    model = v.get("model") or {}
    motion = v.get("motion_report")
    if isinstance(motion, dict):
        by = {}
        for f in motion.get("flags", []):
            k = str(f.get("measure"))
            e = by.setdefault(k, {"n": 0, "worst": None, "clip": "", "threshold": f.get("threshold")})
            e["n"] += 1
            val = f.get("value")
            if val is not None and (e["worst"] is None or float(val) > float(e["worst"])):
                e["worst"], e["clip"] = val, str(f.get("clip"))
        for k, e in by.items():
            if e["worst"] is None:
                out.append("motion: %s in %d clip(s)" % (k, e["n"]))
            else:
                out.append("motion: %s over %s in %d clip(s), worst %s %.2f" % (
                    k, e["threshold"], e["n"], e["clip"], float(e["worst"])))
    elif model.get("format") == "forge-creature" and model.get("animations"):
        out.append("motion: no report")
    return out or ["no flags"]


def _still(model):
    """(clip, how) the still figures show: a creature's first idle clip, else the rest pose."""
    if isinstance(model, dict) and model.get("format") == "forge-creature":
        for a in model.get("animations", []):
            if a.get("kind") in IDLE_KINDS:
                return a["name"], "first idle clip, frame 0"
    return "", "rest pose"


def _strips(model, n):
    """[(title, [cell fields])] of a model: a joint's sweep or a clip's frames."""
    out = []
    if not isinstance(model, dict):
        return out
    if model.get("format") == "forge-articulated":
        for j in model.get("joints", []):
            if j.get("kind") == "fixed" or not j.get("axis"):
                continue
            lo, hi = float(j["range"][0]), float(j["range"][1])
            values = [hi] if n <= 1 else [round(lo + (hi - lo) * i / (n - 1), 4) for i in range(n)]
            title = "joint %s (%s %g to %g %s)" % (j["part"], j["kind"], lo, hi, j.get("unit", "degrees"))
            out.append((title, [{"pose": [{"node": j["part"], "kind": j["kind"], "axis": j["axis"], "value": v}],
                                 "value": v, "joint": j["part"]} for v in values]))
    elif model.get("format") == "forge-creature":
        for a in model.get("animations", []):
            frames, fps = int(a.get("frames", 1)), float(a.get("fps", 24.0)) or 24.0
            length = frames / fps
            loop = bool(a.get("loop"))
            times = [length * i / n if loop else (length * i / max(n - 1, 1) if n > 1 else 0.0) for i in range(n)]
            if not loop:
                times = [min(t, (frames - 1) / fps) for t in times]
            title = "%s (%s, %d frames at %g fps%s)" % (a["name"], a.get("kind") or a.get("source", "clip"), frames,
                                                       fps, ", loop" if loop else "")
            out.append((title, [{"anim": a["name"], "time": round(t, 5), "clip": a["name"]} for t in times]))
    return out


def layout(variants, camera, banner=None):
    """The sheet's layout. variants: [{"label", "failed", "reason", "glb", "verdict"
    ({"class", "reasons"} or None), "model", "motion_report", "mesh_report"}]. camera: pitch,
    directions or yaws, figure_height, front, front_height, strip_frames, strip_yaw."""
    cam = dict(DEFAULTS, **{k: v for k, v in camera.items() if v is not None})
    yaws = cam.get("yaws")
    if yaws is None:
        n = int(cam["directions"])
        yaws = [round(360.0 * i / n, 3) for i in range(n)]
    pitch = float(cam["pitch"])
    fig = int(cam["figure_height"])
    front_h = int(cam.get("front_height") or fig * 3)
    strip_n = int(cam["strip_frames"])
    strip_yaw = float(cam.get("strip_yaw", yaws[1] if len(yaws) > 1 else yaws[0]))
    sizes = [_size(v.get("model")) for v in variants if not v.get("failed")]
    ref_h = max([s[1] for s in sizes] or [1.8])
    ref_d = max([math.hypot(s[0], s[2]) for s in sizes] or [1.0])
    ref = max(ref_h, 0.6 * ref_d)
    p = math.radians(pitch)
    ppm_small = fig / ref
    small = [int(math.ceil(max(ref_d, 0.5 * ref_h) * ppm_small * 1.3)) + 6,
             int(math.ceil((ref_h * abs(math.cos(p)) + ref_d * abs(math.sin(p))) * ppm_small * 1.15)) + 6]
    ppm_large = front_h / ref
    large = [int(math.ceil(max(ref_d, 0.5 * ref_h) * ppm_large * 1.2)) + 6, int(math.ceil(ref_h * ppm_large * 1.15)) + 6]
    cells, texts, failures, out_variants = [], [], [], []
    width, y = 64, PAD
    if banner:
        texts.append({"at": [PAD, y], "text": banner.upper(), "color": "fail", "size": 15})
        width = max(width, PAD * 2 + text_width(banner.upper(), 15))
        y += LINE + 6
    for index, v in enumerate(variants):
        rec = {"label": v["label"], "failed": bool(v.get("failed")), "reason": v.get("reason", ""),
               "glb": v.get("glb"), "kind": (v.get("model") or {}).get("format"), "pose": "", "pose_how": "",
               "verdict": None, "verdict_reasons": [], "flags": [], "cells": []}
        x = PAD
        header = "%s   %s" % (v["label"], ("FAILED: " + str(v.get("reason", ""))) if v.get("failed") else "pending review")
        if not v.get("failed"):
            clip, how = _still(v.get("model"))
            rec["pose"], rec["pose_how"] = clip or "rest", how
            header += "   pose %s (%s)" % (clip or "rest", how)
        texts.append({"at": [x, y], "text": header, "color": "fail" if v.get("failed") else "text", "size": 14})
        width = max(width, PAD * 2 + text_width(header, 14))
        y += LINE + 2
        if v.get("verdict"):
            vd = v["verdict"]
            rec["verdict"], rec["verdict_reasons"] = vd["class"], list(vd.get("reasons") or [])
            line = vd["class"] + (": " + "; ".join(vd["reasons"]) if vd.get("reasons") else "")
            texts.append({"at": [x, y], "text": line, "color": "pass" if vd["class"] == "acceptable" else "fail",
                          "size": 13})
            width = max(width, PAD * 2 + text_width(line, 13))
            y += LINE
        if v.get("failed"):
            r = [x, y, max(len(yaws) * (small[0] + PAD) - PAD, small[0]), small[1]]
            failures.append(r)
            rec["cells"].append({"kind": "failure", "rect": r})
            width = max(width, r[0] + r[2] + PAD)
            y += small[1] + PAD
        else:
            w, h, ln, centre = _size(v.get("model"))
            clip, _ = _still(v.get("model"))
            row = small[1]
            for yaw in yaws:
                c = {"variant": index, "kind": "small", "rect": [x, y, small[0], small[1]], "yaw": yaw,
                     "pitch": pitch, "ppm": round(ppm_small, 4), "target": centre, "anim": clip, "time": 0.0, "pose": []}
                cells.append(c)
                rec["cells"].append(c)
                x += small[0] + PAD
            if cam["front"]:
                c = {"variant": index, "kind": "front", "rect": [x, y, large[0], large[1]], "yaw": 0.0, "pitch": 0.0,
                     "ppm": round(ppm_large, 4), "target": centre, "anim": clip, "time": 0.0, "pose": []}
                cells.append(c)
                rec["cells"].append(c)
                x += large[0] + PAD
                row = max(row, large[1])
            width = max(width, x)
            y += row + PAD
            for title, strip in _strips(v.get("model"), strip_n):
                texts.append({"at": [PAD, y], "text": title, "color": "text", "size": 12})
                width = max(width, PAD * 2 + text_width(title, 12))
                y += LINE
                x = PAD
                for fields in strip:
                    c = dict({"variant": index, "kind": "strip", "rect": [x, y, small[0], small[1]], "yaw": strip_yaw,
                              "pitch": pitch, "ppm": round(ppm_small, 4), "target": centre, "anim": "", "time": 0.0,
                              "pose": []}, **fields)
                    cells.append(c)
                    rec["cells"].append(c)
                    x += small[0] + PAD
                width = max(width, x)
                y += small[1] + PAD
            for f in flags(v):
                texts.append({"at": [PAD, y], "text": f, "color": "flag", "size": 12})
                rec["flags"].append(f)
                width = max(width, PAD * 2 + text_width(f, 12))
                y += LINE
        y += PAD * 2
        out_variants.append(rec)
    cam_out = {"pitch": pitch, "yaws": yaws, "figure_height": fig, "front": bool(cam["front"]),
               "front_height": front_h, "strip_frames": strip_n, "strip_yaw": strip_yaw}
    return {"format": "forge-sheet-layout", "version": 1, "status": "pending review", "size": [width, y],
            "camera": cam_out, "banner": banner or None, "variants": out_variants, "cells": cells, "texts": texts,
            "failures": failures}
