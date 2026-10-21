"""Smooth normals and a baked normal map on an exported model without exporting it again:
python -m forge.normals.

    python -m forge.normals SOURCE --out DIR [--raw RAW.glb [--via PAINTED.glb]] [--crease 60]
                            [--normals auto|always|keep] [--size 0] [--max-size 2048]
                            [--relief 3.0] [--no-map] [--replace ours|all] [--device CPU|GPU]
                            [--tolerance 0.01] [--config forge.local.json]

SOURCE is an exporter's folder (<name>.glb, with model.json) or a GLB file. Steps:

1. The normals (tools/blender/glb_normals.py, no Blender): every triangle primitive without
   normals or with half its triangles or more shaded flat (--normals auto; always: every one;
   keep: none) gets creased smooth normals: edges whose faces meet at more than --crease
   degrees stay hard. Skeleton, skins, animations, nodes, materials and images are kept.
2. The normal map (unless --no-map): with --raw (the raw mesh before the mesh stage's
   reduction, in any frame: it is brought onto the model as it is, by its bounds, or found by
   a search over the two meshes' axes refined by closest points; with --via, the mesh in the
   raw mesh's frame that the model was exported from (the painted mesh) is found on the model
   instead, which is exact, and the raw mesh follows it), Blender (blender_python of
   the configuration) bakes a tangent-space map per base-colour image from it on the model's
   new normals (tools/blender/normal_bake.py; Cycles on --device, CPU by default); without
   --raw, or when it does not line up, the map is flat. The painted texture's relief is
   blended in (--relief; 0 for none). Size: --size, else the base colour's, at most
   --max-size. Each material with a base colour gets it as its normalTexture (PNG); a map the
   material already had that this command did not write is kept unless --replace all.
3. The check after writing: every triangle primitive has normals and is not shaded flat,
   every material with a base colour has a lossless normal map, and the nodes, skins,
   animations, materials and every image of the source (base colour, metallic-roughness,
   emissive...) are still there, byte for byte.

Writes into DIR: the GLB (same name) and normals.json (the report: normals before and after,
the bake and its fit, the maps, the check's flags). Exit code 0, 2 (bad arguments or input),
3 (the check flagged the written GLB). Nothing is sent anywhere; the GPU is used only with
--device GPU (then under the machine-wide GPU lock).
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools", "blender")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import glb_normals as gn  # noqa: E402

BAKE_SCRIPT = os.path.join(TOOLS, "normal_bake.py")
SCRIPTS = [os.path.join(TOOLS, "glb_normals.py"), os.path.join(TOOLS, "glb_materials.py")]


def code_fingerprint():
    """The SHA-256 of the normals step's code (the factory's specification of a normals job),
    line endings normalised (a checkout's CRLF or LF give the same)."""
    h = hashlib.sha256()
    for p in SCRIPTS:
        with open(p, "rb") as f:
            h.update(f.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def _find_source(source):
    """The GLB of an exporter's folder (model.json's name, else its only GLB) or a GLB file."""
    if os.path.isdir(source):
        glbs = sorted(f for f in os.listdir(source) if f.lower().endswith(".glb"))
        model = os.path.join(source, "model.json")
        name = None
        if os.path.isfile(model):
            with open(model, encoding="utf-8-sig") as f:
                name = json.load(f).get("name")
        pick = (name + ".glb") if name and (name + ".glb") in glbs else (glbs[0] if len(glbs) == 1 else None)
        if pick is None:
            raise ValueError("%s: no single GLB (found %s)" % (source, ", ".join(glbs) or "none"))
        return os.path.join(source, pick)
    if not os.path.isfile(source):
        raise ValueError("%s: no such file or folder" % source)
    return source


def _decode(data):
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def _resize(rgb, size):
    from PIL import Image
    if rgb.shape[0] == size[1] and rgb.shape[1] == size[0]:
        return rgb
    return np.asarray(Image.fromarray(rgb).resize(tuple(size), Image.BILINEAR))


def _signature(rgb):
    a = np.asarray(rgb, np.float64)
    h, w = a.shape[:2]
    ys = np.linspace(0, h, 9).astype(int)
    xs = np.linspace(0, w, 9).astype(int)
    return np.array([[a[ys[i]:ys[i + 1], xs[j]:xs[j + 1], :3].reshape(-1, 3).mean(0) for j in range(8)]
                     for i in range(8)])


def run_bake(glb, raw, work, config, size=0, max_size=2048, device="CPU", tolerance=None, via=None, timeout=7200,
             emit=print):
    """Blender's bake of a GLB's normal maps from a raw mesh (tools/blender/normal_bake.py).
    Returns its record (bake.json) with each map's file made absolute."""
    exe = config.get("blender_python")
    if not exe or not os.path.isfile(exe):
        raise ValueError("blender_python is not configured (forge.local.json): the bake needs it")
    cmd = [exe, BAKE_SCRIPT, "--glb", glb, "--out", work, "--raw", os.path.abspath(raw), "--device", device,
           "--size", str(int(size)), "--max-size", str(int(max_size))]
    if tolerance is not None:
        cmd += ["--tolerance", str(tolerance)]
    if via:
        cmd += ["--via", os.path.abspath(via)]
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    lock = None
    if device == "GPU":
        from . import gpu_lock
        lock = gpu_lock.acquire("forge.normals", gpu_lock.lock_dir())
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           cwd=work, **flags)
    finally:
        if lock:
            from . import gpu_lock
            gpu_lock.release(path=lock)
    with open(os.path.join(work, "bake.stdout.log"), "w", encoding="utf-8") as f:
        f.write(r.stdout)
    with open(os.path.join(work, "bake.stderr.log"), "w", encoding="utf-8") as f:
        f.write(r.stderr)
    path = os.path.join(work, "bake.json")
    if r.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError("the bake failed (exit %s): %s" % (r.returncode, (r.stdout + r.stderr)[-2000:]))
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    for m in rec.get("maps", []):
        m["file"] = os.path.join(work, m["file"])
    if rec.get("fit"):
        emit("FIT %s p90 %.5f m (tolerance %.5f m) %s" % (rec["fit"]["how"], rec["fit"]["distance_m"]["p90"],
                                                       rec["fit"]["tolerance_m"], "used" if rec["fit"]["used"] else
                                                       "NOT USED"))
    return rec


def _hashes(glb):
    return {hashlib.sha256(glb.image_bytes(i)).hexdigest() for i in range(len(glb.doc.get("images", [])))
            if "bufferView" in glb.doc["images"][i] or glb.doc["images"][i].get("uri", "").startswith("data:")}


def refresh(src, out_dir, raw=None, crease=gn.CREASE_DEG, normals="auto", size=0, max_size=2048,
            relief=gn.RELIEF_STRENGTH, make_map=True, replace="ours", device="CPU", tolerance=None, via=None,
            config=None, emit=print):
    """Smooth normals and the normal map of the GLB src written to out_dir (same name) with
    normals.json. Returns the report (its "flags" empty when the check passed)."""
    config = config or {}
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(src)
    work = tempfile.mkdtemp(prefix="forge_normals_")
    try:
        smoothed = os.path.join(work, name)
        if normals == "keep":
            shutil.copyfile(src, smoothed)
            smooth = {"mode": "keep", "primitives": gn.normals_state(src)}
        else:
            smooth = gn.smooth_glb(src, smoothed, crease, mode=normals)
        report = {"format": "forge-normals", "version": 1, "status": "pending review", "source": name,
                  "crease_deg": crease, "normals": smooth, "map": None, "flags": []}
        glb = gn.Glb(smoothed)
        if make_map:
            bake = None
            if raw:
                bake = run_bake(smoothed, raw, work, config, size, max_size, device, tolerance, via=via, emit=emit)
                report["bake"] = {k: v for k, v in bake.items() if k != "maps"}
                if bake.get("raw_problem"):
                    report["flags"].append("bake: " + bake["raw_problem"])
            maps, records = {}, {}
            baked = [(m, np.array(m["signature"])) for m in (bake or {}).get("maps", [])]
            for mi in range(len(glb.doc.get("materials", []))):
                ii = gn.base_colour_image(glb, mi)
                if ii is None:
                    continue
                base = _decode(glb.image_bytes(ii))
                h, w = base.shape[:2]
                side = size or min(max(w, h), max_size)
                target = (side, side)
                rec = {"size": list(target), "baked": False, "relief": relief}
                n = np.dstack([np.zeros((side, side)), np.zeros((side, side)), np.ones((side, side))])
                if baked:
                    sig = _signature(base)
                    m, _ = min(baked, key=lambda x: float(np.abs(x[1] - sig).mean()))
                    from PIL import Image
                    b = np.asarray(Image.open(m["file"]).convert("RGB"))
                    n = gn.decode_normals(_resize(b, target))
                    rec.update(baked=True, from_map=os.path.basename(m["file"]))
                if relief:
                    n = gn.blend_normals(n, gn.relief_normals(_resize(base, target), relief))
                maps[mi] = gn.encode_normals(n)
                records[str(mi)] = rec
            done = gn.set_normal_maps(glb, maps, replace=replace)
            for mi, what in done.items():
                records[str(mi)]["slot"] = what
            report["map"] = {"materials": records, "max_size": max_size, "size": size or None}
        out = os.path.join(work, "out_" + name)
        glb.save(out)
        check = gn.check_glb(out, require_maps=make_map)
        report["check"] = check
        report["flags"] += check["flags"]
        # Everything else kept: nodes, skins, animations, materials and the source's images.
        a, b = gn.Glb(src), gn.Glb(out)
        for key in ("nodes", "skins", "animations", "materials", "meshes"):
            if len(a.doc.get(key, [])) != len(b.doc.get(key, [])):
                report["flags"].append("%s: %d in the source, %d written" % (key, len(a.doc.get(key, [])),
                                                                             len(b.doc.get(key, []))))
        lost = _hashes(a) - _hashes(b)
        replaced = {hashlib.sha256(a.image_bytes(i)).hexdigest() for i, im in enumerate(a.doc.get("images", []))
                    if im.get("name", "").startswith(gn.NORMAL_IMAGE)}
        if lost - replaced:
            report["flags"].append("%d image(s) of the source are missing from the written GLB" % len(lost - replaced))
        for k in ("emissiveTexture",):
            if sum(k in m for m in a.doc.get("materials", [])) != sum(k in m for m in b.doc.get("materials", [])):
                report["flags"].append("an emissive map was lost")
        report["status"] = "pending review" if not report["flags"] else "flagged"
        shutil.copyfile(out, os.path.join(out_dir, name))
        with open(os.path.join(out_dir, "normals.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        nb = sum(1 for r in (report["map"] or {}).get("materials", {}).values() if r["baked"])
        emit("NORMALS %s rewritten=%d/%d maps=%d baked=%d flags=%d -> %s" % (
            name, sum(1 for p in smooth["primitives"] if p.get("rewritten")), len(smooth["primitives"]),
            len((report["map"] or {}).get("materials", {})), nb, len(report["flags"]), out_dir))
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m forge.normals", description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="an exporter's folder or a GLB file")
    ap.add_argument("--out", required=True, help="output folder (may be the source folder)")
    ap.add_argument("--raw", default=None, help="the raw mesh before the reduction (GLB, OBJ or PLY)")
    ap.add_argument("--via", default=None,
                    help="a mesh in the raw mesh's frame with the model's own surface (the painted mesh the model was "
                         "exported from): the model is found on it and the raw mesh follows")
    ap.add_argument("--crease", type=float, default=gn.CREASE_DEG, help="edges sharper than this stay hard (degrees)")
    ap.add_argument("--normals", default="auto", choices=("auto", "always", "keep"))
    ap.add_argument("--size", type=int, default=0, help="normal map size (0: the base colour's, at most --max-size)")
    ap.add_argument("--max-size", type=int, default=2048)
    ap.add_argument("--relief", type=float, default=gn.RELIEF_STRENGTH, help="the painted relief's strength (0: none)")
    ap.add_argument("--no-map", action="store_true", help="smooth normals only, no normal map")
    ap.add_argument("--replace", default="ours", choices=("ours", "all"),
                    help="ours: replace only a normal map this command wrote; all: any")
    ap.add_argument("--device", default="CPU", choices=("CPU", "GPU"), help="Cycles device of the bake")
    ap.add_argument("--tolerance", type=float, default=None,
                    help="how far the raw mesh may lie from the model (share of its extent; default 0.01)")
    ap.add_argument("--config", default=os.path.join(ROOT, "forge.local.json"))
    a = ap.parse_args(argv)
    try:
        from .config import load_config
        config = load_config(a.config if os.path.isfile(a.config) else None)
        src = _find_source(os.path.abspath(a.source))
        if a.raw and not os.path.isfile(a.raw):
            raise ValueError("%s: no such raw mesh" % a.raw)
        if a.via and not os.path.isfile(a.via):
            raise ValueError("%s: no such mesh" % a.via)
        rep = refresh(src, os.path.abspath(a.out), raw=a.raw, crease=a.crease, normals=a.normals, size=a.size,
                      max_size=a.max_size, relief=a.relief, make_map=not a.no_map, replace=a.replace,
                      device=a.device, tolerance=a.tolerance, via=a.via, config=config)
    except (ValueError, OSError) as e:
        print("forge.normals: %s" % e, file=sys.stderr)
        return 2
    for f in rep["flags"]:
        print("FLAG", f)
    return 3 if rep["flags"] else 0


if __name__ == "__main__":
    sys.exit(main())
