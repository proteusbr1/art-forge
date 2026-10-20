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

- Generating new motion (text-to-motion) for creatures. A humanoid's motions come from
  libraries, captures and HY-Motion Lite (section 13, built 2026-10-19); a creature's from
  procedural motion kinds and role-space libraries (section 7).
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
| `humanoid` | skinned, human skeleton (optionally one approved body for every variant, section 14) | soldier, humanoid creature, android | **this phase** |
| `articulated` | rigid parts on pivots | tank, turret, door, ramp, rotor, plated walker | built (2026-10-19) |
| `creature` | skinned, own skeleton | quadruped, insectoid, serpent, hovering torso | built (2026-10-19) |
| `held_item` | rigid, held by a figure (item space, sockets) | carbine, pistol, lantern, tool case | built (2026-10-19) |
| `iso_tile` | a 2D sprite on a fixed isometric grid that keeps a given footprint | floor, wall, fence, crop field, tree, bale | built (2026-10-20) |

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
8. **Only the cut's own open edges are a failure** (after the first real night, 2026-10-19).
   The generated meshes (TRELLIS.2 remeshed, then painted) are not watertight: on the night
   every one of 42 variants had open edges before any cut (9 to 428), and counting every open
   edge of a part after its cut rejected all of them. The exporter now takes the mesh's open
   edges before the cuts; a part's open edge along one of them is the source's, not the
   cut's. Where a cut crosses such a hole its section is an open chain: the nearest free
   ends on the plane are bridged before the fill, a loop the fill leaves gets a fan, the
   round is repeated, and a lone edge of a sliver on the plane is reported, not flagged.
   Reason: a cut left open is a real failure a person would see; the source's holes are not
   the parts stage's and were there before.
9. **A `[low, high]` edge resolves to the waist nearest the parent**, not to the narrowest
   level anywhere in the range (same date). Reason: on the night's tanks the narrowest
   section in a turret's range was often its own tapering top, which left the turret on the
   hull, and a barrel's narrowest section lies anywhere along it. The first level, from the
   parent's side, where the section stops shrinking (and is no wider than the narrowest plus
   half the range's spread) is the ring above the deck and the barrel's base at the turret's
   face. A tiny or empty part's flag now says that the order's box probably misses the part,
   and the `max_width` setting flags an object generated facing sideways (two of the night's
   30 vehicles were wider than long).

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
11. **The map takes the skeletons the rig tools really predict** (after the first real
    night, 2026-10-19, where 11 of 18 creature exports stopped on an invalid map). SkinTokens
    predicted the same humanoid template for every body: two arms with hands and two
    four-bone legs, whatever the order asked. So: `min_pairs` lets a paired chain go without
    its last pairs (the four-armed body got two arms on every variant); a required single
    chain no run points along takes the nearest remaining limb (a serpent's tail predicted as
    legs pointing down); the mirror run beside a single chain becomes its twin and moves with
    it (the other leg of that tail, the other half of a propulsion unit); a single chain must
    run along the middle unless it points its way and has a twin (a beast's vestigial arms
    were taken for its neck); a chain with fewer bones than joints gives them the joints in
    order. Reason: per-variant hand work cannot be the normal path (decision 3), and each
    rule answers one measured failure.
12. **A skeleton predicted a quarter turn off is turned by its feet** (same date). Two of the
    beast's four rigs lay with their left and right front to back (the generated mesh faced
    glTF +X). The mirrored runs give the left-right line; past 45 degrees from the side, the
    exporter turns the rig a quarter turn so the feet of the pairs at the root path point to
    the front, or stops with a `facing:` problem when no feet tell. Smaller turns are not
    made. Reason: a sideways creature walks sideways in the game; the feet are the cue that
    held on the night (within 2 degrees of the side axis on both turned rigs).

### The `held_item` recipe (2026-10-19)

An object a figure holds: a carbine, a pistol, a lantern, a tool case. The humanoid exporter
already puts held items in a figure's hands, but it needs, per item, `<name>.glb` in item
space and `<name>.json` with its class and sockets (the socket file of `docs/conventions.md`).
This recipe makes the object and that pair, so a family's folder of picked items can be a
humanoid order's `inputs.items` as it is.

| Stage | Default | Options | Skippable |
|---|---|---|---|
| reference | `qwen21_base`, 1536x1024 (style `view_held_item`: a strict side view, the front to the right) | `qwen21_turbo`, `klein`, `input_image` | no |
| cutout | `birefnet` | `qwen_edit_character` (text `cutout_held_item`, else the view) | no |
| mesh | `trellis2` | `hunyuan21` | no |
| paint | `hunyuan_paint` | `trellis2_texture` | yes |
| item | `item_frame` (Blender, `tools/blender/held_item_export.py`; the geometry in `held_item_geometry.py`, no `bpy`) | | no |
| hold | `figure_hold` (the order's reference figure made ready by `held_item_figure.py`, then the humanoid exporter with this item and the class's clips) | | yes |
| review | `item_sheet` (`forge/item_sheet_layout.py` lays it out, `tools/review_godot/item_sheet.gd` renders it) | | no |

Then `final` (acceptance limit `mesh_flags`; reserve variants), which also copies the picked
item's `<name>.glb` and `<name>.json` to `<out>/<family>/items/`. Output per variant
(`v<seed>_item/`): the item GLB in item space, the socket file, `model.json`,
`mesh_report.json`, `frame.json` and `sockets.json`; and, with the hold stage,
`v<seed>_hold/` (the reference figure holding the item, its reports).

