"""Stage catalogue: for each recipe, the stages an order may tune piece by piece.

A recipe with stages lists them here: each stage has a default option, may be skippable,
and each option says how it runs and which named settings it accepts. An order picks
options in a piece's "stages" and skips stages in "skip" ([stage, ...]). A `stages` entry
is either an option name or an object {"option": <name>, "settings": {<setting>: value}}.
A new model becomes a new option (OPTIONS) of a stage and is then available to every
recipe that has that stage.

Option fields (all optional except kind):
  kind      "comfy" (a ComfyUI workflow), "local" (a step of the factory without the
            card), "local_gpu" (a local tool that uses the card) or "input" (a file the
            order supplies: no job)
  workflow  file of forge/workflows (comfy)
  tool      name of the runner in forge.local_gpu.RUNNERS (local_gpu)
  group     scheduling and estimate group
  heavy     True: goes alone in the server queue after /free
  roles     {node id: output role} of the workflow
  mesh_role role of the mesh a Hy3DInPaint node writes
  outputs   the roles the option hands to the next stage
  in_stage  the option adds no job of its own: it changes the job of that stage
  meaning   what the option does (English, for the documentation)
  params    names of the piece "params" the option reads
  inputs    names of the piece "inputs" the option reads
  tools     configuration keys (forge.config.KEYS) the option needs on this machine
  settings  {name: declaration} of the named settings an order may give (below)
  ignored   {tool argument: reason} of a local tool: arguments the tool is known to ignore
            or override (the effective-parameters record says why it cannot know them)

A setting declaration:
  type      "int" (whole number), "number", "seed" (whole number 0 to 2**64-1), "bool" or
            "combo" (a name from the server's list for that input: checked by the
            environment check, forge.readiness)
  min, max  inclusive range ("int", "number")
  step      the value must be a multiple of it ("int")
  to        list of "<node>.<input>" of the option's workflow it lands on (comfy)
  arg       the argument of the option's tool it lands on (local, local_gpu)
  meaning   what it changes (English, for the documentation and error texts)
A setting lands in the job's "set" ({"<node>.<input>": value}) or "args" ({arg: value}),
so it enters the job specification (hash) like any value and changes only the jobs of
its stage and what depends on them.
"""

SEED_MAX = 2 ** 64 - 1


def _per_sampler(prefix, node, what, steps, guidance=(0.0, 100.0), cfg_input="cfg"):
    """Steps, seed, guidance and scheduler settings of one KSampler node."""
    return {
        prefix + "_steps": {"type": "int", "min": 1, "max": steps, "to": ["%s.steps" % node],
                            "meaning": "steps of the %s sampler" % what},
        prefix + "_seed": {"type": "seed", "to": ["%s.seed" % node],
                           "meaning": "seed of the %s sampler (default: the variant's seed)" % what},
        prefix + "_guidance": {"type": "number", "min": guidance[0], "max": guidance[1],
                               "to": ["%s.%s" % (node, cfg_input)],
                               "meaning": "guidance (CFG) of the %s sampler" % what},
        prefix + "_scheduler": {"type": "combo", "to": ["%s.scheduler" % node],
                                "meaning": "scheduler of the %s sampler" % what},
    }


# TRELLIS.2 (forge/workflows/trellis2_mesh.json): four KSampler nodes, one per stage of the
# model: sparse structure (3), shape at 512 (18), shape upsampled (23), texture (12).
TRELLIS2_SAMPLERS = (("structure", "3"), ("shape", "18"), ("upsample", "23"), ("texture", "12"))
TRELLIS2_SETTINGS = {}
for _what, _node in TRELLIS2_SAMPLERS:
    TRELLIS2_SETTINGS.update(_per_sampler(_what, _node, _what, 200))
TRELLIS2_SETTINGS["sampler"] = {"type": "combo", "to": ["%s.sampler_name" % n for _, n in TRELLIS2_SAMPLERS],
                                "meaning": "sampler of the four samplers"}
TRELLIS2_SETTINGS["resolution"] = {"type": "int", "min": 1024, "max": 2048, "step": 128,
                                   "to": ["94.target_resolution"],
                                   "meaning": "voxel resolution of the upsampled shape"}

