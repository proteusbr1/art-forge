"""Recipes: each piece of the order expands into factory jobs (see RECIPES at the end).
The order is read by forge.order.load_order, which merges the family defaults of the
per-piece keys (variants, stages, skip, params, inputs, review) into each piece.

  figure        FLUX.2 Klein concept, N seeds -> automatic pick -> 3 more views by
                Qwen-Image-Edit 2511 (90, 180, 270 degrees) -> sheet -> Hunyuan3D 2.1
                shape + Paint (PBR) from the picked front view (unrigged)
  vehicle       Klein 3/4 concept, N seeds -> pick -> 4 views (sides, back, top) -> sheet ->
                emissive mask of the lights (Qwen-Image-Edit) -> mesh with a 4096 texture
  object_3d     Klein concept, N seeds -> pick -> Hunyuan3D 2.1 shape + Paint; with
                inputs.reference the supplied RGBA image goes straight to the mesh
  concept       Klein concept, N seeds -> pick (2D only)
  illustration  finished picture (no background removal), picked by detail
  prop          object_3d with the prop view, or concept when the piece has "3d": false
  material      Qwen-Image 2.1 Turbo seamless texture at 1024, N seeds -> pick -> 2K
                repaint -> rolled by half -> seam band repainted (inpaint) -> rolled back
  humanoid      per variant: reference (Qwen Image 2.1 base) -> pose (Qwen Image Edit 2511)
                -> cutout (BiRefNet) -> mesh (TRELLIS.2, reduced; raw mesh kept) -> paint
                (Hunyuan3D Paint) -> rig (SkinTokens) -> export (Blender); then one review
                sheet (Godot) of every variant and a final copy of the chosen one
Raw jobs may also be listed under "tasks" (any workflow of the workflows folder, with values).

N is the piece's "variants" (the inherited "seeds" is still read when variants is absent).
params.tris and params.texture set the mesh's face budget and texture size (the inherited
"faces" and "texture_size" are still read when they are absent).
"""

import os

from . import stages
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
            # The shape before the reduction (the humanoid's bake source) is not used here.
            "drop": list(stages.HUNYUAN21_RAW_NODES),
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
    """A vehicle seen whole: 3/4 concept, side, back, other side and top views, sheet,
    emissive mask of the lights, mesh with a 4096 texture."""
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


def recipe_figure(fam, a, st):
    """A figure without a rig: front concept, three more views, sheet, optional emissive
    mask, mesh."""
    jobs = concept_jobs(fam, a, st, "view_figure", 8, (1024, 1536))
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


# --- humanoid: every variant goes through every stage; review and final see them all

HUMANOID_SIZE = (1024, 1536)
# Qwen Image Edit 2511 with the Lightning LoRA: 4 steps, CFG 1.
EDIT_VALUES = {"$NEGATIVE$": "", "$STEPS$": 4, "$CFG$": 1.0, "$LORA$": 1.0}
REVIEW_DEFAULTS = {"pitch": 60, "directions": 8, "figure_height": 96, "front": True}
EXPORT_PARAMS = ("height", "head", "hands", "feet", "widen")
# The rig's seed unless the order's rig setting "seed" gives another (the lab's SkinTokens
# setting: seed 9001, 10 beams).
RIG_SEED = 9001


def variant_bone_map(given, variant):
    """The bone map file of one variant: inputs.bone_map is a file for every variant, or an
    object {"v<seed>" or "<seed>": file} for some of them (SkinTokens predicts a skeleton per
    variant); a variant it does not name gets the proposal."""
    if isinstance(given, dict):
        return given.get(variant) or given.get(variant[1:])
    return given or None


def humanoid_texts(st, fam, a):
    """(reference prompt, pose edit text, cutout edit text) of a humanoid piece. The pose and
    cutout edits describe what the result shows: style.pose_humanoid and
    style.cutout_humanoid when the order gives them, else style.view_humanoid."""
    view = a.get("view", fam.get("view", "view_humanoid"))
    text = prompt_of(st, fam, a, view)
    return text, st.get("pose_humanoid") or st["view_humanoid"], st.get("cutout_humanoid") or st["view_humanoid"]


