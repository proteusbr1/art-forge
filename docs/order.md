# The order

One JSON file per order, written by the game's operator (a person or an LLM agent working
from the game's art list) and kept in the game's own repository. Keys are in English. It
keeps the job-list format of the factory core (`version`, `style`, `families`, `assets`,
`tasks`) and adds per-piece keys (`recipe`, `variants`, `stages`, `skip`, `params`,
`inputs`, `review`). Families are in priority order; each piece (an entry of a family's
`assets`) expands into jobs through its recipe.

Check an order before a night's run:

    .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Check
    .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Estimate

`-Check` prints two results.

1. **The order** (`forge.order.check_order`, without the server): `order: valid`, or every
   problem it finds. It checks the format, known recipes and stage options, stage settings
   (known names, types and ranges, settings that land on no job), valid `skip` names,
   `variants`, `params` types, that `inputs` files exist, that the style texts the recipes
   need are present, that the tools the chosen stages need are set in `forge.local.json`
   and exist, and that every `set` lands on a node the job keeps.
2. **The environment** (`forge.readiness.check_environment`), only when the order is
   valid: `environment: verified`; `environment: not ready` with the list of what is
   missing; or `environment: not verified` when the server does not answer (local
   validation done, environment not checked; never reported as ready). It checks that the
   server has every node class the jobs' workflows use; that every model file a workflow
   names is in its loader's option list; that every setting or `set` value that lands on a
   list input (a sampler, a scheduler) is in the server's list; that the local tools the
   chosen stages need are present with their weight files (SkinTokens checkpoints, UniRig
   weights); and it records the versions of Blender (`bpy`) and Godot when they are
   configured, and who holds the GPU lock.