# Shared option definitions: option name -> fields. A recipe's stage lists the options it
# offers (CATALOGUE); the same option behaves the same in every recipe.
OPTIONS = {
    "klein": {
        "kind": "comfy", "workflow": "klein_t2i.json", "group": "klein", "roles": {"13": "rgb", "18": "rgba"},
        "settings": {
            "steps": {"type": "int", "min": 1, "max": 50, "to": ["10.steps"], "meaning": "sampling steps"},
            "guidance": {"type": "number", "min": 0.0, "max": 30.0, "to": ["8.cfg"], "meaning": "guidance (CFG)"},
            "sampler": {"type": "combo", "to": ["9.sampler_name"], "meaning": "sampler"},
        }},
    "hunyuan21": {
        "kind": "comfy", "workflow": "hunyuan21_shape_paint.json", "group": "hy3d", "heavy": True,
        "roles": {"6": "shape", "12": "albedo", "13": "metal_roughness", "14": "paint_views"},
        "params": ("tris", "texture"),
        "settings": {
            "shape_steps": {"type": "int", "min": 1, "max": 100, "to": ["3.steps"], "meaning": "steps of the shape model"},
            "shape_guidance": {"type": "number", "min": 1.0, "max": 30.0, "to": ["3.guidance_scale"],
                               "meaning": "guidance of the shape model"},
            "shape_seed": {"type": "seed", "to": ["3.seed"], "meaning": "seed of the shape model"},
            "octree_resolution": {"type": "int", "min": 64, "max": 1024, "step": 8, "to": ["4.octree_resolution"],
                                  "meaning": "resolution of the surface extraction"},
            "paint_steps": {"type": "int", "min": 1, "max": 100, "to": ["9.steps"], "meaning": "steps of the Paint views"},
            "paint_guidance": {"type": "number", "min": 1.0, "max": 10.0, "to": ["9.guidance_scale"],
                               "meaning": "guidance of the Paint views"},
            "paint_seed": {"type": "seed", "to": ["9.seed"],
                           "meaning": "seed of the Paint stage (its diffusion noise is fixed by the node: see OVERRIDDEN)"},
        }},
    # TRELLIS.2 shape, remesh and reduction to params.tris (game_mesh, 403); raw_mesh (405)
    # is the remeshed shape before the reduction. Its own texture bake (painted_mesh, 322)
    # is kept only when no separate paint stage follows (the humanoid's paint skipped or
    # "trellis2_texture"); otherwise the job drops TRELLIS2_TEXTURE_NODES.
    "trellis2": {
        "kind": "comfy", "workflow": "trellis2_mesh.json", "group": "trellis2",
        "roles": {"403": "game_mesh", "405": "raw_mesh", "322": "painted_mesh"},
        "outputs": ("game_mesh", "raw_mesh"), "params": ("tris", "texture"),
        "settings": TRELLIS2_SETTINGS},
}

def _edit_settings(what):
    """Settings of a Qwen Image Edit 2511 workflow (qwen_edit_2511.json and
    qwen_edit_character.json share their node ids)."""
    return {
        "steps": {"type": "int", "min": 1, "max": 100, "to": ["14.steps"], "meaning": "steps of the %s edit" % what},
        "guidance": {"type": "number", "min": 0.0, "max": 100.0, "to": ["14.cfg"],
                     "meaning": "guidance (CFG) of the %s edit" % what},
        "seed": {"type": "seed", "to": ["14.seed"], "meaning": "seed of the %s edit (default: the variant's seed)" % what},
        "sampler": {"type": "combo", "to": ["14.sampler_name"], "meaning": "sampler of the %s edit" % what},
        "scheduler": {"type": "combo", "to": ["14.scheduler"], "meaning": "scheduler of the %s edit" % what},
        "lora_strength": {"type": "number", "min": 0.0, "max": 2.0, "to": ["4.strength_model"],
                          "meaning": "strength of the Lightning LoRA of the %s edit" % what},
    }


# Seeds of the local rig tools: whole numbers that numpy and torch both accept.
RIG_SEED = {"type": "int", "min": 0, "max": 2 ** 32 - 1, "arg": "seed",
            "meaning": "seed of the rig model (default 9001, the validated setting)"}

