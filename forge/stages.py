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
  type      "int" (whole number), "number", "seed" (whole number 0 to 2**64-1), "bool",
            "combo" (a name from the server's list for that input: checked by the
            environment check, forge.readiness) or "choice" (one of "choices", checked here)
  min, max  inclusive range ("int", "number")
  choices   the names a "choice" setting may take
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
# The glow of the humanoid and creature exporters (tools/blender/glow_regions.py): by default
# the colours the piece's text names as glowing ("glowing cyan seams", forge/recipes.py
# glow_colours) light up on the painted texture; nothing glows when the text names none.
GLOW_SETTINGS = {
    "glow": {"type": "bool", "arg": "glow",
             "meaning": "false: nothing glows; true: the glow also when the piece's text names no glowing colour "
                        "(then the hues 165-225, cyan to blue)"},
    "glow_colours": {"type": "colours", "arg": "glow_colours",
                     "meaning": "the glowing colours (names such as cyan, emerald, violet, magenta, orange, or "
                                "low-high hue degrees, comma separated) instead of those the piece's text names"},
    "glow_strength": {"type": "number", "min": 0.0, "max": 100.0, "arg": "glow_strength",
                      "meaning": "emissive strength of what glows (default 2)"},
    "glow_value": {"type": "number", "min": 0.0, "max": 1.0, "arg": "glow_value",
                   "meaning": "a light's brightest texels: value (max of R, G, B) at least this (default 0.42)"},
    "glow_chroma": {"type": "number", "min": 0.0, "max": 1.0, "arg": "glow_chroma",
                    "meaning": "a light's brightest texels: chroma (max - min of R, G, B) at least this (default 0.30)"},
    "glow_max_region": {"type": "number", "min": 0.0, "max": 1.0, "arg": "glow_max_region",
                        "meaning": "the largest glowing region, as a share of the texture (default 0.006)"},
}

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
        # Hunyuan3D Paint's views with the cutout projected (forge/paint_views.py): three jobs,
        # <variant>/paint_views (the views, ComfyUI), <variant>/paint_concept (the cutout laid on
        # them, local) and <variant>/paint (their bake, ComfyUI: painted_mesh, albedo,
        # metal_roughness, as hunyuan_paint's).
        "hunyuan_concept": {
            "kind": "comfy", "workflow": "hunyuan21_paint_views.json", "group": "hunyuan_paint", "heavy": True,
            "roles": {"14": "views", "15": "mr_views", "16": "positions", "17": "normals"},
            "bake_workflow": "hunyuan21_bake_views.json", "bake_group": "hunyuan_bake",
            "bake_roles": {"12": "albedo", "13": "metal_roughness"}, "mesh_role": "painted_mesh",
            "outputs": ("painted_mesh",), "params": ("texture",),
            "meaning": "Hunyuan3D Paint's six views, the cutout itself laid on the "
                       "view that shows what it shows where the surface faces its camera (when its silhouette fits "
                       "that view: an orthographic cutout such as a held item's side view or a figure's front), "
                       "enlarged and baked (forge/paint_views.py)",
            "settings": {
                "steps": {"type": "int", "min": 1, "max": 100, "to": ["9.steps"], "meaning": "steps of the Paint views"},
                "guidance": {"type": "number", "min": 1.0, "max": 10.0, "to": ["9.guidance_scale"],
                             "meaning": "guidance of the Paint views"},
                "seed": {"type": "seed", "to": ["9.seed"],
                         "meaning": "seed of the Paint views (its diffusion noise is fixed by the node: see OVERRIDDEN)"},
                "view_size": {"type": "int", "min": 512, "max": 1024, "step": 256, "to": ["9.view_size"],
                              "meaning": "size of the Paint views (default 768)"},
                "bake_view_size": {"type": "int", "min": 512, "max": 4096, "step": 256, "arg": "size",
                                   "meaning": "size the views are enlarged to before the bake (default 2048)"},
                "match_colour": {"type": "bool", "arg": "match_colour",
                                 "meaning": "also bring every view's colours to the cutout's (default false)"},
                "min_fit": {"type": "number", "min": 0.0, "max": 1.0, "arg": "min_fit",
                            "meaning": "the least overlap (IoU) of the cutout's silhouette with a view for the cutout "
                                       "to be laid on it (default 0.88; 1 never lays it)"},
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
            "tools": ("blender_python",), "inputs": ("clips", "items", "bone_map", "body"),
            "params": ("tris", "texture", "height", "head", "hands", "feet", "widen", "image_format"),
            "settings": {
                "normal_map": {"type": "int", "min": 256, "max": 8192, "arg": "normal_map",
                               "meaning": "size of the baked normal map (default: params.texture)"},
                **GLOW_SETTINGS,
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
                # With inputs.body only (the conform's flags: tools/blender/humanoid_fit.py).
                "conform_joint_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "conform_joint_cm",
                                     "meaning": "conform flag: a body joint this far (cm) from the conformed "
                                                "variant's (default 4)"},
                "conform_outside_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "conform_outside_cm",
                                       "meaning": "conform flag: a body joint this far (cm) outside the outfit "
                                                  "(default 1.5)"},
                "conform_scale": {"type": "number", "min": 1.0, "max": 10.0, "arg": "conform_scale",
                                  "meaning": "conform flag: a segment stretched or shrunk by more than this factor "
                                             "(default 1.5)"},
                "conform_torn_share": {"type": "number", "min": 0.0, "max": 1.0, "arg": "conform_torn_share",
                                       "meaning": "conform flag: this share of the edges stretched over twice or "
                                                  "under half their length (default 0.002)"},
                "conform_floor_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "conform_floor_cm",
                                     "meaning": "conform flag: the outfit's soles this far (cm) below or above the "
                                                "body's floor (default 4)"},
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

