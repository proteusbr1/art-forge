# art-forge: design

Date: 2026-10-18. State: approved design.

## 1. Goal

A local art factory that serves any game. A game hands over an **order** (the list of the
art it wants, piece by piece) and the factory spends the night generating on ComfyUI and
local tools, with no one watching, until it delivers files ready for the game to use.
The factory knows no game: it knows **recipes** (ways of producing) and **models per
stage**. The game picks the recipe and the settings of each piece in the order.

The first deliverable is a **factory of reproducible candidates for review**: every
output stays pending human review until a real night proves the whole chain delivers
usable characters. The contracts that make candidates comparable and auditable (explicit
bone maps, per-stage settings with effective parameters, mesh files kept through the
stages, motion and mesh reports, a two-level readiness check, machine-wide GPU
coordination) are detailed in the implementation plan.

## 2. Out of scope for this phase

- Generating new motion (text-to-motion). In this phase a humanoid's animation comes from
  a motion library supplied by the order, and a creature's from procedural motion kinds and
  role-space libraries (section 7); HY-Motion Lite is the planned motion option of the next
  phase.
- Finger rigs, hand grafting and fine contact with held items: they cost a lot and the
  first use case sees characters from above and from afar.
- Game-side adapters (how a game installs what the factory produces). They live in each
  game's repository.
- Running GPU inference while building. The factory is finished and checked dry before
  its first real run.

## 3. Principles

- **Neutral:** no game names, briefs, prompts, images or outputs in this repository.
  Examples and tests use generic subjects. Each game keeps its orders in its own
  repository.
- **Local:** only the HTTP API of a local ComfyUI server and local tools. No paid APIs or
  remote services; workflows with paid API nodes are refused on load.
- **The order decides:** recipe, settings, per-stage model choice and input files come
  from the order. A recipe only defines the sequence of stages and its defaults.
- **One night without stopping:** every variant runs end to end; the human pick happens
  at the end, looking at the finished product, and changing the pick redoes only what
  depends on it.
- **Provenance:** every output records model, revision, prompt, seeds, inputs and hashes,
  so it can be regenerated or audited.
- **Art rules belong to the order:** rules such as "no lettering or logos" or "describe
  only what should be seen" are written in the game's prompts; the factory does not
  impose them.

## 4. Repository and code origin

History starts clean in this public repository. Code and comments in English; PowerShell
compatible with Windows PowerShell 5.1 and ASCII only. System Python 3 with NumPy and
Pillow. Media, weights, outputs and the machine configuration are never committed.

Origin, recorded in `docs/provenance.md` when the code lands:

- **Factory core:** seeded from the author's earlier private factory (queue limits,
  resume by specification hash, a frozen copy of the program during a run, automatic
  picks marked for review, one retry, stop file, estimate, provenance export), with its
  tests.
- **Workflows:** production ones only: Qwen Image 2.1 base and Turbo (generation, hires,
  inpaint, tile), Qwen Image Edit 2511 (character and view angles), FLUX.2 Klein,
  TRELLIS.2, Hunyuan3D 2.1 shape and Paint, mesh reduction.
- **Humanoid rig export:** a generic port of the author's earlier humanoid exporter
  (Blender), without its game-specific parts.

## 5. Machine configuration

`forge.local.json` (ignored by git, with a committed `forge.local.example.json`): the
ComfyUI address, the Python with dependencies, the Blender Python (`bpy`), the
SkinTokens checkout and its Python environment, the UniRig folder, venv and weights
(optional), Godot. Heavy tools are not duplicated: the configuration points
to existing installs. A missing tool makes the order check fail before anything starts,
naming what is missing.

## 6. The order

One JSON file per order, written by the game's operator (a person or an LLM agent working
from the game's art list). It keeps the job-list format of the factory core (`version`,
`style`, `families`, `assets`, `tasks`) and adds, per piece:

| Key | Meaning |
|---|---|
| `recipe` | a recipe from section 7 |
| `variants` | how many variants (seeds) run end to end |
| `stages` | per-stage model choice: stage → option from the catalogue (e.g. mesh → `hunyuan21`) |
| `skip` | stages to skip (e.g. no rig for a humanoid statue) |
| `params` | piece settings: triangles, texture size, proportions (head, hands, width), height |
| `inputs` | files the game supplies: motion library, held items, a ready reference image |
| `review` | review-sheet camera: pitch, directions, figure height in pixels, plus a front view |

A family may set the same keys as defaults for its pieces. `inputs` paths are absolute or
relative to the order file; their hashes enter the job specification (changing an input
redoes only what depends on it).

The launcher checks an order before running (`-Check`): known recipe and options, required
keys, input files present, configured tools present and, when the server answers, the
ComfyUI node classes the workflows use.

## 7. Recipe catalogue

The recipe is chosen by how the thing moves, not by what it is:

| Recipe | How it moves | Examples | Phase |
|---|---|---|---|
| `concept` | 2D concept with background removed | character study, item | core |
| `illustration` | finished 2D picture | encyclopedia art, portrait, scene | core |
| `material` | seamless repeating 2D | floor, wall, sky | core |
| `object_3d`, `prop`, `vehicle` | rigid, no moving parts | crate, rock, weapon, vehicle seen whole | core |
| `humanoid` | skinned, human skeleton | soldier, humanoid creature, android | **this phase** |
| `articulated` | rigid parts on pivots | tank, turret, door, ramp, rotor, plated walker | built (2026-10-19) |
| `creature` | skinned, own skeleton | quadruped, insectoid, serpent, hovering torso | built (2026-10-19) |

A new recipe is added only when a way of moving appears that the table does not cover.
A new model is added as an option of a stage and becomes available to every recipe that
has that stage.

### The `humanoid` recipe

Each variant goes through every stage; none waits for a human pick.

| Stage | Default | Options in this phase |
|---|---|---|
| reference | Qwen Image 2.1 base, 60 steps, 1024x1536: full body in A-pose, empty hands held away from the body, neutral grey background | Qwen 2.1 Turbo, FLUX.2 Klein, image supplied in `inputs` |
| pose | Qwen Image Edit 2511: corrects pose and details of the reference (A-pose, hands apart and open, full body in frame, nothing held) | skipped with `skip` |
| cutout | BiRefNet, RGBA | Qwen Image Edit character |
| mesh | TRELLIS.2, then reduction to `params.tris` (default 25k) | Hunyuan3D 2.1 shape |
| paint | Hunyuan3D Paint 2.1, 10 steps, texture `params.texture` (default 2048) | TRELLIS.2's own texture |
| rig | SkinTokens (skeleton and weights); bones renamed to Godot's humanoid profile (`SkeletonProfileHumanoid`) from the skeleton's tree; bones without a role (fingers, extras) keep their names | UniRig |
| export | reduction with a baked normal map, proportions from `params`, region mask (skin, hair, glow), retarget of the motion library from `inputs` with planted feet and hands on the held items' sockets | no library: rest pose only |
| review | Godot off-screen: each variant seen by the `review` camera (small, as in game) and from the front (large), one sheet per piece | — |

Output per piece: one GLB per variant (mesh, humanoid-profile skeleton, animations,
texture and normal map), `model.json` (height, animations, sockets per held-item class,
bones, region reference values), `regions_mask.png` and the sheet. All formats are
documented in `docs/conventions.md`.

The motion library is a JSON of canonical joints per frame (named joints in body
coordinates, what each pose holds, how the feet stand). The game produces its own with
the timing it needs; the factory only retargets. Held items are GLBs with the socket
convention documented in the factory.

### The `articulated` recipe (2026-10-19)

Rigid parts on pivots: a tank with a turning turret and an elevating barrel, a hovering
gun platform, a disc whose plates open, a plated walker, an aircraft with tilting rotor
nacelles, a rear ramp and a sliding door. The game moves the parts itself: no skeleton and
no skinning. Every variant goes through every stage, as the humanoid's.