def recipe_humanoid(fam, a, st):
    """A skinned figure with a human skeleton. For each seed of seeds_for(family/piece,
    variants) a chain of jobs <family>/<piece>/v<seed>/<stage>: reference -> pose ->
    cutout -> mesh (game_mesh reduced to params.tris, raw_mesh before the reduction) ->
    paint (painted_mesh) -> rig -> export (raw_mesh as the bake source) ; then one review
    sheet of every variant and one final copy of the chosen variant. Stage options and
    skips come from the piece (forge/stages.py HUMANOID_OPTIONS)."""
    fid, aid = fam["id"], a["id"]
    opts = stages.chosen_options("humanoid", a)
    fields = {stage: stages.option("humanoid", stage, o) for stage, o in opts.items()}
    inputs = a.get("inputs") or {}
    tris = param(a, "tris", "faces", stages.HUMANOID_PARAMS["tris"])
    tex = param(a, "texture", "texture_size", stages.HUMANOID_PARAMS["texture"])
    w, h = a.get("size", fam.get("size", HUMANOID_SIZE))
    text, pose_text, cutout_text = humanoid_texts(st, fam, a)
    keep_texture = opts.get("paint", "trellis2_texture") == "trellis2_texture"
    jobs, variants = [], []
    for s in seeds_for(fid + "/" + aid, variants_of(fam, a, 4)):
        v = "v%d" % s
        chain = []

        def add(stage, job):
            job.update({"id": "%s/%s/%s/%s" % (fid, aid, v, stage), "stage": stage, "variant": v})
            job.setdefault("group", fields[stage]["group"])
            if fields[stage].get("ignored"):
                # Arguments the catalogue knows the tool ignores (the provenance says why).
                job["ignored"] = dict(fields[stage]["ignored"])
            jobs.append(job)
            chain.append(job["id"])
            return job["id"]

        def comfy(stage, values, **extra):
            f = fields[stage]
            job = {"kind": "comfy", "workflow": f["workflow"], "values": values, "roles": dict(f["roles"])}
            if f.get("heavy"):
                job["heavy"] = True
            job.update(extra)
            return add(stage, job)

        # reference: a picture of the figure (rgb, and its mask or rgba), or the order's file
        mask = rgba = None
        if opts["reference"] == "input_image":
            image = {"file": inputs.get("reference")}
            rgba = image
        else:
            values = {"$PROMPT$": text, "$SEED$": s}
            if opts["reference"] != "qwen21_turbo":
                values.update({"$WIDTH$": w, "$HEIGHT$": h})
            if opts["reference"] != "klein":
                values["$NEGATIVE$"] = ""
            ref = comfy("reference", values)
            image = {"ref": ref, "role": "rgb"}
            if "mask" in fields["reference"]["outputs"]:
                mask = {"ref": ref, "role": "mask"}
            else:
                rgba = {"ref": ref, "role": "rgba"}
        # pose: Qwen Image Edit corrects the pose of the reference
        if "pose" in opts:
            pose = comfy("pose", dict(EDIT_VALUES, **{"$IMAGE1$": image, "$PROMPT$": pose_text, "$SEED$": s}))
            image, mask, rgba = {"ref": pose, "role": "rgb"}, {"ref": pose, "role": "mask"}, None
        # cutout: the figure on a transparent background (rgba)
        if opts["cutout"] == "birefnet":
            ins = {"image": image, "mask": mask} if mask else {"image": rgba or image}
            cut = add("cutout", {"kind": "cutout", "inputs": ins})
        else:
            cut = comfy("cutout", dict(EDIT_VALUES, **{"$IMAGE1$": image, "$PROMPT$": cutout_text, "$SEED$": s}))
        cut_ref = {"ref": cut, "role": "rgba"}
        # mesh: game_mesh (reduced to params.tris) and raw_mesh (before the reduction)
        if opts["mesh"] == "trellis2":
            roles = {"403": "game_mesh", "405": "raw_mesh"}
            extra = {}
            if keep_texture:
                roles["322"] = "painted_mesh"
                extra["set"] = {"147.texture_size": tex, "196.resolution": tex}
            else:
                extra["drop"] = list(stages.TRELLIS2_TEXTURE_NODES)
            mesh = comfy("mesh", {"$IMAGE$": cut_ref, "$SEED$": s, "$TRIS$": tris}, roles=roles, **extra)
        else:
            mesh = comfy("mesh", {"$IMAGE$": cut_ref, "$SEED$": s, "$FACES$": tris},
                         drop=list(stages.HUNYUAN21_PAINT_NODES))
        last, role = mesh, "painted_mesh"
        # paint: Hunyuan3D Paint over game_mesh (else TRELLIS.2's own texture stays)
        if opts.get("paint") == "hunyuan_paint":
            last = comfy("paint", {"$IMAGE$": cut_ref, "$SEED$": s, "$TEXTURE$": tex,
                                   "$MESH$": {"ref": mesh, "role": "game_mesh", "transport": "local_path"}},
                         mesh_role="painted_mesh",
                         set_on_failure={"9.texture_size": min(tex, 2048), "9.view_size": 512})
        painted = {"ref": last, "role": "painted_mesh"}
        # rig, export: local tools on the card (forge/local_gpu.py RUNNERS)
        if "rig" in opts:
            # The rig seed is the validated setting (9001 with 10 beams), not the variant's
            # seed; the stage's "seed" setting overrides it.
            args = {"method": opts["rig"], "seed": RIG_SEED}
            if opts["rig"] == "skintokens":
                args.update(beams=10, postprocess=False)
            last, role = add("rig", {"kind": "local_gpu", "tool": "rig", "inputs": {"mesh": painted}, "args": args}), "rig"
        if "export" in opts:
            ins = {"rig": {"ref": last, "role": "rig"}, "bake_source": {"ref": mesh, "role": "raw_mesh"}}
            for name, key in (("clips", "file"), ("items", "folder")):
                if inputs.get(name):
                    ins[name] = {key: inputs[name]}
            bone_map = variant_bone_map(inputs.get("bone_map"), v)
            if bone_map:
                ins["bone_map"] = {"file": bone_map}
            args = {"name": aid, "tris": tris, "normal_map": tex}
            args.update({k: v for k, v in (a.get("params") or {}).items() if k in EXPORT_PARAMS})
            last, role = add("export", {"kind": "local_gpu", "tool": "export", "inputs": ins, "args": args}), "glb"
        variants.append({"variant": v, "job": last, "role": role, "chain": list(chain)})
    last_ids = [x["job"] for x in variants]
    # review: one sheet of every variant (failures marked), once none is still running
    review = {"id": "%s/%s/review" % (fid, aid), "kind": "local_gpu", "tool": "review", "stage": "review",
              "group": fields["review"]["group"], "partial": True, "from": last_ids, "variants": variants,
              "args": dict(REVIEW_DEFAULTS, **(a.get("review") or {}))}
    if fields["review"].get("ignored"):
        review["ignored"] = dict(fields["review"]["ignored"])
    jobs.append(review)
    # final: the chosen variant copied to final/ (automatic pick provisional, pending review)
    jobs.append({"id": "%s/%s/final" % (fid, aid), "kind": "final", "group": "local", "partial": True,
                 "from": last_ids, "variants": variants})
    return jobs