The order gives per piece (or family) `item`: `class` (the hold class written in the socket
file, the word the motion library's `holds` names), `template` (a class template; default the
class), `sockets` (sockets added to or replacing the template's, or `null` to drop one) and
`clips` (the library clips the hold stage shows); `params.length` or `params.height` (the
real size, required); and for the hold stage `inputs.figure` (a rigged humanoid GLB) and
`inputs.clips` (the motion library).

Decisions, each with its reason:

1. **The rigid object chain of the other variant recipes** (reference, cutout, mesh, paint, as
   the articulated recipe's, with every variant end to end and the decision at the end), not
   `object_3d`'s concept seeds with an early pick. Reason: the factory's rule is that the
   human pick happens on the finished product; an item's quality shows only once it is
   meshed, framed and in a hand.
2. **The concept is a strict side view with the front to the right** (the order's
   `view_held_item` says so; the order check requires it), so the default frame is fixed by
   the recipe: the generated front points along glTF +x and its top along glTF +y. Reason:
   a side view shows an item's whole profile (grip, magazine, barrel). The first real night
   showed that the view does not fix the mesh's orientation: the frame is now found
   (decision 11). The stage settings `front` and `top` (any glTF axis) fix it for a whole
   piece instead, and a person corrects one variant in its
   `frame.json` (`"manual": true`, with `front`, `top` and optionally `rotate_deg`), which
   redoes only that variant's item and hold, as the articulated recipe's frame file.
3. **Squared to its principal axes only when long and straight and clearly turned.** A class
   template marks whether its items are long and straight (rifle, carbine, heavy weapon,
   launcher, melee: yes; pistol, device, thrown object: no). Such an item is turned so its
   longest principal axis lies on +x, then rolled so its second axis lies on +z, only when the
   turn is between 8 and 30 degrees and the item is elongated. Reason: the principal axes of
   a gun with a dropped stock, a grip and a magazine are not its bore's: about 5 degrees off
   on the tests' synthetic carbine, so a mesh that came out straight must not be tilted by
   them; a pistol's grip pulls them much further. An elongated item whose longest axis is more
   than 30 degrees from +x is flagged (`frame: ...`), not turned: the concept was probably not
   a side view.
4. **Real size from the order** (`params.length`: the extent along the item's front axis, or
   `params.height`), required, because a generated mesh has no unit. The socket file's
   `length_m` is the largest extent and its `box` the item's box in item space.
5. **Sockets from class templates, snapped to the mesh.** A template gives each socket a
   position in normalised item coordinates (`u` back to front, `v` right to left, `w` bottom to
   top) and a snap: `section` (the centre of the cross-section at station `u` among the surface
   points whose height lies in the socket's `band`: a pistol grip under the receiver, a
   foregrip under the barrel), `surface` (the closest surface point: a shoulder rest, a top
   rail) or `none`. Shipped templates: `pistol`, `rifle` and `carbine` (the same), `heavy`,
   `launcher`, `device`, `thrown`, `melee`; the order adds or replaces sockets in `item.sockets`.
   Reason: the hand positions of an item class are stable proportions of the item, while the
   exact place of a grip varies per variant; the section finds it whatever the mesh density.
   The grip is the origin (the convention of the humanoid exporter). A person corrects one
   variant's sockets in its `sockets.json` (`"manual": true`, positions in item space; a grip
   other than the origin moves the origin there), which redoes only that variant.
6. **Structural flags gate, fit measures only rank.** Mesh flags (as the articulated recipe's
   material flags), a required socket that is missing (no geometry at its station), a socket
   off the mesh (farther than `tolerance_cm` from the surface and outside the extent of its own
   station's cross-section) and the frame flag reject a variant by default (`mesh_flags` 0).
   Among the acceptable variants the final pick takes the smallest fit excess: the largest
   move of a snapped socket from its template position (share of the length), the squaring
   turn and the share of the surface outside the largest connected piece, each divided by a
   reference value (0.15, 15 degrees, 0.05) and summed. Reason: the factory gates only on
   defects it can name; the fit measures say how well a variant matches its class's layout,
   which is a ranking, and their reference values have not been measured on generated meshes.
7. **The hold reuses the humanoid exporter** on the order's reference figure, with a folder of
   this one item and a library of the class's clips (`item.clips`, else the library's first
   `idle` and first `aim` clip holding the class), at the figure's full mesh (no reduction, so
   no normal-map bake). The figure may be a rig tool's output or a figure the humanoid
   exporter already wrote: `held_item_figure.py` removes the item bones, the `item_ref` mesh
   and the animations first. Reason: the request was one placement code, not two; the sheet
   shows exactly what a humanoid night will do with the item. The humanoid exporter is used
   unchanged (its hash is in the humanoid jobs' specifications).
8. **The hold is a review aid, not a property of the item.** It is a stage of its own after
   the item, outside the variant's decision: a failed hold (for example a figure whose bone
   map is invalid) shows on the sheet and does not reject the item; its motion report's flags
   are lines on the sheet. The review waits for the holds and its specification covers their
   files; the final decision does not wait for them. Reason: the same figure fails the same
   way for every variant, which says nothing about the items.
9. **One sheet of its own.** Per variant: the item alone from every direction of the review
   camera at the scale of a 1.8 m figure `figure_height` pixels tall (as in the game), a large
   side view (front to the right) with every socket marked and labelled and a line of their
   positions, the figure holding the item in each hold clip from four directions, the flag
   lines and the fit line. Laid out by `forge/item_sheet_layout.py` (tested without Godot) and
   rendered by `item_sheet.gd`, which puts a second instance of the item GLB under the
   figure's `item_ref` node. Reason: the articulated and creature sheet's files are in their
   jobs' specifications, so they are left as they are.
10. **The family folder of picked items** (`<out>/<family>/items/`, the picked variant's
    `<name>.glb` and `<name>.json`, written by `final` and removed when no variant passed).
    Reason: a humanoid order's `inputs.items` is a folder of such pairs; the whole family's
    picks are one folder. A piece cannot be named `items`. The variant folders and `final/`
    hold other JSON files too; none of them has both `class` and `sockets`, so the humanoid
    exporter would skip them in any case.

After the first real night (2026-10-20: 30 pieces, 88 variants with the reserves, TRELLIS.2
meshes painted by Hunyuan3D Paint), which accepted 26 variants and left 11 pieces without a
pick, mostly for the frame flag (`frame: the longest axis is 90 degrees from the front axis`),
the item tool was changed:

