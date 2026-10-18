"""Recipes: each piece of the order expands into factory jobs (see RECIPES at the end).
The order is read by forge.order.load_order, which merges the family defaults of the
per-piece keys (variants, stages, skip, params, inputs, review) into each piece.

  unit          FLUX.2 Klein concept, N seeds -> automatic pick -> 3 more views by
                Qwen-Image-Edit 2511 (90, 180, 270 degrees) -> sheet -> Hunyuan3D 2.1
                shape + Paint (PBR) from the picked front view
  vehicle       Klein 3/4 concept, N seeds -> pick -> 4 views (sides, back, top) -> sheet ->
                emissive mask of the lights (Qwen-Image-Edit) -> mesh with a 4096 texture
  object_3d     Klein concept, N seeds -> pick -> Hunyuan3D 2.1 shape + Paint; with
                inputs.reference the supplied RGBA image goes straight to the mesh
  concept       Klein concept, N seeds -> pick (2D only)
  illustration  finished picture (no background removal), picked by detail
  prop          object_3d with the prop view, or concept when the piece has "3d": false
  material      Qwen-Image 2.1 Turbo seamless texture at 1024, N seeds -> pick -> 2K
                repaint -> rolled by half -> seam band repainted (inpaint) -> rolled back
Raw jobs may also be listed under "tasks" (any workflow of the workflows folder, with values).

N is the piece's "variants" (the inherited "seeds" is still read when variants is absent).
params.tris and params.texture set the mesh's face budget and texture size (the inherited
"faces" and "texture_size" are still read when they are absent).
"""

from .factory_util import seeds_for
from .stages import apply_settings

SIGMAS_HIRES = "0.704534, 0.414568, 0"


def variants_of(fam, a, default, family_key="seeds"):
    """Number of seeds of a piece: "variants" (merged from the family by load_order), else the
    inherited "seeds" of the piece or family, else the recipe's default."""
    if "variants" in a:
        return a["variants"]
    if "variants" in fam:
        return fam["variants"]
    return a.get("seeds", fam.get(family_key, default))


def param(a, name, inherited, default):
    """A piece param: params[name], else the inherited per-piece key, else the default."""
    p = a.get("params") or {}
    if name in p:
        return p[name]
    return a.get(inherited, default)


def prompt_of(st, fam, a, view):
    # "tier": a level of the piece (for example a technology level) whose style text decides
    # its materials and look; the family style adds only context.
    tier = st["tier_" + a["tier"]] if a.get("tier") else ""
    parts = [a["brief"], tier, fam.get("style", ""), st.get("global", ""), st[view]]
    return " ".join(p.strip() for p in parts if p and p.strip())


def concept_jobs(fam, a, st, view, default_seeds, default_size):
    fid, aid = fam["id"], a["id"]
    n = variants_of(fam, a, default_seeds)
    w, h = a.get("size", fam.get("size", default_size))
    view = a.get("view", fam.get("view", view))
    text = prompt_of(st, fam, a, view)
    jobs, ids = [], []
    for s in seeds_for(fid + "/" + aid, n):
        jid = "%s/%s/concept_s%d" % (fid, aid, s)
        ids.append(jid)
        jobs.append({"id": jid, "kind": "comfy", "group": "klein", "workflow": "klein_t2i.json",
                     "values": {"$PROMPT$": text, "$WIDTH$": w, "$HEIGHT$": h, "$SEED$": s},
                     "roles": {"13": "rgb", "18": "rgba"}})
    jobs.append({"id": "%s/%s/pick" % (fid, aid), "kind": "pick", "group": "local", "from": ids,
                 "role": "rgba", "metric": "object"})
    return jobs


def hy3d_job(fam, a, pick_id, faces, texture=2048, view=768):
    """Hunyuan3D 2.1 shape + Paint from the picked concept (pick_id) or, when pick_id is None,
    from the piece's inputs.reference image. A retry after a failure uses a 2048 texture and
    512 views (the Paint stage is what runs out of memory)."""
    fid, aid = fam["id"], a["id"]
    image = {"ref": pick_id, "role": "rgba"} if pick_id else {"file": a["inputs"]["reference"]}
    return {"id": "%s/%s/mesh" % (fid, aid), "kind": "comfy", "group": "hy3d", "heavy": True,
            "workflow": "hunyuan21_shape_paint.json",
            "values": {"$IMAGE$": image, "$SEED$": seeds_for(fid + aid, 1)[0],
                       "$FACES$": param(a, "tris", "faces", faces)},
            "set": {"9.texture_size": param(a, "texture", "texture_size", texture),
                    "9.view_size": a.get("paint_view_size", view)},
            "set_on_failure": {"9.texture_size": 2048, "9.view_size": 512},
            "roles": {"6": "shape", "12": "albedo", "13": "metal_roughness", "14": "paint_views"},
            "mesh_role": "painted_mesh"}


