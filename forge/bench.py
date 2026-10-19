"""The motion bench: one figure, one held item and a few clips of a motion library, exported
with the humanoid exporter and played in Godot from the game's camera and from close, so that
a person can judge and refine a motion library clip by clip (docs/bench.md).

  python -m forge.bench --rig RIG.glb --clips LIBRARY.json --out DIR
        [--items DIR [--item NAME]] [--only CLIP[,CLIP...]] [--height 1.8]
        [--yaws 45,-90,-135] [--pitch 30] [--game-px 115] [--close-px 440] [--close-yaw -35]
        [--format webp|gif] [--compare PREVIOUS_DIR] [--title TEXT] [--keep-frames]
        [--config forge.local.json]

Steps:
1. The clips: --only names clips of the library (exact names or shell patterns such as
   "*_rifle*"), in the order given; without it every clip. <out>/clips.json is the library
   with only those clips (and its holds).
2. The item: --items with --item copies that one item (<name>.glb and <name>.json) to
   <out>/items/, so its class's sockets are that item's own; --items alone passes the whole
   folder (the exporter averages the sockets of each class).
3. The figure: a rigged GLB, or a figure the humanoid exporter already wrote (its item bones,
   item_ref and animations are removed first by tools/blender/held_item_figure.py).
4. The export: tools/blender/humanoid_export.py on the CPU (the mesh is not reduced, a
   256-pixel normal map): <out>/export/ (figure.glb, model.json, motion_report.json).
5. The render: tools/review_godot/bench.gd off screen, every frame of every clip, from the
   game's camera (orthographic, --pitch degrees; twice the game size for each of --yaws and
   the game size, the standing figure --game-px pixels tall, in the eight directions) and from
   close (--close-yaw, --close-px tall), over
   a checkered ground that moves back by the clip's travel (a planted foot stays on its
   square).
6. The previews: <out>/<clip>.webp (or .gif), animated, every view side by side with its
   label; <out>/<clip>.png, a sheet of eight frames; <out>/index.html, one row per clip with
   its previews, the motion report's measures and the bench's own (the item's direction, the
   ankles' gap and the slide of a planted foot), and those of --compare (a previous bench
   folder) under them; <out>/bench.json.

No GPU lock is taken and nothing is sent to ComfyUI: Blender runs on the CPU and Godot renders
small frames.
"""

import argparse
import fnmatch
import html
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time

from .config import load_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_SCRIPT = os.path.join(ROOT, "tools", "blender", "humanoid_export.py")
FIGURE_SCRIPT = os.path.join(ROOT, "tools", "blender", "held_item_figure.py")
GODOT_PROJECT = os.path.join(ROOT, "tools", "review_godot")
FORMAT = "forge-bench"
BACKGROUND = (0.15, 0.16, 0.18)
BACKGROUND_RGB = tuple(int(round(c * 255)) for c in BACKGROUND)
LABEL = 18  # pixels of the label band over each view
# The motion report's measures the page shows.
MEASURES = ("hand_socket_cm", "foot_slide_cm", "penetration_cm", "reach_shortfall_cm", "stretch_p95")
SHORT = {"hand_socket_cm": "hand to socket cm", "foot_slide_cm": "foot slide cm (stance)",
         "penetration_cm": "item in body cm", "reach_shortfall_cm": "reach short cm", "stretch_p95": "stretch p95"}
# A planted foot (the bench's own measure): its ankle within this many body heights of its
# lowest, for at least PLANT_MIN_FRAMES frames in a row; the first and the last frame of each
# such run (a heel coming down, a heel lifting) are not counted in its slide.
PLANT_HEIGHT = 0.005
PLANT_MIN_FRAMES = 3
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class BenchError(RuntimeError):
    pass


# ---- the clips --------------------------------------------------------------------------------