# Options of the humanoid recipe (forge/recipes.py recipe_humanoid). "outputs" are the
# roles each option hands to the next stage. Kinds besides comfy and local_gpu: "local"
# (a step of the factory without the card) and "input" (a file of the order, no job).
HUMANOID_OPTIONS = {
    "reference": {
        "qwen21_base": {
            "kind": "comfy", "workflow": "qwen_image_21_base_t2i.json", "group": "qwen21_base",
            "roles": {"8": "rgb", "12": "mask"}, "outputs": ("rgb", "mask"),
            "settings": {
                "steps": {"type": "int", "min": 1, "max": 200, "to": ["6.steps"], "meaning": "sampling steps"},
                "guidance": {"type": "number", "min": 0.0, "max": 100.0, "to": ["6.cfg"], "meaning": "guidance (CFG)"},
                "sampler": {"type": "combo", "to": ["6.sampler_name"], "meaning": "sampler"},
                "scheduler": {"type": "combo", "to": ["6.scheduler"], "meaning": "scheduler"},
            }},
        "qwen21_turbo": {
            "kind": "comfy", "workflow": "qwen_image_21_turbo_t2i.json", "group": "qwen_t2i",
            "roles": {"12": "rgb", "16": "mask"}, "outputs": ("rgb", "mask"),
            "settings": {
                "guidance": {"type": "number", "min": 0.0, "max": 100.0, "to": ["9.cfg"], "meaning": "guidance (CFG)"},
                "sampler": {"type": "combo", "to": ["7.sampler_name"], "meaning": "sampler"},
            }},
        "klein": dict(OPTIONS["klein"], outputs=("rgb", "rgba")),
        "input_image": {
            "kind": "input", "group": "local", "inputs": ("reference",), "outputs": ("image",),
            "meaning": "the picture in inputs.reference (no generation)"},
    },
    "pose": {
        "qwen_edit_2511": {
            "kind": "comfy", "workflow": "qwen_edit_2511.json", "group": "qwen_edit",
            "roles": {"16": "rgb", "20": "mask"}, "outputs": ("rgb", "mask"), "settings": _edit_settings("pose")},
    },
    "cutout": {
        "birefnet": {
            "kind": "local", "group": "local", "outputs": ("rgba",),
            "meaning": "the BiRefNet foreground mask the previous workflow saved becomes the alpha"},
        "qwen_edit_character": {
            "kind": "comfy", "workflow": "qwen_edit_character.json", "group": "qwen_edit",
            "roles": {"16": "rgb", "20": "mask", "32": "rgba"}, "outputs": ("rgba",),
            "settings": _edit_settings("cutout")},
    },
    "mesh": {
        "trellis2": OPTIONS["trellis2"],
        # Hunyuan3D 2.1 shape only (the Paint nodes of the workflow dropped): the paint
        # stage paints the reduced mesh. raw_mesh: the shape before the reduction.
        "hunyuan21": dict(OPTIONS["hunyuan21"], roles={"6": "game_mesh", "16": "raw_mesh"},
                          outputs=("game_mesh", "raw_mesh"), params=("tris",),
                          settings={k: v for k, v in OPTIONS["hunyuan21"]["settings"].items()
                                    if not k.startswith("paint_")}),
    },
    "paint": {
        "hunyuan_paint": {
            "kind": "comfy", "workflow": "hunyuan21_paint_mesh.json", "group": "hunyuan_paint", "heavy": True,
            "roles": {"12": "albedo", "13": "metal_roughness", "14": "paint_views"}, "mesh_role": "painted_mesh",
            "outputs": ("painted_mesh",), "params": ("texture",),
            "settings": {
                "steps": {"type": "int", "min": 1, "max": 100, "to": ["9.steps"], "meaning": "steps of the Paint views"},
                "guidance": {"type": "number", "min": 1.0, "max": 10.0, "to": ["9.guidance_scale"],
                             "meaning": "guidance of the Paint views"},
                "seed": {"type": "seed", "to": ["9.seed"],
                         "meaning": "seed of the Paint stage (its diffusion noise is fixed by the node: see OVERRIDDEN)"},
                "view_size": {"type": "int", "min": 512, "max": 1024, "step": 256, "to": ["9.view_size"],
                              "meaning": "size of the Paint views"},
            }},
        # TRELLIS.2's own texture: the mesh job keeps its bake (only with mesh trellis2).
        "trellis2_texture": {
            "kind": "comfy", "workflow": "trellis2_mesh.json", "group": "trellis2", "roles": {"322": "painted_mesh"},
            "outputs": ("painted_mesh",), "params": ("texture",), "in_stage": "mesh",
            "meaning": "the mesh job keeps TRELLIS.2's texture bake (its texture_* settings are mesh settings)"},
    },
    "rig": {
        "skintokens": {
            "kind": "local_gpu", "tool": "rig", "group": "local_gpu", "outputs": ("rig",),
            "tools": ("skintokens_dir", "skintokens_python"),
            "settings": {
                "seed": RIG_SEED,
                "beams": {"type": "int", "min": 1, "max": 64, "arg": "beams", "meaning": "beams of the skeleton search"},
                "postprocess": {"type": "bool", "arg": "postprocess", "meaning": "SkinTokens' own post-processing"},
            }},
        "unirig": {
            "kind": "local_gpu", "tool": "rig", "group": "local_gpu", "outputs": ("rig",),
            "tools": ("unirig_dir", "unirig_venv", "unirig_hf", "unirig_shim"),
            "settings": {"seed": RIG_SEED}},
    },
    "export": {
        "humanoid_export": {
            "kind": "local_gpu", "tool": "export", "group": "local_gpu",
            "outputs": ("glb", "model", "regions_mask", "bone_map", "mesh_report", "motion_report"),
            "tools": ("blender_python",), "inputs": ("clips", "items", "bone_map"),
            "params": ("tris", "texture", "height", "head", "hands", "feet", "widen", "image_format"),
            "settings": {
                "normal_map": {"type": "int", "min": 256, "max": 8192, "arg": "normal_map",
                               "meaning": "size of the baked normal map (default: params.texture)"},
                "glow": {"type": "bool", "arg": "glow", "meaning": "also mark what glows in the regions mask"},
                "foot_slide_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "foot_slide_cm",
                                  "meaning": "motion report threshold: foot slide in cm"},
                "hand_socket_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "hand_socket_cm",
                                   "meaning": "motion report threshold: wrist to socket in cm"},
                "reach_shortfall_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "reach_shortfall_cm",
                                       "meaning": "motion report threshold: IK reach shortfall in cm"},
                "stretch_p95": {"type": "number", "min": 0.0, "max": 10.0, "arg": "stretch_p95",
                                "meaning": "motion report threshold: 95th percentile of edge stretch"},
                "penetration_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "penetration_cm",
                                   "meaning": "motion report threshold: body inside a held item in cm"},
            }},
    },
    "review": {
        "godot_sheet": {
            "kind": "local_gpu", "tool": "review", "group": "local_gpu", "outputs": ("sheet", "layout"),
            "tools": ("godot",),
            "settings": {
                "front_height": {"type": "int", "min": 64, "max": 2048, "arg": "front_height",
                                 "meaning": "height of the large front view in pixels"},
                "strip_frames": {"type": "int", "min": 1, "max": 32, "arg": "strip_frames",
                                 "meaning": "frames of each motion strip"},
            }},
    },
}
HUMANOID_DEFAULTS = {"reference": "qwen21_base", "pose": "qwen_edit_2511", "cutout": "birefnet", "mesh": "trellis2",
                     "paint": "hunyuan_paint", "rig": "skintokens", "export": "humanoid_export", "review": "godot_sheet"}