def emissive_job(fam, a, pick_id, st):
    fid, aid = fam["id"], a["id"]
    return {"id": "%s/%s/emissive" % (fid, aid), "kind": "comfy", "group": "qwen_edit", "workflow": "qwen_edit_2511.json",
            "values": {"$IMAGE1$": {"ref": pick_id, "role": "rgb"}, "$PROMPT$": st["emissive"], "$NEGATIVE$": "",
                       "$SEED$": seeds_for(fid + aid, 1)[0] + 7, "$STEPS$": 4, "$CFG$": 1.0, "$LORA$": 1.0},
            "roles": {"16": "rgb", "20": "background_mask"}}


def view_job(jid, pick, sets):
    return {"id": jid, "kind": "comfy", "group": "qwen_edit", "workflow": "qwen_edit_view_angles.json",
            "images": {"1": {"ref": pick, "role": "rgb"}}, "set": sets,
            "roles": {"18": "rgb", "23": "rgba"}}


def recipe_concept(fam, a, st):
    return concept_jobs(fam, a, st, "view_object", 6, (1536, 1024))


def has_reference(a):
    return bool((a.get("inputs") or {}).get("reference"))


def recipe_object_3d(fam, a, st):
    if has_reference(a):
        return [hy3d_job(fam, a, None, 40000)]
    jobs = concept_jobs(fam, a, st, "view_object", 6, (1536, 1024))
    return jobs + [hy3d_job(fam, a, jobs[-1]["id"], 40000)]


def recipe_prop(fam, a, st):
    if has_reference(a) and a.get("3d", fam.get("3d", True)):
        return [hy3d_job(fam, a, None, 30000)]
    jobs = concept_jobs(fam, a, st, "view_prop", 4, (1024, 1024))
    if a.get("3d", fam.get("3d", True)):
        jobs.append(hy3d_job(fam, a, jobs[-1]["id"], 30000))
    return jobs


def recipe_illustration(fam, a, st):
    """A finished illustration (no background removal); picked by detail."""
    jobs = concept_jobs(fam, a, st, "view_illustration", 6, (1536, 1024))
    jobs[-1]["role"], jobs[-1]["metric"] = "rgb", "image"
    return jobs


def recipe_vehicle(fam, a, st):
    """Craft seen large (world map, air combat): 3/4 concept, side, back, other side and top
    views, sheet, emissive mask of the lights, mesh with a 4096 texture."""
    jobs = concept_jobs(fam, a, st, "view_vehicle", 8, (1536, 1024))
    pick = jobs[-1]["id"]
    fid, aid = fam["id"], a["id"]
    sheet = [{"ref": pick, "role": "rgba"}]
    for name, h, v in (("side", 90, 30), ("back", 180, 30), ("other_side", 270, 30), ("top", 0, 60)):
        jid = "%s/%s/view_%s" % (fid, aid, name)
        jobs.append(view_job(jid, pick, {"10.horizontal_angle": h, "10.vertical_angle": v,
                                         "16.seed": seeds_for(fid + aid, 1)[0] + h + v}))
        sheet.append({"ref": jid, "role": "rgba"})
    jobs.append({"id": "%s/%s/sheet" % (fid, aid), "kind": "sheet", "group": "local", "inputs": sheet})
    jobs.append(emissive_job(fam, a, pick, st))
    jobs.append(hy3d_job(fam, a, pick, 120000, texture=4096, view=768))
    return jobs


def recipe_unit(fam, a, st):
    jobs = concept_jobs(fam, a, st, "view_unit", 8, (1024, 1536))
    pick = jobs[-1]["id"]
    fid, aid = fam["id"], a["id"]
    sheet = [{"ref": pick, "role": "rgba"}]
    for ang in (90, 180, 270):
        jid = "%s/%s/view_%d" % (fid, aid, ang)
        jobs.append(view_job(jid, pick, {"10.horizontal_angle": ang, "16.seed": seeds_for(fid + aid, 1)[0] + ang}))
        sheet.append({"ref": jid, "role": "rgba"})
    jobs.append({"id": "%s/%s/sheet" % (fid, aid), "kind": "sheet", "group": "local", "inputs": sheet})
    if a.get("emissive"):
        jobs.append(emissive_job(fam, a, pick, st))
    jobs.append(hy3d_job(fam, a, pick, 60000))
    return jobs


