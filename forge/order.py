"""The order: one JSON file per order, written by the game's operator (docs/order.md).

load_order(path) reads it, merges the family defaults of the per-piece keys into each piece
and resolves the input paths; check_order(order, config, server) lists every problem that
would stop or spoil a night's run before anything is sent to the server.
"""

import copy
import json
import os

from . import stages
from .config import missing_tools

# Per-piece keys a family may set as defaults for its pieces. Dictionaries merge key by key
# (the piece wins); the other keys are taken from the family when the piece has none.
PIECE_KEYS = ("recipe", "variants", "stages", "skip", "params", "inputs", "review")
DICT_KEYS = ("stages", "params", "inputs", "review")
FORMAT_VERSION = 1


def _resolve(path, base):
    if not isinstance(path, str):
        return path
    path = os.path.expandvars(os.path.expanduser(path))
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))


def _resolve_files(v, base):
    """{"file": path} references inside a raw task's values or images, made absolute."""
    if isinstance(v, dict):
        out = {k: _resolve_files(x, base) for k, x in v.items()}
        if "file" in out and "ref" not in out:
            out["file"] = _resolve(out["file"], base)
        return out
    if isinstance(v, list):
        return [_resolve_files(x, base) for x in v]
    return v


def merge_piece(fam, piece):
    """The piece with the family defaults of PIECE_KEYS merged in (a new dictionary)."""
    out = dict(piece)
    for k in PIECE_KEYS:
        if k in DICT_KEYS:
            if isinstance(fam.get(k), dict) or isinstance(piece.get(k), dict):
                merged = dict(fam.get(k) or {})
                merged.update(piece.get(k) or {})
                out[k] = merged
        elif k not in piece and k in fam:
            out[k] = copy.deepcopy(fam[k])
    return out


def merge_order(order, base):
    """The order with family defaults merged into every piece and input paths absolute
    (relative paths are relative to base, the order's folder)."""
    order = copy.deepcopy(order)
    for fam in order.get("families", []) if isinstance(order.get("families"), list) else []:
        if not isinstance(fam, dict) or not isinstance(fam.get("assets"), list):
            continue
        pieces = []
        for piece in fam["assets"]:
            if not isinstance(piece, dict):
                pieces.append(piece)
                continue
            p = merge_piece(fam, piece)
            if isinstance(p.get("inputs"), dict):
                p["inputs"] = {k: _resolve(v, base) for k, v in p["inputs"].items()}
            pieces.append(p)
        fam["assets"] = pieces
        if isinstance(fam.get("inputs"), dict):
            fam["inputs"] = {k: _resolve(v, base) for k, v in fam["inputs"].items()}
    tasks = order.get("tasks")
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict):
                for key in ("values", "images"):
                    if key in t:
                        t[key] = _resolve_files(t[key], base)
    return order


def load_order(path, base=None):
    """The order of a file, with family defaults merged into each piece and inputs paths
    resolved to absolute (relative to the order file, or to base when given)."""
    with open(path, encoding="utf-8-sig") as f:
        order = json.load(f)
    if not isinstance(order, dict):
        raise ValueError("%s: an order is a JSON object" % path)
    return merge_order(order, base or os.path.dirname(os.path.abspath(path)))


# --- checking

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_ok(kind, v):
    if kind == "int":
        return _is_int(v) and v > 0
    if kind == "number":
        return _is_number(v) and v > 0
    if kind == "bool":
        return isinstance(v, bool)
    return True


def _type_text(kind):
    return {"int": "a whole number above zero", "number": "a number above zero",
            "bool": "true or false"}.get(kind, kind)


def _safe_id(v):
    return isinstance(v, str) and v and "/" not in v and "\\" not in v and v not in (".", "..")


