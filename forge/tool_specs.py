"""The order's part lists, body plans and motions, checked by the same code the local tools
use (tools/blender/articulated_parts.py, creature_bones.py, creature_motion.py: modules
without bpy), so the order check and the tools never disagree."""

import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools", "blender")


def _tool(name):
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    return importlib.import_module(name)


def glow_colours_problem(colours):
    """None when colours are valid glow colours (tools/blender/glow_regions.py), else why."""
    return _tool("glow_regions").hue_problem(colours)


def glow_colours_in_text(text):
    """The glow colours a piece's text names (tools/blender/glow_regions.py)."""
    return _tool("glow_regions").colours_in_text(text)


def check_parts(parts):
    return _tool("articulated_parts").check_parts(parts)


def check_body(body):
    return _tool("creature_bones").check_body(body)


def body_plan(body):
    return _tool("creature_bones").body_plan(body)


def check_motions(motions, body, library=None):
    """Problems of a piece's motions for a valid body (library: the piece's inputs.clips
    document, or None)."""
    return _tool("creature_motion").check_motions(motions, body_plan(body), library)


def read_library(path):
    """(document, problems) of a forge-creature-clips file."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError) as ex:
        return None, ["%s cannot be read: %s" % (path, ex)]
    return doc, _tool("creature_motion").check_library(doc)


def library_files(motions):
    """The library files the motions name ("library": {"file"}), in order, once each."""
    out = []
    for m in motions or []:
        if isinstance(m, dict) and m.get("source") == "library" and isinstance(m.get("library"), dict):
            f = m["library"].get("file")
            if isinstance(f, str) and f not in out:
                out.append(f)
    return out


# ---- held items ---------------------------------------------------------------------------------

def check_item(item):
    """Problems of a held-item piece's item object (tools/blender/held_item_geometry.py)."""
    return _tool("held_item_geometry").check_item(item)


def check_figure(path):
    """Problems of the held-item recipe's reference figure: a GLB with a skin (a rigged
    humanoid: a rig tool's output, or a figure the humanoid exporter wrote)."""
    try:
        doc = _tool("glb_materials").read_glb(path)[0]
    except Exception as ex:  # an unreadable file is a problem of the order, not a crash
        return ["cannot be read as a GLB (%s)" % ex]
    if not doc.get("skins"):
        return ["has no skin: it must be a rigged humanoid GLB"]
    return []


def clip_kind(name, clip):
    """A motion library clip's kind: its "kind", else its name's first word."""
    return clip.get("kind") or name.split("_")[0]


def hold_class(clip):
    """The held-item class a library clip holds ("<class>" or "<class>_<mode>"), or None."""
    holds = clip.get("holds")
    return holds.split("_")[0] if isinstance(holds, str) and holds else None


def select_hold_clips(library, cls, names=None):
    """The clips of a motion library a held item of class cls is shown in: the given names,
    else the first clip of kind "idle" and the first of kind "aim" that hold the class, else
    the first clip that holds it. Returns (names, problems)."""
    anims = library.get("anims") if isinstance(library, dict) else None
    if not isinstance(anims, dict):
        return [], ["the motion library has no anims object"]
    if names:
        missing = [n for n in names if n not in anims]
        if missing:
            return [], ["the motion library has no clip %s" % ", ".join(missing)]
        wrong = [n for n in names if hold_class(anims[n]) != cls]
        if wrong:
            return [], ["clip %s does not hold class %s" % (", ".join(wrong), cls)]
        return list(names), []
    holding = [n for n, c in anims.items() if isinstance(c, dict) and hold_class(c) == cls]
    out = []
    for kind in ("idle", "aim"):
        found = next((n for n in holding if clip_kind(n, anims[n]) == kind), None)
        if found:
            out.append(found)
    if not out and holding:
        out.append(holding[0])
    if not out:
        return [], ["no clip of the motion library holds class %s" % cls]
    return out, []


def check_hold_clips(path, item):
    """Problems of the motion library a held item's hold stage reads."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError) as ex:
        return ["%s cannot be read: %s" % (path, ex)]
    return select_hold_clips(doc, item.get("class"), item.get("clips"))[1]