| Stage | Default | Options | Skippable |
|---|---|---|---|
| reference | `qwen21_base`, 1536x1024: the whole object in its rest position (style `view_articulated`) | `qwen21_turbo`, `klein`, `input_image` | no |
| cutout | `birefnet` | `qwen_edit_character` (text `cutout_articulated`, else the view) | no |
| mesh | `trellis2` | `hunyuan21` | no |
| paint | `hunyuan_paint` | `trellis2_texture` | yes |
| parts | `region_split` (Blender, `tools/blender/articulated_export.py`) | | no |
| review | `layout_sheet` (the factory lays the sheet out, Godot renders it) | | no |

Then `final`, the same decision as the humanoid's (acceptance limits: `mesh_flags`;
reserve variants). Output per variant: one GLB with one node per part, nested as the
parts' parents, each node's origin at its pivot, no animation; `model.json` (parts,
joints with kind, axis, range and pivot in glTF axes), `mesh_report.json` and
`frame.json`.

The order gives the parts per piece (key `parts`): id, parent, how the part is found
(`find`: a box, or a union of boxes, in normalised object coordinates) and its joint (kind
turn, hinge, spin, slide or fixed; axis; pivot; range). Joints nest through the parents.

Decisions, each with its reason:

1. **The whole object first, the parts derived from it** (option (a) of the request:
   separate the mesh into parts by regions the order gives), not views of each part meshed
   apart and assembled (option (b)). Reason: one generated object is one design by
   construction, while separately meshed parts come at their own scale, position and style
   and need an alignment that cannot be checked without the GPU; (a) is exact geometry
   that the tests check on synthetic meshes. (b) stays a later option for parts hidden
   inside the object (a mesh generator makes only the outer surface).
