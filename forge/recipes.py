"""Recipes: each piece of the order expands into factory jobs (see RECIPES at the end).
The order is read by forge.order.load_order, which merges the family defaults of the
per-piece keys (variants, stages, skip, params, inputs, review) into each piece.

  figure        FLUX.2 Klein concept, N seeds -> automatic pick -> 3 more views by
                Qwen-Image-Edit 2511 (90, 180, 270 degrees) -> sheet -> Hunyuan3D 2.1
                shape + Paint (PBR) from the picked front view (unrigged) -> normals
  vehicle       Klein 3/4 concept, N seeds -> pick -> 4 views (sides, back, top) -> sheet ->
                emissive mask of the lights (Qwen-Image-Edit) -> mesh with a 4096 texture
                -> normals
  object_3d     Klein concept, N seeds -> pick -> Hunyuan3D 2.1 shape + Paint -> normals;
                with inputs.reference the supplied RGBA image goes straight to the mesh
                (normals: the painted mesh with smooth normals, edges over 60 degrees hard,
                tools/blender/glb_normals.py; the paint stage writes none)
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

from . import acceptance, stages, tool_specs
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


def normals_job(fam, a):
    """The painted mesh again with smooth normals (the paint stage's GLB has none, so viewers
    shade it flat): a local step (tools/blender/glb_normals.py smooth_glb, edges over 60
    degrees hard), output role painted_mesh."""
    fid, aid = fam["id"], a["id"]
    return {"id": "%s/%s/normals" % (fid, aid), "kind": "normals", "group": "local",
            "input": {"ref": "%s/%s/mesh" % (fid, aid), "role": "painted_mesh"}}


def mesh_and_paint(recipe, fam, a, mesh_job):
    """The mesh job of a one-mesh recipe (object_3d, prop, vehicle) and its paint: by default
    Hunyuan3D Paint inside it; with the piece's paint option hunyuan_concept the mesh job keeps
    its shape only and the paint stage's three jobs (paint_jobs) paint it. Then the normals step
    over the painted mesh."""
    fid, aid = fam["id"], a["id"]
    opts = stages.chosen_options(recipe, a)
    if opts.get("paint") != "hunyuan_concept":
        return [mesh_job, normals_job(fam, a)]
    tex = mesh_job.get("set", {}).get("9.texture_size", 2048)
    mesh_job["drop"] = list(mesh_job.get("drop", [])) + list(stages.HUNYUAN21_PAINT_NODES)
    mesh_job["roles"] = {"6": "shape"}
    mesh_job.pop("mesh_role", None)
    for key in ("set", "set_on_failure"):
        mesh_job[key] = {k: v for k, v in mesh_job.get(key, {}).items() if not k.startswith("9.")}
    jobs = [mesh_job]
    fields = {"paint": stages.option(recipe, "paint", "hunyuan_concept")}

    def add(stage, job, name=None):
        job.update({"id": "%s/%s/%s" % (fid, aid, name or stage), "stage": stage})
        job.setdefault("group", fields[stage]["group"])
        jobs.append(job)
        return job["id"]
    last = paint_jobs(add, None, opts, fields, mesh_job["values"]["$IMAGE$"], {"ref": mesh_job["id"], "role": "shape"},
                      mesh_job["values"]["$SEED$"], tex)
    normals = normals_job(fam, a)
    normals["input"] = {"ref": last, "role": "painted_mesh"}
    return jobs + [normals]


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
        return mesh_and_paint("object_3d", fam, a, hy3d_job(fam, a, None, 40000))
    jobs = concept_jobs(fam, a, st, "view_object", 6, (1536, 1024))
    return jobs + mesh_and_paint("object_3d", fam, a, hy3d_job(fam, a, jobs[-1]["id"], 40000))


def recipe_prop(fam, a, st):
    if has_reference(a) and a.get("3d", fam.get("3d", True)):
        return mesh_and_paint("prop", fam, a, hy3d_job(fam, a, None, 30000))
    jobs = concept_jobs(fam, a, st, "view_prop", 4, (1024, 1024))
    if a.get("3d", fam.get("3d", True)):
        jobs.extend(mesh_and_paint("prop", fam, a, hy3d_job(fam, a, jobs[-1]["id"], 30000)))
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
    jobs.extend(mesh_and_paint("vehicle", fam, a, hy3d_job(fam, a, pick, 120000, texture=4096, view=768)))
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
    jobs.append(normals_job(fam, a))
    return jobs


def recipe_material(fam, a, st):
    fid, aid = fam["id"], a["id"]
    view = {"front": "material_front", "top": "material_top", None: "material_top"}.get(a.get("view"), a.get("view"))
    text = " ".join(p for p in (st[view], a["brief"], fam.get("material_style", ""), st.get("material_look", "")) if p)
    neg = st["material_negative"]
    n = variants_of(fam, a, 4, "material_seeds")
    texture = stages.chosen_options("material", a)["texture"]
    ids = []
    jobs = []
    for s in seeds_for(fid + "/" + aid, n):
        jid = "%s/%s/raw_s%d" % (fid, aid, s)
        ids.append(jid)
        if texture == "klein4b":
            # FLUX.2 Klein 4B: no negative text; the background removal branch is dropped.
            jobs.append({"id": jid, "kind": "comfy", "group": "klein4b", "workflow": "klein4b_t2i.json",
                         "values": {"$PROMPT$": text, "$WIDTH$": 1024, "$HEIGHT$": 1024, "$SEED$": s},
                         "drop": list(stages.KLEIN_RGBA_NODES), "roles": {"13": "rgb"}})
            continue
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
# The params an approved body sets (inputs.body): the order check refuses them on its pieces.
BODY_PARAMS = ("height", "head", "hands", "feet", "widen")
# Qwen Image Edit 2511 with the Lightning LoRA: 4 steps, CFG 1.
EDIT_VALUES = {"$NEGATIVE$": "", "$STEPS$": 4, "$CFG$": 1.0, "$LORA$": 1.0}
REVIEW_DEFAULTS = {"pitch": 60, "directions": 8, "figure_height": 96, "front": True}
EXPORT_PARAMS = ("height", "head", "hands", "feet", "widen", "image_format")
# The rig's seed unless the order's rig setting "seed" gives another (the lab's SkinTokens
# setting: seed 9001, 10 beams).
RIG_SEED = 9001


def body_model_path(body):
    """The model.json of an approved body GLB (next to it)."""
    return os.path.join(os.path.dirname(body), "model.json")


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


# The paint options whose last job is a Hunyuan3D Paint bake (painted_mesh, albedo, metal_roughness).
HUNYUAN_PAINTS = ("hunyuan_paint", "hunyuan_concept")


def paint_jobs(add, comfy, opts, fields, cut_ref, mesh, s, tex):
    """The paint stage's jobs over `mesh` (a reference to the mesh to paint) from the cutout
    `cut_ref`; returns the id of the job whose painted_mesh the next stages take, or None when no
    paint job runs (paint skipped, or TRELLIS.2's own texture).

    hunyuan_paint: one job (Hunyuan3D Paint's views and bake). hunyuan_concept: <variant>/paint_views
    (the six views with their metal-roughness, position and normal maps; takes the Paint settings),
    <variant>/paint_concept (forge/paint_views.py: the cutout laid on the views; takes
    bake_view_size and min_fit) and <variant>/paint (the bake of those views, painted_mesh)."""
    option = opts.get("paint")
    local_mesh = dict(mesh, transport="local_path")
    if option == "hunyuan_paint":
        return comfy("paint", {"$IMAGE$": cut_ref, "$SEED$": s, "$TEXTURE$": tex, "$MESH$": local_mesh},
                     mesh_role="painted_mesh",
                     set_on_failure={"9.texture_size": min(tex, 2048), "9.view_size": 512})
    if option != "hunyuan_concept":
        return None
    f = fields["paint"]
    views = add("paint", {"kind": "comfy", "workflow": f["workflow"], "heavy": True, "roles": dict(f["roles"]),
                          "values": {"$IMAGE$": cut_ref, "$SEED$": s, "$MESH$": local_mesh},
                          "set_on_failure": {"9.view_size": 512}, "takes": ("set",)}, name="paint_views")

    def nth(role, i):
        return {"ref": views, "role": role if i == 0 else "%s_%d" % (role, i)}
    ins = {"cutout": dict(cut_ref)}
    for i in range(6):
        ins.update({"view_%d" % i: nth("views", i), "mr_%d" % i: nth("mr_views", i),
                    "position_%d" % i: nth("positions", i), "normal_%d" % i: nth("normals", i)})
    concept = add("paint", {"kind": "paint_views", "group": "local", "inputs": ins, "args": {}, "takes": ("args",)},
                  name="paint_concept")
    values = {"$MESH$": local_mesh, "$TEXTURE$": tex}
    for i in range(6):
        values["$ALBEDO%d$" % i] = {"ref": concept, "role": "albedo_%d" % i}
        values["$MR%d$" % i] = {"ref": concept, "role": "mr_%d" % i}
    return add("paint", {"kind": "comfy", "workflow": f["bake_workflow"], "group": f["bake_group"], "values": values,
                         "roles": dict(f["bake_roles"]), "mesh_role": "painted_mesh", "takes": ()})


def recipe_humanoid(fam, a, st):
    """A skinned figure with a human skeleton. For each seed of seeds_for(family/piece,
    variants) a chain of jobs <family>/<piece>/v<seed>/<stage>: reference -> pose ->
    cutout -> mesh (game_mesh reduced to params.tris, raw_mesh before the reduction) ->
    paint (painted_mesh) -> rig -> export (raw_mesh as the bake source, the painted mesh as
    the material source and the paint stage's metal_roughness) ; then one review sheet of
    every variant and one final decision (forge/acceptance.py: the best acceptable variant,
    else "no_variant_passed"). Stage options and skips come from the piece
    (forge/stages.py HUMANOID_OPTIONS).

    "reserve" (default 0) more variants, with the next seeds, are expanded with every job
    held (key "hold") until the first final outcome is "no_variant_passed"; then they run
    and a second review (review_reserve) and decision (final_reserve) cover every variant."""
    fid, aid = fam["id"], a["id"]
    opts = stages.chosen_options("humanoid", a)
    fields = {stage: stages.option("humanoid", stage, o) for stage, o in opts.items()}
    inputs = a.get("inputs") or {}
    tris = param(a, "tris", "faces", stages.HUMANOID_PARAMS["tris"])
    tex = param(a, "texture", "texture_size", stages.HUMANOID_PARAMS["texture"])
    w, h = a.get("size", fam.get("size", HUMANOID_SIZE))
    text, pose_text, cutout_text = humanoid_texts(st, fam, a)
    glow_colours = tool_specs.glow_colours_in_text(text)
    keep_texture = opts.get("paint", "trellis2_texture") == "trellis2_texture"
    body = inputs.get("body") if "export" in opts else None
    jobs, variants = [], []
    motions_job = None
    if isinstance(a.get("motions"), list) and "export" in opts:
        from .motion_jobs import piece_motions_job
        motions_job = piece_motions_job(fam, a, inputs)
        jobs.append(motions_job)
    n = variants_of(fam, a, 4)
    final_id = "%s/%s/final" % (fid, aid)
    hold = {"until": final_id, "outcome": acceptance.NO_VARIANT_PASSED}
    for i, s in enumerate(seeds_for(fid + "/" + aid, n + reserve_of(a))):
        v = "v%d" % s
        chain = []
        reserve = i >= n

        def add(stage, job, name=None):
            job.update({"id": "%s/%s/%s/%s" % (fid, aid, v, name or stage), "stage": stage, "variant": v})
            if reserve:
                job["hold"] = dict(hold)
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
        last = paint_jobs(add, comfy, opts, fields, cut_ref, {"ref": mesh, "role": "game_mesh"}, s, tex) or last
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
            # The painted mesh (the rig's input) is the material source; Hunyuan3D Paint's own
            # metallic-roughness map goes in losslessly.
            ins = {"rig": {"ref": last, "role": "rig"}, "bake_source": {"ref": mesh, "role": "raw_mesh"},
                   "material_source": dict(painted)}
            if opts.get("paint") in HUNYUAN_PAINTS:
                ins["metal_roughness"] = {"ref": painted["ref"], "role": "metal_roughness"}
            for name, key in (("clips", "file"), ("items", "folder")):
                if inputs.get(name):
                    ins[name] = {key: inputs[name]}
            if motions_job is not None:
                # The motions list: the piece's own motion library (forge/motion_jobs.py).
                ins["clips"] = {"ref": motions_job["id"], "role": "library"}
            bone_map = variant_bone_map(inputs.get("bone_map"), v)
            if bone_map:
                ins["bone_map"] = {"file": bone_map}
            args = {"name": aid, "tris": tris, "normal_map": tex}
            args.update({k: v for k, v in (a.get("params") or {}).items() if k in EXPORT_PARAMS})
            # What glows: the colours the piece's text names as glowing, weighed by the cutout
            # the paint stage projected (forge/local_gpu.py job_glow).
            if glow_colours:
                args["glow_colours"] = glow_colours
                ins["reference"] = dict(cut_ref)
            tool = "export"
            if body:
                # The approved body: the variant conformed to it and exported on its skeleton
                # (forge/local_gpu.py run_body_export); the body sets the height and proportions.
                tool = "body_export"
                ins["body"], ins["body_model"] = {"file": body}, {"file": body_model_path(body)}
                for k in BODY_PARAMS:
                    args.pop(k, None)
            last, role = add("export", {"kind": "local_gpu", "tool": tool, "inputs": ins, "args": args}), "glb"
        entry = {"variant": v, "job": last, "role": role, "chain": list(chain)}
        if reserve:
            entry["reserve"] = True
        variants.append(entry)
    # The decisions' limits (the order's accept, else the defaults) and the stage held
    # responsible for the painted maps (the mesh stage's TRELLIS.2 bake when paint is skipped).
    decision = {"accept": acceptance.limits(a.get("accept"), "humanoid_body") if body else acceptance.limits(
                    a.get("accept")),
                "paint_stage": "paint" if "paint" in opts else "mesh"}
    main, extra = variants[:n], variants[n:]

    def decide(suffix, covered, held):
        # review: one sheet of the variants (failures marked), once none is still running;
        # final: the best acceptable variant copied to final/ (pending review), else none.
        ids = [x["job"] for x in covered]
        review = {"id": "%s/%s/review%s" % (fid, aid, suffix), "kind": "local_gpu", "tool": "review",
                  "stage": "review", "group": fields["review"]["group"], "partial": True, "even_if_none": True,
                  "from": ids, "variants": covered, "args": dict(REVIEW_DEFAULTS, **(a.get("review") or {}))}
        review.update(decision)
        if fields["review"].get("ignored"):
            review["ignored"] = dict(fields["review"]["ignored"])
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


def reserve_of(a):
    """Reserve variants of a piece (merged from the family by load_order; default 0)."""
    r = a.get("reserve", 0)
    return r if isinstance(r, int) and not isinstance(r, bool) and r > 0 else 0


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
        names = ["v%d" % s for s in seeds_for(where, variants_of({}, piece, 4) + reserve_of(piece))]
        for key in bone_map:
            if key not in names and "v" + str(key) not in names:
                problems.append("%s: inputs.bone_map names %r, which is no variant of this piece (variants: %s)" % (
                    where, key, ", ".join(names)))
    tex = params.get("texture")
    if isinstance(tex, int) and not isinstance(tex, bool):
        if opts.get("paint") in HUNYUAN_PAINTS and not (512 <= tex <= 4096 and tex % 512 == 0):
            problems.append("%s: params.texture %d: Hunyuan3D Paint takes 512 to 4096, a multiple of 512" % (where, tex))
        elif opts["mesh"] == "trellis2" and opts.get("paint", "trellis2_texture") == "trellis2_texture" \
                and not (256 <= tex <= 8192 and tex % 256 == 0):
            problems.append("%s: params.texture %d: TRELLIS.2's bake takes 256 to 8192, a multiple of 256" % (where, tex))
    if "motions" in piece:
        check_humanoid_motions(where, piece, opts, problems)
    check_humanoid_body(where, piece, opts, problems)


def check_humanoid_body(where, piece, opts, problems):
    """An approved body (inputs.body): a humanoid exporter's GLB with its model.json, not the
    piece's own output; the body sets the height and proportions; the conform settings and
    limits need it."""
    from . import body as bodymod
    inputs = piece.get("inputs") or {}
    params = piece.get("params") or {}
    body = inputs.get("body")
    settings = stages.entry_settings((piece.get("stages") or {}).get("export"))
    if not body:
        given = sorted(k for k in settings if k in stages.CONFORM_SETTINGS)
        if given:
            problems.append("%s: export settings %s are the conform's: they need inputs.body (an approved body)" % (
                where, ", ".join(given)))
        return
    if not isinstance(body, str) or "export" not in opts:
        return  # the generic input check names it
    fam, pid = where.split("/", 1) if "/" in where else ("", where)
    if bodymod.wears_own_output(body, fam, pid):
        problems.append("%s: inputs.body is this piece's own output (%s): a piece cannot wear its own pick (set "
                        "\"body\": null in the piece's inputs to drop a family's body)" % (where, body))
    for k in BODY_PARAMS:
        if k in params:
            problems.append("%s: params.%s: the approved body (inputs.body) sets the height and proportions; "
                            "remove it" % (where, k))
    problems.extend(bodymod.check_body(body, where))


def check_humanoid_motions(where, piece, opts, problems):
    """The motions list of a humanoid piece (forge/motion_clips.py check_motions)."""
    from . import motion_clips as mc
    if "export" not in opts:
        problems.append("%s: motions need the export stage (it plays them on the figure)" % where)
    inputs = piece.get("inputs") or {}
    default = None
    clips = inputs.get("clips")
    if isinstance(clips, str) and os.path.isfile(clips):
        default, why = mc.read_library(clips)
        problems.extend("%s: inputs.clips: %s" % (where, p) for p in why)
        if why:
            default = None
    libraries = {}
    for m in piece["motions"] if isinstance(piece["motions"], list) else []:
        lib = m.get("library") if isinstance(m, dict) else None
        f = lib.get("file") if isinstance(lib, dict) else None
        if isinstance(f, str) and f not in libraries:
            if f != mc.STARTER and not os.path.isfile(f):
                problems.append("%s: motion library not found: %s" % (where, f))
                libraries[f] = None
                continue
            doc, why = mc.read_library(f)
            problems.extend("%s: %s: %s" % (where, f, p) for p in why)
            libraries[f] = None if why else doc
    problems.extend("%s: %s" % (where, p) for p in mc.check_motions(
        piece["motions"], default, {k: v for k, v in libraries.items() if v is not None}))


# --- articulated and creature: whole object or body generated per variant, then split into
# parts on pivots (articulated) or rigged and animated in the roles of a body plan (creature)

ARTICULATED_SIZE = (1536, 1024)
CREATURE_SIZE = (1024, 1536)
# Piece params the local tools of these recipes take (when the order gives them).
PARTS_PARAMS = ("tris", "height", "length", "front", "image_format")
# The shading params of the parts and held items (smooth by angle, the baked normal map).
SHADING_PARAMS = ("normal_map", "crease", "relief")


def _front_chain(fam, a, st, recipe, s, add, comfy, opts, fields, size, view):
    """The stages a variant of the articulated and creature recipes shares with the humanoid:
    reference, pose (creature), cutout, mesh and paint. Returns (mesh job, painted reference)."""
    inputs = a.get("inputs") or {}
    tris = param(a, "tris", "faces", stages.HUMANOID_PARAMS["tris"])
    tex = param(a, "texture", "texture_size", stages.HUMANOID_PARAMS["texture"])
    w, h = a.get("size", fam.get("size", size))
    text = prompt_of(st, fam, a, a.get("view", fam.get("view", view)))
    edit_text = st.get("pose_" + recipe) or st[view]
    cutout_text = st.get("cutout_" + recipe) or st[view]
    keep_texture = opts.get("paint", "trellis2_texture") == "trellis2_texture"
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
    if "pose" in opts:
        pose = comfy("pose", dict(EDIT_VALUES, **{"$IMAGE1$": image, "$PROMPT$": edit_text, "$SEED$": s}))
        image, mask, rgba = {"ref": pose, "role": "rgb"}, {"ref": pose, "role": "mask"}, None
    if opts["cutout"] == "birefnet":
        ins = {"image": image, "mask": mask} if mask else {"image": rgba or image}
        cut = add("cutout", {"kind": "cutout", "inputs": ins})
    else:
        cut = comfy("cutout", dict(EDIT_VALUES, **{"$IMAGE1$": image, "$PROMPT$": cutout_text, "$SEED$": s}))
    cut_ref = {"ref": cut, "role": "rgba"}
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
    last = paint_jobs(add, comfy, opts, fields, cut_ref, {"ref": mesh, "role": "game_mesh"}, s, tex) or mesh
    return mesh, {"ref": last, "role": "painted_mesh"}


def _variants(fam, a, st, recipe, size, view, tail, review_tool="sheet", final_extra=None):
    """Every variant's chain (the shared front, then `tail(add, v, mesh, painted, opts)`,
    which adds the recipe's own last stages and returns (last job, its role), or (job, role,
    extra) where extra may hold "review_from" (more jobs the review waits for and whose files
    enter its specification) and "variant" (more keys of the variant's entry)), then the review
    sheet (its runner: review_tool) and the final decision (final_extra: more keys of the
    final jobs), with reserve variants as the humanoid's."""
    fid, aid = fam["id"], a["id"]
    opts = stages.chosen_options(recipe, a)
    fields = {stage: stages.option(recipe, stage, o) for stage, o in opts.items()}
    jobs, variants = [], []
    review_from = {}
    n = variants_of(fam, a, 4)
    final_id = "%s/%s/final" % (fid, aid)
    hold = {"until": final_id, "outcome": acceptance.NO_VARIANT_PASSED}
    for i, s in enumerate(seeds_for(fid + "/" + aid, n + reserve_of(a))):
        v = "v%d" % s
        chain = []
        reserve = i >= n

        def add(stage, job, name=None, v=v, chain=chain, reserve=reserve):
            job.update({"id": "%s/%s/%s/%s" % (fid, aid, v, name or stage), "stage": stage, "variant": v})
            if reserve:
                job["hold"] = dict(hold)
            job.setdefault("group", fields[stage]["group"])
            if fields[stage].get("ignored"):
                job["ignored"] = dict(fields[stage]["ignored"])
            jobs.append(job)
            chain.append(job["id"])
            return job["id"]

        def comfy(stage, values, add=add, **extra):
            f = fields[stage]
            job = {"kind": "comfy", "workflow": f["workflow"], "values": values, "roles": dict(f["roles"])}
            if f.get("heavy"):
                job["heavy"] = True
            job.update(extra)
            return add(stage, job)

        mesh, painted = _front_chain(fam, a, st, recipe, s, add, comfy, opts, fields, size, view)
        got = tail(add, v, mesh, painted, opts)
        last, role = got[0], got[1]
        more = got[2] if len(got) > 2 else {}
        entry = {"variant": v, "job": last, "role": role, "chain": list(chain)}
        entry.update(more.get("variant") or {})
        if reserve:
            entry["reserve"] = True
        if more.get("review_from"):
            review_from[v] = list(more["review_from"])
        variants.append(entry)
    decision = {"accept": acceptance.limits(a.get("accept"), recipe),
                "paint_stage": "paint" if "paint" in opts else "mesh"}
    main, extra = variants[:n], variants[n:]

    def decide(suffix, covered, held):
        ids = [x["job"] for x in covered]
        later = [j for x in covered for j in review_from.get(x["variant"], [])]
        review = {"id": "%s/%s/review%s" % (fid, aid, suffix), "kind": "local_gpu", "tool": review_tool,
                  "stage": "review", "group": fields["review"]["group"], "partial": True, "even_if_none": True,
                  "from": ids + later, "variants": covered, "args": dict(REVIEW_DEFAULTS, **(a.get("review") or {}))}
        review.update(decision)
        final = dict({"id": "%s/%s/final%s" % (fid, aid, suffix), "kind": "final", "group": "local",
                      "partial": True, "even_if_none": True, "from": ids, "variants": covered,
                      "reserve": {"variants": [x["variant"] for x in extra],
                                  "decision": "%s/%s/final_reserve" % (fid, aid) if extra and not held else None}},
                     **decision)
        final.update(final_extra or {})
        if held:
            review["hold"], final["hold"] = dict(hold), dict(hold)
        jobs.extend([review, final])
    decide("", main, False)
    if extra:
        decide("_reserve", variants, True)
    return jobs


def _tool_params(a):
    p = a.get("params") or {}
    return {k: p[k] for k in PARTS_PARAMS if k in p}


def recipe_articulated(fam, a, st):
    """Rigid parts on pivots. For each seed of seeds_for(family/piece, variants) a chain
    <family>/<piece>/v<seed>/<stage>: reference (the whole object) -> cutout -> mesh -> paint
    -> parts (the painted mesh split into the order's parts, one node per part, each origin
    at its pivot; tools/blender/articulated_export.py); then one review sheet and the final
    decision. The whole object is generated once and the parts derived from it, so they are
    one design."""

    def tail(add, v, mesh, painted, opts):
        # The raw mesh (before the mesh stage's reduction) is the normal map's bake source.
        ins = {"mesh": painted, "bake_source": {"ref": mesh, "role": "raw_mesh"}}
        if opts.get("paint") in HUNYUAN_PAINTS:
            ins["metal_roughness"] = {"ref": painted["ref"], "role": "metal_roughness"}
        p = a.get("params") or {}
        args = dict({"name": a["id"], "parts": a.get("parts")}, **_tool_params(a))
        args.update({k: p[k] for k in SHADING_PARAMS if k in p})
        return add("parts", {"kind": "local_gpu", "tool": "parts", "inputs": ins, "args": args}), "glb"
    return _variants(fam, a, st, "articulated", ARTICULATED_SIZE, "view_articulated", tail)


def recipe_creature(fam, a, st):
    """Skinned, with its own skeleton. Per variant: reference -> pose -> cutout -> mesh ->
    paint -> rig (SkinTokens or UniRig: a skeleton of its own) -> export (the predicted bones
    mapped onto the order's body plan or template by the tree and the geometry, the order's
    motions made in those roles; tools/blender/creature_export.py); then one review sheet and
    the final decision."""
    inputs = a.get("inputs") or {}
    from . import tool_specs
    glow_colours = tool_specs.glow_colours_in_text(prompt_of(st, fam, a, a.get("view", fam.get("view", "view_creature"))))

    def tail(add, v, mesh, painted, opts):
        args = {"method": opts["rig"], "seed": RIG_SEED}
        if opts["rig"] == "skintokens":
            args.update(beams=10, postprocess=False)
        rig = add("rig", {"kind": "local_gpu", "tool": "rig", "inputs": {"mesh": painted}, "args": args})
        ins = {"rig": {"ref": rig, "role": "rig"}}
        if opts.get("paint") in HUNYUAN_PAINTS:
            ins["metal_roughness"] = {"ref": painted["ref"], "role": "metal_roughness"}
        if inputs.get("clips"):
            ins["clips"] = {"file": inputs["clips"]}
        libs = tool_specs.library_files(a.get("motions"))
        if libs:
            ins["libraries"] = [{"file": f} for f in libs]
        bone_map = variant_bone_map(inputs.get("bone_map"), v)
        if bone_map:
            ins["bone_map"] = {"file": bone_map}
        args = dict({"name": a["id"], "body": a.get("body"), "motions": a.get("motions") or []}, **_tool_params(a))
        if glow_colours:
            # What glows (forge/local_gpu.py job_glow), weighed by the variant's cutout.
            args["glow_colours"] = glow_colours
            ins["reference"] = {"ref": "%s/%s/%s/cutout" % (fam["id"], a["id"], v), "role": "rgba"}
        return add("export", {"kind": "local_gpu", "tool": "creature_export", "inputs": ins, "args": args}), "glb"
    return _variants(fam, a, st, "creature", CREATURE_SIZE, "view_creature", tail)


# --- held_item: an object a figure holds, generated from a strict side view, put in item
# space with its sockets (the socket file the humanoid exporter reads)

HELD_ITEM_SIZE = (1536, 1024)
ITEM_PARAMS = ("tris", "height", "length", "image_format", "normal_map", "crease", "relief")
# The family folder every picked item of a held-item family is copied to (<name>.glb and
# <name>.json side by side): a humanoid order's inputs.items.
ITEMS_FOLDER = "items"


def recipe_held_item(fam, a, st):
    """An object a figure holds. Per variant <family>/<piece>/v<seed>/<stage>: reference (a
    strict side view, the front to the right) -> cutout -> mesh -> paint -> item (item space
    at the real size, the class template's sockets, the socket file;
    tools/blender/held_item_export.py) -> hold (the humanoid exporter run on the order's
    reference figure with this item and the class's clips; for the review only); then one
    review sheet (the item from the review camera, a side view with its sockets, the figure
    holding it) and the final decision, which also copies the picked item's GLB and socket
    file to <family>/items/."""
    inputs = a.get("inputs") or {}
    item_spec = a.get("item") or {}

    def tail(add, v, mesh, painted, opts):
        # The cutout the mesh was made from: the item step finds the mesh's frame by its silhouette.
        # The raw mesh (before the mesh stage's reduction) is the normal map's bake source.
        ins = {"mesh": painted, "reference": {"ref": "%s/%s/%s/cutout" % (fam["id"], a["id"], v), "role": "rgba"},
               "bake_source": {"ref": mesh, "role": "raw_mesh"}}
        if opts.get("paint") in HUNYUAN_PAINTS:
            ins["metal_roughness"] = {"ref": painted["ref"], "role": "metal_roughness"}
        p = a.get("params") or {}
        args = dict({"name": a["id"], "item": a.get("item")}, **{k: p[k] for k in ITEM_PARAMS if k in p})
        item = add("item", {"kind": "local_gpu", "tool": "item", "inputs": ins, "args": args})
        if "hold" not in opts:
            return item, "glb"
        hold_ins = {"glb": {"ref": item, "role": "glb"}, "sockets": {"ref": item, "role": "sockets"},
                    "figure": {"file": inputs.get("figure")}, "clips": {"file": inputs.get("clips")}}
        hold_args = {"name": a["id"], "class": item_spec.get("class")}
        if item_spec.get("clips"):
            hold_args["clips"] = list(item_spec["clips"])
        hold = add("hold", {"kind": "local_gpu", "tool": "hold", "inputs": hold_ins, "args": hold_args})
        return item, "glb", {"review_from": [hold], "variant": {"hold": hold}}
    return _variants(fam, a, st, "held_item", HELD_ITEM_SIZE, "view_held_item", tail, review_tool="item_sheet",
                     final_extra={"items_folder": ITEMS_FOLDER})


def check_held_item(where, piece, problems):
    from . import tool_specs
    _check_front(where, "held_item", piece, problems)
    params = piece.get("params") or {}
    if "height" not in params and "length" not in params:
        problems.append("%s: params.length or params.height is required (the item's real size in metres: its "
                        "length front to back, or its height)" % where)
    if where.rsplit("/", 1)[-1] == ITEMS_FOLDER:
        problems.append("%s: a held item cannot be named %r: the family's folder of picked items has that name" % (
            where, ITEMS_FOLDER))
    if "item" not in piece:
        problems.append("%s: item is required (the hold class and its socket template)" % where)
        return
    found = tool_specs.check_item(piece["item"])
    problems.extend("%s: %s" % (where, p) for p in found)
    if found or "hold" not in stages.chosen_options("held_item", piece):
        return
    inputs = piece.get("inputs") or {}
    for name, what in (("figure", "a rigged humanoid GLB"), ("clips", "the motion library")):
        if not inputs.get(name):
            problems.append("%s: the hold stage needs inputs.%s (%s), or skip hold" % (where, name, what))
    figure = inputs.get("figure")
    if isinstance(figure, str) and os.path.isfile(figure):
        problems.extend("%s: inputs.figure %s" % (where, p) for p in tool_specs.check_figure(figure))
    clips = inputs.get("clips")
    if isinstance(clips, str) and os.path.isfile(clips):
        problems.extend("%s: inputs.clips: %s" % (where, p)
                        for p in tool_specs.check_hold_clips(clips, piece["item"]))


def _check_front(where, recipe, piece, problems):
    """The stage combinations of the shared front stages (as the humanoid's)."""
    opts = stages.chosen_options(recipe, piece)
    inputs = piece.get("inputs") or {}
    params = piece.get("params") or {}
    if opts["mesh"] != "trellis2" and opts.get("paint", "trellis2_texture") == "trellis2_texture":
        problems.append("%s: skipping paint keeps TRELLIS.2's own texture, which needs mesh trellis2 (mesh %s has "
                        "none: use paint hunyuan_paint)" % (where, opts["mesh"]))
    if opts["reference"] == "input_image":
        path = inputs.get("reference")
        if not path:
            problems.append("%s: reference input_image needs inputs.reference (a picture of the subject)" % where)
        elif "pose" not in opts and opts["cutout"] == "birefnet" and os.path.isfile(path):
            why = _alpha_problem(path)
            if why:
                problems.append("%s: inputs.reference %s: with cutout birefnet%s it must have a transparent "
                                "background (or use cutout qwen_edit_character)" % (
                                    where, why, " and pose skipped" if recipe == "creature" else ""))
    tex = params.get("texture")
    if isinstance(tex, int) and not isinstance(tex, bool):
        if opts.get("paint") in HUNYUAN_PAINTS and not (512 <= tex <= 4096 and tex % 512 == 0):
            problems.append("%s: params.texture %d: Hunyuan3D Paint takes 512 to 4096, a multiple of 512" % (where, tex))
        elif opts["mesh"] == "trellis2" and opts.get("paint", "trellis2_texture") == "trellis2_texture" \
                and not (256 <= tex <= 8192 and tex % 256 == 0):
            problems.append("%s: params.texture %d: TRELLIS.2's bake takes 256 to 8192, a multiple of 256" % (where, tex))
    if "height" in params and "length" in params:
        problems.append("%s: give params.height or params.length, not both (the model is scaled by one)" % where)


def check_articulated(where, piece, problems):
    from . import tool_specs
    _check_front(where, "articulated", piece, problems)
    if "parts" not in piece:
        problems.append("%s: parts is required (the object's parts with their regions and joints)" % where)
    else:
        problems.extend("%s: %s" % (where, p) for p in tool_specs.check_parts(piece["parts"]))


def check_creature(where, piece, problems):
    from . import tool_specs
    _check_front(where, "creature", piece, problems)
    if "body" not in piece:
        problems.append("%s: body is required (the body plan or the skeleton template)" % where)
        return
    found = tool_specs.check_body(piece["body"])
    problems.extend("%s: %s" % (where, p) for p in found)
    if found:
        return
    inputs = piece.get("inputs") or {}
    library = None
    if isinstance(inputs.get("clips"), str) and os.path.isfile(inputs["clips"]):
        library, why = tool_specs.read_library(inputs["clips"])
        problems.extend("%s: inputs.clips: %s" % (where, p) for p in why)
        if why:
            library = None
    motions = piece.get("motions", [])
    uses_default = any(isinstance(m, dict) and m.get("source") == "library" and
                       "file" not in (m.get("library") or {}) for m in motions or [])
    if uses_default and not inputs.get("clips"):
        problems.append("%s: a motion of source library without library.file needs inputs.clips" % where)
    problems.extend("%s: %s" % (where, p) for p in tool_specs.check_motions(motions, piece["body"], library))
    for f in tool_specs.library_files(motions):
        if not os.path.isfile(f):
            problems.append("%s: motion library not found: %s" % (where, f))
        else:
            problems.extend("%s: %s: %s" % (where, f, p) for p in tool_specs.read_library(f)[1])
    bone_map = inputs.get("bone_map")
    if isinstance(bone_map, dict):
        names = ["v%d" % s for s in seeds_for(where, variants_of({}, piece, 4) + reserve_of(piece))]
        for key in bone_map:
            if key not in names and "v" + str(key) not in names:
                problems.append("%s: inputs.bone_map names %r, which is no variant of this piece (variants: %s)" % (
                    where, key, ", ".join(names)))


def recipe_iso_tile(fam, a, st):
    """A sprite of an isometric tile grid remade from the game's sprite (forge/tile_jobs.py)."""
    from .tile_jobs import recipe_iso_tile as expand_tile
    return expand_tile(fam, a, st)


RECIPES = {"vehicle": recipe_vehicle, "illustration": recipe_illustration, "figure": recipe_figure,
           "object_3d": recipe_object_3d, "concept": recipe_concept, "prop": recipe_prop,
           "material": recipe_material, "humanoid": recipe_humanoid, "articulated": recipe_articulated,
           "creature": recipe_creature, "held_item": recipe_held_item, "iso_tile": recipe_iso_tile}
# Recipe-specific checks of a piece, called by forge.order.check_order with the piece's
# valid stages and skips: check(where, piece, problems).
PIECE_CHECKS = {"humanoid": check_humanoid, "articulated": check_articulated, "creature": check_creature,
                "held_item": check_held_item}


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
        # The humanoid motions made once for the whole family (text, bvh): forge/motion_jobs.py.
        from .motion_jobs import MOTIONS_ASSET, made_motion_jobs
        for j in made_motion_jobs(fam):
            j["family"], j["asset"] = fam["id"], MOTIONS_ASSET
            jobs.append(j)
        # An iso_tile family's seam materials and set sheet (forge/tile_jobs.py).
        from .tile_jobs import family_jobs
        jobs.extend(family_jobs(fam, st))
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
    if job.get("hold"):
        out.append(job["hold"]["until"])  # a held job waits for that job's outcome
    return out
