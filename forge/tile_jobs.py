"""The iso_tile recipe: sprites of an isometric tile grid remade from the game's own sprites
(docs/order.md, "The iso-tile recipe"; design, section 7).

A piece is one part of a tile (a floor, a west wall, a north wall or an object) drawn as a 2D
sprite in a fixed cell of the grid; the game supplies its original sprite (inputs.sprite),
which fixes the footprint. Per variant:

  floor, west_wall, north_wall
    guide (the original's face taken out of the sprite into a flat texture, the seam group's
    material detail laid over it) -> picture (Qwen Image Edit 2511 repaints it) -> edges (the
    border blended into the seam group's material, the ring repainted by Qwen Image 2.1 inpaint)
    -> sprite (the texture put back on the exact face geometry at the order's scale: albedo,
    normal, shaded and emissive frames, and the checks)
  object, form "picture"
    guide (the original sprite, or a block of it in the piece's variant grid, upscaled) ->
    picture -> sprite (the picture cut back into the grid's columns)
  object, form "mesh"
    guide -> picture -> cutout -> mesh (TRELLIS.2) -> paint (Hunyuan3D Paint) -> render (Blender:
    the mesh fitted to the original's silhouette and rendered by the grid's camera) -> sprite

then one review sheet and the final decision per piece. Per family: one seamless material per
seam group (`seams`), shared by the floors and walls that join, and one set sheet (`<family>/_set/
sheet`): a small map composed from the game's layout (`layout`) or a test arrangement.
"""

import math
import os

from . import acceptance, stages
from .factory_util import seeds_for

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "blender")

TILE_KEYS = ("kind", "seam", "grid", "height", "thickness", "rect", "cutout", "name", "glow", "dome", "alpha", "clip",
             "relief", "ground")
GROUNDS = ("keep", "cut")
CUTOUTS = ("original", "picture", "none")
ALPHAS = ("binary", "smooth")
SEAM_KEYS = ("kind", "brief", "span", "variants", "view", "note", "seamless", "saturation")
# How a seam group's picked texture is made seamless: "cut" (local: wrapped at the length whose
# continuation matches its start, through a min-error seam; patterns keep their phase) or
# "inpaint" (rolled by half and the cross repainted by Qwen Image 2.1 Turbo inpaint).
SEAMLESS = ("cut", "inpaint")
SEAM_KINDS = ("floor", "wall")
DEFAULT_SCALE = 4
DEFAULT_VARIANTS = 3
# The border band of a face texture that its seam group's material owns, as a share of a tile.
DEFAULT_BAND = 0.08
# The views of the pictures (style keys), by the piece's kind.
VIEWS = {"floor": "view_tile_floor", "west_wall": "view_tile_wall", "north_wall": "view_tile_wall",
         "object": "view_tile_object"}
SET_ASSET = "_set"
SEAM_PREFIX = "_seam_"
EDIT_VALUES = {"$NEGATIVE$": "", "$STEPS$": 4, "$CFG$": 1.0, "$LORA$": 1.0}


def _grid_module():
    import importlib
    import sys
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    return importlib.import_module("iso_grid")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def seam_span(seam):
    """[along u, along v] tiles a seam group's material covers: a floor's [x, y], a wall's
    [n, 1]."""
    span = seam.get("span", 1)
    if seam.get("kind") == "floor":
        return list(span) if isinstance(span, list) else [span, span]
    return [span if not isinstance(span, list) else max(span), 1]