The server is only asked for `GET /object_info`; nothing is sent and nothing is loaded.
Exit code: 0 (valid and verified), 1 (order problems or not ready), 3 (not verified).
Complete samples that pass the check:
[tests/fixtures/order_generic.json](../tests/fixtures/order_generic.json) (a material, an
object and a concept) and [tests/fixtures/order_humanoid.json](../tests/fixtures/order_humanoid.json)
(two humanoids; see [the humanoid recipe](#the-humanoid-recipe)).

## Top level

| Key | Meaning |
|---|---|
| `version` | format version, `1` (required) |
| `note` | free comment |
| `style` | shared style texts (table below) |
| `families` | list of families (required) |
| `tasks` | optional raw jobs (below) |

`style` (each key is an English text):

| Key | Used by |
|---|---|
| `global` | every concept (unit, vehicle, object_3d, concept, illustration, prop) and every humanoid reference |
| `view_unit`, `view_object`, `view_prop`, `view_vehicle`, `view_illustration` | the default view of each recipe (required when the order has a piece of that recipe) |
| `view_humanoid` | the view of every humanoid reference, and the text of its pose and cutout edits unless the next two are given (required when the order has a humanoid). It describes only what should be seen: full body in an A-pose, open empty hands held away from the body, plain grey background, even light |
| `pose_humanoid`, `cutout_humanoid` | optional: the text of the humanoid's pose edit and of its `qwen_edit_character` cutout (what the edited picture shows) |
| other `view_*` (for example `view_creature`, `view_icon`) | when a family or piece asks for that `"view"` |
| `material_top`, `material_front` | materials seen from above (default) or from the front |
| other `material_*` (for example `material_sky`) | a material whose `"view"` names it |
| `material_look` | appended to every material |
| `material_negative` | negative text of every material (required when there is a material) |
| `emissive` | the emissive-mask request (required with a vehicle, or a unit with `emissive`) |
| `tier_<level>` | one per level the pieces use (for example `tier_basic`, `tier_advanced`) |

## Family

| Key | Meaning |
|---|---|
| `id` | family id (becomes a folder; no slashes) |
| `assets` | list of pieces |
| `recipe` | default recipe of its pieces |
| `style` | text appended to the family's concepts |
| `material_style` | text appended to the family's materials |
| `view` | `style` key used as the view instead of the recipe's default |
| `seeds` | inherited: number of concept seeds (`variants` replaces it) |
| `material_seeds` | inherited: number of material seeds (default 4; `variants` replaces it) |
| `size` | `[width, height]` of the concepts |
| `3d` | `false` makes a `prop` stop at the concept |
| `note` | free comment |

A family may also set any per-piece key (`recipe`, `variants`, `stages`, `skip`,
`params`, `inputs`, `review`) as a default for its pieces. The objects (`stages`,
`params`, `inputs`, `review`) merge key by key with the piece's own (the piece wins); the
other keys apply when the piece does not set them.

## Piece

Inherited keys:

| Key | Meaning |
|---|---|
| `id` | piece id (becomes a folder; no slashes) |
| `brief` | the art-direction text of the piece (required) |
| `recipe` | recipe (else the family's) |
| `tier` | a level of the piece; uses `style["tier_" + tier]` |
| `view` | concepts: a `style` key; materials: `front`, `top` or a `material_*` key |
| `seeds` | number of seeds (`variants` replaces it) |
| `size` | `[width, height]` of the concept |
| `emissive` | `true`: a unit also makes the emissive mask |
| `3d` | `false`: a prop without a mesh |
| `faces` | maximum faces of the mesh (`params.tris` replaces it) |
| `texture_size` | Hunyuan Paint texture (default 2048, vehicle 4096; `params.texture` replaces it) |
| `paint_view_size` | size of the Paint views (default 768) |
| `repeat_vertical` | a front material that also repeats vertically |
| `horizontal_only` | a material that repeats only horizontally |
| `note` | free comment |

New keys:

| Key | Meaning |
|---|---|
| `variants` | how many variants (seeds) run; a whole number of at least 1 |
| `stages` | per-stage choice: `{stage: option}` or `{stage: {"option": option, "settings": {setting: value}}}` from the recipe's catalogue (below) |
| `skip` | list of stages to skip (only stages the catalogue marks skippable) |
| `params` | piece settings (table below) |
| `inputs` | files the game supplies `{name: path}`; paths are absolute or relative to the order file |
| `review` | review-sheet camera, for recipes with a review stage (table below) |

`review` does not apply to the core recipes. The stage catalogue is `forge/stages.py`.

### Stages and settings

A `stages` entry is an option name, or an object with the option (default: the stage's
default) and its named settings:

    "stages": {"mesh": {"option": "hunyuan21", "settings": {"shape_steps": 40, "paint_guidance": 2.5}},
               "concept": {"settings": {"steps": 6, "sampler": "euler"}}}

A family may set `stages` for its pieces; a piece's entry for a stage replaces the
family's entry for that stage whole. Each setting lands on `<node>.<input>` of the
option's workflow (the job's `set`) or on an argument of its tool (the job's `args`), so
it enters the job's specification hash like any value: changing one setting redoes only
the jobs of that stage and what depends on them. An unknown setting, a value of the wrong
type or out of range, settings of a skipped stage and settings that land on no job of the
piece are problems of the order. A `combo` setting (a sampler or scheduler name) is
checked against the server's list by the environment check.

Types: `int` (whole number in its range, sometimes a multiple of a step), `number`,
`seed` (whole number from 0 to 2^64 - 1), `bool`, `combo` (a name).

The core recipes have one option per stage (the models they always used): stage
`concept` (option `klein`) in `concept`, `illustration`, `object_3d`, `prop`, `vehicle`
and `unit`; stage `mesh` (option `hunyuan21`) in `object_3d`, `prop`, `vehicle` and
`unit`. `material` has no stages. No core stage can be skipped.

| Option | Workflow | Setting | Type and range | Lands on |
|---|---|---|---|---|
| `klein` | `klein_t2i.json` | `steps` | int 1-50 | `10.steps` |
| | | `guidance` | number 0-30 | `8.cfg` |
| | | `sampler` | combo | `9.sampler_name` |
| `hunyuan21` | `hunyuan21_shape_paint.json` | `shape_steps` | int 1-100 | `3.steps` |
| | | `shape_guidance` | number 1-30 | `3.guidance_scale` |
| | | `shape_seed` | seed | `3.seed` |
| | | `octree_resolution` | int 64-1024, multiple of 8 | `4.octree_resolution` |
| | | `paint_steps` | int 1-100 | `9.steps` |
| | | `paint_guidance` | number 1-10 | `9.guidance_scale` |
| | | `paint_seed` | seed | `9.seed` (overridden: see below) |
| `trellis2` | `trellis2_mesh.json` | `structure_steps`, `shape_steps`, `upsample_steps`, `texture_steps` | int 1-200 | `3.steps`, `18.steps`, `23.steps`, `12.steps` |
| | | `structure_seed`, `shape_seed`, `upsample_seed`, `texture_seed` | seed | `3.seed`, `18.seed`, `23.seed`, `12.seed` |
| | | `structure_guidance`, `shape_guidance`, `upsample_guidance`, `texture_guidance` | number 0-100 | `<sampler>.cfg` |
| | | `structure_scheduler`, `shape_scheduler`, `upsample_scheduler`, `texture_scheduler` | combo | `<sampler>.scheduler` |
| | | `sampler` | combo | `sampler_name` of the four samplers |
| | | `resolution` | int 1024-2048, multiple of 128 | `94.target_resolution` |

TRELLIS.2 has four samplers, one per stage of the model: sparse structure (node 3), shape
at 512 (18), shape upsampled (23) and texture (12); without a setting each keeps the
workflow's value and the variant's seed. `trellis2` is the default mesh option of the
`humanoid` recipe (below). A texture setting on a job that drops the texture bake is
refused: in a humanoid the bake is dropped unless paint is skipped or `trellis2_texture`.

Without settings a piece's jobs are the same as before (no hash changes).

`params` (an unknown name, a name the recipe does not read, or a wrong type is a problem):

| Name | Type | Meaning | Read by |
|---|---|---|---|
| `tris` | whole number above 0 | triangle (face) budget of a mesh | object_3d, prop, vehicle, unit; humanoid mesh and export (default 25000) |
| `texture` | whole number above 0 | texture size in pixels | object_3d, prop, vehicle, unit; humanoid paint (512 to 4096, a multiple of 512) or TRELLIS.2 bake (256 to 8192, a multiple of 256), and the export's normal map (default 2048) |
| `height` | number above 0 | figure height in metres | humanoid export |
| `head`, `hands`, `feet`, `widen` | number above 0 | proportions of a figure | humanoid export |

`inputs`:

| Name | Read by | Meaning |
|---|---|---|
| `reference` | object_3d, prop | an RGBA image (background removed) that replaces the concepts: the piece goes straight to the mesh, with no concept seeds and no pick |
| `reference` | humanoid with reference `input_image` | the picture of the figure that replaces the generated reference (every variant starts from it); with pose skipped and cutout `birefnet` it must have a transparent background |
| `clips` | humanoid export | the motion library (JSON, [conventions.md](conventions.md)); without it the GLB has the rest pose only |
| `items` | humanoid export | a folder of held items (`<name>.json` sockets next to `<name>.glb`); it enters the hash by the files inside it |
| `bone_map` | humanoid export | a bone map JSON that replaces the proposed one (roles to the rig's bone names) |

`review` (humanoid only): `pitch` (number, degrees above the horizon, default 60),
`directions` (whole number, default 8), `figure_height` (whole number, pixels of the
small figure, default 96), `front` (true or false: one large front view too, default
true).

Defaults per recipe: seeds 8 (unit, vehicle), 6 (object_3d, concept, illustration),
4 (prop, material); size 1024x1536 (unit), 1024x1024 (prop), 1536x1024 (the others); faces
60000 (unit), 120000 (vehicle), 40000 (object_3d), 30000 (prop).

Concept text: `brief`, the tier text, the family `style`, `global` and the view, in that
order. Material text: the material view, `brief`, `material_style` and `material_look`.
Seeds are fixed: derived from `family/piece` (the same order gives the same seeds).

## Recipes

| Recipe | Jobs |
|---|---|
| `concept` | Klein concept (N seeds) -> pick (2D only) |
| `illustration` | finished picture (background kept), picked by detail |
| `material` | Qwen Image 2.1 Turbo texture at 1024 (N seeds) -> pick -> 2K repaint -> rolled by half -> seam band repainted (inpaint) -> rolled back |
| `object_3d` | Klein concept -> pick -> Hunyuan3D 2.1 shape and Paint; with `inputs.reference`, only the mesh |
| `prop` | as `object_3d` with the prop view; no mesh with `"3d": false` |
| `vehicle` | 3/4 concept (N seeds) -> pick -> views `side`, `back`, `other_side`, `top` -> sheet -> emissive mask -> mesh with a 4096 texture |
| `unit` | Klein concept -> pick -> 3 Qwen Image Edit views (90, 180, 270 degrees) -> sheet -> emissive mask if `emissive` -> unrigged Hunyuan3D mesh |
| `humanoid` | per variant: reference -> pose -> cutout -> mesh -> paint -> rig -> export; then review and final ([below](#the-humanoid-recipe)) |

Job ids: `family/piece/step`, with steps `concept_s<seed>`, `pick`, `view_90`,
`view_180`, `view_270` (unit), `view_side`, `view_back`, `view_other_side`, `view_top`
(vehicle), `sheet`, `emissive`, `mesh`, and for materials `raw_s<seed>`, `pick`, `2k`,
`rolled`, `seam`, `seamless`. A humanoid's ids are `family/piece/v<seed>/<stage>` per
variant, plus `family/piece/review` and `family/piece/final`.

## The humanoid recipe

A skinned figure with a human skeleton. Each variant (one per seed of `variants`, default
4, derived from `family/piece`) goes through every stage without waiting for a person;
then one review sheet shows every variant and one final job copies the chosen variant.
Everything it delivers is a candidate pending human review.

| Stage | Default | Other options | Skippable | Hands on |
|---|---|---|---|---|
| `reference` | `qwen21_base` (Qwen Image 2.1 base, 60 steps, 1024x1536) | `qwen21_turbo` (Qwen Image 2.1 Turbo, 896x1120), `klein` (FLUX.2 Klein), `input_image` (`inputs.reference`, no job) | no | picture `rgb` and BiRefNet mask `mask` (Klein: `rgba`) |
| `pose` | `qwen_edit_2511` (Qwen Image Edit 2511, Lightning LoRA, 4 steps): the picture edited to the text of `pose_humanoid`, else `view_humanoid` | | yes | `rgb`, `mask` |
| `cutout` | `birefnet` (local: the BiRefNet mask the previous workflow saved becomes the alpha; without a mask the picture's own alpha) | `qwen_edit_character` (Qwen Image Edit character edit, text `cutout_humanoid`, else `view_humanoid`) | no | `rgba` |
| `mesh` | `trellis2` (TRELLIS.2 at 1024, remesh, reduction to `params.tris`) | `hunyuan21` (Hunyuan3D 2.1 shape only, reduced to `params.tris`; heavy) | no | `game_mesh` (reduced) and `raw_mesh` (before the reduction) |
| `paint` | `hunyuan_paint` (Hunyuan3D Paint 2.1 over `game_mesh` by its local path, 10 steps, `params.texture`; heavy) | `trellis2_texture` (the mesh job keeps TRELLIS.2's own bake, at `params.texture`) | yes: TRELLIS.2's texture stays | `painted_mesh` |
| `rig` | `skintokens` (seed: the variant's, 10 beams) | `unirig` | yes, with export (a statue) | `rig` |
| `export` | `humanoid_export` (Blender: bone map, Godot humanoid names, `raw_mesh` as `--bake-source`, `params`, `inputs.clips`, `inputs.items`, `inputs.bone_map`) | | yes | `glb`, `model`, `regions_mask`, `bone_map`, `mesh_report`, `motion_report` |
| `review` | `godot_sheet` (Godot off screen, the piece's `review` camera) | | no | `sheet`, `layout` |

The order check refuses: a skipped paint or `trellis2_texture` with mesh `hunyuan21`
(it has no texture of its own), rig skipped without export, `input_image` without
`inputs.reference`, an `inputs.reference` without a transparent background when pose is
skipped and the cutout is `birefnet`, a `params.texture` the chosen paint cannot make,
and a style without `view_humanoid`. The rig, export and review tools must be set in
`forge.local.json` (`skintokens_dir` and `skintokens_python`, or the four `unirig_*`
keys; `blender_python`; `godot`).

Settings of the humanoid options (besides `klein`, `trellis2` and `hunyuan21` above; the
humanoid's `hunyuan21` takes only `shape_steps`, `shape_guidance`, `shape_seed` and
`octree_resolution`):

| Option | Setting | Type and range | Lands on |
|---|---|---|---|
| `qwen21_base` | `steps` | int 1-200 | `6.steps` |
| | `guidance` | number 0-100 | `6.cfg` |
| | `sampler`, `scheduler` | combo | `6.sampler_name`, `6.scheduler` |
| `qwen21_turbo` | `guidance` | number 0-100 | `9.cfg` |
| | `sampler` | combo | `7.sampler_name` |
| `qwen_edit_2511`, `qwen_edit_character` | `steps` | int 1-100 | `14.steps` |
| | `guidance` | number 0-100 | `14.cfg` |
| | `seed` | seed | `14.seed` |
| | `sampler`, `scheduler` | combo | `14.sampler_name`, `14.scheduler` |
| | `lora_strength` | number 0-2 | `4.strength_model` |
| `hunyuan_paint` | `steps` | int 1-100 | `9.steps` |
| | `guidance` | number 1-10 | `9.guidance_scale` |
| | `seed` | seed | `9.seed` (overridden by the node, as `paint_seed`) |
| | `view_size` | int 512-1024, multiple of 256 | `9.view_size` |
| `skintokens` | `seed` | int 0 to 2^32 - 1 | argument `seed` |
| | `beams` | int 1-64 | argument `beams` |
| | `postprocess` | bool | argument `postprocess` |
| `unirig` | `seed` | int 0 to 2^32 - 1 | argument `seed` |
| `humanoid_export` | `normal_map` | int 256-8192 | argument `normal_map` (default `params.texture`) |
| | `glow` | bool | argument `glow` (the regions mask also marks what glows) |
| | `foot_slide_cm`, `hand_socket_cm`, `reach_shortfall_cm`, `penetration_cm` | number 0-100 | the motion report's thresholds |
| | `stretch_p95` | number 0-10 | the motion report's stretch threshold |
| `godot_sheet` | `front_height` | int 64-2048 | argument `front_height` |
| | `strip_frames` | int 1-32 | argument `strip_frames` |

`birefnet`, `input_image` and `trellis2_texture` have no settings (TRELLIS.2's texture
sampler is set with the mesh's `texture_*` settings).

Jobs and files, per variant `v<seed>` in `<out>/<family>/<piece>/`: `v<seed>_reference_*`,
`v<seed>_pose_*`, `v<seed>_cutout_rgba.png`, `v<seed>_mesh_game_mesh.glb` and
`v<seed>_mesh_raw_mesh.glb`, `v<seed>_paint_*` (albedo, metal-roughness, paint views,
`v<seed>_paint_painted_mesh.glb`), and the folders `v<seed>_rig/` and `v<seed>_export/`
(the runners' outputs, logs and step provenance, [conventions.md](conventions.md)). The
piece's `review/` holds `sheet.png` and its layout.

- A failed variant does not stop the others: only its own later stages wait. An export
  stopped by an invalid bone map (or a mesh that cannot be exported) is not tried again:
  its report is in `v<seed>_export/`.
- `review` runs once no variant is still active and at least one finished; a variant that
  did not finish appears as a failure mark with the stage and the error.
- `final` waits likewise for at least one complete variant. `pick_final.json` works as
  every pick: the automatic choice is provisional (the first variant that finished every
  stage with no mesh or motion flag in its reports, else the first that finished) and
  always `"review": "pending"`; `"manual": true` with another candidate in `chosen` redoes
  only the copy. The chosen variant's files of its last stage (the export's, or the painted
  mesh of a statue) are copied to `<out>/<family>/<piece>/final/`.

Estimates (`-Estimate`, seconds per job on one 24 GB card, from earlier measurements of
the same workflows, to be replaced by a real night's `timings.csv`): `qwen21_base` 90,
`qwen_edit` 23, `trellis2` 45, `hunyuan_paint` 90, `hy3d` 330, `local_gpu` 60 (a rig, an
export with its bake or a sheet).

Example ([tests/fixtures/order_humanoid.json](../tests/fixtures/order_humanoid.json)):
a `crew` family of two humanoids, 4 variants each, a review camera from above at 60
degrees with 8 directions, the synthetic motion library and held item of
`tests/fixtures/humanoid/`, and one piece with TRELLIS.2's sampler steps given:

    {"id": "guard", "brief": "A guard in a padded dark blue vest ...",
     "stages": {"mesh": {"option": "trellis2",
                         "settings": {"structure_steps": 12, "shape_steps": 24, "upsample_steps": 12}}}}

`-Check` on this machine:

    order: valid (...\tests\fixtures\order_humanoid.json)
    environment: verified
      version: blender_python bpy 4.2.0
      version: godot 4.7.2.stable.official.ed1daf0bf
      note: 1013 node classes on the server
      note: 11 model files checked against the loaders' lists

`-Estimate`:

    hunyuan_paint     8 jobs,     8 left, ~0.20 GPU h
    local            10 jobs,    10 left, ~0.00 GPU h
    local_gpu        18 jobs,    18 left, ~0.30 GPU h
    qwen21_base       8 jobs,     8 left, ~0.20 GPU h
    qwen_edit         8 jobs,     8 left, ~0.05 GPU h
    trellis2          8 jobs,     8 left, ~0.10 GPU h
      crew                  60 jobs, ~0.85 GPU h in all
    total 60 jobs, ~0.9 GPU hours left (estimate)

## Raw jobs (`tasks`)

| Key | Meaning |
|---|---|
| `id` | `family/piece/step`, or `family/step` (piece `general`) |
| `workflow` | a file of `forge/workflows/` |
| `values` | replaces the `$NAME$` markers of the workflow; a value may be `{"ref": "<job id>", "role": "rgb"}` (the output of another job) or `{"file": "<path>"}` (a file the order supplies, absolute or relative to the order file); either may add `"transport": "local_path"` to pass the file's local path instead of uploading it (only to a loopback server) |
| `images` | `{"<node>": reference}` sets the `image` of a LoadImage node |
| `set` | `{"<node>.<input>": value}` |
| `set_on_failure` | the same, applied on the second try |
| `drop` | node ids removed before sending (an optional branch, such as a texture bake); a remaining node that uses a removed one is an error |
| `roles` | `{"<node>": "<role>"}` names the outputs |
| `after` | job ids that must finish first |
| `heavy` | `true`: goes alone in the queue after `/free` |
| `stage` | the recipe stage the job belongs to (settings land on it) |
| `tool` | `local_gpu` jobs: the runner in `forge.local_gpu.RUNNERS` (the order check refuses an unknown one) |
| `args` | `local_gpu` jobs: the requested arguments of the tool |
| `ignored` | `local_gpu` jobs: `{argument: reason}` the tool is known to ignore or override |
| `mesh_role` | role of the mesh a Hy3DInPaint node writes |
| `group` | scheduling group (default `other`) |
| `kind` | default `comfy`; `local_gpu` for a local tool that uses the card (below); the recipes also make `pick`, `sheet`, `roll`, `unroll`, `cutout` and `final` (local steps) |
| `inputs` | `local_gpu` and `cutout` jobs: `{name: reference}`; besides `ref` and `file`, a reference may be `{"folder": "<path>"}` (a folder the order supplies: it enters the hash by the sorted relative paths and contents of its files) |
| `from`, `partial` | jobs over candidates: with `partial: true` the job waits while one of `from` is still active and runs with those that finished (blocked only when none did) |

The hash of a job's specification covers its workflow file, its values and the content
(SHA-256) of every input, whether produced by another job or supplied by the order. A
changed input file redoes only the jobs that use it (and what depends on them); moving
the order and its files elsewhere redoes nothing.

## Local GPU jobs and the card

A job of kind `local_gpu` runs a local tool that uses the card (its runner,
`forge.local_gpu.RUNNERS[tool](factory, job, out_dir)`, returns the files, the arguments
it actually passed and its provenance record). Such a job runs alone:

- only when the whole ComfyUI queue is empty, prompts of any client included
  (`queue_running` and `queue_pending`), then after `/free`;
- the processes `nvidia-smi` lists are logged; while one holds more than `gpu_busy_mb`
  of VRAM (default 2048) the job waits, up to `gpu_wait` seconds (default 300), then goes.
  Without `nvidia-smi`, or when the driver reports no amount per process (Windows display
  drivers report `[N/A]`), the log says so and the job goes;
- from the moment it is ready until it has run, nothing is sent to ComfyUI;
- a failure is tried once more, again only once the queue is empty; the log names an
  out-of-memory cause (from the error or the tool's log) and an external prompt that
  started meanwhile. After the second failure it is `failed` and only its dependants wait.
  A failure the tool says another try would repeat (the export's invalid bone map or a mesh
  it cannot export) is `failed` at once.

Every factory run holds the machine-wide GPU lock file (`%LOCALAPPDATA%\art-forge\gpu.lock`,
or the `FORGE_LOCK_DIR` folder) for its whole length, and each local GPU job and each
runner of `forge/local_gpu.py` takes it too, so two factories never share the card: the
second refuses to start ("another art-forge process holds the GPU lock", exit 2). It is
an operating-system lock: it is released when its process ends, even when the process is
killed; a lock file left by a process that is gone is taken over and the log says so.
`-Status` shows who holds it.

## Workflows

All in ComfyUI API format, in `forge/workflows/`. `$PREFIX$`, `$PREFIX_MASK$` and
`$MESHNAME$` are filled by the factory; every other marker must be given in `values`.

| Workflow | Model | Markers | Outputs (node) |
|---|---|---|---|
| `qwen_image_21_base_t2i.json` | Qwen Image 2.1 base, 60 steps, CFG 1.0 | `$PROMPT$` `$NEGATIVE$` `$WIDTH$` `$HEIGHT$` `$SEED$` | picture (8), background mask (12) |
| `qwen_image_21_turbo_t2i.json` | Qwen Image 2.1 Turbo, 896x1120 | `$PROMPT$` `$NEGATIVE$` `$SEED$` | picture (12), background mask (16) |
| `qwen_image_21_turbo_tile.json` | Qwen Image 2.1 Turbo | `$PROMPT$` `$NEGATIVE$` `$WIDTH$` `$HEIGHT$` `$SEED$` | picture (12) |
| `qwen_image_21_turbo_hires.json` | Qwen Image 2.1 Turbo, repaint of a picture | `$PROMPT$` `$NEGATIVE$` `$IMAGE$` `$WIDTH$` `$HEIGHT$` `$SIGMAS$` `$SEED$` | picture (12) |
| `qwen_image_21_turbo_inpaint.json` | Qwen Image 2.1 Turbo, masked repaint | `$PROMPT$` `$NEGATIVE$` `$IMAGE$` `$MASK$` `$SEED$` | picture (12) |
| `qwen_edit_2511.json` | Qwen Image Edit 2511 with the Lightning LoRA | `$IMAGE1$` `$PROMPT$` `$NEGATIVE$` `$SEED$` `$STEPS$` `$CFG$` `$LORA$` | picture (16), background mask (20) |
| `qwen_edit_character.json` | Qwen Image Edit 2511, character edit | as `qwen_edit_2511.json` | picture (16), background mask (20), RGBA (32) |
| `qwen_edit_view_angles.json` | Qwen Image Edit 2511 with the multiple-angles LoRA | none (image in node 1 through `images`; angles by `set` on node 10, seed on node 16) | picture (18), RGBA (23) |
| `klein_t2i.json` | FLUX.2 Klein 9B, 4 steps | `$PROMPT$` `$WIDTH$` `$HEIGHT$` `$SEED$` | picture (13), RGBA (18) |
| `hunyuan21_shape_paint.json` | Hunyuan3D 2.1 shape and Paint | `$IMAGE$` `$SEED$` `$FACES$` | shape (6), shape before the face reduction (16, floaters removed), albedo (12), metal-roughness (13), paint views (14), painted mesh (`mesh_role`) |
| `hunyuan21_paint_mesh.json` | Hunyuan3D 2.1 Paint over a given mesh, 10 steps | `$IMAGE$` `$MESH$` (local path) `$SEED$` `$TEXTURE$` | albedo (12), metal-roughness (13), paint views (14), painted mesh (`mesh_role`) |
| `trellis2_mesh.json` | TRELLIS.2 at 1024 with remesh | `$IMAGE$` (RGBA) `$SEED$` `$TRIS$` (the `DecimateMesh` target) | remeshed and reduced mesh (403), remeshed mesh before the reduction (405), textured mesh (322) |

The texture bake of `trellis2_mesh.json` is optional: a job that lists
`forge.stages.TRELLIS2_TEXTURE_NODES` in `drop` makes only the untextured mesh (403). The
bake's texture size is set with `set` on node 147 (`texture_size`, default 2048) and node
196 (`resolution`, the UV atlas). The core recipes drop nodes 15 and 16 of
`hunyuan21_shape_paint.json` (`forge.stages.HUNYUAN21_RAW_NODES`); the humanoid's
`hunyuan21` mesh drops its Paint nodes 7 to 14 instead (`HUNYUAN21_PAINT_NODES`).
Workflows with paid API nodes are refused on load.

## Outputs

In `<out>/<family>/<piece>/`, each file as `<step>_<role>.<ext>`:

- concepts and views: `_rgb.png` and `_rgba.png` (background removed); an output without a
  role becomes `_n<node>`, and extra outputs of one node get `_1`, `_2`...;
- pick: `pick_<step>.json` and a copy of the chosen picture `<step>_chosen.png`;
- `sheet.png` (views side by side, 1024 high);
- emissive: `emissive_rgb.png`, `emissive_background_mask.png`;
- mesh: `mesh_albedo.png`, `mesh_metal_roughness.png`, `mesh_paint_views.png`,
  `mesh_painted_mesh.glb` (and `mesh_shape.glb` when the server lists the export node's
  file in its history);
- material: `raw_s<seed>_rgb.png`, `2k_rgb.png`, `rolled_image.png`, `rolled_mask.png`,
  `seam_rgb.png`, the result `<piece>.png` and the preview `<piece>_tile2x2.png`;
- humanoid: the step of a variant's job is `v<seed>_<stage>`; local GPU jobs write into
  the folder `<step>/`; plus `pick_final.json` and `final/` (see the humanoid recipe).

At the root of `<out>`: `manifest.json` (each job with its state, specification hash,
files and hashes, times, tries and provenance), `state.json` (count per state: `done`,
`failed`, `blocked`, `waiting`, `ready`, `queued`; GPU hours left; server queue; picks to
review; failures), `timings.csv` (`finished;job;family;asset;group;status;exec_seconds;
wall_seconds`), `factory.log`, `program/` (the frozen copy the run uses) and, while
running, `factory.pid`.

## Picks and review

Picks are automatic: sharpness of the object and a clean background for concepts
(`object`), detail and even light for materials (`material`), detail for illustrations
(`image`). `pick_<step>.json` holds `chosen`, `manual`, `review` (`"pending"` or
`"manual"`), `metric`, `candidates` (each with `job`, `score` and its measures) and
`how_to_change`. To change it: put another candidate's id in `chosen` and set
`"manual": true`; the next pass redoes only what depends on the pick. A humanoid's
`pick_final.json` has the same keys (each candidate with `job`, `variant`, `complete`,
`flags` and `status`, and the chosen `variant`); its `metric` says how the automatic
choice was made. `state.json` counts it among the picks to review.

## Provenance

When a job runs, the manifest records under `provenance`: family, piece, kind, group,
workflow name and SHA-256, the model files the workflow names (every input ending in
`.safetensors`, `.ckpt`, `.pth`, `.gguf` or `.bin`, with node, class and input name), the
full prompt and negative, the seeds, the settings applied (`set`) and the inputs (file,
SHA-256, the factory job that produced it and the pick it went through, or
`"order_input": true` for a file the order supplied; images a raw job names directly on
the server appear with `"factory_output": false`) and `parameters`.

`parameters` is the record of what each job was asked to use and what it really used.
For a ComfyUI job, per node of the submitted prompt (`{node: {"class", "inputs"}}`, links
between nodes left out), each input has `requested` (what the factory sent) and
`effective` (read back from the prompt the server ran, in its `/history`), with
`"changed": true` when they differ. `effective` is `null` with a `reason` when it cannot
be known: an input the catalogue marks as ignored or overridden by its implementation
(`forge.stages.OVERRIDDEN`: the Hunyuan3D Paint seed, `9.seed`, because the Paint
pipeline draws its diffusion noise from a generator fixed at seed 0), a history that holds
no prompt, or an input missing from it. For a local GPU job, per argument: `requested`
(the job's `args`) and `effective` (what the runner reports it passed), `null` with a
reason when the runner does not report it or the job's `ignored` names it.

    python -m forge --out <folder> --provenance <file.json>

writes one record per output file of every finished job: `asset`, `family`, `job`,
`step`, `role`, `file` (relative to `<out>`), `sha256`, `present`, `finished`,
`workflow`, `workflow_sha256`, `models`, `prompt`, `negative`, `seeds`, `settings`,
`inputs`, `parameters` (and `chosen` for picks). It needs no server and no GPU.