# The review sheet of the articulated and creature recipes: the factory lays the sheet out
# (forge/sheet_layout.py) and Godot renders the cells (tools/review_godot/sheet.gd).
LAYOUT_SHEET = {
    "kind": "local_gpu", "tool": "sheet", "group": "local_gpu", "outputs": ("sheet", "layout"), "tools": ("godot",),
    "settings": {
        "front_height": {"type": "int", "min": 64, "max": 2048, "arg": "front_height",
                         "meaning": "height of the large front view in pixels"},
        "strip_frames": {"type": "int", "min": 1, "max": 32, "arg": "strip_frames",
                         "meaning": "frames of each strip (a joint's sweep or a clip)"},
    }}
_FRONT_STAGES = {stage: dict(HUMANOID_OPTIONS[stage]) for stage in ("reference", "cutout", "mesh", "paint")}

# Options of the articulated recipe (forge/recipes.py recipe_articulated): the whole object
# is generated, then split into parts on pivots.
ARTICULATED_OPTIONS = dict(_FRONT_STAGES, **{
    "parts": {
        "region_split": {
            "kind": "local_gpu", "tool": "parts", "group": "local_gpu",
            "outputs": ("glb", "model", "mesh_report", "frame"), "tools": ("blender_python",),
            "params": ("tris", "height", "length", "front", "image_format", "normal_map", "crease", "relief"),
            "meaning": "the painted mesh turned to face +Z, scaled, smooth by angle with a normal map baked from the "
                       "raw mesh, cut into the order's parts by their regions, "
                       "each cut closed, each part's origin at its pivot, one node per part",
            "settings": {
                "min_share": {"type": "number", "min": 0.0, "max": 0.5, "arg": "min_share",
                              "meaning": "a part with less of the surface than this share is flagged as tiny "
                                         "(default 0.002)"},
                "snap": {"type": "bool", "arg": "snap",
                         "meaning": "square a footprint that came out turned by up to 30 degrees (default true)"},
                "max_width": {"type": "number", "min": 0.1, "max": 10.0, "arg": "max_width",
                              "meaning": "the widest the footprint may be, as a share of its length front to back: a "
                                         "wider one is flagged as facing sideways (default: not checked)"},
            }},
    },
    "review": {"layout_sheet": LAYOUT_SHEET},
})
ARTICULATED_DEFAULTS = {"reference": "qwen21_base", "cutout": "birefnet", "mesh": "trellis2", "paint": "hunyuan_paint",
                        "parts": "region_split", "review": "layout_sheet"}
ARTICULATED_SKIPPABLE = ("paint",)