def tile_spec(fam, a):
    """The piece's tile object, normalised: kind, seam, grid [x, y] (its variant grid: the game
    draws frame (y % gy) * gx + (x % gx) at map tile (x, y)), height and thickness (walls),
    rect (the canvas, grid pixels), cutout, name, glow, dome, alpha."""
    t = dict(a.get("tile") or {})
    kind = t.get("kind")
    seams = fam.get("seams") or {}
    seam = t.get("seam")
    grid = t.get("grid")
    if grid is None:
        if seam in seams:
            u = seam_span(seams[seam])
            grid = {"floor": u, "west_wall": [1, u[0]], "north_wall": [u[0], 1]}.get(kind, [1, 1])
        else:
            grid = [1, 1]
    face = kind in ("floor", "west_wall", "north_wall")
    return {"kind": kind, "seam": seam, "grid": list(grid), "height": t.get("height", 1.0),
            "thickness": t.get("thickness"), "rect": t.get("rect"),
            "cutout": t.get("cutout", ("none" if kind == "floor" else "original") if face else "picture"), "name": t.get("name", a["id"]),
            "glow": t.get("glow"), "dome": t.get("dome", 0.0 if face else 0.6),
            "alpha": t.get("alpha", "binary" if face else "smooth"), "clip": bool(t.get("clip", False)),
            "relief": t.get("relief", 1.0), "ground": t.get("ground", "keep")}


def frames_along(spec):
    """The tiles a face texture covers: [along u, along v] (a floor's grid; a wall's frames
    along it)."""
    gx, gy = spec["grid"]
    if spec["kind"] == "floor":
        return [gx, gy]
    if spec["kind"] == "west_wall":
        return [gy, 1]
    return [gx, 1]


def texture_size(g, spec, texels):
    G = _grid_module()
    along = frames_along(spec)
    if spec["kind"] == "floor":
        return G.face_texture_size(g, "floor", along, texels)
    return G.face_texture_size(g, spec["kind"], along[0], texels)


def material_text(st, fam, seam):
    view = seam.get("view") or ("material_top" if seam.get("kind") == "floor" else "material_front")
    parts = (st[view], seam.get("brief", ""), fam.get("material_style", ""), st.get("material_look", ""))
    return " ".join(p.strip() for p in parts if p and p.strip())


def seam_jobs(fam, gid, seam, st, g, pieces=None):
    """The seamless material of a seam group: Qwen Image 2.1 Turbo at the size of the group's
    texture (N seeds) -> pick (by its detail at the sprites' scales) -> made seamless: "cut"
    (default; local, tile_steps.step_seamless, which also writes the group's colour record from
    its pieces' originals) or "inpaint" (rolled by half, the cross repainted, rolled back: the
    `material` recipe's chain without its 2K repaint)."""
    fid = fam["id"]
    asset = SEAM_PREFIX + gid
    text = material_text(st, fam, seam)
    neg = st["material_negative"]
    kind = "floor" if seam.get("kind") == "floor" else "west_wall"
    texels = (fam.get("tile_texels") or 384)
    span = seam_span(seam)
    G = _grid_module()
    w, h = G.face_texture_size(g, kind, span if kind == "floor" else span[0], texels)
    n = seam.get("variants", 4)
    base = "%s/%s" % (fid, asset)
    jobs, ids = [], []
    for s in seeds_for(base, n):
        jid = "%s/raw_s%d" % (base, s)
        ids.append(jid)
        jobs.append({"id": jid, "kind": "comfy", "group": "qwen_t2i", "workflow": "qwen_image_21_turbo_tile.json",
                     "values": {"$PROMPT$": text, "$NEGATIVE$": neg, "$WIDTH$": w, "$HEIGHT$": h, "$SEED$": s},
                     "roles": {"12": "rgb"}})
    pick = base + "/pick"
    scale = max([(a.get("params") or {}).get("scale", DEFAULT_SCALE) for a in pieces or []] or [DEFAULT_SCALE])
    edge = math.hypot(g["tile"][0] / 2.0, g["tile"][1] / 2.0)  # a tile edge on screen, grid pixels
    jobs.append({"id": pick, "kind": "pick", "group": "local", "from": ids, "role": "rgb", "metric": "tile_material",
                 "metric_args": {"texels_per_tile": texels, "frame_px_per_tile": round(edge * scale, 3),
                                 "map_px_per_tile": round(edge * 2, 3)}})
    if seam.get("seamless", "cut") == "cut":
        members = [a for a in pieces or [] if tile_spec(fam, a)["seam"] == gid]
        info = []
        inputs = {"material": {"ref": pick, "role": "rgb"}}
        for k, a in enumerate(members):
            sp = tile_spec(fam, a)
            info.append({"id": a["id"], "kind": sp["kind"], "thickness": sp["thickness"],
                         "material": "picture" not in stages.chosen_options("iso_tile", a)})
            inputs["sprite_%02d" % k] = {"file": (a.get("inputs") or {}).get("sprite")}
        jobs.append({"id": base + "/seamless", "kind": "tile_seamless", "group": "local", "inputs": inputs,
                     "args": {"axes": "xy" if kind == "floor" else "x", "size": [w, h], "pieces": info,
                              "grid": fam.get("grid") or {}, "saturation": seam.get("saturation", 0.5)}})
        for j in jobs:
            j["family"], j["asset"] = fid, asset
        return jobs
    s0 = seeds_for(base, 1)[0]
    roll = base + "/rolled"
    jobs.append({"id": roll, "kind": "roll", "group": "local", "input": {"ref": pick, "role": "rgb"}, "axes": "xy"})
    paint = base + "/seam"
    jobs.append({"id": paint, "kind": "comfy", "group": "qwen_inpaint", "workflow": "qwen_image_21_turbo_inpaint.json",
                 "values": {"$PROMPT$": text, "$NEGATIVE$": neg, "$SEED$": s0 + 1000,
                            "$IMAGE$": {"ref": roll, "role": "image"}, "$MASK$": {"ref": roll, "role": "mask"}},
                 "roles": {"12": "rgb"}})
    jobs.append({"id": base + "/seamless", "kind": "unroll", "group": "local",
                 "rolled": {"ref": roll, "role": "image"}, "painted": {"ref": paint, "role": "rgb"}, "axes": "xy"})
    for j in jobs:
        j["family"], j["asset"] = fid, asset
    return jobs