HUMANOID_SKIPPABLE = ("pose", "paint", "rig", "export")
# Piece params the humanoid recipe uses when the order does not give them.
HUMANOID_PARAMS = {"tris": 25000, "texture": 2048}

# recipe -> stage -> {"default": option, "skippable": bool, "options": {option: fields}}.
# The core recipes have one option per stage (the models they always used); the stages
# exist so that an order can give their settings.
_CONCEPT = {"default": "klein", "skippable": False, "options": {"klein": OPTIONS["klein"]}}
_MESH = {"default": "hunyuan21", "skippable": False, "options": {"hunyuan21": OPTIONS["hunyuan21"]}}
CATALOGUE = {
    "concept": {"concept": _CONCEPT},
    "illustration": {"concept": _CONCEPT},
    "material": {},
    "object_3d": {"concept": _CONCEPT, "mesh": _MESH},
    "prop": {"concept": _CONCEPT, "mesh": _MESH},
    "vehicle": {"concept": _CONCEPT, "mesh": _MESH},
    "figure": {"concept": _CONCEPT, "mesh": _MESH},
    "humanoid": {stage: {"default": HUMANOID_DEFAULTS[stage], "skippable": stage in HUMANOID_SKIPPABLE,
                         "options": opts} for stage, opts in HUMANOID_OPTIONS.items()},
}