# Options of the creature recipe (forge/recipes.py recipe_creature): as the humanoid's up to
# the rig; the export maps the predicted bones onto the order's body plan and makes the
# order's motions.
CREATURE_OPTIONS = dict(_FRONT_STAGES, **{
    "pose": HUMANOID_OPTIONS["pose"],
    "rig": HUMANOID_OPTIONS["rig"],
    "export": {
        "creature_export": {
            "kind": "local_gpu", "tool": "creature_export", "group": "local_gpu",
            "outputs": ("glb", "model", "regions_mask", "bone_map", "mesh_report", "motion_report", "clips"),
            "tools": ("blender_python",), "inputs": ("clips", "bone_map"),
            "params": ("tris", "height", "length", "front", "image_format"),
            "settings": {
                **GLOW_SETTINGS,
                "foot_slide_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "foot_slide_cm",
                                  "meaning": "motion report threshold: foot slide in cm"},
                "reach_shortfall_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "reach_shortfall_cm",
                                       "meaning": "motion report threshold: IK reach shortfall in cm"},
                "stretch_p95": {"type": "number", "min": 0.0, "max": 10.0, "arg": "stretch_p95",
                                "meaning": "motion report threshold: 95th percentile of edge stretch"},
                "below_ground_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "below_ground_cm",
                                    "meaning": "motion report threshold: deepest vertex below the ground in cm"},
            }},
    },
    "review": {"layout_sheet": LAYOUT_SHEET},
})
CREATURE_DEFAULTS = {"reference": "qwen21_base", "pose": "qwen_edit_2511", "cutout": "birefnet", "mesh": "trellis2",
                     "paint": "hunyuan_paint", "rig": "skintokens", "export": "creature_export",
                     "review": "layout_sheet"}
CREATURE_SKIPPABLE = ("pose", "paint")
# Options of the held-item recipe (forge/recipes.py recipe_held_item): the object generated
# from a strict side view, put in item space with its sockets (the socket file the humanoid
# exporter reads), then held by a reference figure for the review.
ITEM_AXES = ("auto", "+x", "-x", "+y", "-y", "+z", "-z")
HELD_ITEM_OPTIONS = dict(_FRONT_STAGES, **{
    "item": {
        "item_frame": {
            "kind": "local_gpu", "tool": "item", "group": "local_gpu",
            "outputs": ("glb", "sockets", "model", "mesh_report", "frame", "socket_map"), "tools": ("blender_python",),
            "params": ("tris", "height", "length", "image_format", "normal_map", "crease", "relief"),
            "meaning": "the painted mesh put in item space (front +x, top +z, the grip at the origin) at its real size, "
                       "its frame found from the cutout's silhouette (and a gun's grip and barrel), squared when long "
                       "and straight, its sockets proposed from the class template, and the socket file",
            "settings": {
                "front": {"type": "choice", "choices": ITEM_AXES, "arg": "front",
                          "meaning": "the glTF axis the generated item's front points along (default auto: found "
                                     "from the reference picture's silhouette; give front and top to fix them)"},
                "top": {"type": "choice", "choices": ITEM_AXES, "arg": "top",
                        "meaning": "the glTF axis the generated item's top points along (default auto)"},
                "snap": {"type": "bool", "arg": "snap",
                         "meaning": "square the item when it came out turned (to the reference's silhouette, 3 to 30 "
                                    "degrees; without one, to its principal axes, 8 to 30) (default: the class template's)"},
                "tolerance_cm": {"type": "number", "min": 0.0, "max": 20.0, "arg": "tolerance_cm",
                                 "meaning": "how far from the surface a socket may lie before it is flagged off the "
                                            "mesh, outside its cross-section (default 1)"},
            }},
    },
    "hold": {
        "figure_hold": {
            "kind": "local_gpu", "tool": "hold", "group": "local_gpu",
            "outputs": ("glb", "model", "mesh_report", "motion_report", "bone_map", "clips", "figure"),
            "tools": ("blender_python",), "inputs": ("figure", "clips"),
            "meaning": "the humanoid exporter run on the order's reference figure with this variant's item and the "
                       "class's clips of the order's motion library (for the review sheet)",
            "settings": {
                "height": {"type": "number", "min": 0.3, "max": 4.0, "arg": "height",
                           "meaning": "the reference figure's height in metres (default 1.8)"},
                "hand_socket_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "hand_socket_cm",
                                   "meaning": "motion report threshold: wrist to socket in cm"},
                "penetration_cm": {"type": "number", "min": 0.0, "max": 100.0, "arg": "penetration_cm",
                                   "meaning": "motion report threshold: body inside the item in cm"},
            }},
    },
    "review": {
        "item_sheet": {
            "kind": "local_gpu", "tool": "item_sheet", "group": "local_gpu", "outputs": ("sheet", "layout"),
            "tools": ("godot",),
            "settings": {
                "side_width": {"type": "int", "min": 128, "max": 2048, "arg": "side_width",
                               "meaning": "width of the large side view with the sockets in pixels (default 640)"},
                "hold_height": {"type": "int", "min": 64, "max": 1024, "arg": "hold_height",
                                "meaning": "height of each held-pose figure in pixels (default twice figure_height)"},
            }},
    },
})
HELD_ITEM_DEFAULTS = {"reference": "qwen21_base", "cutout": "birefnet", "mesh": "trellis2", "paint": "hunyuan_paint",
                      "item": "item_frame", "hold": "figure_hold", "review": "item_sheet"}