def seamless_ref(fam, gid):
    return {"ref": "%s/%s%s/seamless" % (fam["id"], SEAM_PREFIX, gid), "role": "rgb"}


def group_colour_ref(fam, gid):
    """The seam group's colour record (made by the "cut" seamless step), or None."""
    seam = (fam.get("seams") or {}).get(gid) or {}
    if seam.get("seamless", "cut") != "cut":
        return None
    return {"ref": "%s/%s%s/seamless" % (fam["id"], SEAM_PREFIX, gid), "role": "colour"}


def reserve_of(a):
    r = a.get("reserve", 0)
    return r if _is_int(r) and r > 0 else 0


def picture_size(g, spec, texels):
    """The size of an object's picture: its block's canvas with a margin, about 1.2 MP."""
    import math
    G = _grid_module()
    x0, y0, x1, y1 = block_canvas(g, spec)
    w, h = (x1 - x0) * 1.2, (y1 - y0) * 1.2
    f = math.sqrt(1200000.0 / (w * h))
    return [max(64, int(round(w * f / 16.0)) * 16), max(64, int(round(h * f / 16.0)) * 16)]


def block_canvas(g, spec):
    """(x0, y0, x1, y1) grid pixels of the union of the canvases of a piece's variant grid,
    tile (0, 0)'s cell at the origin."""
    G = _grid_module()
    rx, ry, rw, rh = G.canvas(g, spec.get("rect"))
    gx, gy = spec["grid"]
    xs, ys = [], []
    for i in range(gx):
        for j in range(gy):
            ox, oy = G.screen_position(g, (i, j, 0))
            xs += [ox + rx, ox + rx + rw]
            ys += [oy + ry, oy + ry + rh]
    return min(xs), min(ys), max(xs), max(ys)