# Inputs of a workflow that an implementation is known to ignore or override: the
# effective-parameters record keeps what was requested and says why the effective value
# is unknown. workflow -> {"<node>.<input>": reason}.
PAINT_SEED = ("Hy3DMultiViewsGenerator passes the seed to seed_everything, but the Paint "
              "pipeline draws its diffusion noise from a generator fixed at seed 0 "
              "(hy3dpaint/utils/multiview_utils.py of ComfyUI-Hunyuan3d-2-1)")
OVERRIDDEN = {
    "hunyuan21_shape_paint.json": {"9.seed": PAINT_SEED},
    "hunyuan21_paint_mesh.json": {"9.seed": PAINT_SEED},
}

# Piece "params": name -> (type, meaning). "int" and "number" must be greater than zero; a
# "choice" is one of PARAM_CHOICES[name].
PARAMS = {
    "tris": ("int", "triangle (face) budget of a mesh"),
    "texture": ("int", "texture size in pixels"),
    "height": ("number", "height of a figure in metres"),
    "head": ("number", "head scale"),
    "hands": ("number", "hand scale"),
    "feet": ("number", "foot scale"),
    "widen": ("number", "body width scale"),
    "image_format": ("choice", "image formats of an exported GLB: lossless (every map PNG) or compact "
                               "(base colour JPEG at quality 92, the technical maps PNG)"),
}
PARAM_CHOICES = {"image_format": ("lossless", "compact")}

# Params and inputs a recipe reads itself (besides those of its stage options).
RECIPE_PARAMS = {
    "object_3d": ("tris", "texture"),
    "prop": ("tris", "texture"),
    "vehicle": ("tris", "texture"),
    "figure": ("tris", "texture"),
}
RECIPE_INPUTS = {
    "object_3d": ("reference",),
    "prop": ("reference",),
}
# Inputs that are folders (they enter the job specification by the files inside them);
# every other input is a file.
FOLDER_INPUTS = ("items",)
# Inputs that may also be an object {"v<seed>" or "<seed>": file}: one file per variant.
VARIANT_INPUTS = ("bone_map",)

# Nodes of forge/workflows/trellis2_mesh.json that only the texture bake uses: a job that
# lists them in "drop" makes the untextured mesh alone (the shape for another paint stage).
TRELLIS2_TEXTURE_NODES = ("12", "93", "98", "118", "147", "196", "210", "238", "247", "322")
# Nodes of forge/workflows/hunyuan21_shape_paint.json: the Paint branch (dropped when a
# separate paint stage paints the mesh) and the export of the shape before the reduction
# (dropped by the recipes that do not use it).
HUNYUAN21_PAINT_NODES = ("7", "8", "9", "10", "11", "12", "13", "14")
HUNYUAN21_RAW_NODES = ("15", "16")

# Piece "review" camera: key -> type. Only for recipes with a "review" stage.
REVIEW_KEYS = {
    "pitch": "number",       # degrees above the horizon
    "directions": "int",     # number of directions around the figure
    "figure_height": "int",  # height of the small figure in pixels
    "front": "bool",         # also one large front view
}


def stages_of(recipe):
    return CATALOGUE.get(recipe, {})


def entry_option(entry):
    """The option name of a piece's `stages` entry (a name or {"option", "settings"})."""
    if isinstance(entry, dict):
        return entry.get("option")
    return entry


def entry_settings(entry):
    if isinstance(entry, dict) and isinstance(entry.get("settings"), dict):
        return entry["settings"]
    return {}


def chosen_options(recipe, piece):
    """{stage: option} of a piece: the defaults, the piece's "stages", minus its "skip"."""
    skip = set(piece.get("skip", []))
    picks = piece.get("stages", {})
    out = {}
    for stage, st in stages_of(recipe).items():
        if stage in skip:
            continue
        out[stage] = (entry_option(picks[stage]) if stage in picks else None) or st["default"]
    return out


def chosen_settings(recipe, piece):
    """{stage: {setting: value}} the piece gives for its chosen (not skipped) stages."""
    picks = piece.get("stages", {})
    return {stage: entry_settings(picks.get(stage)) for stage in chosen_options(recipe, piece)
            if entry_settings(picks.get(stage))}


def option(recipe, stage, name):
    return stages_of(recipe)[stage]["options"][name]


def allowed_params(recipe, piece):
    names = set(RECIPE_PARAMS.get(recipe, ()))
    for stage, opt in chosen_options(recipe, piece).items():
        names.update(stages_of(recipe)[stage]["options"].get(opt, {}).get("params", ()))
    return names