11. **The frame is found, from the cutout's silhouette, not assumed.** Measured on the night:
    the generators kept the picture's up on glTF +y (every gun variant) but laid its
    left-right on glTF +z (44 variants), +x (15), -x (16) or -z (13) (the frames found,
    checked by eye on side views of every variant against its picture), so the fixed default
    (front +x) was 90 degrees off on 57 variants and reversed on 16; 13 of the 26 accepted
    variants and 9 of the 19 picks faced the wrong way, which no flag caught (a compact
    template is not flagged; a reversed item has its longest axis on +x). The item stage now
    takes the cutout the mesh was made from and scores every frame with its front and top on
    glTF axes (and on the principal axes of a long item turned further than 30 degrees from
    every axis) by the overlap of its side silhouette with the cutout's; the best frame wins,
    with a pull towards a top on glTF +y and against a tilt. Reason: the generators rebuild
    the picture, so their mesh seen from the right side is the picture; on the night the
    frame chosen for each of the 46 gun variants scored 0.55 to 0.88 and ranked at least 0.13
    above the best frame with another front (but for the crossed mesh of decision 12). A
    cylinder or a box rolled a quarter turn about its length can match a little better (by
    up to 0.021); the pull of 0.03 keeps their levers and windows on top.
12. **A gun's grip and barrel check the silhouette, and real failures stay flagged.** For the
    gun templates, the profile's height is larger in the back half (grip, magazine, stock),
    the barrel is the thin end and the bottom edge varies more than the top: measured on all
    46 gun variants in the frame their silhouette chose, never against it (front cue 0.70 to
    6.3, up cue 0.019 to 0.25). A clear cue against the silhouette is a flag (the cues
    disagree), as are a mesh whose best silhouette score is below 0.5 (none on the night), a
    long item whose two largest spreads are within 1.3 (a heavy-weapon variant came out as two
    crossed bodies) and a long item whose longest axis is more than 30 degrees from the found
    front. Without a cutout (the tool run alone) the cues decide a gun's front and top, and a
    compact item keeps the default frame. Reason: the gate must name what it rejects; a
    round or symmetric item that matches several frames equally has no wrong frame to flag.
13. **The squaring follows the cutout, and sockets snap to the geometry near their station.**
    With a cutout, a long item is turned about its side axis to the silhouette's best match
    (3 to 30 degrees) and not rolled: the night's meshes came out straight (every gun but
    two poor meshes within 2 degrees of its picture), while the principal axes of three guns lay 8.6 to 11.5
    degrees off their bore and squaring by them dropped their silhouette scores (0.72 to
    0.46, 0.86 to 0.68, 0.80 to 0.78). A section socket keeps the run of points nearest its
    height (the centre of a band holding a tube's bottom and a grip's top fell in the air
    between them) and, when its centre is not on the mesh, moves to the nearest station
    within 0.2 of the length whose centre is, centred on the piece it found; with none it is
    missing, as before. The launcher template's grip moved from 0.45 to 0.3 of the length:
    the night's launcher variants had their pistol grips at about 0.2 to 0.35. The item
    stage's specification moves on purpose (the cutout is a new input, the scripts changed):
    a resumed night redoes the items, the holds, the reviews and the decisions.

Verified on 2026-10-20, without the GPU: the geometry on synthetic meshes made from the
night's failures (a carbine laid along each of glTF +x, -x, +z and -z, upside down, turned 45
degrees about the up axis, tilted 15 degrees, with and without its picture; a pistol reversed;
a grenade lying on its side; two crossed bodies; a picture of another object; a launcher grip
behind its station and a grip out of reach) and the Blender smoke test with a cutout. On the
CPU, on copies of the night's 88 variants (all 30 pieces): 87 acceptable (before: 26), the one
rejected the crossed heavy weapon; every accepted gun faces its picture within 2.5 degrees of
tilt (the silhouette's best turn) and 1 degree of yaw (the item's top view), but one poor
pistol mesh (score 0.55) left 6 degrees of tilt; every other item within 9.2 degrees (the
worst, broken meshes). Never ran: the changed item stage in a factory night, the holds and the
review sheets of the new frames, and a person's look at the picks (several accepted meshes are
visibly poor, fans of long triangles: the gate has no measure of mesh quality).

### The `iso_tile` recipe (2026-10-20)

A 2D sprite on a fixed isometric grid that must keep a given footprint: the floors, walls and
objects of a 2D strategy game's map, each drawn in a cell of a 2:1 grid and joined to its
neighbours by the game. The game supplies each piece's own sprite (`inputs.sprite`): it fixes
the footprint, which the game's collision and sight volumes match, and it is the picture's
guide. The order says the piece's kind and, for walls, the height and thickness the game's
volume gives (`tile`), and the factory remakes the sprite at the order's scale with an albedo,
a normal map, a shaded frame and an optional emissive frame.

