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