HELD_ITEM_SKIPPABLE = ("paint", "hold")
# Options of the iso_tile recipe (forge/tile_jobs.py recipe_iso_tile): a sprite of an isometric
# tile grid remade from the game's sprite. Floors and walls: the face taken out of the sprite,
# repainted flat, its border joined to its seam group's material and put back on the exact face;
# objects: the sprite repainted (a picture cut back into the grid) or meshed and rendered.
TILE_SPRITE_SETTINGS = {
    "max_overflow": {"type": "number", "min": 0.0, "max": 1.0, "arg": "max_overflow",
                     "meaning": "footprint flag: the largest share of the sprite outside the original's silhouette "
                                "(widened by tolerance) (default 0.04 for floors and walls, 0.08 for objects)"},
    "min_cover": {"type": "number", "min": 0.0, "max": 1.0, "arg": "min_cover",
                  "meaning": "footprint flag: the least share of the original's silhouette the sprite covers "
                             "(default 0.85 for floors and walls, 0.6 for objects)"},
    "tolerance": {"type": "number", "min": 0.0, "max": 8.0, "arg": "tolerance",
                  "meaning": "grid pixels the original's silhouette is widened by before the overflow is measured "
                             "(default 1)"},
    "max_seam": {"type": "number", "min": 1.0, "max": 20.0, "arg": "max_seam",
                 "meaning": "seam flag: how many times more a texture's opposite edges may differ than neighbouring "
                            "texel rows (default 2.5)"},
    "max_shading": {"type": "number", "min": 0.0, "max": 2.0, "arg": "max_shading",
                    "meaning": "painted-light flag: the largest brightness slope across the albedo (a face's) or "
                               "toward one side of the object, as a share of its mean, that the original's own "
                               "slope does not have (default 0.35 for floors and walls, 0.6 for objects)"},
    "normal_strength": {"type": "number", "min": 0.0, "max": 10.0, "arg": "normal_strength",
                        "meaning": "how steep the relief the albedo's detail gives the normal map (default 1)"},
    "exposure": {"type": "choice", "choices": ("original", "none"), "arg": "exposure",
                 "meaning": "original (default): the shaded sprite's mean brightness brought to the original's "
                            "(0.6 to 1.6 times); none: as lit"},
    "light_key": {"type": "number", "min": 0.0, "max": 2.0, "arg": "light_key",
                  "meaning": "strength of the shaded sprite's key light, from the upper left (default 0.55)"},
    "light_fill": {"type": "number", "min": 0.0, "max": 2.0, "arg": "light_fill",
                   "meaning": "strength of the fill light, from the right (default 0.15)"},
    "light_ambient": {"type": "number", "min": 0.0, "max": 2.0, "arg": "light_ambient",
                      "meaning": "the shaded sprite's ambient light (default 0.5)"},
    "cavity": {"type": "number", "min": 0.0, "max": 2.0, "arg": "cavity",
               "meaning": "how much the relief's hollows are darkened in the shaded sprite (default 0.6)"},
    "base_ao": {"type": "number", "min": 0.0, "max": 1.0, "arg": "base_ao",
                "meaning": "how much a wall darkens toward its foot in the shaded sprite (default 0.18)"},
    "glow_strength": {"type": "number", "min": 0.0, "max": 1.0, "arg": "glow_strength",
                      "meaning": "the emissive frame's brightness (default 1)"},
    "saturation": {"type": "number", "min": 0.0, "max": 1.0, "arg": "saturation",
                   "meaning": "the share of the way the albedo's mean saturation is brought toward the original's, "
                              "only up, at most 1.5 times (default 0.5); a seam group's faces take their group's gain"},
    "sharpen": {"type": "number", "min": 0.0, "max": 2.0, "arg": "sharpen",
                "meaning": "an unsharp mask on the albedo at the frame's scale (default 0.4)"},
    "fit": {"type": "choice", "choices": ("silhouette", "none"), "arg": "fit",
            "meaning": "silhouette (default): an object's picture scaled (0.8 to 1.25) and moved (up to 4 grid "
                       "pixels) to overlap the original's silhouette best; none: placed as the guide was"},
    "min_render_iou": {"type": "number", "min": 0.0, "max": 1.0, "arg": "min_render_iou",
                       "meaning": "render flag: the least overlap of a meshed object's silhouette with the "
                                  "original's (default 0.72)"},
    "mesh_albedo": {"type": "choice", "choices": ("picture", "paint"), "arg": "mesh_albedo",
                    "meaning": "picture (default): a meshed object's albedo is its picture fitted to the render's "
                               "silhouette (the paint where the picture does not reach); paint: the painted mesh's"},
    "delight": {"type": "number", "min": 0.0, "max": 1.0, "arg": "delight",
                "meaning": "how far the picture laid on a mesh has its sides' brightness evened (its painted light "
                           "taken out; the mesh's normals light it again) (default 0.7)"},
    "overlap": {"type": "number", "min": 0.0, "max": 2.0, "arg": "overlap",
                "meaning": "grid pixels a floor's or wall's frame is grown past its exact face (binary alpha), the "
                           "new pixels taking the nearest opaque colour, so neighbouring frames drawn smaller overlap "
                           "and no dark line shows between them (default 0.75 for floors and walls; 0 for objects and "
                           "faces cut by their picture)"},
    "max_busy": {"type": "number", "min": 0.5, "max": 5.0, "arg": "max_busy",
                 "meaning": "busy flag and calming: the most fine detail a repainted face of a seam group (against its "
                            "group's material) or an object with tile.match (against that floor) keeps, as a ratio of "
                            "their detail contrast (default 1.4)"},
    "calm": {"type": "number", "min": 0.0, "max": 1.0, "arg": "calm",
             "meaning": "the least factor a busy picture's fine detail is scaled by to bring it to max_busy (default "
                        "0.5; 1: never calmed, only flagged)"},
    "max_repeat": {"type": "number", "min": 0.0, "max": 1.0, "arg": "max_repeat",
                   "meaning": "repeat flag of a scattered floor (tile.scatter): the largest share of the block's forms "
                              "that repeats from tile to tile (default 0.45)"},
    "max_layout": {"type": "number", "min": 0.0, "max": 2.0, "arg": "max_layout",
                   "meaning": "layout flag of a repainted face: the largest mean colour distance of its broad forms "
                              "from its guide's (default 0.22; 0.12 with tile.trim)"},
}
ISO_TILE_OPTIONS = {
    "guide": {
        "unproject": {
            "kind": "local", "group": "local", "outputs": ("guide", "layout"), "inputs": ("sprite", "silhouette"),
            "meaning": "a floor's or wall's face taken out of the sprite into a flat texture (the seam group's "
                       "material detail laid over its colours); an object's sprite, or a block of it in the "
                       "piece's variant grid, upscaled onto grey",
            "settings": {
                "detail": {"type": "number", "min": 0.0, "max": 2.0, "arg": "detail",
                           "meaning": "how much of the seam group's material detail the face's guide gets "
                                      "(default 1)"},
                "blur": {"type": "number", "min": 0.0, "max": 4.0, "arg": "blur",
                         "meaning": "blur of the upscaled original, in its pixels (default 0.6: no pixel steps)"},
            }},
    },
    "picture": {
        "qwen_edit_2511": dict(HUMANOID_OPTIONS["pose"]["qwen_edit_2511"], settings=_edit_settings("picture")),
    },
    "edges": {
        "inpaint_ring": {
            "kind": "comfy", "workflow": "qwen_image_21_turbo_inpaint.json", "group": "qwen_inpaint",
            "roles": {"12": "rgb"}, "outputs": ("rgb",),
            "meaning": "the picture's border band replaced by the seam group's material and the ring between "
                       "them repainted (Qwen Image 2.1 Turbo inpaint)"},
    },
    "form": {
        "picture": {"kind": "local", "group": "local", "in_stage": "sprite",
                    "meaning": "an object is its picture, cut back into the grid's columns"},
        "mesh": {"kind": "local_gpu", "tool": "tile_render", "group": "local_gpu", "tools": ("blender_python",),
                 "params": ("tris", "texture"),
                 "meaning": "an object meshed (TRELLIS.2), painted (Hunyuan3D Paint) and rendered by the grid's "
                            "camera (Blender, CPU), fitted to the original's silhouette"},
    },
    "sprite": {
        "tile_sprite": {
            "kind": "local", "group": "local", "params": ("scale",),
            "outputs": ("tile", "mesh_report"),
            "meaning": "the frames at the order's scale (albedo, normal, shaded, emissive), the checks",
            "settings": TILE_SPRITE_SETTINGS},
    },
    "review": {
        "tile_review": {"kind": "local", "group": "local", "outputs": ("sheet",),
                        "meaning": "every variant beside the original, alone and repeated"},
    },
}
ISO_TILE_DEFAULTS = {"guide": "unproject", "picture": "qwen_edit_2511", "edges": "inpaint_ring", "form": "picture",
                     "sprite": "tile_sprite", "review": "tile_review"}
