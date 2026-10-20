"""Glow on an exported model without exporting it again: python -m forge.glow.

Finds what glows on a model's painted base colour (tools/blender/glow_regions.py: the
colours the piece's text names as glowing, bright, saturated, brighter than around them, in
small regions, and the reference picture's lit colours when given) and writes a copy of the
GLB with that emissive map in its material's emissive slot, plus the mask and a report.

    python -m forge.glow SOURCE --out DIR [--colours cyan,emerald] [--text TEXT]
                         [--order ORDER.json --piece FAMILY/PIECE] [--reference PICTURE]
                         [--strength 2.0] [--set name=value ...] [--off]

SOURCE is an exporter's folder (the humanoid's or the creature's: <name>.glb with model.json
and, for a humanoid, regions_mask.png) or a GLB file (a figure's painted mesh). The glow
colours come from --colours, else from --text, else from the piece's text in the order
(--order and --piece: the same text the factory composes for the piece's concepts), else the
model's own record (model.json regions.glow_colours). --off clears the emissive slot and the
mask's blue channel. Nothing is sent anywhere and no GPU is used.

Writes into DIR: the GLB (same name), glow.json (the report), glow_emissive.png (the map) and
the mask: regions_mask.png (the source folder's, red skin and green hair kept, blue replaced)
or, for a GLB without one, glow_mask.png (the glow weight in grey); with a model.json, a copy
with regions.glow_* updated. DIR may be the source folder (the files are replaced).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools", "blender")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import glow_regions  # noqa: E402

SETTING_NAMES = ("value", "chroma", "saturation", "region_saturation", "chroma_contrast", "contrast", "weak_value", "weak_chroma", "core_value", "max_region",
                 "max_total", "reference_weight")
# The default view of each recipe's concepts (forge/recipes.py).
VIEWS = {"humanoid": "view_humanoid", "creature": "view_creature", "articulated": "view_articulated",
         "held_item": "view_held_item", "figure": "view_figure", "vehicle": "view_vehicle", "prop": "view_prop",
         "object_3d": "view_object", "illustration": "view_illustration"}
SCRIPTS = [os.path.join(TOOLS, "glow_regions.py"), os.path.abspath(__file__)]


def piece_text(order_path, piece_ref):
    """The text the factory composes for a piece's concepts (forge.recipes.prompt_of)."""
    from .order import load_order
    from .recipes import prompt_of
    order = load_order(order_path)
    fid, _, aid = piece_ref.partition("/")
    st = order.get("style") or {}
    for fam in order.get("families", []):
        if fam.get("id") != fid:
            continue
        for a in fam.get("assets", []):
            if a.get("id") == aid:
                recipe = a.get("recipe") or fam.get("recipe")
                view = a.get("view", fam.get("view", VIEWS.get(recipe, "view_figure")))
                return prompt_of(dict({view: ""}, **st), fam, a, view)
    raise ValueError("%s: no piece %s" % (order_path, piece_ref))


def _find_source(source):
    """(glb, regions mask or None, model.json or None) of an export folder or a GLB file."""
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
        regions = os.path.join(source, "regions_mask.png")
        return (os.path.join(source, pick), regions if os.path.isfile(regions) else None,
                model if os.path.isfile(model) else None)
    if not os.path.isfile(source):
        raise ValueError("%s: no such file or folder" % source)
    return source, None, None


def glow_model(glb, out_dir, colours, reference=None, regions=None, model=None, settings=None, strength=None,
               off=False, emit=print):
    """Detect the glow of a GLB's base colour and write the GLB, the mask, the map, the report
    and the model record into out_dir (glow_regions.glow_files). colours: names or ranges
    (glow_regions.parse_hues). A glow written earlier (by an exporter or by this command) is
    replaced; an emissive map of the material's own is kept. Returns the report."""
    settings = {k: v for k, v in (settings or {}).items() if v is not None}
    colours = [] if off else list(colours or [])
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="forge_glow_")
    try:
        name = os.path.basename(glb)
        files = {"glb": name, "report": "glow.json", "emissive": "glow_emissive.png",
                 "mask": "regions_mask.png" if regions else "glow_mask.png"}
        rep = glow_regions.glow_files(
            glb, colours, out_glb=os.path.join(tmp, name), reference=reference, regions=regions,
            regions_out=os.path.join(tmp, files["mask"]) if regions else None,
            mask_out=None if regions else os.path.join(tmp, files["mask"]),
            emissive_out=os.path.join(tmp, files["emissive"]), settings=settings, strength=strength)
        report = dict({"format": "forge-glow", "version": 1, "status": "pending review", "source": name}, **rep)
        if model:
            with open(model, encoding="utf-8-sig") as f:
                info = json.load(f)
            info["regions"] = dict(info.get("regions") or {}, **glow_regions.glow_record(report))
            with open(os.path.join(tmp, "model.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=1)
            files["model"] = "model.json"
        report["files"] = files
        with open(os.path.join(tmp, "glow.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        for fname in files.values():
            shutil.copyfile(os.path.join(tmp, fname), os.path.join(out_dir, fname))
        emit("GLOW %s colours=%s pixels=%d share=%.5f -> %s" % (
            name, ",".join(colours) or "none", report["glow_pixels"], report["glow_share"], out_dir))
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _parse_set(items):
    out = {}
    for item in items or []:
        name, _, value = item.partition("=")
        name = name.strip()
        if name not in SETTING_NAMES:
            raise ValueError("unknown setting %r (settings: %s)" % (name, ", ".join(SETTING_NAMES)))
        out[name] = float(value)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m forge.glow", description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="an exporter's folder or a GLB file")
    ap.add_argument("--out", required=True, help="output folder (may be the source folder)")
    ap.add_argument("--colours", default=None, help="glow colours: names or low-high degrees, comma separated")
    ap.add_argument("--text", default=None, help="a text naming the glowing colours (the piece's brief)")
    ap.add_argument("--order", default=None, help="an order file: the piece's text gives the colours")
    ap.add_argument("--piece", default=None, help="FAMILY/PIECE in --order")
    ap.add_argument("--reference", default=None, help="the reference picture the paint stage projected (RGBA cutout)")
    ap.add_argument("--strength", type=float, default=None, help="emissive strength (default %s)" %
                    glow_regions.DEFAULTS["strength"])
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="a detection setting: %s" % ", ".join(SETTING_NAMES))
    ap.add_argument("--off", action="store_true", help="clear the glow (no emissive map, mask blue 0)")
    a = ap.parse_args(argv)
    try:
        glb, regions, model = _find_source(os.path.abspath(a.source))
        if a.colours is not None:
            colours = [c.strip() for c in a.colours.split(",") if c.strip()]
        elif a.text is not None:
            colours = glow_regions.colours_in_text(a.text)
        elif a.order:
            if not a.piece:
                raise ValueError("--order needs --piece FAMILY/PIECE")
            colours = glow_regions.colours_in_text(piece_text(a.order, a.piece))
        elif model:
            with open(model, encoding="utf-8-sig") as f:
                colours = list(((json.load(f).get("regions") or {}).get("glow_colours")) or [])
        else:
            raise ValueError("no glow colours: give --colours, --text or --order with --piece")
        glow_regions.parse_hues(colours)
        glow_model(glb, os.path.abspath(a.out), colours, reference=a.reference, regions=regions, model=model,
                   settings=_parse_set(a.set), strength=a.strength, off=a.off)
    except (ValueError, OSError) as e:
        print("forge.glow: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