def recipe_material(fam, a, st):
    fid, aid = fam["id"], a["id"]
    view = {"front": "material_front", "top": "material_top", None: "material_top"}.get(a.get("view"), a.get("view"))
    text = " ".join(p for p in (st[view], a["brief"], fam.get("material_style", ""), st.get("material_look", "")) if p)
    neg = st["material_negative"]
    n = variants_of(fam, a, 4, "material_seeds")
    ids = []
    jobs = []
    for s in seeds_for(fid + "/" + aid, n):
        jid = "%s/%s/raw_s%d" % (fid, aid, s)
        ids.append(jid)
        jobs.append({"id": jid, "kind": "comfy", "group": "qwen_t2i", "workflow": "qwen_image_21_turbo_tile.json",
                     "values": {"$PROMPT$": text, "$NEGATIVE$": neg, "$WIDTH$": 1024, "$HEIGHT$": 1024, "$SEED$": s},
                     "roles": {"12": "rgb"}})
    pick = "%s/%s/pick" % (fid, aid)
    jobs.append({"id": pick, "kind": "pick", "group": "local", "from": ids, "role": "rgb", "metric": "material"})
    hires = "%s/%s/2k" % (fid, aid)
    s0 = seeds_for(fid + "/" + aid, 1)[0]
    jobs.append({"id": hires, "kind": "comfy", "group": "qwen_hires", "workflow": "qwen_image_21_turbo_hires.json",
                 "values": {"$PROMPT$": text, "$NEGATIVE$": neg, "$IMAGE$": {"ref": pick, "role": "rgb"},
                            "$WIDTH$": 2048, "$HEIGHT$": 2048, "$SIGMAS$": SIGMAS_HIRES, "$SEED$": s0 + 500},
                 "roles": {"12": "rgb"}})
    axes = "x" if (a.get("view") == "front" and not a.get("repeat_vertical")) or a.get("horizontal_only") else "xy"
    roll = "%s/%s/rolled" % (fid, aid)
    jobs.append({"id": roll, "kind": "roll", "group": "local", "input": {"ref": hires, "role": "rgb"}, "axes": axes})
    paint = "%s/%s/seam" % (fid, aid)
    jobs.append({"id": paint, "kind": "comfy", "group": "qwen_inpaint", "workflow": "qwen_image_21_turbo_inpaint.json",
                 "values": {"$PROMPT$": text, "$NEGATIVE$": neg, "$SEED$": s0 + 1000,
                            "$IMAGE$": {"ref": roll, "role": "image"}, "$MASK$": {"ref": roll, "role": "mask"}},
                 "roles": {"12": "rgb"}})
    jobs.append({"id": "%s/%s/seamless" % (fid, aid), "kind": "unroll", "group": "local",
                 "rolled": {"ref": roll, "role": "image"}, "painted": {"ref": paint, "role": "rgb"}, "axes": axes})
    return jobs


RECIPES = {"vehicle": recipe_vehicle, "illustration": recipe_illustration, "unit": recipe_unit,
           "object_3d": recipe_object_3d, "concept": recipe_concept, "prop": recipe_prop,
           "material": recipe_material}


def expand(job_list, only=None):
    st = job_list.get("style", {})
    jobs = []
    for fam in job_list["families"]:
        if only and fam["id"] not in only:
            continue
        for a in fam["assets"]:
            recipe = a.get("recipe", fam.get("recipe"))
            # The piece's stage settings land in its jobs ("set" or "args"): see forge/stages.py.
            for j in apply_settings(recipe, a, RECIPES[recipe](fam, a, st)):
                j["family"], j["asset"] = fam["id"], a["id"]
                jobs.append(j)
    for t in job_list.get("tasks", []):
        fam = t["id"].split("/")[0]
        if only and fam not in only:
            continue
        j = dict(t)
        j.setdefault("kind", "comfy")
        j.setdefault("group", "other")
        j.setdefault("family", fam)
        j.setdefault("asset", t["id"].split("/")[1] if t["id"].count("/") >= 2 else "general")
        jobs.append(j)
    seen = set()
    for j in jobs:
        if j["id"] in seen:
            raise ValueError("duplicate job id " + j["id"])
        seen.add(j["id"])
    return jobs


def refs_of(job):
    out = []

    def walk(v):
        if isinstance(v, dict):
            if "ref" in v:
                out.append(v["ref"])
            else:
                for x in v.values():
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for key in ("values", "images", "inputs", "input", "rolled", "painted"):
        if key in job:
            walk(job[key])
    out += job.get("from", []) + job.get("after", [])
    return out
