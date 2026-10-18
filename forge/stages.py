"""Stage catalogue: for each recipe, the stages an order may tune piece by piece.

A recipe with swappable stages lists them here: each stage has a default option, may be
skippable, and each option says how it runs. An order picks options in a piece's "stages"
({stage: option}) and skips stages in "skip" ([stage, ...]). A new model becomes a new
option of a stage and is then available to every recipe that has that stage.

Option fields (all optional except kind):
  kind      "comfy" (a ComfyUI workflow), "local" (no GPU) or "local_gpu" (a local tool
            that uses the card)
  workflow  file of forge/workflows (comfy)
  group     scheduling and estimate group
  heavy     True: goes alone in the server queue after /free
  roles     {node id: output role} of the workflow
  params    names of the piece "params" the option reads
  inputs    names of the piece "inputs" the option reads
  tools     configuration keys (forge.config.KEYS) the option needs on this machine

The core recipes (concept, illustration, material, object_3d, prop, vehicle, unit) have no
swappable stages: their catalogue is empty and only their own params and inputs apply.
"""

# recipe -> stage -> {"default": option, "skippable": bool, "options": {option: fields}}
CATALOGUE = {
    "concept": {},
    "illustration": {},
    "material": {},
    "object_3d": {},
    "prop": {},
    "vehicle": {},
    "unit": {},
}

# Piece "params": name -> (type, meaning). "int" and "number" must be greater than zero.
PARAMS = {
    "tris": ("int", "triangle (face) budget of a mesh"),
    "texture": ("int", "texture size in pixels"),
    "height": ("number", "height of a figure in metres"),
    "head": ("number", "head scale"),
    "hands": ("number", "hand scale"),
    "feet": ("number", "foot scale"),
    "widen": ("number", "body width scale"),
}

# Params and inputs a recipe reads itself (besides those of its stage options).
RECIPE_PARAMS = {
    "object_3d": ("tris", "texture"),
    "prop": ("tris", "texture"),
    "vehicle": ("tris", "texture"),
    "unit": ("tris", "texture"),
}
RECIPE_INPUTS = {
    "object_3d": ("reference",),
    "prop": ("reference",),
}

# Nodes of forge/workflows/trellis2_mesh.json that only the texture bake uses: a job that
# lists them in "drop" makes the untextured mesh alone (the shape for another paint stage).
TRELLIS2_TEXTURE_NODES = ("12", "93", "98", "118", "147", "196", "210", "238", "247", "322")

# Piece "review" camera: key -> type. Only for recipes with a "review" stage.
REVIEW_KEYS = {
    "pitch": "number",       # degrees above the horizon
    "directions": "int",     # number of directions around the figure
    "figure_height": "int",  # height of the small figure in pixels
    "front": "bool",         # also one large front view
}


def stages_of(recipe):
    return CATALOGUE.get(recipe, {})


def chosen_options(recipe, piece):
    """{stage: option} of a piece: the defaults, the piece's "stages", minus its "skip"."""
    skip = set(piece.get("skip", []))
    picks = piece.get("stages", {})
    out = {}
    for stage, st in stages_of(recipe).items():
        if stage in skip:
            continue
        out[stage] = picks.get(stage, st["default"])
    return out


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