def allowed_inputs(recipe, piece):
    names = set(RECIPE_INPUTS.get(recipe, ()))
    for stage, opt in chosen_options(recipe, piece).items():
        names.update(stages_of(recipe)[stage]["options"].get(opt, {}).get("inputs", ()))
    return names


def tools_needed(recipe, piece):
    """Configuration keys the chosen stages of a piece need on this machine."""
    keys = set()
    for stage, opt in chosen_options(recipe, piece).items():
        keys.update(stages_of(recipe)[stage]["options"].get(opt, {}).get("tools", ()))
    return sorted(keys)


# --- settings

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def setting_problem(decl, v):
    """None when v is a valid value of a setting declaration, else what it must be."""
    kind = decl["type"]
    if kind == "seed":
        return None if _is_int(v) and 0 <= v <= SEED_MAX else "a whole number from 0 to %d" % SEED_MAX
    if kind == "bool":
        return None if isinstance(v, bool) else "true or false"
    if kind == "combo":
        return None if isinstance(v, str) and v.strip() else "a name (text)"
    lo, hi = decl.get("min"), decl.get("max")
    if kind == "int":
        ok = _is_int(v)
        what = "a whole number"
    else:
        ok = _is_number(v)
        what = "a number"
    if lo is not None and hi is not None:
        what += " from %s to %s" % (lo, hi)
    elif lo is not None:
        what += " of at least %s" % lo
    elif hi is not None:
        what += " of at most %s" % hi
    step = decl.get("step")
    if step:
        what += " (a multiple of %s)" % step
    if not ok or (lo is not None and v < lo) or (hi is not None and v > hi) or (step and v % step):
        return what
    return None


def settings_problems(fields, settings):
    """Problems of the settings an order gives to one option: [(setting, text)]."""
    declared = fields.get("settings", {})
    out = []
    for name, v in settings.items():
        if name not in declared:
            out.append((name, "unknown setting (settings: %s)" % (", ".join(sorted(declared)) or "none")))
            continue
        p = setting_problem(declared[name], v)
        if p:
            out.append((name, "must be %s, not %r" % (p, v)))
    return out


def setting_targets(fields, settings, drop=()):
    """({"<node>.<input>": value}, {arg: value}) where the given settings land. A setting
    with several targets skips those on nodes the job drops (TRELLIS.2's "sampler" while
    another stage paints: the texture sampler is gone); a setting whose every target is
    dropped keeps them, so the order check names it."""
    sets, args = {}, {}
    declared = fields.get("settings", {})
    for name in sorted(settings):
        if name not in declared:
            raise ValueError("unknown setting %r (settings: %s); check the order with -Check" % (
                name, ", ".join(sorted(declared)) or "none"))
        decl = declared[name]
        targets = list(decl.get("to", ()))
        kept = [k for k in targets if k.split(".", 1)[0] not in drop]
        if len(targets) > 1 and kept:
            targets = kept
        for key in targets:
            sets[key] = settings[name]
        if decl.get("arg"):
            args[decl["arg"]] = settings[name]
    return sets, args


def job_stage(job, recipe_stages):
    """The stage of a recipe a job belongs to: its "stage" key, else the stage whose chosen
    option uses the job's workflow (comfy) or tool (local_gpu)."""
    if job.get("stage"):
        return job["stage"]
    for stage, fields in recipe_stages.items():
        if job.get("kind", "comfy") == "comfy" and fields.get("workflow") and fields["workflow"] == job.get("workflow"):
            return stage
        if fields.get("tool") and fields["tool"] == job.get("tool"):
            return stage
    return None


def apply_settings(recipe, piece, jobs):
    """Put the piece's stage settings into its jobs ("set" for comfy jobs, "args" for local
    tools). The order check has validated them; a job's own "set" keys are overridden."""
    given = chosen_settings(recipe, piece)
    if not given:
        return jobs
    chosen = {stage: option(recipe, stage, opt) for stage, opt in chosen_options(recipe, piece).items()}
    for j in jobs:
        stage = job_stage(j, chosen)
        if stage not in given:
            continue
        sets, args = setting_targets(chosen[stage], given[stage], j.get("drop", ()))
        if sets:
            j["set"] = dict(j.get("set", {}), **sets)
        if args:
            j["args"] = dict(j.get("args", {}), **args)
        j["stage"] = stage
    return jobs