ISO_TILE_SKIPPABLE = ("picture", "edges")
STAGE_ORDER = {
    "articulated": ("reference", "cutout", "mesh", "paint", "parts", "review"),
    "creature": ("reference", "pose", "cutout", "mesh", "paint", "rig", "export", "review"),
    "held_item": ("reference", "cutout", "mesh", "paint", "item", "hold", "review"),
    "iso_tile": ("guide", "picture", "edges", "form", "sprite", "review"),
}

# recipe -> stage -> {"default": option, "skippable": bool, "options": {option: fields}}.
# The core recipes have one option per stage (the models they always used); the stages
# exist so that an order can give their settings.
_CONCEPT = {"default": "klein", "skippable": False, "options": {"klein": OPTIONS["klein"]}}
_MESH = {"default": "hunyuan21", "skippable": False, "options": {"hunyuan21": OPTIONS["hunyuan21"]}}
# The paint of the one-mesh recipes (object_3d, prop, vehicle): by default Hunyuan3D Paint inside the
# mesh job; hunyuan_concept drops the mesh job's Paint branch and paints its shape in three jobs.
_PAINT = {"default": "hunyuan21_inline", "skippable": False, "options": {
    "hunyuan21_inline": {"kind": "comfy", "group": "hy3d", "in_stage": "mesh", "outputs": ("painted_mesh",),
                         "meaning": "Hunyuan3D Paint inside the mesh job (hunyuan21_shape_paint.json: its paint_* "
                                    "settings are mesh settings)"},
    "hunyuan_concept": HUMANOID_OPTIONS["paint"]["hunyuan_concept"]}}