def recipe_iso_tile(fam, a, st):
    """A sprite of an isometric tile grid, remade from the game's sprite (module docstring)."""
    G = _grid_module()
    fid, aid = fam["id"], a["id"]
    g = G.grid_of(fam.get("grid"))
    spec = tile_spec(fam, a)
    kind = spec["kind"]
    face = kind != "object"
    opts = stages.chosen_options("iso_tile", a)
    fields = {stage: stages.option("iso_tile", stage, o) for stage, o in opts.items()}
    inputs = a.get("inputs") or {}
    sprite = {"file": inputs.get("sprite")}
    params = a.get("params") or {}
    scale = params.get("scale", DEFAULT_SCALE)
    seam = spec["seam"] if face else None
    material = seamless_ref(fam, seam) if seam else None
    texels = (fam.get("tile_texels") or 384)
    band = fam.get("seam_band", DEFAULT_BAND)
    view = a.get("view", fam.get("view", VIEWS.get(kind, "view_tile_object")))
    from .recipes import prompt_of
    text = prompt_of(st, fam, a, view) if "picture" in opts else ""
    size = texture_size(g, spec, texels) if face else picture_size(g, spec, texels)
    base_args = {"tile": spec, "grid": fam.get("grid") or {}, "size": size}
    jobs = []
    # The guide is the piece's, shared by its variants (it has no seed).
    guide_id = "%s/%s/guide" % (fid, aid)
    guide_inputs = {"sprite": sprite}
    if material and "picture" in opts:
        guide_inputs["material"] = dict(material)
    guide = {"id": guide_id, "kind": "tile_guide", "group": "local", "stage": "guide", "inputs": guide_inputs,
             "args": dict(base_args)}
    if "picture" in opts:
        jobs.append(guide)
    n = a.get("variants", fam.get("variants", DEFAULT_VARIANTS)) if "picture" in opts else 1
    final_id = "%s/%s/final" % (fid, aid)
    hold = {"until": final_id, "outcome": acceptance.NO_VARIANT_PASSED}
    variants = []
    extra_n = reserve_of(a) if "picture" in opts else 0
    for i, s in enumerate(seeds_for(fid + "/" + aid, n + extra_n)):
        v = "v%d" % s
        reserve = i >= n
        chain = []

        def add(stage, job, v=v, chain=chain, reserve=reserve):
            job.update({"id": "%s/%s/%s/%s" % (fid, aid, v, stage if "step" not in job else job.pop("step")),
                        "stage": stage, "variant": v})
            if reserve:
                job["hold"] = dict(hold)
            job.setdefault("group", (fields.get(stage) or {}).get("group", "local"))
            jobs.append(job)
            chain.append(job["id"])
            return job["id"]

        sprite_inputs = {"sprite": dict(sprite)}
        if inputs.get("silhouette"):
            sprite_inputs["silhouette"] = {"file": inputs["silhouette"]}
        if "picture" in opts:
            f = fields["picture"]
            pic = add("picture", {"kind": "comfy", "workflow": f["workflow"], "roles": dict(f["roles"]),
                                  "values": dict(EDIT_VALUES, **{"$IMAGE1$": {"ref": guide_id, "role": "guide"},
                                                                 "$PROMPT$": text, "$SEED$": s})})
            texture = {"ref": pic, "role": "rgb"}
            sprite_inputs["mask"] = {"ref": pic, "role": "mask"}
        else:
            texture = dict(material)
        if face:
            if material:
                sprite_inputs["material"] = dict(material)
                colour = group_colour_ref(fam, seam)
                if colour:
                    sprite_inputs["group_colour"] = colour
            if material and "picture" in opts and "edges" in opts:
                blend = add("edges", {"kind": "tile_blend", "group": "local", "step": "blend",
                                      "inputs": {"picture": texture, "material": dict(material)},
                                      "args": {"tile": spec, "band": band}})
                f = fields["edges"]
                ring = add("edges", {"kind": "comfy", "workflow": f["workflow"], "roles": dict(f["roles"]),
                                     "values": {"$PROMPT$": " ".join(x for x in (a["brief"], material_text(
                                         st, fam, (fam.get("seams") or {}).get(seam, {}))) if x),
                                                "$NEGATIVE$": st.get("material_negative", ""), "$SEED$": s + 1000,
                                                "$IMAGE$": {"ref": blend, "role": "image"},
                                                "$MASK$": {"ref": blend, "role": "mask"}}})
                texture = {"ref": ring, "role": "rgb"}
            sprite_inputs["texture"] = texture
        elif opts.get("form") == "mesh":
            f = stages.option("iso_tile", "form", "mesh")
            pic_ref = texture
            cut = add("form", {"kind": "cutout", "group": "local", "step": "cutout",
                               "inputs": {"image": pic_ref, "mask": {"ref": pic_ref["ref"], "role": "mask"}}})
            tris = params.get("tris", 20000)
            tex = params.get("texture", 1024)
            mesh = add("form", {"kind": "comfy", "workflow": "trellis2_mesh.json", "group": "trellis2", "step": "mesh",
                                "roles": {"403": "game_mesh", "405": "raw_mesh"},
                                "values": {"$IMAGE$": {"ref": cut, "role": "rgba"}, "$SEED$": s, "$TRIS$": tris},
                                "drop": list(stages.TRELLIS2_TEXTURE_NODES)})
            paint = add("form", {"kind": "comfy", "workflow": "hunyuan21_paint_mesh.json", "group": "hunyuan_paint",
                                 "heavy": True, "step": "paint",
                                 "roles": {"12": "albedo", "13": "metal_roughness", "14": "paint_views"},
                                 "mesh_role": "painted_mesh",
                                 "values": {"$IMAGE$": {"ref": cut, "role": "rgba"}, "$SEED$": s, "$TEXTURE$": tex,
                                            "$MESH$": {"ref": mesh, "role": "game_mesh", "transport": "local_path"}},
                                 "set_on_failure": {"9.texture_size": min(tex, 1024), "9.view_size": 512}})
            render = add("form", {"kind": "local_gpu", "tool": "tile_render", "group": "local_gpu", "step": "render",
                                  "inputs": {"mesh": {"ref": paint, "role": "painted_mesh"}, "sprite": dict(sprite)},
                                  "args": {"tile": spec, "grid": fam.get("grid") or {}, "scale": scale}})
            sprite_inputs["render"] = {"ref": render, "role": "albedo"}
            sprite_inputs["render_normal"] = {"ref": render, "role": "normal"}
            sprite_inputs["render_fit"] = {"ref": render, "role": "fit"}
            # The picture's own colours are laid on the render where they fit (tile_steps).
            sprite_inputs["texture"] = texture
            sprite_inputs["guide_layout"] = {"ref": guide_id, "role": "layout"}
        else:
            sprite_inputs["texture"] = texture
            sprite_inputs["guide_layout"] = {"ref": guide_id, "role": "layout"}
        args = dict(base_args, scale=scale, band=band if material else None, form=opts.get("form", "picture") if not face else "face",
                    text=" ".join(x for x in (a["brief"], fam.get("style", ""), st.get("global", "")) if x))
        sp = add("sprite", {"kind": "tile_sprite", "group": "local", "inputs": sprite_inputs, "args": args})
        entry = {"variant": v, "job": sp, "role": "tile", "chain": list(chain)}
        if reserve:
            entry["reserve"] = True
        variants.append(entry)
    decision = {"accept": acceptance.limits(a.get("accept"), "iso_tile"), "paint_stage": "picture"}
    main, extra = variants[:n], variants[n:]

    def decide(suffix, covered, held):
        ids = [x["job"] for x in covered]
        review = {"id": "%s/%s/review%s" % (fid, aid, suffix), "kind": "tile_review", "group": "local",
                  "stage": "review", "partial": True, "even_if_none": True, "from": ids, "variants": covered,
                  "inputs": {"sprite": dict(sprite)}, "args": {"tile": spec, "grid": fam.get("grid") or {},
                                                              "scale": scale}}
        review.update(decision)
        final = dict({"id": "%s/%s/final%s" % (fid, aid, suffix), "kind": "final", "group": "local",
                      "partial": True, "even_if_none": True, "from": ids, "variants": covered,
                      "reserve": {"variants": [x["variant"] for x in extra],
                                  "decision": "%s/%s/final_reserve" % (fid, aid) if extra and not held else None}},
                     **decision)
        if held:
            review["hold"], final["hold"] = dict(hold), dict(hold)
        jobs.extend([review, final])
    decide("", main, False)
    if extra:
        decide("_reserve", variants, True)
    return jobs