def _alpha_problem(path):
    """Why a picture cannot be its own cutout (no transparent background), or None."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            if "A" not in im.getbands() and "transparency" not in im.info:
                return "has no alpha channel"
            lo, _ = im.convert("RGBA").getchannel("A").getextrema()
            return None if lo < 255 else "is fully opaque"
    except Exception as ex:
        return "cannot be read (%s)" % ex


def check_humanoid(where, piece, problems):
    """The stage combinations and inputs a humanoid piece needs."""
    opts = stages.chosen_options("humanoid", piece)
    inputs = piece.get("inputs") or {}
    params = piece.get("params") or {}
    if opts["mesh"] != "trellis2" and opts.get("paint", "trellis2_texture") == "trellis2_texture":
        problems.append("%s: %s keeps TRELLIS.2's own texture, which needs mesh trellis2 (mesh %s has none: "
                        "use paint hunyuan_paint)" % (
                            where, "skipping paint" if "paint" not in opts else "paint trellis2_texture",
                            opts["mesh"]))
    if "rig" not in opts and "export" in opts:
        problems.append("%s: export needs the rig: skip export too (a statue: the sheet shows the painted mesh)" % where)
    if opts["reference"] == "input_image":
        path = inputs.get("reference")
        if not path:
            problems.append("%s: reference input_image needs inputs.reference (a picture of the figure)" % where)
        elif "pose" not in opts and opts["cutout"] == "birefnet" and os.path.isfile(path):
            why = _alpha_problem(path)
            if why:
                problems.append("%s: inputs.reference %s: with pose skipped and cutout birefnet it must have a "
                                "transparent background (or use cutout qwen_edit_character)" % (where, why))
    bone_map = inputs.get("bone_map")
    if isinstance(bone_map, dict):
        names = ["v%d" % s for s in seeds_for(where, variants_of({}, piece, 4))]
        for key in bone_map:
            if key not in names and "v" + str(key) not in names:
                problems.append("%s: inputs.bone_map names %r, which is no variant of this piece (variants: %s)" % (
                    where, key, ", ".join(names)))
    tex = params.get("texture")
    if isinstance(tex, int) and not isinstance(tex, bool):
        if opts.get("paint") == "hunyuan_paint" and not (512 <= tex <= 4096 and tex % 512 == 0):
            problems.append("%s: params.texture %d: Hunyuan3D Paint takes 512 to 4096, a multiple of 512" % (where, tex))
        elif opts["mesh"] == "trellis2" and opts.get("paint", "trellis2_texture") == "trellis2_texture" \
                and not (256 <= tex <= 8192 and tex % 256 == 0):
            problems.append("%s: params.texture %d: TRELLIS.2's bake takes 256 to 8192, a multiple of 256" % (where, tex))


RECIPES = {"vehicle": recipe_vehicle, "illustration": recipe_illustration, "figure": recipe_figure,
           "object_3d": recipe_object_3d, "concept": recipe_concept, "prop": recipe_prop,
           "material": recipe_material, "humanoid": recipe_humanoid}
# Recipe-specific checks of a piece, called by forge.order.check_order with the piece's
# valid stages and skips: check(where, piece, problems).
PIECE_CHECKS = {"humanoid": check_humanoid}


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