2. **Regions in normalised object coordinates** ([side, forward, up], each axis divided
   by the object's own extent), with an edge given as [low, high] resolved to the
   **narrowest cross-section** of the parent's triangles between those values. Reason:
   each variant has its own proportions; the waist between a turret and its hull, a
   nacelle and its wing, moves from variant to variant, and the section of the triangles
   finds it whatever the mesh density. A union of boxes takes a sub-assembly spread over
   several places.
3. **Exact cuts closed on both sides.** The parent is cut along the box's planes; the
   part takes the faces inside; both open edges are filled and given a plain `interior`
   material. Reason: a part that turns or opens must not show holes; a thin box gives a
   closed plate (a door, a ramp, a hatch) and the opening it leaves is closed too.
4. **Pivot `cut`** is the area-weighted centre of the largest cut on one plane (where
   the part meets its parent's body), not the centre of every cut. Reason: a box may also
   cut a thin piece crossing it (a barrel through a turret's box), which would pull the
   pivot off its ring. Named faces (`bottom`, `back`, ...) and points cover the rest.
5. **The frame.** The exported object faces glTF +Z. The order gives `params.front`, the
   glTF axis the generated front points along (default `+z`, toward the reference's
   camera); a footprint that came out turned by up to 30 degrees is squared; and a person
   may correct one variant's frame in its `frame.json` with `"manual": true` (it redoes
   only that variant's parts, as a manual bone map does). Reason: how a mesh generator
   orients an object seen at three-quarters has not been measured on the GPU; the review
   sheet shows each variant's front and the correction costs no new generation.
6. **No normal-map bake in this phase.** Reason: the mesh stage already reduces the mesh
   to `params.tris`; the cuts add few triangles; the painted texture carries the detail.
   A bake per part is a later option.
7. **The review sheet is laid out by the factory** (`forge/sheet_layout.py`) and Godot
   only renders its cells (`tools/review_godot/sheet.gd`): the object in every direction of
   the review camera and from the front, then one strip per joint sweeping its range.
   Reason: the layout is then tested without Godot, and the same renderer serves both new
   recipes. The humanoid's sheet (`main.gd`) is left as it is, so its jobs keep their
   specification.

### The `creature` recipe (2026-10-19)

Skinned, with its own skeleton: a serpent-bodied creature with a humanoid torso, a
four-armed biped, a two-legged beast with a big jaw, a legless torso over a propulsion
unit, a walker with reverse ankles.

| Stage | Default | Options | Skippable |
|---|---|---|---|
| reference | `qwen21_base`, 1024x1536 (style `view_creature`) | `qwen21_turbo`, `klein`, `input_image` | no |
| pose | `qwen_edit_2511` (text `pose_creature`, else the view) | | yes |
| cutout | `birefnet` | `qwen_edit_character` (text `cutout_creature`) | no |
| mesh | `trellis2` | `hunyuan21` | no |
| paint | `hunyuan_paint` | `trellis2_texture` | yes |
| rig | `skintokens` (seed 9001, 10 beams) | `unirig` | no |
| export | `creature_export` (Blender, `tools/blender/creature_export.py`) | | no |
| review | `layout_sheet` | | no |

Then `final` (acceptance limits `mesh_flags`, `missing_clips`, `foot_slide_cm`,
`reach_shortfall_cm`, `stretch_p95`, `below_ground_cm`; reserve variants). Output per
variant: the skinned GLB with one animation per motion, `model.json`, `bone_map.json`,
`mesh_report.json`, `motion_report.json` and `clips.json` (every clip in role space).

Decisions, each with its reason:

1. **The humanoid's stages up to the rig, the same options.** Reason: the reference,
   cutout, mesh, paint and rig steps do not depend on the body; SkinTokens and UniRig
   predict category-agnostic skeletons.
2. **A body plan, or a skeleton template as data.** The order's `body` is either a plan
   (chains: id, the chain they hang from, the way they point, pairs, and optionally their
   number of joints, the way each joint bends and their relative lengths) or a template
   (every bone with its parent, rest head and tail in body coordinates, role and bend),
   which expands into a plan with each chain's exact direction. Reason: a plan is short to
   write for most bodies; a species whose limbs a plan cannot say (three-segment legs with
   a reverse ankle) gives its template, and both go through the same mapping and motions.
3. **The predicted bones are mapped onto the plan without human work**, by the tree and
   the geometry: from the root down its single children to the first branch (the hub);
   candidate runs of single children hanging from the parent chain (a short branching
   connector also offers its children); each scored by its direction against the chain's
   and its length; the best pairs taken first; left and right by the side of the body;
   a chain that others hang from carries on through its branches (a spine past the arms);
   a missing chain at the end of its parent takes the bones past the parent's last branch
   (a neck grown into the spine). Every map is validated; an invalid map stops the export
   and a person corrects `bone_map.json` (`"manual": true`), or the order gives
   `inputs.bone_map`, as the humanoid's. Reason: the rig tools predict a different
   skeleton per variant, so per-variant hand work cannot be the normal path.
4. **Joints and bends.** Each mapped bone takes the joint of its chain its middle falls in
   (by length, against the chain's relative joint lengths) and that joint's bend. Reason:
   a predicted chain rarely has the plan's bone count; the bend decides which way a knee or
   a reverse ankle flexes in a gait and how limbs curl in a fall.
5. **Movements come from the `motions` list of section 13**, per piece with a family
   default, each entry with its name, `source`, frames, fps, loop, holds, contacts and
   settings. Sources made now: `procedural` (a motion kind with its settings: `breathe`,
   `hover`, `undulate`, `gait`, `strike`, `aim`, `flinch`, `collapse`) and `library` (a
   clip of a role-space library, `forge-creature-clips`, resampled to the entry's frames).
   `text` and `bvh` keep their names and keys (`prompt`, `file`) and are refused by the
   order check until they exist. Reason: one list for every source, so the next phase adds
   sources without renaming anything.
6. **Clips in role space, the canonical form.** Every exported creature also writes
   `clips.json`: per frame the root's move (body heights) and per role the chain's
   accumulated turn at each position along it (0 to 1, body axes). Another skeleton with
   the same roles plays it whatever its bone counts (the library source does exactly that).
   Reason: as the humanoid's canonical joints, this is what a motion generator or a game
   can hand over once for every variant.
7. **Legged gaits by exact inverse kinematics.** Each foot follows a stance (planted,
   moving back at the body's speed) and a swing (lifted forward); hip and knee are solved
   by the law of cosines, the bones below them keep their rest orientation (a long foot
   stays a reverse ankle); the body lowers so every foot stays in reach. Stance frames and
   travel follow the humanoid library's convention, so the motion report measures foot
   slide the same way. Default phases alternate left and right and along each side (a
   biped walks, four legs trot, six legs go in tripods).
8. **No clip sinks into the ground**: a pose whose lowest bone would go below the rest
   pose's is lifted; a fall keeps its lowest bone on a floor going down to `lie`. The
   motion report adds `below_ground_cm`, an acceptance limit of the creature only, so the
   humanoid's limits, and the specification of its jobs, do not change.
9. **Item bones** (`item`, `item_l`, `item_ref` as the humanoid's) at the tips of the roles
   the plan's `holds` names. Reason: armed creatures hold the game's items.
10. **No normal-map bake and no carry of other maps from the painted mesh in this phase**:
    the paint stage's metallic-roughness map replaces the rig's, the rest stays as the rig
    delivers it, and the written GLB is checked. A later option, as for the articulated
    recipe.

### Limbless figures

A shapeless figure that floats or crawls (a blob) needs no recipe of its own: the `figure`
recipe makes a painted, unrigged mesh (`mesh_painted_mesh.glb`, at `params.tris` faces and
`params.texture`), and the game animates it (a bob, a squash). The sample creature order
shows two.

## 8. Running and the GPU

Launcher `Run-Forge.ps1`: `-Order <order> -Out <folder>`, plus `-Check`, `-Estimate`,
`-Status`, `-Stop`, `-Now`, `-Foreground`, `-RetryFailed`, `-Only`, `-Limit`. It runs
detached from the console; the output folder belongs to the game, never to this
repository.

- ComfyUI jobs: at most 6 factory prompts in the server queue, Hunyuan jobs alone after
  `/free`, bursts of the same model, and interactive users can jump the queue with
  `front: true`.
- Local stages that use the GPU (SkinTokens or UniRig, normal-map baking in Cycles, the
  Godot review render) form an exclusive group: they run only with no factory prompt in the ComfyUI
  queue and after `/free`; while they run, nothing is sent to ComfyUI. Hunyuan never runs
  outside the server.
- Local stages without GPU run between passes.
- Pick: `<out>/<family>/<piece>/` holds the final pick file pointing to the chosen
  variant (initial automatic pick marked for review); the chosen variant is copied to
  `<out>/<family>/<piece>/final/`. Changing the pick (marking it manual) redoes only the
  copy.
- State, timing log and run log as in the core (`state.json`, a CSV of times, the factory
  log), with local stages timed by the process clock.

## 9. Errors

- A failed job is retried once; then it is marked failed and only its dependants wait;
  the rest of the night goes on. `-RetryFailed` tries again.
- A failed local stage keeps its error output next to the job.
- A variant that fails at a stage does not stop the other variants of the piece; the
  sheet shows the failure in place of the figure.
- Server down: the factory waits and retries.

## 10. Verification of this phase (no GPU)

- Unit tests without a server: order check, recipe expansion with `stages`, `skip`,
  `variants` and `params`, specification hashes with `inputs`, bone renaming on a sample
  skeleton, sheet assembly.
- The inherited core tests still pass.
- `-Check` and `-Estimate` on a generic sample order (a humanoid, an object, a material).
- Smoke test of the local stages without inference: from an existing rigged skeleton,
  export, renaming, retarget of a sample library and sheet, checking the re-imported GLB
  (bones, animations, scale) and the sheet image.
- Provenance exported from the smoke test.
- The articulated and creature recipes (2026-10-19): unit tests of the part geometry, the
  role map on synthetic skeletons (a serpent, a four-legged beast, a four-armed biped, a
  hovering torso, a template with reverse ankles) and the procedural clips (loops close,
  planted feet stay in the ground's frame, reach, falls, role-space round trip); recipe
  expansion and order checks; a night with the fake server and stand-ins; the sheet's
  layout; `-Check` and `-Estimate` on their sample orders; and smoke tests of the parts
  exporter, the creature exporter and the layout sheet on synthetic Blender fixtures. The
  specification hashes of the sample humanoid and core orders are pinned
  (`tests/test_spec_pins.py`). Nothing of these recipes has run on generated meshes.

## 11. Who writes and runs orders

An operator (a person or an LLM agent) writes the orders with the settings it judges best
and starts the factory, after checking that the ComfyUI server is free. A human judges
the review sheets. The first real night runs a small order to validate the `humanoid`
recipe before spending a night on a full art list.

## 12. Next phases

1. A first real humanoid order from a game, with its adapter in that game's repository.
2. Full art lists in the existing recipes.
3. `articulated` and `creature` (built 2026-10-19, section 7): their first real night,
   which will say whether the default frame (`params.front`) and the regions fit generated
   meshes and how the rig tools' skeletons map onto body plans.
4. The order's `motions` list (section 13) for the remaining sources: `text` (HY-Motion
   Lite) and `bvh` for the humanoid and the creature, and `library` for the humanoid. The
   creature already reads the list with `procedural` and `library`.
5. Held items with sockets for creatures beyond the item bones of `body.holds`.
6. Publishing the research behind the stage defaults (which model won each stage, steps,
   pitfalls) as documentation.

## 13. Next phase: the `motions` list in the order

Today the order can only hand over finished movements (a motion library in `inputs`).
In the next phase a humanoid piece (or its family, as a default) lists the movements it
needs, one entry per movement, and each entry says where the movement comes from. The
factory never decides which movements a character has; with no list and no library the
character keeps its rest pose.

| Key | Meaning |
|---|---|
| `name` | the clip name the game will look up (e.g. `walk_rifle`) |
| `source` | `library`, `text` or `bvh` |
| `library` | for `library`: the library file and the clip name inside it (defaults to the order's `inputs.clips` and `name`) |
| `prompt` | for `text`: what the movement is, described plainly (e.g. "throws a grenade overhand and steps forward") |
| `file` | for `bvh`: the capture file, plus the frame range to take |
| `frames`, `fps` | length and rate the game wants; a generated or captured clip is resampled to them |
| `loop` | the clip must close on itself (walk cycles, idles) |
| `holds` | what the hands hold during the clip (a held-item class) and where |
| `contacts` | the frames where each foot is planted (stance), used by retargeting and by the motion report |
| `settings` | per-source settings, e.g. steps and seed for HY-Motion Lite, recorded with their effective values |

Every source ends in the same canonical-joint format the motion library uses today, so
retargeting, foot planting, hand placement and the motion report work the same whatever
the source. A `text` clip is generated once per piece family (not per variant) and keeps
its own provenance (model, prompt, seed, effective settings), so the same movement can be
reviewed and reused across variants. The library of today becomes one source among three.

State on 2026-10-19: the creature recipe reads this list (keys `name`, `source`, `kind`,
`settings`, `library`, `prompt`, `file`, `frames`, `fps`, `loop`, `holds`, `contacts`,
`note`) with the sources `procedural` (a motion kind of the creature, its parameters in
`settings`) and `library` (a role-space clip, `forge-creature-clips`); `text` and `bvh`
are refused by the order check until they exist. A creature's canonical clip format is
role space (accumulated turns along each chain), the creature's counterpart of the
canonical joints; a text-to-motion source for creatures will have to end in it. The
humanoid does not read the list yet.

Open points for that phase's design: HY-Motion Lite runs through its own ComfyUI node and
a separate server instance; generated motion needed contact, hip and cycle corrections in
earlier research, so the correction steps and their acceptance thresholds have to be
part of the stage, not manual work.