def family_jobs(fam, st):
    """The family's own jobs when it has iso_tile pieces: the seamless material of every seam
    group a piece uses, and the set sheet over every piece's decision."""
    pieces = [a for a in fam.get("assets", []) if (a.get("recipe") or fam.get("recipe")) == "iso_tile"]
    if not pieces:
        return []
    G = _grid_module()
    g = G.grid_of(fam.get("grid"))
    seams = fam.get("seams") or {}
    used = []
    for a in pieces:
        spec = tile_spec(fam, a)
        if spec["kind"] != "object" and spec["seam"] and spec["seam"] not in used:
            used.append(spec["seam"])
    jobs = []
    for gid in used:
        jobs += seam_jobs(fam, gid, seams[gid], st, g, pieces)
    finals = ["%s/%s/final" % (fam["id"], a["id"]) for a in pieces]
    sheet = {"id": "%s/%s/sheet" % (fam["id"], SET_ASSET), "kind": "tile_sheet", "group": "local",
             "partial": True, "even_if_none": True, "from": finals,
             "pieces": [{"id": a["id"], "final": "%s/%s/final" % (fam["id"], a["id"]), "tile": tile_spec(fam, a),
                         "sprite": {"file": (a.get("inputs") or {}).get("sprite")}} for a in pieces],
             "inputs": {},
             "args": {"grid": fam.get("grid") or {}, "scale": max((a.get("params") or {}).get("scale", DEFAULT_SCALE)
                                                                  for a in pieces)}}
    if fam.get("layout"):
        sheet["inputs"]["layout"] = {"file": fam["layout"]}
    jobs.append(sheet)
    for j in jobs:
        j["family"] = fam["id"]
        j["asset"] = SET_ASSET if j is sheet else j["asset"]
    return jobs