def _check_piece(where, recipe, piece, config, problems):
    from .recipes import RECIPES
    if recipe not in RECIPES:
        problems.append("%s: unknown recipe %r (known: %s)" % (where, recipe, ", ".join(sorted(RECIPES))))
        return
    if not isinstance(piece.get("brief"), str) or not piece["brief"].strip():
        problems.append("%s: brief is required (the art direction text of the piece)" % where)
    if "variants" in piece and not (_is_int(piece["variants"]) and piece["variants"] >= 1):
        problems.append("%s: variants must be a whole number of at least 1, not %r" % (where, piece["variants"]))
    cat = stages.stages_of(recipe)
    picks = piece.get("stages", {})
    if not isinstance(picks, dict):
        problems.append("%s: stages must be an object {stage: option}" % where)
        picks = {}
    for stage, opt in picks.items():
        if stage not in cat:
            problems.append("%s: recipe %s has no stage %r%s" % (
                where, recipe, stage, " (stages: %s)" % ", ".join(cat) if cat else " (it has no swappable stages)"))
        elif opt not in cat[stage]["options"]:
            problems.append("%s: stage %s of recipe %s has no option %r (options: %s)" % (
                where, stage, recipe, opt, ", ".join(sorted(cat[stage]["options"]))))
    skip = piece.get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(s, str) for s in skip):
        problems.append("%s: skip must be a list of stage names" % where)
        skip = []
    skippable = [s for s, st in cat.items() if st.get("skippable")]
    for s in skip:
        if s not in skippable:
            problems.append("%s: stage %r cannot be skipped in recipe %s (skippable: %s)" % (
                where, s, recipe, ", ".join(skippable) or "none"))
    piece = dict(piece, stages={k: v for k, v in picks.items() if k in cat and v in cat[k]["options"]},
                 skip=[s for s in skip if s in skippable])
    params = piece.get("params", {})
    if not isinstance(params, dict):
        problems.append("%s: params must be an object" % where)
        params = {}
    allowed = stages.allowed_params(recipe, piece)
    for name, v in params.items():
        if name not in stages.PARAMS:
            problems.append("%s: unknown param %r (known: %s)" % (where, name, ", ".join(sorted(stages.PARAMS))))
        elif name not in allowed:
            problems.append("%s: param %r is not used by recipe %s with these stages (used: %s)" % (
                where, name, recipe, ", ".join(sorted(allowed)) or "none"))
        elif not _type_ok(stages.PARAMS[name][0], v):
            problems.append("%s: param %s must be %s, not %r" % (where, name, _type_text(stages.PARAMS[name][0]), v))
    inputs = piece.get("inputs", {})
    if not isinstance(inputs, dict):
        problems.append("%s: inputs must be an object {name: path}" % where)
        inputs = {}
    allowed = stages.allowed_inputs(recipe, piece)
    for name, path in inputs.items():
        if name not in allowed:
            problems.append("%s: input %r is not used by recipe %s with these stages (used: %s)" % (
                where, name, recipe, ", ".join(sorted(allowed)) or "none"))
        elif not isinstance(path, str) or not path:
            problems.append("%s: input %s must be a path" % (where, name))
        elif not os.path.exists(path):
            problems.append("%s: input %s not found: %s" % (where, name, path))
    review = piece.get("review")
    if review is not None:
        if "review" not in stages.chosen_options(recipe, piece):
            problems.append("%s: recipe %s has no review stage; remove review" % (where, recipe))
        elif not isinstance(review, dict):
            problems.append("%s: review must be an object" % where)
        else:
            for k, v in review.items():
                if k not in stages.REVIEW_KEYS:
                    problems.append("%s: unknown review key %r (known: %s)" % (
                        where, k, ", ".join(stages.REVIEW_KEYS)))
                elif not _type_ok(stages.REVIEW_KEYS[k], v):
                    problems.append("%s: review %s must be %s, not %r" % (where, k, _type_text(stages.REVIEW_KEYS[k]), v))
    for p in missing_tools(config, stages.tools_needed(recipe, piece)):
        problems.append("%s: %s" % (where, p))


def _check_files(where, v, problems):
    if isinstance(v, dict):
        if "file" in v and "ref" not in v:
            if not isinstance(v["file"], str) or not os.path.exists(v["file"]):
                problems.append("%s: input file not found: %s" % (where, v["file"]))
        for x in v.values():
            _check_files(where, x, problems)
    elif isinstance(v, list):
        for x in v:
            _check_files(where, x, problems)