def select_clips(library, only=None):
    """The clip names of a motion library the bench plays: every clip (in the library's order)
    without `only`, else each name or shell pattern of `only` in its order (a pattern's
    matches in the library's order), each clip once. Raises BenchError naming what matched
    nothing."""
    anims = library.get("anims") if isinstance(library, dict) else None
    if not isinstance(anims, dict) or not anims:
        raise BenchError("the motion library has no clips (\"anims\")")
    names = list(anims)
    if not only:
        return names
    out, missing = [], []
    for word in only:
        word = word.strip()
        if not word:
            continue
        hits = [n for n in names if n == word] or [n for n in names if fnmatch.fnmatchcase(n, word)]
        if not hits:
            missing.append(word)
        out += [n for n in hits if n not in out]
    if missing:
        raise BenchError("no clip of the library matches %s" % ", ".join(missing))
    if not out:
        raise BenchError("no clip selected")
    return out


def library_subset(library, names):
    """The library with only the named clips (every other key kept: holds, canon...)."""
    doc = {k: v for k, v in library.items() if k != "anims"}
    doc["anims"] = {n: library["anims"][n] for n in names}
    return doc


# ---- the views --------------------------------------------------------------------------------

def view_layout(height_m, yaws=(45.0, -90.0, -135.0), pitch=30.0, game_px=115, close_px=440, close_yaw=-35.0,
                close_pitch=12.0, small_yaws=(0.0, 45.0, 90.0, 135.0, 180.0, -135.0, -90.0, -45.0)):
    """The views of a frame and where each goes: the close view on the left, the game's
    camera at twice the game size beside it (one per yaw), and under those the game size (one
    per `small_yaws`, by default the game's eight directions). Returns (size [w, h], views); a
    view: name, label, rect [x, y, w, h] (under a LABEL-pixel band), yaw, pitch, ppm (pixels
    per metre), target, scale."""
    if not yaws:
        raise BenchError("at least one game direction is needed")
    small_yaws = list(small_yaws or yaws)
    views = []
    game_ppm = game_px / (height_m * math.cos(math.radians(pitch)))
    close_ppm = close_px / (height_m * math.cos(math.radians(close_pitch)))
    target = [0.0, round(0.45 * height_m, 4), 0.0]
    cw, ch = int(round(close_px * 0.9)), int(round(close_px * 1.2))
    views.append({"name": "close", "label": "close %+g" % close_yaw, "rect": [0, LABEL, cw, ch], "yaw": close_yaw,
                  "pitch": close_pitch, "ppm": round(close_ppm, 4), "target": target, "scale": "close"})
    gw, gh = int(round(game_px * 1.6)), int(round(game_px * 1.5))
    x = cw
    for yaw in yaws:
        views.append({"name": "game2x_%g" % yaw, "label": "game x2 %+g" % yaw, "rect": [x, LABEL, 2 * gw, 2 * gh],
                      "yaw": yaw, "pitch": pitch, "ppm": round(2 * game_ppm, 4), "target": target,
                      "scale": "game2x"})
        x += 2 * gw
    y = 2 * LABEL + 2 * gh
    sw = max(int(round(game_px * 0.9)), (x - cw) // len(small_yaws))
    gx = cw
    for yaw in small_yaws:
        views.append({"name": "game_%g" % yaw, "label": "%+g" % yaw, "rect": [gx, y, sw, gh], "yaw": yaw,
                      "pitch": pitch, "ppm": round(game_ppm, 4), "target": target, "scale": "game"})
        gx += sw
    return [max(x, gx), max(LABEL + ch, y + gh)], views


# ---- the bench's own measures ------------------------------------------------------------------

def item_measures(dirs):
    """The held item's direction over a clip (per frame {yaw_deg, pitch_deg} or None): the
    mean and the largest size of its yaw from the figure's front, and its pitch's mean, lowest
    and highest. None when the item is hidden on every frame."""
    d = [x for x in dirs or [] if x]
    if not d:
        return None
    yaw = [float(x["yaw_deg"]) for x in d]
    pitch = [float(x["pitch_deg"]) for x in d]
    return {"yaw_mean_deg": round(sum(yaw) / len(yaw), 1), "yaw_max_deg": round(max(abs(v) for v in yaw), 1),
            "pitch_mean_deg": round(sum(pitch) / len(pitch), 1), "pitch_min_deg": round(min(pitch), 1),
            "pitch_max_deg": round(max(pitch), 1)}


def _runs(flags, loop):
    """Runs of consecutive true frames (unwrapped indices; a loop's run may wrap)."""
    n = len(flags)
    runs, cur = [], []
    for i, f in enumerate(flags):
        if f:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if loop and len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == n - 1:
        runs[0] = runs[-1] + [i + n for i in runs[0]]
        runs.pop()
    return runs


def feet_measures(ankles, height, travel=None, loop=True):
    """The ankles over a clip (per frame [[side, forward, up] left, [..] right] metres, or
    None): the smallest gap across between the left and the right ankle (cm; below 0 the legs
    cross), and the largest slide of a planted foot in the ground's frame (cm; a foot is
    planted on the frames its ankle is within PLANT_HEIGHT body heights of its lowest, at least
    PLANT_MIN_FRAMES in a row, the run's first and last frame left out; the clip's travel, body
    heights a pass, is added). None without ankles."""
    a = [x for x in ankles or [] if x]
    if not a or len(a) != len(ankles):
        return None
    n = len(a)
    gap = min(f[0][0] - f[1][0] for f in a) * 100.0
    trav = travel if isinstance(travel, (list, tuple)) and len(travel) == 2 else (0.0, 0.0)
    slide = 0.0
    for k in (0, 1):
        ups = [f[k][2] for f in a]
        low = min(ups)
        planted = [u <= low + PLANT_HEIGHT * height for u in ups]
        for run in _runs(planted, loop):
            if len(run) < PLANT_MIN_FRAMES:
                continue
            pts = []
            for i in run[1:-1]:
                p = a[i % n][k]
                t = float(i) / n * height
                pts.append((p[0] + float(trav[0]) * t, p[1] + float(trav[1]) * t))
            slide = max([slide] + [math.hypot(p[0] - q[0], p[1] - q[1]) * 100.0 for p in pts for q in pts])
    return {"ankle_gap_min_cm": round(gap, 1), "planted_slide_cm": round(slide, 1)}


def clip_rows(names, model, report, render):
    """One row per clip: name, kind, holds, follow, hands, frames, fps, the motion report's
    measures (and those over its thresholds), the item's direction, the feet's measures and
    whether it was rendered."""
    anims = {a["name"]: a for a in (model or {}).get("animations", [])}
    clips = (report or {}).get("clips", {})
    thresholds = (report or {}).get("thresholds", {})
    rendered = (render or {}).get("clips", {})
    height = float((model or {}).get("height", 1.8))
    rows = []
    for n in names:
        a = anims.get(n, {})
        r = clips.get(n, {})
        measures = {k: r.get(k) for k in MEASURES}
        over = [k for k in MEASURES if measures[k] is not None and k in thresholds and measures[k] > thresholds[k]]
        got = rendered.get(n, {})
        rows.append({"name": n, "kind": a.get("kind"), "holds": a.get("holds"), "follow": a.get("follow"),
                     "hands": a.get("hands") or {}, "frames": a.get("frames"), "fps": a.get("fps"),
                     "measures": measures, "over": over, "item": item_measures(got.get("item")),
                     "feet": feet_measures(got.get("ankles"), height, a.get("travel"), bool(a.get("loop", True))),
                     "exported": n in anims,
                     "rendered": bool(got.get("frames")) and not got.get("missing", False)})
    return rows


# ---- the page ---------------------------------------------------------------------------------

def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%.3f" % v if abs(v) < 10 else "%.1f" % v).rstrip("0").rstrip(".")
    return str(v)


def _measure_table(row, thresholds):
    out = ["<table class=m>"]
    for k in MEASURES:
        v = row["measures"].get(k)
        cls = " class=over" if k in row.get("over", []) else ""
        lim = thresholds.get(k)
        out.append("<tr><td>%s</td><td%s>%s</td><td class=muted>%s</td></tr>" % (
            SHORT[k], cls, _fmt(v), "" if lim is None else "&le; " + _fmt(lim)))
    feet = row.get("feet")
    if feet:
        cls = " class=over" if feet["ankle_gap_min_cm"] < 0 else ""
        out.append("<tr><td>ankle gap min cm</td><td%s>%s</td><td class=muted>&lt; 0 crossed</td></tr>" % (
            cls, _fmt(feet["ankle_gap_min_cm"])))
        out.append("<tr><td>planted foot slide cm</td><td>%s</td><td class=muted>ground frame</td></tr>" % (
            _fmt(feet["planted_slide_cm"])))
    it = row.get("item")
    if it:
        out.append("<tr><td>item yaw mean / max</td><td>%s / %s</td><td class=muted>deg, + left</td></tr>" % (
            _fmt(it["yaw_mean_deg"]), _fmt(it["yaw_max_deg"])))
        out.append("<tr><td>item pitch mean (range)</td><td>%s (%s to %s)</td><td class=muted>deg, + up</td></tr>"
                   % (_fmt(it["pitch_mean_deg"]), _fmt(it["pitch_min_deg"]), _fmt(it["pitch_max_deg"])))
    out.append("</table>")
    return "".join(out)


def page_html(doc, compare=None, compare_dir=None):
    """The bench page: one row per clip with its previews and measures; with `compare` (a
    previous bench.json) and its folder relative to this page, the previous previews and
    measures under each row."""
    thresholds = doc.get("thresholds") or {}
    title = html.escape(doc.get("title") or "motion bench")
    prev = {r["name"]: r for r in (compare or {}).get("clips", [])}
    parts = ["<!doctype html><html><head><meta charset=utf-8><title>%s</title><style>"
             "body{background:#202226;color:#e4e6ea;font:14px sans-serif;margin:16px}"
             "h1{font-size:18px}td,th{vertical-align:top;padding:4px 8px;text-align:left}"
             "table.m td{padding:1px 6px;font-size:12px}.over{color:#ff9b4a;font-weight:bold}"
             ".muted{color:#8a8f98}tr.clip th{border-top:1px solid #444;padding-top:14px}"
             "img{display:block;max-width:100%%}a{color:#7fc4ff}</style></head><body>" % title,
             "<h1>%s</h1>" % title]
    parts.append("<p class=muted>%s &middot; item %s &middot; %d clips &middot; %s &middot; pending review</p>" % (
        html.escape(os.path.basename(doc.get("rig") or "")), html.escape(str(doc.get("item"))),
        len(doc.get("clips", [])), html.escape(doc.get("made") or "")))
    if compare is not None:
        parts.append("<p class=muted>under each clip: %s (%s)</p>" % (
            html.escape(compare_dir or ""), html.escape(compare.get("made") or "")))
    parts.append("<table>")
    for row in doc.get("clips", []):
        hands = ", ".join("%s on %s" % kv for kv in sorted(row.get("hands", {}).items())) or "none"
        parts.append("<tr class=clip><th colspan=2 id=\"%s\">%s <span class=muted>%s &middot; holds %s &middot; "
                     "follow %s &middot; %s frames at %s fps &middot; hands %s</span></th></tr>" % (
                         html.escape(row["name"]), html.escape(row["name"]), html.escape(str(row.get("kind"))),
                         html.escape(str(row.get("holds"))), html.escape(str(row.get("follow"))),
                         _fmt(row.get("frames")), _fmt(row.get("fps")), html.escape(hands)))
        if row.get("rendered") and row.get("preview"):
            cell = "<td><img src=\"%s\" alt=\"%s\"><a href=\"%s\">eight frames</a></td>" % (
                html.escape(row["preview"]), html.escape(row["name"]), html.escape(row.get("sheet") or ""))
        else:
            cell = "<td class=over>%s</td>" % ("not rendered" if row.get("exported", True) else "not exported")
        parts.append("<tr>%s<td>%s</td></tr>" % (cell, _measure_table(row, thresholds)))
        if compare is not None:
            old = prev.get(row["name"])
            if old and old.get("rendered") and old.get("preview"):
                src = "/".join(x for x in (compare_dir, old["preview"]) if x)
                parts.append("<tr><td><span class=muted>before</span><img src=\"%s\" alt=\"before\"></td><td>%s</td>"
                             "</tr>" % (html.escape(src), _measure_table(old, compare.get("thresholds") or {})))
            else:
                parts.append("<tr><td class=muted colspan=2>before: not in %s</td></tr>" % html.escape(
                    compare_dir or ""))
    parts.append("</table></body></html>")
    return "\n".join(parts)


# ---- the steps --------------------------------------------------------------------------------

def _run(cmd, out, step, timeout=1800):
    so, se = os.path.join(out, step + ".stdout.log"), os.path.join(out, step + ".stderr.log")
    t0 = time.time()
    with open(so, "w", encoding="utf-8", errors="replace") as fo, \
            open(se, "w", encoding="utf-8", errors="replace") as fe:
        fo.write("$ %s\n" % subprocess.list2cmdline(cmd))
        fo.flush()
        code = subprocess.call(cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, timeout=timeout, **FLAGS)
    if code != 0:
        raise BenchError("%s failed (exit %d), see %s and %s" % (step, code, so, se))
    return round(time.time() - t0, 1)


def glb_nodes(path):
    """The node names of a GLB (its JSON chunk)."""
    with open(path, "rb") as f:
        head = f.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            raise BenchError("not a GLB file: %s" % path)
        length = struct.unpack("<I", head[12:16])[0]
        doc = json.loads(f.read(length).decode("utf-8"))
    return [n.get("name", "") for n in doc.get("nodes", [])]


def _load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _write(path, data):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow before 10.1
        return ImageFont.load_default()


def sheet_frames(count, picks=8):
    """The frame indices of a sheet: `picks` frames spread over the clip, first and last
    included."""
    if count <= 1:
        return [0]
    return sorted(set(int(round(k * (count - 1) / float(picks - 1))) for k in range(picks)))


def assemble(frames_dir, name, views, fps, out, fmt="webp", title=None):
    """The animated preview <out>/<name>.<fmt> of the frames Godot wrote (each view labelled)
    and the sheet <out>/<name>.png of eight of them at half size. Returns (preview, sheet)
    file names."""
    from PIL import Image, ImageDraw
    folder = os.path.join(frames_dir, name)
    files = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
    if not files:
        raise BenchError("no frames of %s in %s" % (name, folder))
    font = _font(13)
    frames = []
    for i, fn in enumerate(files):
        img = Image.open(os.path.join(folder, fn)).convert("RGB")
        d = ImageDraw.Draw(img)
        for k, v in enumerate(views):
            x, y = v["rect"][0], v["rect"][1] - LABEL
            text = v["label"] if k else "%s  %s  %d/%d" % (title or name, v["label"], i + 1, len(files))
            d.text((x + 4, y + 2), text, fill=(220, 224, 230), font=font)
        frames.append(img)
    preview = "%s.%s" % (name, fmt)
    duration = int(round(1000.0 / max(fps, 1.0)))
    if fmt == "gif":
        pal = [f.convert("P", palette=Image.ADAPTIVE, colors=255) for f in frames]
        pal[0].save(os.path.join(out, preview), save_all=True, append_images=pal[1:], duration=duration, loop=0,
                    disposal=1)
    else:
        frames[0].save(os.path.join(out, preview), save_all=True, append_images=frames[1:], duration=duration,
                       loop=0, quality=80, method=0)
    pick = sheet_frames(len(frames))
    w, h = frames[0].size
    sw, sh = w // 2, h // 2
    sheet = Image.new("RGB", (sw * 2, sh * ((len(pick) + 1) // 2)), BACKGROUND_RGB)
    for k, i in enumerate(pick):
        sheet.paste(frames[i].resize((sw, sh), Image.LANCZOS), ((k % 2) * sw, (k // 2) * sh))
    sheet_name = name + ".png"
    sheet.save(os.path.join(out, sheet_name))
    return preview, sheet_name


def run_bench(rig, clips, out, config, items=None, item=None, only=None, height=1.8, yaws=(45.0, -90.0, -135.0),
              pitch=30.0, game_px=115, close_px=440, close_yaw=-35.0, fmt="webp", compare=None, title=None,
              keep_frames=False):
    """The whole bench (module docstring). Returns the bench document (also <out>/bench.json)."""
    blender, godot = config.get("blender_python"), config.get("godot")
    if not blender or not os.path.exists(blender):
        raise BenchError("blender_python is not configured (forge.local.json)")
    if not godot or not os.path.exists(godot):
        raise BenchError("godot is not configured (forge.local.json)")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    library = _load(clips)
    names = select_clips(library, only)
    prev, rel = None, None
    if compare:
        prev_path = os.path.join(compare, "bench.json")
        if not os.path.exists(prev_path):
            raise BenchError("--compare: no bench.json in %s" % compare)
        prev = _load(prev_path)
        rel = os.path.relpath(os.path.abspath(compare), out).replace(os.sep, "/")
    clips_out = os.path.join(out, "clips.json")
    _write(clips_out, library_subset(library, names))
    items_out, item_glb = None, None
    if item:
        if not items:
            raise BenchError("--item needs --items (the folder of held items)")
        src_glb, src_json = os.path.join(items, item + ".glb"), os.path.join(items, item + ".json")
        for p in (src_glb, src_json):
            if not os.path.exists(p):
                raise BenchError("the item file is missing: %s" % p)
        items_out = os.path.join(out, "items")
        shutil.rmtree(items_out, ignore_errors=True)
        os.makedirs(items_out)
        shutil.copyfile(src_glb, os.path.join(items_out, item + ".glb"))
        shutil.copyfile(src_json, os.path.join(items_out, item + ".json"))
        item_glb = os.path.join(items_out, item + ".glb")
    elif items:
        items_out = os.path.abspath(items)
    timings = {}
    figure_rig = os.path.abspath(rig)
    if "item_ref" in glb_nodes(rig):
        figure_rig = os.path.join(out, "figure_rig.glb")
        timings["figure"] = _run([blender, FIGURE_SCRIPT, "--figure", os.path.abspath(rig), "--out", figure_rig,
                                  "--report", os.path.join(out, "figure.json")], out, "figure")
    export = os.path.join(out, "export")
    for stale in ("figure.glb", "model.json", "motion_report.json", "mesh_report.json"):
        if os.path.exists(os.path.join(export, stale)):
            os.remove(os.path.join(export, stale))
    os.makedirs(export, exist_ok=True)
    cmd = [blender, EXPORT_SCRIPT, "--rig", figure_rig, "--out", export, "--name", "figure", "--device", "CPU",
           "--tris", "100000000", "--tex", "256", "--height", str(height), "--clips", clips_out]
    if items_out:
        cmd += ["--items", items_out]
    timings["export"] = _run(cmd, out, "export")
    glb, model_path, report_path = (os.path.join(export, f) for f in ("figure.glb", "model.json", "motion_report.json"))
    if not os.path.exists(glb) or not os.path.exists(model_path):
        raise BenchError("the export wrote no GLB, see %s" % os.path.join(out, "export.stdout.log"))
    model = _load(model_path)
    report = _load(report_path) if os.path.exists(report_path) else {}
    H = float(model.get("height", height))
    size, views = view_layout(H, yaws, pitch, game_px, close_px, close_yaw)
    frames_dir = os.path.join(out, "frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir)
    anims = {a["name"]: a for a in model.get("animations", [])}
    req = {"out": frames_dir, "figure": glb, "item": item_glb, "height": H, "size": size,
           "background": list(BACKGROUND), "ground": True, "views": views,
           "clips": [{"name": n, "frames": anims[n]["frames"], "fps": anims[n]["fps"],
                      "travel": anims[n].get("travel")} for n in names if n in anims]}
    req_path = os.path.join(out, "render_request.json")
    _write(req_path, req)
    timings["render"] = _run([godot, "--path", GODOT_PROJECT, "--audio-driver", "Dummy", "--windowed", "--position",
                              "-20000,-20000", "--resolution", "64x64", "res://bench.tscn", "--", "--request",
                              req_path], out, "render")
    render_path = os.path.join(frames_dir, "render.json")
    render = _load(render_path) if os.path.exists(render_path) else {}
    rows = clip_rows(names, model, report, render)
    t1 = time.time()
    for row in rows:
        if row["rendered"]:
            row["preview"], row["sheet"] = assemble(frames_dir, row["name"], views, row["fps"] or 24.0, out, fmt,
                                                    title)
    timings["previews"] = round(time.time() - t1, 1)
    if not keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
    timings["total"] = round(time.time() - t0, 1)
    doc = {"format": FORMAT, "version": 1, "status": "pending review", "title": title,
           "made": time.strftime("%Y-%m-%d %H:%M:%S"), "rig": os.path.abspath(rig), "clips_file": os.path.abspath(clips),
           "item": item, "height": H, "views": views, "size": size, "thresholds": report.get("thresholds", {}),
           "clips": rows, "timings": timings, "compare": os.path.abspath(compare) if compare else None}
    _write(os.path.join(out, "bench.json"), doc)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page_html(doc, prev, rel))
    return doc


def _floats(text):
    return [float(y) for y in text.split(",") if y.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m forge.bench", description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True, help="a rigged humanoid GLB, or a figure the exporter wrote")
    ap.add_argument("--clips", required=True, help="the motion library (JSON)")
    ap.add_argument("--out", required=True, help="the bench's folder")
    ap.add_argument("--items", default="", help="the folder of held items (<name>.glb and <name>.json)")
    ap.add_argument("--item", default="", help="the held item's name in --items (only that item is used)")
    ap.add_argument("--only", default="", help="clips: names or shell patterns, comma separated (default: all)")
    ap.add_argument("--height", type=float, default=1.8, help="the figure's height in metres")
    ap.add_argument("--yaws", default="45,-90,-135",
                    help="the game camera's directions, degrees around the figure (0 in front, + towards its left)")
    ap.add_argument("--pitch", type=float, default=30.0, help="the game camera's degrees above the horizon")
    ap.add_argument("--game-px", type=int, default=115, help="the standing figure's height on screen in the game")
    ap.add_argument("--close-px", type=int, default=440, help="the figure's height in the close view")
    ap.add_argument("--close-yaw", type=float, default=-35.0, help="the close view's direction (degrees)")
    ap.add_argument("--format", default="webp", choices=("webp", "gif"))
    ap.add_argument("--compare", default="", help="a previous bench folder shown under each clip")
    ap.add_argument("--title", default="")
    ap.add_argument("--keep-frames", action="store_true", help="keep Godot's PNG frames (<out>/frames)")
    ap.add_argument("--config", default=os.path.join(ROOT, "forge.local.json"))
    a = ap.parse_args(argv)
    try:
        doc = run_bench(a.rig, a.clips, a.out, load_config(a.config), items=a.items or None, item=a.item or None,
                        only=[w for w in a.only.split(",") if w.strip()] or None, height=a.height,
                        yaws=_floats(a.yaws), pitch=a.pitch, game_px=a.game_px, close_px=a.close_px,
                        close_yaw=a.close_yaw, fmt=a.format, compare=a.compare or None, title=a.title or None,
                        keep_frames=a.keep_frames)
    except BenchError as ex:
        print("BENCH FAILED", ex, flush=True)
        return 1
    for row in doc["clips"]:
        extra = []
        if row.get("feet"):
            extra.append("gap=%s slide=%s" % (_fmt(row["feet"]["ankle_gap_min_cm"]),
                                              _fmt(row["feet"]["planted_slide_cm"])))
        if row.get("item"):
            extra.append("item_yaw=%s pitch=%s" % (_fmt(row["item"]["yaw_mean_deg"]),
                                                   _fmt(row["item"]["pitch_mean_deg"])))
        print("%-20s %s %s" % (row["name"], " ".join("%s=%s" % (k, _fmt(row["measures"][k])) for k in MEASURES),
                               " ".join(extra)))
    print("BENCH", os.path.join(os.path.abspath(a.out), "index.html"), json.dumps(doc["timings"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