# ---- the order check

def check_family(fam, problems):
    """Problems of a family with iso_tile pieces: its grid, seam groups and layout."""
    fid = fam.get("id")
    G = _grid_module()
    problems.extend("family %s: %s" % (fid, p) for p in G.grid_problems(fam.get("grid")))
    seams = fam.get("seams", {})
    if not isinstance(seams, dict):
        problems.append("family %s: seams must be an object {group: {kind, brief, span}}" % fid)
        seams = {}
    for gid, seam in seams.items():
        where = "family %s: seam group %s" % (fid, gid)
        if not isinstance(gid, str) or not gid or "/" in gid:
            problems.append("%s: a group id is a name without slashes" % where)
        if not isinstance(seam, dict):
            problems.append("%s must be an object" % where)
            continue
        for k in seam:
            if k not in SEAM_KEYS:
                problems.append("%s: unknown key %r (known: %s)" % (where, k, ", ".join(SEAM_KEYS)))
        if seam.get("kind") not in SEAM_KINDS:
            problems.append("%s: kind must be floor or wall" % where)
        if not isinstance(seam.get("brief"), str) or not seam["brief"].strip():
            problems.append("%s: brief is required (the material shared by the group's borders)" % where)
        span = seam.get("span", 1)
        ok = (_is_int(span) and 1 <= span <= 8) or (
            seam.get("kind") == "floor" and isinstance(span, list) and len(span) == 2 and
            all(_is_int(x) and 1 <= x <= 8 for x in span))
        if not ok:
            problems.append("%s: span must be a whole number of tiles from 1 to 8 (a floor group: or [x, y])" % where)
        if "variants" in seam and not (_is_int(seam["variants"]) and seam["variants"] >= 1):
            problems.append("%s: variants must be a whole number of at least 1" % where)
        if "seamless" in seam and seam["seamless"] not in SEAMLESS:
            problems.append("%s: seamless must be one of %s" % (where, ", ".join(SEAMLESS)))
        if "saturation" in seam and not (_num(seam["saturation"]) and 0 <= seam["saturation"] <= 1):
            problems.append("%s: saturation must be a number from 0 to 1 (the share of the way to the originals' "
                            "saturation)" % where)
    band = fam.get("seam_band")
    if band is not None and not (_num(band) and 0.02 <= band <= 0.25):
        problems.append("family %s: seam_band must be a number from 0.02 to 0.25 (a share of a tile)" % fid)
    tt = fam.get("tile_texels")
    if tt is not None and not (_is_int(tt) and 64 <= tt <= 1024):
        problems.append("family %s: tile_texels must be a whole number from 64 to 1024 (texels per tile edge)" % fid)
    layout = fam.get("layout")
    if layout is not None:
        if not isinstance(layout, str) or not os.path.isfile(layout):
            problems.append("family %s: layout not found: %s" % (fid, layout))
        else:
            from . import tile_steps
            ids = [a.get("id") for a in fam.get("assets", []) if isinstance(a, dict)]
            problems.extend("family %s: layout: %s" % (fid, p) for p in tile_steps.layout_problems(layout, ids))
    for a in fam.get("assets", []):
        if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"].startswith("_"):
            problems.append("family %s: piece %s: an iso_tile family's piece ids cannot start with _ (the family's "
                            "own folders)" % (fid, a["id"]))


