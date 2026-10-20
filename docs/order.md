# The order

One JSON file per order, written by the game's operator (a person or an LLM agent working
from the game's art list) and kept in the game's own repository. Keys are in English. It
keeps the job-list format of the factory core (`version`, `style`, `families`, `assets`,
`tasks`) and adds per-piece keys (`recipe`, `variants`, `stages`, `skip`, `params`,
`inputs`, `review`, `accept`, `reserve`, and the recipe-specific `parts`, `body`, `motions`,
`item`). Families are in priority order; each piece (an entry of a family's
`assets`) expands into jobs through its recipe.

Check an order before a night's run:

    .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Check
    .\Run-Forge.ps1 -Order <order.json> -Out <folder> -Estimate

`-Check` prints two results.

1. **The order** (`forge.order.check_order`, without the server): `order: valid`, or every
   problem it finds. It checks the format, known recipes and stage options, stage settings
   (known names, types and ranges, settings that land on no job), valid `skip` names,
   `variants`, `params` types, `accept` keys and limits, `reserve`, that `inputs` files exist, that the style texts the recipes
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
(two humanoids; see [the humanoid recipe](#the-humanoid-recipe)),
[tests/fixtures/order_articulated.json](../tests/fixtures/order_articulated.json),
[tests/fixtures/order_creature.json](../tests/fixtures/order_creature.json) and
[tests/fixtures/order_held_item.json](../tests/fixtures/order_held_item.json), and
[tests/fixtures/order_humanoid_motions.json](../tests/fixtures/order_humanoid_motions.json)
(a humanoid motions list with every source).

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
| `global` | every concept (figure, vehicle, object_3d, concept, illustration, prop) and every humanoid reference |
| `view_figure`, `view_object`, `view_prop`, `view_vehicle`, `view_illustration` | the default view of each recipe (required when the order has a piece of that recipe) |
| `view_humanoid` | the view of every humanoid reference, and the text of its pose and cutout edits unless the next two are given (required when the order has a humanoid). It describes only what should be seen: full body in an A-pose, open empty hands held away from the body, plain grey background, even light |
| `pose_humanoid`, `cutout_humanoid` | optional: the text of the humanoid's pose edit and of its `qwen_edit_character` cutout (what the edited picture shows) |
| `view_articulated`, `view_creature` | the view of every articulated or creature reference (required when the order has one) |
| `cutout_articulated`, `pose_creature`, `cutout_creature` | optional: the text of those edits (else the view) |
| `view_held_item` | the view of every held item's reference: a strict side view of the whole item, its front to the right (required when the order has one) |
| `cutout_held_item` | optional: the text of its `qwen_edit_character` cutout (else `view_held_item`) |
| other `view_*` (for example `view_creature`, `view_icon`) | when a family or piece asks for that `"view"` |
| `material_top`, `material_front` | materials seen from above (default) or from the front |
| other `material_*` (for example `material_sky`) | a material whose `"view"` names it |
| `material_look` | appended to every material |
| `material_negative` | negative text of every material (required when there is a material) |
| `emissive` | the emissive-mask request (required with a vehicle, or a figure with `emissive`) |
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
`params`, `inputs`, `review`, `accept`, `reserve`, `parts`, `body`, `motions`, `item`) as a
default for its pieces. The objects (`stages`, `params`, `inputs`, `review`, `accept`, `item`)
merge key by key with the piece's own (the piece wins); the other keys apply when the piece
does not set them.

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
| `emissive` | `true`: a figure also makes the emissive mask |
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
| `accept` | hard limits a variant must meet to be picked (humanoid, articulated, creature, held_item; [below](#acceptance-and-reserve-variants)) |
| `reserve` | extra variants run only when no variant passed (humanoid, articulated, creature, held_item; a whole number of at least 0, default 0) |
| `parts` | the parts of an articulated piece ([below](#the-articulated-recipe)) |
| `body`, `motions` | the body plan or template and the movements of a creature ([below](#the-creature-recipe)); `motions` also lists a humanoid's movements ([below](#motions-of-a-humanoid)) |
| `item` | the hold class and socket template of a held item ([below](#the-held-item-recipe)) |

`review`, `accept` and `reserve` do not apply to the core recipes. The stage catalogue is `forge/stages.py`.

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
`seed` (whole number from 0 to 2^64 - 1), `bool`, `combo` (a name), `choice` (one of a fixed
list of names).

The core recipes have one option per stage (the models they always used): stage
`concept` (option `klein`) in `concept`, `illustration`, `object_3d`, `prop`, `vehicle`
and `figure`; stage `mesh` (option `hunyuan21`) in `object_3d`, `prop`, `vehicle` and
`figure`. `material` has no stages. No core stage can be skipped.

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
A setting with several targets (`sampler`) lands only on the samplers the job keeps.

Without settings a piece's jobs are the same as before (no hash changes).

`params` (an unknown name, a name the recipe does not read, or a wrong type is a problem):

| Name | Type | Meaning | Read by |
|---|---|---|---|
| `tris` | whole number above 0 | triangle (face) budget of a mesh | object_3d, prop, vehicle, figure; humanoid mesh and export (default 25000); the mesh of the articulated, creature and held-item recipes |
| `texture` | whole number above 0 | texture size in pixels | object_3d, prop, vehicle, figure; humanoid paint (512 to 4096, a multiple of 512) or TRELLIS.2 bake (256 to 8192, a multiple of 256), and the export's normal map (default 2048) |
| `height` | number above 0 | figure height in metres | humanoid export; articulated parts, creature export and held item (scales instead of `length`) |
| `head`, `hands`, `feet`, `widen` | number above 0 | proportions of a figure | humanoid export |
| `image_format` | `lossless` or `compact` | images of the exported GLB: `lossless` (default) writes every map as PNG; `compact` writes the base colour as JPEG at quality 92 (PNG when it carries opacity) and the technical maps (normal, metallic-roughness, occlusion, emissive) as PNG. A normal or metallic-roughness map or an opacity mask is never JPEG | humanoid export, articulated parts, creature export, held item |
| `length` | number above 0 | length front to back in metres (scales instead of `height`) | articulated parts, creature export, held item (one of `length` and `height` is required) |
| `front` | `+z`, `-z`, `+x`, `-x` | the glTF axis the generated front points along (default `+z`); the export faces +Z | articulated parts, creature export |

`inputs`:

| Name | Read by | Meaning |
|---|---|---|
| `reference` | object_3d, prop | an RGBA image (background removed) that replaces the concepts: the piece goes straight to the mesh, with no concept seeds and no pick |
| `reference` | humanoid with reference `input_image` | the picture of the figure that replaces the generated reference (every variant starts from it); with pose skipped and cutout `birefnet` it must have a transparent background |
| `clips` | humanoid export | the motion library (JSON, [conventions.md](conventions.md)); without it (and without `motions`) the GLB has the rest pose only. With `motions` it is the default library of their `library` entries |
| `items` | humanoid export | a folder of held items (`<name>.json` sockets next to `<name>.glb`); it enters the hash by the files inside it |
| `figure` | held item's hold | a rigged humanoid GLB that holds the item on the review sheet ([below](#the-held-item-recipe)) |
| `clips` | held item's hold | the motion library whose clips holding the item's class the figure plays |
| `bone_map` | humanoid export | a bone map JSON that replaces the proposed one (roles to the rig's bone names), for every variant; or an object `{"v<seed>": file}` (the key may also be the bare seed) with one map per variant, since the rig predicts a skeleton per variant: a variant it does not name gets the proposal, a key that is no variant of the piece is a problem of the order |
| `body` | humanoid export | an approved body: a GLB the humanoid exporter wrote (a variant's `v<seed>_export/<piece>.glb` or a piece's `final/<piece>.glb`, of this run or another), its `model.json` next to it. Every variant is conformed to it and exported on its skeleton ([below](#a-common-body-inputsbody)). A piece's `null` drops its family's body |

`review` (the recipes with a review stage; the held item adds its own settings): `pitch` (number, degrees above the horizon, default 60),
`directions` (whole number, default 8), `figure_height` (whole number, pixels of the
small figure, default 96), `front` (true or false: one large front view too, default
true).

Defaults per recipe: seeds 8 (figure, vehicle), 6 (object_3d, concept, illustration),
4 (prop, material); size 1024x1536 (figure), 1024x1024 (prop), 1536x1024 (the others); faces
60000 (figure), 120000 (vehicle), 40000 (object_3d), 30000 (prop).

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
| `figure` | Klein concept -> pick -> 3 Qwen Image Edit views (90, 180, 270 degrees) -> sheet -> emissive mask if `emissive` -> unrigged Hunyuan3D mesh |
| `humanoid` | per variant: reference -> pose -> cutout -> mesh -> paint -> rig -> export; then review and final ([below](#the-humanoid-recipe)) |
| `articulated` | per variant: reference -> cutout -> mesh -> paint -> parts; then review and final ([below](#the-articulated-recipe)) |
| `creature` | per variant: reference -> pose -> cutout -> mesh -> paint -> rig -> export; then review and final ([below](#the-creature-recipe)) |
| `held_item` | per variant: reference -> cutout -> mesh -> paint -> item -> hold; then review and final, which also fills `<family>/items/` ([below](#the-held-item-recipe)) |

Job ids: `family/piece/step`, with steps `concept_s<seed>`, `pick`, `view_90`,
`view_180`, `view_270` (figure), `view_side`, `view_back`, `view_other_side`, `view_top`
(vehicle), `sheet`, `emissive`, `mesh`, and for materials `raw_s<seed>`, `pick`, `2k`,
`rolled`, `seam`, `seamless`. A humanoid's ids are `family/piece/v<seed>/<stage>` per
variant, plus `family/piece/review` and `family/piece/final` (with `reserve`, also
`family/piece/review_reserve` and `family/piece/final_reserve`).

## The humanoid recipe

A skinned figure with a human skeleton. Each variant (one per seed of `variants`, default
4, derived from `family/piece`) goes through every stage without waiting for a person;
then one review sheet shows every variant and one final job decides: it copies the best
acceptable variant, or says that none passed.
Everything it delivers is a candidate pending human review.

| Stage | Default | Other options | Skippable | Hands on |
|---|---|---|---|---|
| `reference` | `qwen21_base` (Qwen Image 2.1 base, 60 steps, 1024x1536) | `qwen21_turbo` (Qwen Image 2.1 Turbo, 896x1120), `klein` (FLUX.2 Klein), `input_image` (`inputs.reference`, no job) | no | picture `rgb` and BiRefNet mask `mask` (Klein: `rgba`) |
| `pose` | `qwen_edit_2511` (Qwen Image Edit 2511, Lightning LoRA, 4 steps): the picture edited to the text of `pose_humanoid`, else `view_humanoid` | | yes | `rgb`, `mask` |
| `cutout` | `birefnet` (local: the BiRefNet mask the previous workflow saved becomes the alpha; without a mask the picture's own alpha) | `qwen_edit_character` (Qwen Image Edit character edit, text `cutout_humanoid`, else `view_humanoid`) | no | `rgba` |
| `mesh` | `trellis2` (TRELLIS.2 at 1024, remesh, reduction to `params.tris`) | `hunyuan21` (Hunyuan3D 2.1 shape only, reduced to `params.tris`; heavy) | no | `game_mesh` (reduced) and `raw_mesh` (before the reduction) |
| `paint` | `hunyuan_paint` (Hunyuan3D Paint 2.1 over `game_mesh` by its local path, 10 steps, `params.texture`; heavy) | `trellis2_texture` (the mesh job keeps TRELLIS.2's own bake, at `params.texture`) | yes: TRELLIS.2's texture stays | `painted_mesh` |
| `rig` | `skintokens` (seed 9001, 10 beams: the validated setting; the `seed` setting overrides it) | `unirig` (seed 9001) | yes, with export (a statue) | `rig` |
| `export` | `humanoid_export` (Blender: bone map, Godot humanoid names, `raw_mesh` as `--bake-source`, the painted mesh as `--material-source`, Hunyuan3D Paint's `metal_roughness` as `--metal-roughness`, `params`, `inputs.clips`, `inputs.items`, `inputs.bone_map`) | | yes | `glb`, `model`, `regions_mask`, `bone_map`, `mesh_report`, `motion_report` |
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
| | `conform_joint_cm`, `conform_outside_cm`, `conform_floor_cm` | number 0-100 | the conform's thresholds (with `inputs.body` only; defaults 4, 1.5, 4) |
| | `conform_scale` | number 1-10 | the conform's segment stretch threshold (with `inputs.body` only; default 1.5) |
| | `conform_torn_share` | number 0-1 | the conform's tear threshold (with `inputs.body` only; default 0.002) |
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

- A failed variant does not stop the others: only its own later stages wait. A mesh job
  that finishes without its `game_mesh` or `raw_mesh` is a failure of that variant (tried
  once more). An export stopped by an invalid bone map (or a mesh that cannot be
  exported) is not tried again: its report is in `v<seed>_export/`.
- A bone map corrected by hand in `v<seed>_export/bone_map.json` with `"manual": true`
  enters the export's specification: the next run redoes that export, the review and the
  final copy (also after an invalid map, without `-RetryFailed`).
- The scripts a local GPU job runs enter its specification (rig: the SkinTokens entry and
  the checkout's git revision; export: the exporter and the bone map module; review: the
  Godot project): a fixed tool redoes its jobs and what depends on them.
- `review` runs once no variant is still active and at least one finished; a variant that
  did not finish appears as a failure mark with the stage and the error.
- `review` and `final` also run when every variant failed (the sheet shows only failure
  marks, the decision is `no_variant_passed`).
- `final` is the piece's decision. Every variant is classified `acceptable` or `rejected`
  against the piece's limits ([below](#acceptance-and-reserve-variants)); the automatic
  pick is only among the acceptable ones (the one with the fewest mesh flags, then the
  fewest motion flags, then the smallest total excess over the thresholds,
  `value / threshold - 1` summed; the first in order on a tie; each candidate's `rank` is
  in the file), provisional and always `"review": "pending"`. `pick_final.json` works as
  every pick: `"manual": true` with another candidate in `chosen` redoes only the copy.
  The chosen variant's files of its last stage (the export's, or the painted mesh of a
  statue) are copied to `<out>/<family>/<piece>/final/`. With no acceptable variant the
  outcome is `no_variant_passed`: nothing is copied (an earlier copy is removed), the file
  names the stage held responsible most often, and the sheet says so.
- The export carries the painted maps into the GLB: the paint stage's metallic-roughness
  map (Hunyuan3D Paint) replaces the rig's, and maps the rig's material lacks
  (metallic-roughness, opacity, emissive, occlusion; with `trellis2_texture` TRELLIS.2's
  own) are taken from the painted mesh. The written GLB is read back: a map that is lost,
  lossy or cannot be carried is a mesh flag ([conventions.md](conventions.md)).

### A common body (`inputs.body`)

A game wants all its figures of one kind (the outfits and armours of one crew, say) to
share one approved body: the same skeleton (bone names, hierarchy, rest) and proportions,
so that any animation, item socket or attachment works on every outfit. `inputs.body` (piece
or family; a piece's own value wins and `null` drops the family's) names that body: a GLB the
humanoid exporter wrote, with its `model.json` next to it. A path to a run's pick is enough
(`<out>/<family>/<piece>/final/<piece>.glb`); a male and a female body are two pieces' picks,
the family giving one and the pieces of the other kind overriding it.

Every variant still gets its own rig (the rig stage is unchanged). Its export becomes
`body_export` (stage `export`): the variant's rig is conformed to the body (the variant's
bone map as usual; its mesh moved by its own weights so that its joints land on the body's;
bound to the body's skeleton with its own weights; [conventions.md](conventions.md#a-figure-that-wears-an-approved-body)),
then the humanoid exporter runs on it in the body's frame: the GLB has the body's skeleton
(checked against the body's GLB), the body's height and proportions, and motions
retargeted on that skeleton, the same on every variant that wears the body. The body's GLB
and its `model.json` enter the export's specification: a new body (or a re-picked one)
redoes the exports, sheets and decisions only.

The order check refuses with a body: `params.height`, `head`, `hands`, `feet` and `widen` (the
body sets them); a body that is not a GLB, has no readable humanoid `model.json` next to it,
lacks a role's bone, or stands turned more than 15 degrees from facing +z (its shoulders' or
hips' line); a body inside the piece's own output folder (`<family>/<piece>/`: a piece cannot
wear its own pick); and skipping the rig and export. The `conform_*` export settings and the
`conform_flags`, `conform_stretch_p95` and `body_outside_cm` limits are refused on a piece
without a body.

Files: `v<seed>_export/` also holds `conform_report.json` (a role of the export, copied to
`final/`) and `conform/` (the conform's logs, `conformed.glb`, `frame.json`, `body_map.json`
and the variant's own `bone_map.json`). A person corrects a variant's bone map in
`v<seed>_export/conform/bone_map.json` (`"manual": true`), which redoes that export; the
`bone_map.json` next to the GLB is the body's roles. The conform flags reject a variant by
default (`conform_flags` 0, [below](#acceptance-and-reserve-variants)), and the sheet's
label of each variant ends with a line of its conform measures.

### Motions of a humanoid

`motions` (piece, or family as a default; [design](superpowers/specs/2026-10-18-art-forge-design.md),
section 13) lists the clips the exported figure gets, one entry per movement, in the
creature's format. With a list the export plays exactly those clips (the piece's motion
library, made by the job `<family>/<piece>/motions`); without one it plays `inputs.clips` as
it is. The export stage must not be skipped.

| Key | Meaning |
|---|---|
| `name` | the clip name the game looks up (lower case, letters, digits, `_`; unique in the list) |
| `source` | `library`, `text` or `bvh` (`procedural` motion kinds are the creature's) |
| `library` | `library`: `{"file": path, "clip": name}`; the file defaults to `inputs.clips`, the clip to the entry's name; the file `@starter` is the factory's starter library ([data/motions](../data/motions/README.md)); a path is relative to the order |
| `prompt` | `text`: what the movement is, described plainly (at most 60 words) |
| `file` | `bvh`: the capture file (relative to the order); the frame range is the setting `range` |
| `frames`, `fps` | the length and rate the game wants: required (`frames`, 2 to 600) for `text` and `bvh`; a library clip is resampled to `frames` when given. `fps` defaults to 24 (a library clip keeps its own) |
| `loop` | the clip closes on itself (default false; a library clip keeps its own) |
| `kind` | the clip's kind for the review sheet (`idle`, `walk`, `start`, `stop` or another word; default the name's first word) |
| `holds` | the hold kind during the clip (`<class>` or `<class>_<mode>`, as the library's `holds`) or `null`; a library clip keeps its own when absent |
| `contacts` | `{"left": [frames] or "all", "right": ...}`: where each foot is planted; else a library clip's `stance`, else detected |
| `settings` | per source (below) |
| `note` | a comment |

Settings: `text`: `steps` (int 1-200, default 50), `seed` (int 0 to 2^31 - 1, default derived
from `<family>/_motions/<name>`), `guidance` (number 0-20, default 5), `duration` (seconds
generated, 0.7-12; default the clip's length for a one-shot, twice it plus a second, at least
3, for a loop, to find a cycle in), `feet` (`auto`, `clip`, `walk`, `lie`, `free`: how the
exporter stands the feet; `auto` takes `walk` for a loop that travels, `lie` for a one-shot
that ends low, else `clip`). `bvh`: `range` ([first, last] frames of the capture, default all),
`up` (`y` or `z`, default `y`), `joints` (`{canonical joint: capture joint name}` for joints
the name table misses: CMU's BVH conversion and Mixamo names are known), `feet` as above.
`library`: none.

Jobs: each `text` and `bvh` entry of a family's humanoid pieces is one job of the family,
`<family>/_motions/<name>` (folder `<out>/<family>/_motions/<name>/`; the name gets 8
characters of its specification's hash when two entries of one name differ), shared by every
piece that lists it. `text` is a local GPU job (tool `motion_text`: HY-Motion Lite in its own
Python, [conventions.md](conventions.md#motions-list)); `bvh` a local step. Then
`<family>/<piece>/motions` (a local step) writes the piece's `motions/library.json` and
`motions/report.json`, and every export of the piece takes that library. A piece cannot be
named `_motions`. The order check refuses: an unknown key or source, a key of another source,
`text` or `bvh` without `frames`, an empty or too long prompt, a missing capture file or a
capture without the joints it needs, a range past its last frame, a library or clip that does
not exist, a `library` entry without a file when there is no `inputs.clips`, an unknown or
invalid setting, `motions` with the export skipped, and a `text` entry when
`forge.local.json` lacks the HY-Motion keys (`hymotion_python`, `hymotion_dir`,
`hymotion_model`, `hymotion_text_encoder`, `hymotion_clip`; README). Complete sample:
[tests/fixtures/order_humanoid_motions.json](../tests/fixtures/order_humanoid_motions.json).

### Acceptance and reserve variants

`accept` (piece or family; a family's object merges key by key into its pieces) gives
hard limits, separate from the ranking. A variant violating any of them is `rejected`;
the others are `acceptable`.

| Key | Type | Default | Limit on |
|---|---|---|---|
| `mesh_flags` | whole number, at least 0 | 0 | how many mesh flags the variant may have (`mesh_report.json` `flags`; above it, every flag is a violation) |
| `missing_clips` | whole number, at least 0 | 0 | library clips the exported GLB may lack |
| `foot_slide_cm`, `hand_socket_cm`, `reach_shortfall_cm`, `penetration_cm` | number, at least 0 | not gated | the largest value of that measure over the clips |
| `stretch_p95` | number, at least 0 | not gated | the largest edge stretch over the clips |
| `bake_source_offset_m` | number, at least 0 | not gated | `bake_source_offset_m` of the mesh report (a bake source that does not line up is already a mesh flag) |
| `conform_flags` | whole number, at least 0 | 0 | with `inputs.body` only: how many conform flags the variant may have (`conform_report.json` `flags`) |
| `conform_stretch_p95` | number, at least 0 | not gated | with `inputs.body` only: the 95th percentile of the edges' stretch by the conform |
| `body_outside_cm` | number, at least 0 | not gated | with `inputs.body` only: the 99th percentile of how far the body's surface lies outside the conformed outfit, cm |

By default, then, a variant is rejected only for structural defects: a failed job (an
invalid bone map included), any mesh flag (unweighted vertices, weights not normalised,
no UV layer, several materials, no base colour, a bake source that does not line up, a
material map lost, lossy or not carried), a missing clip, an unreadable mesh or motion
report, or a NaN or infinite motion value. Motion limits gate only when the order sets
them in `accept`; until then motion values rank the acceptable variants and stay on the
sheet, `pick_final.json` says `"motion": "not gated: ..."` (else `"gated on <measures>"`)
and the sheet labels an acceptable variant `acceptable: motion not gated`. Each
violation names the stage held responsible (`forge/acceptance.py`, a conservative table:
`unknown` rather than a guess):

| Violation | Stage |
|---|---|
| a failed job | its stage; an export stopped by an invalid bone map: `rig` |
| a painted map that could not be carried (`material slot <slot>: ... could not be carried`, `material source: ...`) or no base-colour texture | `paint` (`mesh` when paint is skipped: TRELLIS.2's own texture) |
| a map missing from or lossy in the exported GLB, any `material slot normal` flag | `export` (it writes the GLB's images and bakes the normal map) |
| any other `material slot` flag | `unknown` |
| vertices without weight, weights not normalised | `rig` |
| a bake source that does not line up, `bake_source_offset_m` | `mesh` |
| no UV layer, more than one material, any other mesh flag | `unknown` |
| a motion limit | `rig`; `library` when the same clip is over a limit on every variant that has a motion report (at least two) |
| a missing clip, an unreadable report | `export` |
| a NaN or infinite motion value | `unknown` |
| a conform flag `joints:`, `scale:`, `outside:`, `tear:`, a `conform_stretch_p95` over its limit | `rig` (the conform follows the predicted joints and weights) |
| a conform flag `floor:` | `unknown` (the outfit's soles or its predicted ankles) |
| `body_outside_cm` over its limit | `mesh` |
| a skeleton that differs from the body's (a mesh flag `skeleton: ...`) | `unknown` |

`reserve` (piece or family, default 0): that many more variants, with the next seeds of
`family/piece` (the first `variants` seeds stay the same), are expanded with their whole
chain but held: they are not ready, do not keep the night going and appear in
`state.json` counts as `held`. When the first decision (`final`) is
`no_variant_passed`, they are released and run like the others; then a second sheet
(`review_reserve`) and a second decision (`final_reserve`, `pick_final_reserve.json`)
cover every variant, the main and the reserve ones. With no acceptable variant there
either, the outcome stays `no_variant_passed`. When the stage held responsible most often
is `library` (the same clips fail on every variant), the reserve is still released but
`pick_final.json` notes that it will likely fail the same way. A manual pick in
`pick_final.json` holds the reserve again. `-Estimate` counts held jobs apart, as "if needed".

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

`-Estimate` (held reserve jobs, when a piece has `reserve`, are counted apart on each
group's line and in a last line `if needed: <n> held reserve jobs, ~<h> GPU hours more`):

    hunyuan_paint     8 jobs,     8 left, ~0.20 GPU h
    local            10 jobs,    10 left, ~0.00 GPU h
    local_gpu        18 jobs,    18 left, ~0.30 GPU h
    qwen21_base       8 jobs,     8 left, ~0.20 GPU h
    qwen_edit         8 jobs,     8 left, ~0.05 GPU h
    trellis2          8 jobs,     8 left, ~0.10 GPU h
      crew                  60 jobs, ~0.85 GPU h in all
    total 60 jobs, ~0.9 GPU hours left (estimate)

## The articulated recipe

Rigid parts on pivots ([design](superpowers/specs/2026-10-18-art-forge-design.md), section
7). Per variant `v<seed>` (default 4 variants): `reference` -> `cutout` -> `mesh` -> `paint`
-> `parts`; then `review` and `final` as the humanoid's (with `accept` and `reserve`).

| Stage | Default | Other options | Skippable | Hands on |
|---|---|---|---|---|
| `reference` | `qwen21_base` at 1536x1024, text: `brief`, tier, family style, `global`, `view_articulated` | `qwen21_turbo`, `klein`, `input_image` | no | `rgb`, `mask` |
| `cutout` | `birefnet` | `qwen_edit_character` (text `cutout_articulated`, else `view_articulated`) | no | `rgba` |
| `mesh` | `trellis2` | `hunyuan21` | no | `game_mesh`, `raw_mesh` |
| `paint` | `hunyuan_paint` | `trellis2_texture` | yes | `painted_mesh` |
| `parts` | `region_split` (Blender: the painted mesh and Hunyuan3D Paint's `metal_roughness`) | | no | `glb`, `model`, `mesh_report`, `frame` |
| `review` | `layout_sheet` (Godot) | | no | `sheet`, `layout` |

`params`: `tris`, `texture` (as the humanoid's mesh and paint), `height` or `length`
(metres; the object is scaled by one of them: its height, or its length front to back),
`front` (`+z`, `-z`, `+x` or `-x`: the glTF axis the generated object's front points
along; default `+z`), `image_format`. `inputs`: `reference` (with `input_image`).

`parts` (piece or family) is required: a list of parts, the root first.

| Key | Meaning |
|---|---|
| `id` | the part's name, lower case (its node in the GLB) |
| `parent` | the part it hangs from, listed before it; none for the root, which keeps everything the other parts do not take |
| `find` | `{"box": [[side, forward, up], [side, forward, up]]}` or `{"boxes": [box, ...]}` (a union): corners in normalised object coordinates (side -0.5 right to 0.5 left, forward -0.5 back to 0.5 front, up 0 ground to 1 top, each divided by the object's extent); a coordinate is a number, `null` (no bound) or `[low, high]` (the waist of the parent between those values nearest the parent's side: [conventions.md](conventions.md#articulated-parts)) |
| `joint` | `kind`: `turn`, `hinge`, `spin` (continuous; its range, default [-180, 180], only sets the sheet's sweep), `slide` (metres) or `fixed` (no axis, no range; a part without a joint is fixed); `axis`: `side`, `forward`, `up`, with `-` for the other way (right-hand rule), or `[side, forward, up]`; `pivot`: `cut` (default: the centre of the largest cut, where the part meets its parent), `center`, `bottom`, `top`, `front`, `back`, `left`, `right` (of the part's box) or `[side, forward, up]`; `range`: `[low, high]` containing 0 (the rest pose) |

A part takes its parent's geometry inside its region, so joints nest (a barrel elevating
on a turret turning on a hull). A thin box takes a surface (a door, a ramp, a hatch, a
nacelle) as a closed plate.

Settings: `region_split` `min_share` (number 0-0.5: a part with less of the surface is
flagged tiny; default 0.002), `snap` (bool: square a footprint turned by up to 30
degrees; default true; a walker or any object whose protrusions are not boxy may want
false) and `max_width` (number 0.1-10: a footprint wider than this share of its length
front to back is flagged as facing sideways; default: not checked); `layout_sheet` `front_height` (int 64-2048) and `strip_frames`
(int 1-32).

`accept`: `mesh_flags` only. The mesh flags of the parts and the stage held responsible:
`part <id> is empty` and `part <id> is tiny` (`unknown`: the region or the generated layout;
the flag says the order's box probably misses the part), `part <id> has <n> open edges where
its cut was not closed` (`parts`; only the cut's own: the generated mesh's holes are
reported, not flagged), `frame: ...` (`unknown`, with `max_width`), and the material flags
as the humanoid's.

A person may correct one variant's frame in `v<seed>_parts/frame.json` with
`"manual": true` and `front` (and `snap_deg`): the next run redoes that variant's parts,
the sheet and the decision.

## The creature recipe

Skinned, with its own skeleton (design, section 7). Per variant `v<seed>` (default 4):
`reference` -> `pose` -> `cutout` -> `mesh` -> `paint` -> `rig` -> `export`; then `review`
and `final`.

| Stage | Default | Other options | Skippable | Hands on |
|---|---|---|---|---|
| `reference` | `qwen21_base` at 1024x1536, text with `view_creature` | `qwen21_turbo`, `klein`, `input_image` | no | `rgb`, `mask` |
| `pose` | `qwen_edit_2511` (text `pose_creature`, else `view_creature`) | | yes | `rgb`, `mask` |
| `cutout` | `birefnet` | `qwen_edit_character` (text `cutout_creature`) | no | `rgba` |
| `mesh` | `trellis2` | `hunyuan21` | no | `game_mesh`, `raw_mesh` |
| `paint` | `hunyuan_paint` | `trellis2_texture` | yes | `painted_mesh` |
| `rig` | `skintokens` (seed 9001, 10 beams) | `unirig` | no | `rig` |
| `export` | `creature_export` (Blender) | | no | `glb`, `model`, `bone_map`, `mesh_report`, `motion_report`, `clips` |
| `review` | `layout_sheet` (Godot) | | no | `sheet`, `layout` |

`params`: `tris`, `texture`, `height` or `length`, `front`, `image_format`. `inputs`:
`reference`, `clips` (a `forge-creature-clips` library, the default of the motions'
`library` source) and `bone_map` (a file, or one per variant, as the humanoid's).

`body` (piece or family) is required: a plan or a template.

A plan, `{"chains": [...], "holds": {"right": role, "left": role}}`; a chain:

| Key | Meaning |
|---|---|
| `id` | lower-case letters and digits |
| `from` | `root` (default) or a chain listed before it |
| `toward` | `up`, `down`, `front`, `back` or `side` (a side chain needs pairs) |
| `pairs` | 1 to 4: the chain on each side; roles `<id>_l<i>`, `<id>_r<i>`, index 0 the highest. A chain hanging from a paired chain is paired the same way |
| `optional` | true: the map may go without it |
| `min_pairs` | a paired chain's pairs the map needs (0 to `pairs`; default all): the pairs from this index on may be missing (a rig tool may predict two arms for a four-armed body); a chain hanging from a missing pair is optional too |
| `joints` | 1 to 12: the joints (segments) of the chain |
| `bends` | per joint: `front`, `back`, `up`, `down`, `out`, `in` or `none`: the way the joint's segment tip moves when it flexes |
| `lengths` | per joint: relative lengths (default equal) |
| `direction` | `[side, forward, up]`: the exact direction (a template gives it) |

A template, `{"template": {"bones": [{"name", "parent", "head", "tail", "role", "bend",
"optional"}]}}` (head and tail in body heights, `[side, forward, up]`; role `root`, a chain
id or `<chain>_l<i>` / `<chain>_r<i>`; a role's bones follow each other), expands into a
plan: one chain per id, its direction, joints, bends and lengths from its bones.

`motions` (piece, or family as a default; the list of the design's section 13):

| Key | Meaning |
|---|---|
| `name` | the clip name the game looks up (lower case, letters, digits, `_`) |
| `source` | `procedural` or `library`; `text` and `bvh` are reserved for a later phase and refused |
| `kind` | `procedural`: `breathe`, `hover`, `undulate`, `gait`, `strike`, `aim`, `flinch`, `collapse` |
| `settings` | `procedural`: the kind's parameters ([conventions.md](conventions.md)) |
| `library` | `library`: `{"file": path, "clip": name}`, defaults the piece's `inputs.clips` and the entry's name; a file is relative to the order |
| `frames`, `fps`, `loop` | required frames (2 to 600) for `procedural`; a library clip is resampled to `frames` when given |
| `holds` | the held-item class during the clip (informative), or `null` |
| `contacts` | `{role: [frames] or "all"}`: where each foot is planted (a library clip's; a procedural clip makes its own) |
| `prompt`, `file`, `note` | `prompt` for `text`, `file` for `bvh` (later); a comment |

Settings: `creature_export` `foot_slide_cm`, `reach_shortfall_cm`, `below_ground_cm`
(number 0-100) and `stretch_p95` (number 0-10): the motion report's thresholds;
`layout_sheet` as above.

`accept`: `mesh_flags`, `missing_clips`, `foot_slide_cm`, `reach_shortfall_cm`,
`stretch_p95`, `below_ground_cm`. An export stopped by an invalid bone map is held on the
`rig`. A person corrects a variant's map in `v<seed>_export/bone_map.json` (`"manual":
true`, `roles` as `{role: [bones base to tip]}`), as the humanoid's.

The rig tools predict the skeleton they know for any body. The export turns a skeleton
predicted a quarter turn off, lets a single chain that no run points along take the nearest
remaining limb, moves the mirror run beside a single chain as its twin (a serpent's tail or a
propulsion unit predicted as two legs) and lets `min_pairs` and `optional` chains go missing
([conventions.md](conventions.md#creature)).

The order check refuses: a missing `parts` or `body`, an invalid part list, plan, template
or motion, a motion source not available yet, a library clip the library lacks, both
`height` and `length`, `parts` outside `articulated`, `body` and `motions` outside
`creature`, and a style without `view_articulated` or `view_creature` when the order has
such a piece. Complete samples:
[tests/fixtures/order_articulated.json](../tests/fixtures/order_articulated.json) (a
tracked vehicle, a disc with opening plates, a transport with nacelles, rotors, a ramp
and a door) and [tests/fixtures/order_creature.json](../tests/fixtures/order_creature.json)
(a serpent with a torso, a four-legged beast with one library clip, a walker given as a
template, and two blobs of the `figure` recipe).

Estimates: the parts, the creature export and the layout sheet count as `local_gpu`
(60 s each), the rig as the humanoid's.

## The held-item recipe

An object a figure holds ([design](superpowers/specs/2026-10-18-art-forge-design.md), section
7): a weapon, a tool, a lamp. Per variant `v<seed>` (default 4): `reference` -> `cutout` ->
`mesh` -> `paint` -> `item` -> `hold`; then `review` and `final` (with `accept` and `reserve`).

| Stage | Default | Other options | Skippable | Hands on |
|---|---|---|---|---|
| `reference` | `qwen21_base` at 1536x1024, text: `brief`, tier, family style, `global`, `view_held_item` | `qwen21_turbo`, `klein`, `input_image` | no | `rgb`, `mask` |
| `cutout` | `birefnet` | `qwen_edit_character` (text `cutout_held_item`, else `view_held_item`) | no | `rgba` |
| `mesh` | `trellis2` | `hunyuan21` | no | `game_mesh`, `raw_mesh` |
| `paint` | `hunyuan_paint` | `trellis2_texture` | yes | `painted_mesh` |
| `item` | `item_frame` (Blender: the painted mesh and Hunyuan3D Paint's `metal_roughness`) | | no | `glb`, `sockets`, `model`, `mesh_report`, `frame`, `socket_map` |
| `hold` | `figure_hold` (Blender: `inputs.figure` made ready, then the humanoid exporter with the item and the class's clips of `inputs.clips`) | | yes | `glb`, `model`, `mesh_report`, `motion_report`, `bone_map`, `clips`, `figure` |
| `review` | `item_sheet` (Godot) | | no | `sheet`, `layout` |

`style.view_held_item` is required: it must ask for a strict side view of the whole item with
its front to the right (the item stage's default frame; [conventions.md](conventions.md)).

`params`: `length` or `height` (one of them is required: the real size in metres, the extent
along the item's front, or its height), `tris`, `texture`, `image_format`. `inputs` (the hold
stage): `figure` (a rigged humanoid GLB: a rig tool's output, or a figure the humanoid exporter
wrote; it must have a skin) and `clips` (a motion library, [conventions.md](conventions.md)).

`item` (piece, or family as a default; a family's object merges key by key into its pieces)
is required:

| Key | Meaning |
|---|---|
| `class` | the hold class written in the socket file: a lower-case word of letters and digits (no `_`), the class the motion library's `holds` names (`rifle` for `rifle` and `rifle_aim`) |
| `template` | the class template of its sockets: `pistol`, `rifle`, `carbine`, `heavy`, `launcher`, `device`, `thrown` or `melee` (default: the class, when it is one of them) |
| `sockets` | optional `{name: socket or null}`: sockets added to or replacing the template's; `null` drops one (the grip cannot be dropped) |
| `clips` | optional: the clips of `inputs.clips` the hold stage shows (each must hold the class); default the library's first `idle` and first `aim` clip holding the class, else its first clip holding it |
| `note` | free comment |

A socket: `at` `[u, v, w]` (normalised item coordinates, each 0 to 1: `u` back to front, `v`
right to left, `w` bottom to top), `snap` (`section`: the centre of the item's cross-section at
station `u` among the surface points whose height `w` lies in `band`; `surface`: the closest
surface point to `at`; `none`: `at` itself; default `section`), `band` (`[low, high]` of `w`,
default `[0, 1]`), `required` (a variant without it is flagged; default false). The templates
(`tools/blender/held_item_geometry.py`; `*` required):

| Template | Squared | Sockets |
|---|---|---|
| `pistol` | no | `grip`* (u 0.22, band 0 to 0.55), `guard` (u 0.3), `muzzle`* (u 1), `top` (surface) |
| `rifle`, `carbine` | yes | `butt`* (u 0, band 0.25 to 0.85), `grip`* (u 0.3, band 0 to 0.45), `guard`* (u 0.62, band 0.2 to 0.65), `muzzle`* (u 1), `top` (surface) |
| `heavy` | yes | `butt` (u 0), `grip`* (u 0.34, band 0 to 0.45), `guard`* (u 0.58, band 0 to 0.6), `muzzle`* (u 1), `top` (surface) |
| `launcher` | yes | `shoulder`* (surface, u 0.42 on top), `butt` (u 0), `grip`* (u 0.45, band 0 to 0.4), `guard` (u 0.62), `muzzle`* (u 1), `top` (surface) |
| `device` | no | `grip`* (u 0.35), `muzzle` (u 1), `top` (surface) |
| `thrown` | no | `grip`* (u 0.5, the centre), `top` (surface) |
| `melee` | yes | `grip`* (u 0.15), `guard` (u 0.32), `tip`* (u 1) |

Settings: `item_frame` `front` and `top` (one of `+x`, `-x`, `+y`, `-y`, `+z`, `-z`: the glTF
axes the generated front and top point along; default `+x` and `+y`), `snap` (bool: square a
long item turned by 8 to 30 degrees; default the template's) and `tolerance_cm` (number 0-20,
default 1: how far from the surface a socket may lie before it is off the mesh, outside its
cross-section); `figure_hold` `height` (number 0.3-4, the figure's height in metres, default
1.8), `hand_socket_cm` and `penetration_cm` (number 0-100, the motion report's thresholds);
`item_sheet` `side_width` (int 128-2048, default 640) and `hold_height` (int 64-1024, default
twice `figure_height`). `review` as the humanoid's (`front`: the side view).

`accept`: `mesh_flags` only. The mesh flags and the stage held responsible: `socket <name> is
missing: ...` and `socket <name> is off the mesh: ...` (`unknown`: the generated shape, the
template or a manual file), `frame: the longest axis is ...` (`unknown`), and the material
flags as the other recipes'. The automatic pick among acceptable variants takes the smallest
fit excess (the mesh report's `fit`; [conventions.md](conventions.md)). A failed hold does not
reject its item: the sheet shows it.

`final` also copies the picked variant's `<piece>.glb` and `<piece>.json` to
`<out>/<family>/items/` (and removes them when no variant passed): that folder is a humanoid
order's `inputs.items`. A held-item piece cannot be named `items`.

A person may correct one variant in `v<seed>_item/frame.json` (`"manual": true`, `front`,
`top`, and optionally `rotate_deg` `[about x, about y, about z]` in place of the squaring) or
`v<seed>_item/sockets.json` (`"manual": true`, `sockets` `{name: [x, y, z]}` metres in item
space; a grip other than `[0, 0, 0]` becomes the origin): the next run redoes that variant's
item and hold, the sheet and the decision. A figure's bone map is corrected in
`v<seed>_hold/bone_map.json` as the humanoid's.

The order check refuses: a missing `item`, an unknown class without a template, an invalid
socket, neither or both of `length` and `height`, the hold stage without `inputs.figure` or
`inputs.clips`, a figure without a skin, a library without a clip holding the class (or an
`item.clips` name it lacks or that holds another class), a piece named `items`, `item`
outside `held_item`, and a style without `view_held_item`. Complete sample:
[tests/fixtures/order_held_item.json](../tests/fixtures/order_held_item.json) (a carbine and a
pistol, a lantern held by its handle, a tool case without the hold stage).

Estimates: the item, the hold and the item sheet count as `local_gpu` (60 s each).

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

Resume also compares each output's SHA-256 with its manifest entry. A missing or changed
output makes its producer ready again, before a dependant can use it. Hashes are cached
while file size and modification time stay unchanged; each new process checks them
again. To supply an edited asset, use an order input rather than overwriting an output.
The export's editable `bone_map.json` is an exception: manual corrections change the
export specification, while proposals and copies of the order's map do not trigger it.
Its GLB, reports and other generated files still get the integrity check.

A ComfyUI job that returns no output files fails even if no later job uses it. As with
other execution failures it is tried once more, then left failed. `-RetryFailed` (CLI
`--retry-failed`) gives failures already present at startup a fresh attempt cycle; newly
failed jobs are not retried indefinitely. Only jobs selected for this run are reopened.

## Local GPU jobs and the card

A job of kind `local_gpu` runs a local tool that uses the card (its runner,
`forge.local_gpu.RUNNERS[tool](factory, job, out_dir)`, returns the files, the arguments
it actually passed and its provenance record). Such a job runs alone:

- only when the whole ComfyUI queue is empty, prompts of any client included
  (`queue_running` and `queue_pending`), then after `/free`;
- after `/free` the job waits one pass (the server unloads its models);
- the processes `nvidia-smi` lists are logged; while one holds more than `gpu_busy_mb`
  of VRAM (default 2048) the job waits, up to `gpu_wait` seconds (default 300), then goes.
  When the driver reports no amount per process (Windows display drivers report `[N/A]`),
  the measure is the whole card's VRAM in use (`nvidia-smi --query-gpu`, else the server's
  `/system_stats`): the job waits while it is above `gpu_used_busy_mb` (default 4096).
  Without either measure the log says so and the job goes;
- the wait for an empty queue has no time limit, but it is visible: `state.json`
  `gpu_wait` (`job`, `since`, `seconds`, `reason`, `running`, `pending`, `holders` with the
  factory's own prompt count and the other clients' prompt ids, and `stalled` once it is
  longer than `queue_wait_warn`, default 3600 s), `-Status` prints it, the log repeats it
  every 15 minutes with the time waited, and `timings.csv` and the manifest record the
  seconds waited (`wait_seconds`, `waited_seconds`);
- while it runs `state.json` names it (`local_gpu_running`), even for a two-hour export;
- from the moment it is ready until it has run, nothing is sent to ComfyUI;
- a failure is tried once more, again only once the queue is empty; the log names an
  out-of-memory cause (from the error or the tool's log) and an external prompt that
  started meanwhile. After the second failure it is `failed` and only its dependants wait.
  A failure the tool says another try would repeat (the export's invalid bone map or a mesh
  it cannot export) is `failed` at once.

Every factory run holds the machine-wide GPU lock file (`%USERPROFILE%\.art-forge\gpu.lock`,
or the `FORGE_LOCK_DIR` folder, which the launcher passes to the detached factory with
`--lock-dir`) for its whole length, and each local GPU job and each
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
  the folder `<step>/`; plus `review/`, `pick_final.json` and `final/`, and with
  `reserve` also `review_reserve/` and `pick_final_reserve.json` (see the humanoid recipe);
  with `motions`, the piece's `motions/` and the family's `<out>/<family>/_motions/<name>/`;
- articulated and creature: as the humanoid, with the folders `v<seed>_parts/` and
  `v<seed>_export/` ([conventions.md](conventions.md));
- held item: as the humanoid, with the folders `v<seed>_item/` and `v<seed>_hold/`, and the
  family's folder of picked items `<out>/<family>/items/`.

At the root of `<out>`: `manifest.json` (each job with its state, specification hash,
files and hashes, times, tries and provenance), `state.json` (count per state: `done`,
`failed`, `blocked`, `waiting`, `ready`, `queued`, `held`; GPU hours left and, for held
reserve jobs, `estimated_gpu_hours_if_needed`; server queue;
`gpu_wait` and `local_gpu_running` (above); picks to review; failures and
`specification_errors`), `timings.csv` (`finished;job;family;asset;group;status;
exec_seconds;wall_seconds;wait_seconds`), `factory.log`, `program/` (the frozen copy the run uses) and, while
running, `factory.pid`.

## Picks and review

Picks are automatic: sharpness of the object and a clean background for concepts
(`object`), detail and even light for materials (`material`), detail for illustrations
(`image`). `pick_<step>.json` holds `chosen`, `manual`, `review` (`"pending"` or
`"manual"`), `metric`, `candidates` (each with `job`, `score` and its measures) and
`how_to_change`. To change it: put another candidate's id in `chosen` and set
`"manual": true`; the next pass redoes only what depends on the pick. A humanoid's
`pick_final.json` has the same keys plus `outcome` (`picked`, `manual` or
`no_variant_passed`; `chosen` and `variant` are `null` for the last), `accept` (the limits
used; `null` = not gated), `motion` (whether motion limits gated), `reserve` (`null`, or
the reserve variants, whether they were `released`, the second `decision` and a `note`), and for `no_variant_passed` `responsible_stage` (the stage held
responsible for the most rejected variants) and `responsible_counts`. Each candidate has
`job`, `variant`, `complete`, `reserve`, `class` (`acceptable` or `rejected`), `reasons`,
`responsible` (stages), `violations` (`measure`, `value`, `limit`, `clip`, `stage`,
`text`), `flags`, `rank` and `status`; `metric` says how the automatic choice was made.
`pick_final_reserve.json` is the same for the second decision. `state.json` counts each
among the picks to review.

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