CATALOGUE = {
    "concept": {"concept": _CONCEPT},
    "illustration": {"concept": _CONCEPT},
    "material": {},
    "object_3d": {"concept": _CONCEPT, "mesh": _MESH, "paint": _PAINT},
    "prop": {"concept": _CONCEPT, "mesh": _MESH, "paint": _PAINT},
    "vehicle": {"concept": _CONCEPT, "mesh": _MESH, "paint": _PAINT},
    "figure": {"concept": _CONCEPT, "mesh": _MESH},
    "humanoid": {stage: {"default": HUMANOID_DEFAULTS[stage], "skippable": stage in HUMANOID_SKIPPABLE,
                         "options": opts} for stage, opts in HUMANOID_OPTIONS.items()},
    "articulated": {stage: {"default": ARTICULATED_DEFAULTS[stage], "skippable": stage in ARTICULATED_SKIPPABLE,
                            "options": ARTICULATED_OPTIONS[stage]} for stage in STAGE_ORDER["articulated"]},
    "creature": {stage: {"default": CREATURE_DEFAULTS[stage], "skippable": stage in CREATURE_SKIPPABLE,
                         "options": CREATURE_OPTIONS[stage]} for stage in STAGE_ORDER["creature"]},
    "held_item": {stage: {"default": HELD_ITEM_DEFAULTS[stage], "skippable": stage in HELD_ITEM_SKIPPABLE,
                          "options": HELD_ITEM_OPTIONS[stage]} for stage in STAGE_ORDER["held_item"]},
    "iso_tile": {stage: {"default": ISO_TILE_DEFAULTS[stage], "skippable": stage in ISO_TILE_SKIPPABLE,
                         "options": ISO_TILE_OPTIONS[stage]} for stage in STAGE_ORDER["iso_tile"]},
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
    "hunyuan21_paint_views.json": {"9.seed": PAINT_SEED},
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
    "length": ("number", "length of an object or creature in metres, front to back (scales it instead of height)"),
    "front": ("choice", "the glTF axis the generated mesh's front points along (+z: toward the reference's camera); "
                        "the exported model faces +Z"),
    "scale": ("int", "art pixels per pixel of the tile grid (iso_tile; default 4)"),
    "normal_map": ("int", "normal map size in pixels of the parts and held items (default: the base colour's, at "
                          "most 4096)"),
    "crease": ("number", "the parts' and held items' crease angle in degrees: edges sharper stay hard (default 60)"),
    "relief": ("number", "strength of the painted texture's relief in the parts' and held items' normal map "
                         "(default 3)"),
}
PARAM_CHOICES = {"image_format": ("lossless", "compact"), "front": ("+z", "-z", "+x", "-x")}

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
# Piece keys only some recipes read (the order check refuses them elsewhere).
RECIPE_KEYS = {"parts": ("articulated",), "body": ("creature",), "motions": ("creature", "humanoid"),
               "item": ("held_item",), "tile": ("iso_tile",)}