def check_iso_tile(where, piece, problems, fam=None):
    """The tile object, inputs and stage combinations of an iso_tile piece."""
    fam = fam or {}
    t = piece.get("tile")
    if not isinstance(t, dict):
        problems.append("%s: tile is required: an object with kind (floor, west_wall, north_wall or object)" % where)
        return
    for k in t:
        if k not in TILE_KEYS:
            problems.append("%s: unknown tile key %r (known: %s)" % (where, k, ", ".join(TILE_KEYS)))
    G = _grid_module()
    kind = t.get("kind")
    if kind not in G.KINDS:
        problems.append("%s: tile.kind must be one of %s, not %r" % (where, ", ".join(G.KINDS), kind))
        return
    opts = stages.chosen_options("iso_tile", piece)
    seams = fam.get("seams") if isinstance(fam.get("seams"), dict) else {}
    seam = t.get("seam")
    face = kind != "object"
    if seam is not None:
        if not face:
            problems.append("%s: an object has no seam group (only floors and walls join)" % where)
        elif seam not in seams:
            problems.append("%s: tile.seam %r is no seam group of the family (groups: %s)" % (
                where, seam, ", ".join(sorted(seams)) or "none"))
        elif (seams[seam].get("kind") == "floor") != (kind == "floor"):
            problems.append("%s: tile.seam %r is a %s group; a %s needs a %s group" % (
                where, seam, seams[seam].get("kind"), kind, "floor" if kind == "floor" else "wall"))
    grid = t.get("grid")
    if grid is not None:
        if not (isinstance(grid, list) and len(grid) == 2 and all(_is_int(x) and 1 <= x <= 8 for x in grid)):
            problems.append("%s: tile.grid must be [x, y] whole numbers from 1 to 8" % where)
        elif kind == "west_wall" and grid[0] != 1:
            problems.append("%s: a west wall's frames run along y: tile.grid must be [1, n]" % where)
        elif kind == "north_wall" and grid[1] != 1:
            problems.append("%s: a north wall's frames run along x: tile.grid must be [n, 1]" % where)
    spec = tile_spec(fam, piece)
    if face and seam in seams and grid is not None:
        span = seam_span(seams[seam])
        want = {"floor": span, "west_wall": [1, span[0]], "north_wall": [span[0], 1]}[kind]
        if list(grid) != want:
            problems.append("%s: tile.grid %s differs from its seam group's span (%s): every piece of a group covers "
                            "the group's span, so their borders join" % (where, grid, want))
    for k in ("height", "thickness"):
        v = t.get(k)
        if v is not None and not (_num(v) and (0 < v <= 1 if k == "height" else 0 <= v <= 0.5)):
            problems.append("%s: tile.%s must be a number %s" % (
                where, k, "above 0 up to 1 (a share of one level)" if k == "height" else "from 0 to 0.5 "
                                                                                       "(a share of a tile edge)"))
    rect = t.get("rect")
    if rect is not None and not (isinstance(rect, list) and len(rect) == 4 and all(_num(x) for x in rect) and
                                 rect[2] > 0 and rect[3] > 0):
        problems.append("%s: tile.rect must be [x, y, width, height] in grid pixels" % where)
    if t.get("cutout") is not None and t["cutout"] not in CUTOUTS:
        problems.append("%s: tile.cutout must be one of %s" % (where, ", ".join(CUTOUTS)))
    if t.get("alpha") is not None and t["alpha"] not in ALPHAS:
        problems.append("%s: tile.alpha must be binary or smooth" % where)
    if t.get("name") is not None and not (isinstance(t["name"], str) and t["name"] and "/" not in t["name"]
                                          and "\\" not in t["name"]):
        problems.append("%s: tile.name must be a file name without slashes" % where)
    if t.get("dome") is not None and not (_num(t["dome"]) and 0 <= t["dome"] <= 2):
        problems.append("%s: tile.dome must be a number from 0 to 2" % where)
    if t.get("relief") is not None and not (_num(t["relief"]) and 0 <= t["relief"] <= 4):
        problems.append("%s: tile.relief must be a number from 0 to 4" % where)
    if t.get("clip") is not None:
        if not isinstance(t["clip"], bool):
            problems.append("%s: tile.clip must be true or false" % where)
        elif t["clip"] and (face or opts.get("form") == "mesh"):
            problems.append("%s: tile.clip is for objects made from their picture (form picture)" % where)
    if t.get("ground") is not None:
        if t["ground"] not in GROUNDS:
            problems.append("%s: tile.ground must be keep or cut" % where)
        elif t["ground"] == "cut" and (face or opts.get("form") == "mesh"):
            problems.append("%s: tile.ground cut is for objects made from their picture (form picture)" % where)
    if t.get("glow") is not None:
        from .tool_specs import glow_colours_problem
        p = glow_colours_problem(t["glow"])
        if p:
            problems.append("%s: tile.glow: %s" % (where, p))
    if spec and spec["cutout"] == "picture" and "picture" not in opts:
        problems.append("%s: tile.cutout picture needs the picture stage" % where)
    if "picture" not in opts:
        if not face:
            problems.append("%s: an object cannot skip the picture stage" % where)
        elif not seam:
            problems.append("%s: a %s that skips the picture stage is its seam group's material: it needs tile.seam"
                            % (where, kind))
    if face and opts.get("form", "picture") != "picture":
        problems.append("%s: form %s is for objects" % (where, opts.get("form")))
    if opts.get("form") == "mesh" and spec and spec["grid"] != [1, 1]:
        problems.append("%s: form mesh makes one object: tile.grid must be [1, 1]" % where)
    inputs = piece.get("inputs") or {}
    sprite = inputs.get("sprite")
    if not sprite:
        problems.append("%s: inputs.sprite is required (the game's sprite of the piece: its footprint)" % where)
    elif isinstance(sprite, str) and os.path.isfile(sprite):
        from . import tile_steps
        problems.extend("%s: inputs.sprite: %s" % (where, p) for p in tile_steps.sprite_problems(
            sprite, G.grid_of(fam.get("grid"))))
    scale = (piece.get("params") or {}).get("scale")
    if scale is not None and not (_is_int(scale) and 1 <= scale <= 32):
        problems.append("%s: params.scale must be a whole number from 1 to 32" % where)