def node_classes(server):
    """The node classes a server has: server is the /object_info answer (a dictionary) or an
    object with get_json (forge.comfy_server.Server)."""
    info = server if isinstance(server, dict) else server.get_json("/object_info")
    return set(info)


def check_order(order, config, server=None):
    """Problems of an order (an empty list when it is valid): format, known recipes, stage
    options, skip, variants, params, inputs present, tools present in config and, with a
    server, the ComfyUI node classes the workflows use. Nothing is sent to the server
    except a GET of /object_info when server is a client object."""
    problems = []
    if not isinstance(order, dict):
        return ["the order must be a JSON object"]
    if order.get("version") != FORMAT_VERSION:
        problems.append("version must be %d, not %r" % (FORMAT_VERSION, order.get("version")))
    if not isinstance(order.get("style", {}), dict):
        problems.append("style must be an object {key: text}")
    fams = order.get("families")
    if not isinstance(fams, list):
        problems.append("families must be a list")
        fams = []
    fam_ids = set()
    for i, fam in enumerate(fams):
        if not isinstance(fam, dict):
            problems.append("families[%d] must be an object" % i)
            continue
        fid = fam.get("id")
        if not _safe_id(fid):
            problems.append("families[%d]: id must be a non-empty name without slashes, not %r" % (i, fid))
            continue
        if fid in fam_ids:
            problems.append("family %s appears twice" % fid)
        fam_ids.add(fid)
        if not isinstance(fam.get("assets"), list):
            problems.append("family %s: assets must be a list" % fid)
            continue
        ids = set()
        for j, piece in enumerate(fam["assets"]):
            if not isinstance(piece, dict):
                problems.append("family %s: assets[%d] must be an object" % (fid, j))
                continue
            pid = piece.get("id")
            if not _safe_id(pid):
                problems.append("family %s: assets[%d]: id must be a non-empty name without slashes, not %r" % (fid, j, pid))
                continue
            if pid in ids:
                problems.append("family %s: piece %s appears twice" % (fid, pid))
            ids.add(pid)
            _check_piece("%s/%s" % (fid, pid), piece.get("recipe"), piece, config, problems)
    tasks = order.get("tasks", [])
    if not isinstance(tasks, list):
        problems.append("tasks must be a list")
        tasks = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or "/" not in t.get("id", ""):
            problems.append("tasks[%d]: id must be family/step or family/piece/step" % i)
            continue
        for key in ("values", "images"):
            _check_files("task %s" % t["id"], t.get(key), problems)
    if problems:
        return problems
    problems.extend(_check_jobs(order, server))
    return problems


def _check_jobs(order, server):
    from .comfy_jobs import drop_nodes
    from .factory_core import load_workflow
    from .recipes import expand, refs_of
    problems = []
    try:
        jobs = expand(order)
    except KeyError as ex:
        return ["the order cannot be expanded: missing key %s (for example a style text a recipe needs)" % ex]
    except Exception as ex:
        return ["the order cannot be expanded: %s" % ex]
    ids = set(j["id"] for j in jobs)
    for j in jobs:
        for r in refs_of(j):
            if r not in ids:
                problems.append("job %s refers to unknown job %s" % (j["id"], r))
    classes = None
    if server is not None:
        try:
            classes = node_classes(server)
        except Exception as ex:
            problems.append("server did not give its node classes: %s" % ex)
    seen = {}
    for j in jobs:
        if j.get("kind", "comfy") != "comfy":
            continue
        name = j.get("workflow")
        try:
            wf, _ = load_workflow(name)
            drop_nodes(wf, j.get("drop", []), name)
        except FileNotFoundError:
            problems.append("job %s: workflow %s not found" % (j["id"], name))
            continue
        except Exception as ex:
            problems.append("job %s: %s" % (j["id"], ex))
            continue
        if classes is None:
            continue
        for node in wf.values():
            cls = node["class_type"]
            if cls not in classes and (name, cls) not in seen:
                seen[(name, cls)] = True
                problems.append("workflow %s uses node class %s, which the server does not have" % (name, cls))
    return problems