# The tools of the humanoid motions' text source (HY-Motion Lite, forge/motion_text.py).
MOTION_TEXT_TOOLS = ("hymotion_python", "hymotion_dir", "hymotion_model", "hymotion_text_encoder", "hymotion_clip")
# The export settings that only a piece wearing an approved body (inputs.body) uses.
CONFORM_SETTINGS = ("conform_joint_cm", "conform_outside_cm", "conform_scale", "conform_torn_share",
                    "conform_floor_cm")
# Inputs that are folders (they enter the job specification by the files inside them);
# every other input is a file.
FOLDER_INPUTS = ("items",)
# Inputs a piece may set to null to drop its family's default (a piece that wears no body).
NULLABLE_INPUTS = ("body",)
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
    opts = chosen_options(recipe, piece)
    for stage, opt in opts.items():
        keys.update(stages_of(recipe)[stage]["options"].get(opt, {}).get("tools", ()))
    if recipe == "humanoid" and "export" in opts and isinstance(piece.get("motions"), list) and any(
            isinstance(m, dict) and m.get("source") == "text" for m in piece["motions"]):
        keys.update(MOTION_TEXT_TOOLS)
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
    if kind == "choice":
        return None if v in decl["choices"] else "one of %s" % ", ".join(decl["choices"])
    if kind == "colours":
        # Glow colours: names or "low-high" hue degrees (tools/blender/glow_regions.py).
        if not (isinstance(v, str) and v.strip()) and not (
                isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v)):
            return "glow colours: a comma-separated text or a list of names or low-high degrees"
        from .tool_specs import glow_colours_problem
        p = glow_colours_problem(v)
        return "glow colours (%s)" % p if p else None
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
        # A stage of several jobs: each says what it takes ("set", "args"; hunyuan_concept)
        if "takes" in j:
            sets = sets if "set" in j["takes"] else {}
            args = args if "args" in j["takes"] else {}
        if sets:
            j["set"] = dict(j.get("set", {}), **sets)
        if args:
            j["args"] = dict(j.get("args", {}), **args)
        j["stage"] = stage
    return jobs
