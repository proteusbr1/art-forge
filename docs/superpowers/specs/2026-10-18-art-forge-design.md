# art-forge: design

Date: 2026-10-18. State: approved design.

## 1. Goal

A local art factory that serves any game. A game hands over an **order** (the list of the
art it wants, piece by piece) and the factory spends the night generating on ComfyUI and
local tools, with no one watching, until it delivers files ready for the game to use.
The factory knows no game: it knows **recipes** (ways of producing) and **models per
stage**. The game picks the recipe and the settings of each piece in the order.

## 2. Out of scope for this phase

- The `articulated` and `creature` recipes (section 7), built when the first order needs
  them.
- Generating new motion (text-to-motion). In this phase animation comes from a motion
  library supplied by the order; HY-Motion Lite is the planned motion option of the next
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
| `object_3d`, `prop`, `vehicle` | rigid, no moving parts | crate, rock, weapon, craft seen whole | core |
| `humanoid` | skinned, human skeleton | soldier, humanoid alien, android | **this phase** |
| `articulated` | rigid parts on pivots | train, tank, turret, door, plated robot | when ordered |
| `creature` | skinned, own skeleton | quadruped, insectoid, tentacles | when ordered |

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

## 11. Who writes and runs orders

An operator (a person or an LLM agent) writes the orders with the settings it judges best
and starts the factory, after checking that the ComfyUI server is free. A human judges
the review sheets. The first real night runs a small order to validate the `humanoid`
recipe before spending a night on a full art list.

## 12. Next phases

1. A first real humanoid order from a game, with its adapter in that game's repository.
2. Full art lists in the existing recipes.
3. `articulated`, with the first order that has a vehicle or a plated robot.
4. A `motion` stage option with HY-Motion Lite: the order describes each movement in
   text and the factory generates and retargets it.
5. `creature`, with the first order that has a non-humanoid creature (including where its
   animation comes from).
6. Publishing the research behind the stage defaults (which model won each stage, steps,
   pitfalls) as documentation.
