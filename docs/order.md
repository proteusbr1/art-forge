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

`-Check` lists every problem it finds (and exits with 1) or says the order has none. It
checks the format, known recipes and stage options, valid `skip` names, `variants`,
`params` types, that `inputs` files exist, that the style texts the recipes need are
present, that the tools the chosen stages need are set in `forge.local.json` and exist,
and, when the ComfyUI server answers, that the server has every node class the workflows
use (a read-only `GET /object_info`; nothing is sent). A complete sample that passes the
check is [tests/fixtures/order_generic.json](../tests/fixtures/order_generic.json).

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
| `global` | every concept (unit, vehicle, object_3d, concept, illustration, prop) |
| `view_unit`, `view_object`, `view_prop`, `view_vehicle`, `view_illustration` | the default view of each recipe (required when the order has a piece of that recipe) |
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
| `stages` | per-stage model choice `{stage: option}` from the recipe's catalogue |
| `skip` | list of stages to skip (only stages the catalogue marks skippable) |
| `params` | piece settings (table below) |
| `inputs` | files the game supplies `{name: path}`; paths are absolute or relative to the order file |
| `review` | review-sheet camera, for recipes with a review stage (table below) |

The core recipes have no swappable stages: their `stages` and `skip` must be empty, and
`review` does not apply to them. The stage catalogue is `forge/stages.py`.

`params` (an unknown name, a name the recipe does not read, or a wrong type is a problem):

| Name | Type | Meaning | Read by |
|---|---|---|---|
| `tris` | whole number above 0 | triangle (face) budget of a mesh | object_3d, prop, vehicle, unit |
| `texture` | whole number above 0 | texture size in pixels | object_3d, prop, vehicle, unit |
| `height` | number above 0 | figure height in metres | recipes with a stage that reads it |
| `head`, `hands`, `feet`, `widen` | number above 0 | proportions of a figure | recipes with a stage that reads them |

`inputs`:

| Name | Read by | Meaning |
|---|---|---|
| `reference` | object_3d, prop | an RGBA image (background removed) that replaces the concepts: the piece goes straight to the mesh, with no concept seeds and no pick |

`review`: `pitch` (number, degrees above the horizon), `directions` (whole number),
`figure_height` (whole number, pixels of the small figure), `front` (true or false: one
large front view too).

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

Job ids: `family/piece/step`, with steps `concept_s<seed>`, `pick`, `view_90`,
`view_180`, `view_270` (unit), `view_side`, `view_back`, `view_other_side`, `view_top`
(vehicle), `sheet`, `emissive`, `mesh`, and for materials `raw_s<seed>`, `pick`, `2k`,
`rolled`, `seam`, `seamless`.

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
| `mesh_role` | role of the mesh a Hy3DInPaint node writes |
| `group` | scheduling group (default `other`) |
| `kind` | default `comfy` |

The hash of a job's specification covers its workflow file, its values and the content
(SHA-256) of every input, whether produced by another job or supplied by the order. A
changed input file redoes only the jobs that use it (and what depends on them); moving
the order and its files elsewhere redoes nothing.

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
| `hunyuan21_shape_paint.json` | Hunyuan3D 2.1 shape and Paint | `$IMAGE$` `$SEED$` `$FACES$` | shape (6), albedo (12), metal-roughness (13), paint views (14), painted mesh (`mesh_role`) |
| `hunyuan21_paint_mesh.json` | Hunyuan3D 2.1 Paint over a given mesh, 10 steps | `$IMAGE$` `$MESH$` (local path) `$SEED$` `$TEXTURE$` | albedo (12), metal-roughness (13), paint views (14), painted mesh (`mesh_role`) |
| `trellis2_mesh.json` | TRELLIS.2 at 1024 with remesh | `$IMAGE$` (RGBA) `$SEED$` `$TRIS$` (the `DecimateMesh` target) | remeshed and reduced mesh (403), textured mesh (322) |

The texture bake of `trellis2_mesh.json` is optional: a job that lists
`forge.stages.TRELLIS2_TEXTURE_NODES` in `drop` makes only the untextured mesh (403). The
bake's texture size is set with `set` on node 147 (`texture_size`, default 2048) and node
196 (`resolution`, the UV atlas). Workflows with paid API nodes are refused on load.

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
  `seam_rgb.png`, the result `<piece>.png` and the preview `<piece>_tile2x2.png`.

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
`"manual": true`; the next pass redoes only what depends on the pick.

## Provenance

When a job runs, the manifest records under `provenance`: family, piece, kind, group,
workflow name and SHA-256, the model files the workflow names (every input ending in
`.safetensors`, `.ckpt`, `.pth`, `.gguf` or `.bin`, with node, class and input name), the
full prompt and negative, the seeds, the settings applied (`set`) and the inputs (file,
SHA-256, the factory job that produced it and the pick it went through, or
`"order_input": true` for a file the order supplied; images a raw job names directly on
the server appear with `"factory_output": false`).

    python -m forge --out <folder> --provenance <file.json>

writes one record per output file of every finished job: `asset`, `family`, `job`,
`step`, `role`, `file` (relative to `<out>`), `sha256`, `present`, `finished`,
`workflow`, `workflow_sha256`, `models`, `prompt`, `negative`, `seeds`, `settings`,
`inputs` (and `chosen` for picks). It needs no server and no GPU.