| Stage | Default | Options | Skippable |
|---|---|---|---|
| guide | `unproject` (local) | | no |
| picture | `qwen_edit_2511` | | yes (a floor or wall is then its seam group's material) |
| edges | `inpaint_ring` (Qwen Image 2.1 Turbo inpaint) | | yes |
| form | `picture` (objects) | `mesh` (TRELLIS.2, Hunyuan3D Paint, a Blender render) | no |
| sprite | `tile_sprite` (local) | | no |
| review | `tile_review` (local) | | no |

Then `final` (acceptance limit `mesh_flags`, the sprite's flags; reserve variants). Per family
the seam groups' materials and one set sheet.

Decisions, each with its reason:

1. **The grid's own geometry, exactly.** `tools/blender/iso_grid.py` (no `bpy`) knows the
   orthographic camera of a 2:1 grid from a few numbers (cell, diamond, level, the diamond's
   top corner): a floor and a wall are parallelograms of the cell, an affine image of a flat
   texture. A face is taken out of the original sprite into a flat texture and put back by the
   same map, at the order's scale with four samples per pixel and axis. Reason: the footprint
   is then exact by construction (a floor covers its diamond, a wall its slab), and what joins
   neighbours (texture edges) can be made to match in texture space, where a picture of the
   sprite itself has a perspective to keep. The game's numbers are order keys (`grid`), so the
   recipe serves any 2:1 grid.
2. **Floors and walls are repainted flat, from the original's face.** The guide is the
   original's face, upscaled and blurred (its pixel steps gone), with the seam group's material
   detail laid over its colours; Qwen Image Edit 2511 repaints it as the brief says. Reason:
   pieces differ by content a seamless material lacks (a window, a door, a burnt patch, crop
   rows), and the edit keeps the original's layout while it adds detail; a text-to-image
   texture would lose the layout, and projecting a plain material on every face (the other
   candidate) would make every plank wall the same. A piece that is its group's plain material
   skips the picture.
3. **Seams are owned by a shared material.** Every face of a seam group has the group's
   seamless material (made once, by the `material` recipe's roll and inpaint chain, at the
   group's texture size) in the outer band of its texture; the ring between band and picture is
   repainted by inpaint, and the exact band is put back after (the VAE changes it slightly).
   Any two pieces of a group then join as the material joins itself. A group's `span` (up to 8
   x 8 tiles) is the variant grid of its pieces: one texture covers the span and the game
   picks a frame by the tile's position, so a field repeats only every span. Reason: a seam
   cannot be checked between pictures made apart, only enforced; giving every piece of a
   group the same border is the cheapest exact rule, and a span keeps the border's repetition
   rare. Pieces of different groups meet at a visible edge, as the original tiles did.
4. **Objects are pictures, or meshes when they are solid.** `form` `picture` (default): the
   original sprite (or a block of it over the variant grid, a field of crops) upscaled onto
   grey, repainted by the edit, cut by BiRefNet's mask and cut back into the grid's columns
   (each pixel to the front-most tile whose column holds it, so the frames drawn back to front
   give the picture again). `form` `mesh`: the picture meshed by TRELLIS.2, painted by
   Hunyuan3D Paint and rendered by Blender on the CPU in the grid's camera, the mesh turned (8
   headings), scaled and placed so that its silhouette overlaps the original's best; the
   render gives an unlit albedo and exact normals. Reason: foliage, crops, debris and thin
   posts come out of mesh generators as blobs, while a bale, a crate or a staircase meshes
   well and gains true light; the order chooses per piece.
5. **No light is painted.** The albedo is asked flat and evenly lit and checked for a
   brightness slope (painted light); the shaded frame is made by the factory from the albedo
   and the normals (a face's relief from its albedo's detail, an object's from its silhouette's
   dome and detail, a mesh's from its geometry) with one light setup for the whole order,
   hollows darkened and a wall darker at its foot, then brought to the original's mean
   brightness. Reason: a game that lights the albedo itself (dynamic lights through the normal
   map) needs it unlit; one that draws a lit picture gets one whose light is the same on
   every piece, whatever each picture did; and a game whose darkening tables were made for the
   original's brightness keeps them.
6. **Binary alpha for faces, smooth for objects.** A floor's or wall's pixel is opaque when its
   centre lies in the face (half-open quads), so neighbouring floors cover each pixel exactly
   once; an object's edge is soft, its colours bled from inside. Reason: two half-covered edge
   pixels drawn over each other leave a seam line; an object has no neighbour to join.
7. **Structural checks gate, measures rank.** Flags: the footprint (the share of the sprite
   outside the original's silhouette, widened by a tolerance and by the order's
   `inputs.silhouette`, and the share of the original's silhouette the sprite covers), the seam
   (a face texture's opposite edges against its neighbouring texels) and the painted light.
   They reject a variant by default (`mesh_flags` 0); the pick among acceptable variants takes
   the smallest sum of measures over their limits. Reason: as the other recipes, the factory
   gates on what it can name and measure; the limits are first guesses (below).
8. **Review sheets drawn by the factory.** The piece's sheet (each variant beside the original:
   shaded, albedo, normal map, the albedo lit by one light from its normals, and the piece
   repeated as the game lays it) and the set sheet (the game's map blocks, else a test
   arrangement, drawn three times: originals, shaded frames, normals lit) are Pillow drawings.
   Reason: the sprites are 2D; the set sheet is where seams, scale and coherence are judged.
9. **Animation frames are pieces.** A game that keys art per frame replaces each frame of an
   animation by its own piece. Reason: nothing animates in the first set; coherence between an
   animation's frames is left for a set that needs it.

Verified on 2026-10-20, without the GPU: unit tests of the geometry (neighbouring floors cover
each pixel once, a face out of a sprite and back, walls with height and thickness, their caps,
the view normals and their encoding), of the steps on synthetic tiles (pieces of a group join,
painted light and a non-wrapping texture are measured, the footprint, a block cut back into its
tiles), the recipe's expansion and order check, `-Check` and `-Estimate` on the sample order,
a night with the fake server and a stand-in render (every piece decided, the set sheet drawn,
a second run doing nothing), and the Blender render on the CPU of a painted box against a
synthetic sprite (overlap 0.96, the albedo unlit, the lid's normal up in the sprite's view).
The steps also ran on a game's real sprites with an earlier pipeline's generated materials
standing in for the pictures (the sprites, the set sheet of two map blocks). Never ran: any
picture, inpaint, material or mesh of a real night; the edit's behaviour on an upscaled pixel
sprite (does it keep the layout, does it paint light); the limits on generated pictures; the
mesh form on generated meshes.

### Limbless figures

A shapeless figure that floats or crawls (a blob) needs no recipe of its own: the `figure`
recipe makes a painted, unrigged mesh (`mesh_painted_mesh.glb`, at `params.tris` faces and
`params.texture`), and the game animates it (a bob, a squash). The sample creature order
shows two.

## 8. Running and the GPU

Launcher `Run-Forge.ps1`: `-Order <order> -Out <folder>`, plus `-Check`, `-Estimate`,
`-Status`, `-Stop`, `-Now`, `-Foreground`, `-RetryFailed`, `-Only`, `-Limit`, `-Server`. It
runs detached from the console; the output folder belongs to the game, never to this
repository.

The machine-wide GPU lock lives in `%USERPROFILE%\.art-forge` (decision of 2026-10-19). It
was in `%LOCALAPPDATA%`, but a packaged desktop application redirects the AppData writes of
the processes it starts into a private copy: a night started detached (WMI) and the tests or
tools started from such an application locked two different files, and each believed it
held the card. A lock file that resolves to another file than its path is now refused, and
the launcher passes its lock folder and server to the detached factory on its command line,
because a WMI process does not inherit the console's environment.

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
- The held-item recipe (2026-10-19): unit tests of the frame, the squaring, the real size,
  the socket templates and their snaps, the socket checks and a manual sockets file on
  synthetic meshes (a carbine, a pistol, a thrown object); recipe expansion and order checks;
  the sheet's layout; a night with the fake server and stand-ins (the pick by fit, the family
  folder, a failed hold that does not reject its item, manual files redoing one variant);
  `-Check` and `-Estimate` on its sample order; and smoke tests of the item tool, the hold
  (on the synthetic humanoid and on a figure the exporter wrote) and the item sheet on
  synthetic Blender fixtures. Nothing of it has run on a generated mesh.
- The iso-tile recipe (2026-10-20): see the end of its part of section 7.
- The humanoid's motions list (2026-10-19): see the end of section 13 for what was verified
  and what never ran. The exporter's fix of the same day is checked by a Blender smoke test
  on the CPU (a synthetic figure facing -y, +x and -x, its bones' tails turned off their
  children: the idle's knees and ankles) and on copies of two real SkinTokens rigs of the
  first night and one UniRig rig (knees as the clip has them, no crouch).

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
4. The order's `motions` list (section 13): built for the humanoid on 2026-10-19 (`library`,
   `text`, `bvh`); next, its first real night, the first HY-Motion Lite run through the
   factory. For the creature, `bvh` with a capture-joint-to-role map, and `text` once a local
   creature text-to-motion model exists.
5. Held items with sockets for creatures beyond the item bones of `body.holds`.
6. `held_item` (built 2026-10-19): its first real night (2026-10-20) showed how the mesh
   generators orient a side view (section 7, held-item decisions 11 to 13); next, a night
   with the changed item stage, and what reference values the fit measures should have.
7. `iso_tile` (built 2026-10-20): its first real night, which will say whether the edit keeps
   an upscaled sprite's layout and lights it flat, what limits the footprint and light checks
   should have, and how the mesh form fits generated meshes to a sprite's silhouette.
8. Publishing the research behind the stage defaults (which model won each stage, steps,
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
humanoid reads the list since the same day (below).

Open points for that phase's design (as written before it was built): HY-Motion Lite runs
through its own ComfyUI node and a separate server instance; generated motion needed
contact, hip and cycle corrections in earlier research, so the correction steps and their
acceptance thresholds have to be part of the stage, not manual work.

### The humanoid's motions list (built 2026-10-19)

A humanoid piece, or its family as a default, lists its motions with the creature's keys
(`name`, `source`, `kind`, `settings`, `library`, `prompt`, `file`, `frames`, `fps`, `loop`,
`holds`, `contacts`, `note`) and the same kind of check. Sources: `library` (a clip of a
motion library: the order's `inputs.clips` by default, a file, or the factory's starter
library `@starter`), `text` (HY-Motion Lite from a plain description) and `bvh` (a capture
file and a frame range). Every source ends in the canonical joints the exporter already
retargets (`forge/motion_clips.py`); the format and the jobs are in
[order.md](../../order.md#motions-of-a-humanoid) and
[conventions.md](../../conventions.md#motions-list).

Decisions, each with its reason:

1. **Made clips belong to the family.** Each `text` or `bvh` entry of a family's humanoid
   pieces is one job, `<family>/_motions/<name>` (with the first 8 characters of its
   specification's hash after the name when two entries of one name differ), shared by
   every piece and variant that lists it, with its own provenance (model, prompt, seed,
   effective settings) and its corrections' report. Reason: the request; a generated
   movement is reviewed once and reused, and a night pays for it once.
2. **The piece's own library, the exporter unchanged.** A piece with motions gets one local
   job, `<family>/<piece>/motions`, that puts its entries into one motion library in the
   list's order, and the export takes that file as its clips. Without motions nothing
   changes: the export reads `inputs.clips` as before. Job arguments name no paths (a
   library is an input, a capture the made job's input), so moving the order redoes nothing.
   Reason: the library format is what the exporter already plays; the list only decides
   which clips the library holds.
3. **HY-Motion Lite in a process of its own, not a ComfyUI node in a second server.** The text
   job is a local GPU job: it waits for the empty ComfyUI queue, `/free` and the GPU lock as
   every local tool, then runs `tools/motion/hymotion_entry.py` in the configured Python with
   the HY-Motion checkout on its path, offline and without writing bytecode (text encoded,
   encoders freed, motion model loaded, one clip generated, process exits). Reason: the
   factory's local GPU discipline already gives the card to one tool at a time; a process
   that exits releases all its VRAM, needs no port, no node installed into a server and no
   rule about who owns a second server (the earlier lab's ComfyUI node kept its pipeline on
   the card between prompts). SkinTokens runs the same way.
4. **Corrections act past a threshold, and every threshold is recorded and checked.** The
   stage measures, corrects when the measure is over its threshold and measures again: the
   loop's closure (the window of the generated clip whose ends are most alike, 0.8 to 1.25
   times the wanted length, resampled; the pelvis's drift becomes `travel`, the pose's
   residual is spread over the clip), the hip height (each frame with a planted foot lifted
   or lowered so its planted ankle stands at the clip's planted height), the foot contacts
   (a planted ankle held at its run's median place in the ground frame, the knee by two-bone
   IK) and nothing below the ground. The thresholds are constants of
   `forge/motion_clips.py`, written in every clip's report; a check that still fails is a
   flag of the clip's report and of the piece's motions report, logged, and the night goes
   on. Reason: the request (no manual work); the values are starting points until generated
   motion has been measured. The flags do not gate a variant and do not reach the review
   sheet yet: the exporter's motion report, which does, measures the feet on the exported
   figure.
5. **Contacts: given, then the clip's, then detected.** An entry's `contacts`, else a library
   clip's `stance`, else the frames where the ankle is within 0.02 body heights of its
   lowest and moves under 0.15 body heights a second in the ground frame (runs of at least
   3 frames). Reason: the order knows best; generated and captured clips carry none.
6. **Body heights from the figure.** Source positions are scaled by the tallest head over the
   floor, or by the bones' lengths when the clip never stands up. Reason: sources come in
   their own units; the canonical format is in body heights.
7. **`frames` is required for `text` and `bvh`; `fps` defaults to 24.** Reason: a generated
   clip has no length the game would want; 24 is the library format's default.
8. **The creature keeps `bvh` and `text` refused.** Reason: a capture's joints map onto a body
   plan's roles only with a map the order would have to give and a rest-pose alignment that
   cannot be checked on the synthetic fixtures; no creature text-to-motion model is installed
   on this machine.
9. **A starter library ships with the factory** (`data/motions/starter_humanoid.json`, named
   `@starter`, its provenance in `data/motions/README.md`): CMU motion capture and MoMask
   clips with generic hold classes, stored byte for byte (`-text`), because its content
   enters the specification of the jobs that read it. Reason: a game can have walking
   figures without generating anything.

Verified without inference: the canonical steps on synthetic motion (a walk with planted
feet, the same walk with floating hips and sliding feet, an idle, a one-shot), the BVH and
HY-Motion joint readers, the starter library through the stage, the order check, the jobs
and their path-free specifications, a night with the fake server where a stand-in replaces
HY-Motion's entry, and `-Check` on this machine (HY-Motion's files present, its modules
imported in its Python). HY-Motion has not generated a clip through the factory: the entry
script has never run.

### The exporter and the joints' axes (2026-10-19)

The first real night's SkinTokens rigs came out crouching in every clip: the exporter turned
each bone by its own axis, and those rigs' joints carry orientations whose axes do not point
to their children. Bones are now turned by the line from their joint to the next mapped
joint (the last bone of a chain by its own axis unless that is more than 45 degrees off its
parent's line), the two-bone IK is solved exactly on the joints, and the figure is turned
about the vertical so that its front is glTF +z. Reason: the joints are what the rig tool
predicts; axes are an artefact of the import. The humanoid exports' specification changes
(the exporter is in it), on purpose.

## 14. A common body for a humanoid's variants (built 2026-10-19)

Each humanoid variant gets its own predicted skeleton and proportions from the rig stage. A
game wants all its figures of one kind (outfits and armours, male and female) to share one
approved body: the same skeleton (bone names, hierarchy, rest) and proportions, so that an
animation, an item socket or an attachment works on every outfit without per-model work. The
order now says "wear this approved body": `inputs.body`, a GLB the humanoid exporter wrote
(its `model.json` next to it). Every variant of a piece that wears it is conformed to it and
exported on its skeleton; a bulky armour stays bulky around the same joints. The order format
is in [order.md](../../order.md#a-common-body-inputsbody), the files in
[conventions.md](../../conventions.md#a-figure-that-wears-an-approved-body).

Decisions, each with its reason:

1. **The variant is conformed by its own skin, then bound with its own weights.** The rig
   is still predicted and mapped with the bone map; the variant's mesh is moved by its own
   weights (linear blend skinning from its rest to the body's) so that its joints land on the
   body's: each limb segment turned onto the body's by the smallest rotation and stretched
   along its own line (bone lengths match, the thickness across stays), after a uniform
   prefit (turn and scale by least squares). Then it is bound to the body's skeleton with its
   own weights renamed (a finger with its hand). Reasons: the predicted weights follow each
   outfit's own geometry, while a weight transfer from the body near the skin would give a
   shoulder pad or a hood the weights of whatever body part is nearest; landmark fitting
   would need landmarks a generated mesh does not have. The skinning path is plain geometry,
   tested on synthetic figures without Blender (`tools/blender/humanoid_fit.py`), and it ran
   without flags on the night's armours (below). Weight transfer stays a later option (for
   tight suits, and for the body's finger bones, which carry no weight now).
2. **Three parts are not forced onto the body's joints.** The torso (hips to neck) is one
   piece: the spine joints a rig tool predicts are not landmarks of the surface, and forcing
   them would redistribute the belly and the chest. The neck and head are one piece set on
   the body's neck joint, unstretched: the night's heavy armour had a neck half the body's
   (a short neck under a big helmet), and stretching it twice tore the collar. End bones
   (hands, toes) are not stretched. Their remaining distances are reported
   (`joint_error_cm`, `head_offset_cm`).
3. **The body wins on height and proportions, and the order check says so**: a piece with a
   body may not set `params.height`, `head`, `hands`, `feet` or `widen` (a problem of the
   order, not a silent override). The exported figure takes the body's values.
4. **The exporter runs in the body's frame.** It normally stands, turns and scales a rig by
   its own mesh (floor, height); a helmet or a thick sole would move the skeleton and change
   the motions' scale. With `--body-frame` (the conform's `frame.json`) the stand, turn, scale
   and widen are the identity and the floor and height are the body's. First built as a
   wrapper (`humanoid_body_export.py`) that ran the unchanged exporter with three statements
   replaced, because the exporter's file is in every humanoid export's specification and
   editing it would redo a running night's exports; when the exporter was next changed on
   purpose (the facing, section 15) the frame became this option and the wrapper was removed.
5. **Acceptance.** The conform report (`conform_report.json`, a file of the export) measures
   the joints before and after, each segment's stretch, the edges' stretch and tears, the
   body's joints outside the conformed outfit, the soles against the body's floor and how far
   the body's surface lies outside the outfit. Flags (joints off, a segment stretched beyond
   1.5, tears, a joint outside, soles 4 cm off) reject a variant by default (`conform_flags`
   0, decision recipe `humanoid_body`, so a humanoid without a body keeps its limits);
   `conform_stretch_p95` and `body_outside_cm` gate only when the order sets them. Inside
   and outside are told by rays that escape (a quarter of 26), not by normals or ray parity:
   the night's generated meshes have inner shells, open seams and mixed winding, which made
   every joint of an armour look outside by its normals. The sheet's label shows a line of
   the measures. The skin poking through is measured as the body's surface outside the
   conformed outfit; it does not gate by default, since each variant is a whole figure and
   the body is never drawn with it.
6. **The exported GLB.** The body's skeleton exactly (names, hierarchy, rest and inverse
   binds, the item bones and `item_ref`), compared with the body's GLB after the export
   (`forge/body.py`; a difference is a mesh flag); the motions retargeted as today, which on
   one skeleton and one frame are the same on every variant; `model.json` `body` names the
   body by its SHA-256.
7. **How a body is given.** `inputs.body` per piece, or per family with a per-piece override
   (`null` drops it): a male and a female body are two values. A path to a run's pick
   (`<out>/<family>/<piece>/final/<piece>.glb`) is enough; the body's GLB and its
   `model.json` enter the export's specification, so a new body, or a changed pick, redoes
   the exports, sheets and decisions only. The order check refuses a body in the piece's own
   output (a piece cannot wear its own pick, which changes with every run) and a body that
   stands turned more than 15 degrees from facing +z: the night's exports showed figures
   turned 22 to 40 degrees (the exporter then squared a figure only to the axis its feet
   point to, fixed in section 15), both automatic picks of the unarmoured pieces among them.
   The check stays for exports made before the fix and for bodies not written by the exporter.
8. **One job, not a new stage.** The conform and the export run in one job (`body_export`,
   stage `export`), so the stage list, the review and the decision stay as they are; the
   conform's settings are export settings (`conform_*`), refused without a body.

Verified on 2026-10-19, without the GPU: the geometry on synthetic figures (other sizes,
proportions, bulk and facing; a scaled and turned copy lands exactly; a joint outside, an
overstretched segment, a tear, low soles and a short neck), the GLB skeleton and motion
comparison, the expansion and order checks, the sample order (`-Check`, `-Estimate`), a night
with the fake server and stand-ins, and a Blender smoke test on the CPU (two synthetic
variants conformed to an exported synthetic body: the body's skeleton within 4e-7 m, the same
motions on both and on the body). On copies of the running night's data, on the CPU with a
normal-map bake from the moved raw mesh: a light armour and a heavy armour conformed to an
unarmoured export, both without flags, the body's skeleton within 5e-7 m, the 45 clips the
same on both variants (difference 0) and within 2e-5 of the body's own. Never ran: the
factory driving `body_export` in a real night, the GPU bake of a conformed variant, the
review sheet in Godot with the conform line, and a game playing a conformed GLB.

## 15. The way an exported humanoid faces (fixed 2026-10-19)

The exporter turned a rig about the vertical by the bone map's front, which is where the toes
point snapped to the nearest horizontal axis. A generated figure can stand turned between two
axes inside its mesh, and the first real night's exports stood turned up to 40 degrees: in a
game such a figure walks and aims visibly askew. The exporter now squares the figure by its body
(`humanoid_bones.body_facing`, without `bpy`; the cues and the report in
[conventions.md](../../conventions.md#the-way-an-exported-figure-faces)).

Decisions, each with its reason:

1. **The front is perpendicular to the body's left-right axis**, which is the sum of the line
   across the hips (the upper legs' joints) and the line across the shoulders (the upper arms'
   joints), each counting by its length. Its sign comes from the map's left and right, which the
   toes chose when the map was proposed. Reason: the hips and shoulders are the joints a rig tool
   places most surely, and they are the body; the toes of a generated figure point where its feet
   were modelled, which on the night was within 10.3 degrees of the body but need not be.
2. **The feet only break a tie.** When the two lines are more than 25 degrees apart the figure is
   twisted: the line nearer the feet is used and the variant gets a mesh flag. Feet more than 60
   degrees from the front used are a flag too. Reason: a variant facing an unknown way must be
   rejected rather than shipped turned; on the night the lines were at most 7.1 degrees apart, so
   the limits leave room without letting a twisted figure through.
3. **No cue from the mesh stage or the face.** The rigs as the rig stage delivered them did not
   all face one axis (19 of 21 faced glTF +Z, 2 faced +X), so a fixed facing of the generated
   mesh is not a cue; the bone map has no bone that points out of the face, and reading a face
   from the mesh was not built.
4. **Checked after the turn.** The cues are measured again on the turned rig; a front more than
   0.5 degrees from glTF +Z is a mesh flag. The retarget's body directions are the glTF front
   (as before, the turned bone map front was exactly that), so the motions do not depend on how
   the generated figure stood: on one night rig exported with the night's 45 clips, the motion
   report's 55 flags and worst values were the same as the night's own export.
5. **The specification moves on purpose.** The exporter and the bone map module are in every
   humanoid export's specification: a resumed night redoes its exports, sheets and decisions. The
   body frame became an exporter option in the same change (section 14, decision 4).

Verified on 2026-10-19, without the GPU: the cues on synthetic skeletons (turned between axes,
feet turned 35 and 55 degrees while the hips and shoulders face front and the reverse, a 10 and
a 40 degree twist, no line across the body) and on the bulky-armour fixtures turned four ways; the
exporter in Blender on the CPU on synthetic figures facing front, +x and -x, turned 30 degrees,
with the feet askew, with the body askew and twisted (each GLB's hips' and shoulders' lines read
from the file within 1 degree of +x but the twisted one, which is flagged); the approved body's
smoke test with bodies exported by the fixed exporter; and copies of the night's 21 rigs on the
CPU without a bake: every front within 0.0 degrees of glTF +Z after the turn and no facing flag,
the worst of each figure's hips' and shoulders' lines 4.5 degrees from across (40.4 before; the
remainder is the angle between the two lines). Never ran: the fixed exporter in a factory night
with the GPU bake, the review sheet of a squared figure, and a game playing one.

## 16. What glows on a figure (2026-10-20)

The exports of the first real nights marked no glowing part on any piece: the armours' cyan
and emerald seams and visors, the figures' psionic nodes, plasma vents, sigils and veins and
the creatures' lit eyes and thrusters were painted on their textures and nothing lit them. The causes: the humanoid exporter's glow was off unless the order set
`glow` (no order did: every `model.json` had `glow_pixels: null`); when on, it took one hue
range (165-225 degrees, cyan to blue), so emerald, violet, magenta and orange lights could not
glow while cobalt-blue plates could (that rule marks 1 % of a powered armour's texture, its blue
plates' highlights with its seams, and nothing on the figures lit in violet, magenta or orange); its thresholds were absolute
(value 0.55) while the paint stage paints lights darker than the picture shows them (a teal
node at value 0.45); it wrote only the mask's blue channel, never the GLB's emissive slot; and
the creature exporter had no glow at all. The colour space was not a cause: the painted base
colour is an 8-bit sRGB PNG and Blender's pixels of it are its sRGB values (the new code reads
the written GLB's image directly).

The glow is now found by `tools/blender/glow_regions.py` (no `bpy`), shared by the humanoid and
creature exporters and the glow command (formats in [order.md](../../order.md), "What glows",
and [conventions.md](../../conventions.md)).

Decisions, each with its reason:

1. **The colours come from the piece's text.** The order already says what glows ("thin
   glowing cyan seams", "glowing emerald or magenta lights"); a colour word bound to a glow
   word in one phrase is a glowing colour, a negation binds nothing. Reason: the factory stays
   game-neutral and an order that never thought of the glow gets it right (the night's 20
   pieces: the 6 conventional ones name none, the 14 others name the colours their pictures
   show); `glow_colours` overrides, `glow: false` turns it off.
2. **On by default for the humanoid and creature exports.** Nothing changes for a piece whose
   text names no glowing colour. Reason: the lights are part of the look the order asked for.
   The vehicle and figure recipes keep their own emissive mask; the articulated and held-item
   exporters have no glow (not built).
3. **Relative cues, not absolute ones.** A light's texels are in a glow colour, saturated
   (chroma 0.30, saturation 0.5) and stand out from what is around them (value 0.08 above the
   mean around, or chroma 0.25 above it without being darker); weaker texels join by hysteresis;
   a white-hot core within 2 texels joins. Reason: the painted lights are dim (value 0.45 to
   0.7) but always brighter or more colourful than their surroundings.
4. **Lights are small and saturated through.** A region larger than 0.6 % of the texture is a
   painted panel (a cerulean plate, a whole violet harness), and a region whose mean saturation
   is under 0.42 is a pastel patch seeded only at its shaded edges (a robed figure's lavender
   head: 7 such regions, means 0.24 to 0.39, against 0.42 to 0.98 for the real lights of every
   piece). Pale skin and white plates (chroma under 0.12) never seed a light.
5. **The reference picture weighs, it does not place.** The cutout's lit colours (bright,
   saturated, brighter than around them, in a glow colour) are counted by hue and saturation;
   a texel whose colour the picture shows lit glows up to 1.6 times more, one it never shows lit
   0.4 times. Reason: the paint stage projects the picture's colours but takes the light out,
   so values do not match and places are unknown without the views; by RGB the lookup missed
   the dimmer painted lights (a small figure's teal nodes were 0.4 % supported by RGB, 51 % by hue and
   saturation).
6. **Hue ranges a little wider than the names.** `emerald` runs to 183 degrees: a light armour's
   "emerald" lines were painted teal (the pictures' lit lines at 163 to 181 degrees).
7. **The GLB is rewritten without Blender.** The emissive map (the texel's colour at full
   value times its weight) goes into each material's emissive slot with
   `KHR_materials_emissive_strength` 2 (Godot 4 reads it as emission energy 2); the binary
   chunk is rebuilt view by view. Reason: one code path for the exporters and for refreshing a
   game's models without a GPU night; a material's own emissive map is kept.

Verified on 2026-10-20, without the GPU and without the server: unit tests on synthetic
textures and on five 128-pixel crops of generated textures (pale skin around a teal node,
white plates and a cyan seam, white plates and an orange paint tab, lavender skin, pale lines
on dark armour); the GLB rewrite read back by `glb_materials.py`; the humanoid exporter
(normal map baked on the CPU) and the creature exporter run with the glow on one real rig each
(a slender humanoid and a hovering creature: emissive slot present, lossless, no mesh flag); `python -m forge.glow`
on copies of a real night's 14 humanoid finals, 4 creature finals and 2 figures' painted
meshes, each rendered in Godot off screen lit and in the dark from four sides. What glows on
them, with the order's texts: two powered armours' visor, chest core and cyan seams (0.39 % and
0.48 % of the texture), two light armours' emerald-teal lines, chest emblem and back unit
(0.42 %, 0.14 %), a slender figure's temple, chest and hip nodes (0.52 %), an armoured brute's
chest vent, eyes and emerald and magenta gauntlet and boot lights (0.18 %), a robed figure's
sigils and gems (0.39 %), an infected civilian's veins on the neck and hands (0.96 %), four
creatures' lit eyes, joints, plates, pods and scale patches (0.13 % to 2.7 %), a translucent
blob's magenta and emerald patches (4.6 %) and a magma blob's cracks and eyes (8.9 %); the
six pieces whose texts name no glowing colour (unarmoured and lightly armoured soldiers,
civilians) nothing; no pale skin, white plate, cobalt plate or orange paint lit. Never ran: a
factory night with the glow, the review sheet showing it, a game drawing it; the translucent
blob's glow is patchy (its painted magenta patches light up as they are painted).
