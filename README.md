# art-forge

A local, game-neutral art factory for ComfyUI. A game hands it an **order**, the list of
every art piece it wants. The factory queues everything on a local ComfyUI server and
local tools, then runs unattended overnight and delivers files a game engine can use:
2D concepts, illustrations, seamless materials, textured 3D objects and, as the next
milestone, rigged and animated humanoid characters.

**Status: design and implementation plan only. No code has been written yet.** See:

- [Design](docs/superpowers/specs/2026-10-18-art-forge-design.md)
- [Implementation plan](docs/superpowers/plans/2026-10-18-art-forge.md)

## Ideas in one page

- **The factory knows recipes, not games.** A recipe is picked by how a thing moves, not by
  what it is: `object` (rigid), `articulated` (rigid parts on pivots: trains, tanks,
  plated robots), `humanoid` (skinned, human skeleton), `creature` (skinned, own
  skeleton), `illustration`, `material`. A train and a plated robot share a recipe; a
  soldier and a humanoid alien share another.
- **The order says everything else**, piece by piece: recipe, prompt, number of variants,
  budgets (triangles, texture size), the review camera, the motion library, and which
  model to use at each stage (for example TRELLIS.2 or Hunyuan3D for the mesh).
- **One night, no stops.** Every variant runs end to end. In the morning a review sheet
  shows each variant from the game's own camera, and you pick one; changing the pick
  redoes only what depends on it.
- **Local only.** Only the HTTP API of a local ComfyUI and local tools; workflows with
  paid API nodes are refused. Provenance (model, prompt, seeds, inputs, hashes) is kept
  for every output.

## Models

The repository will ship ComfyUI workflows only, never model weights. Each model has its
own licence and terms (some restrict commercial use or territories); check them before
using the outputs. A licence table will be added together with the workflows.

## Licence

Licensed under the [Apache License 2.0](LICENSE). The licence covers this repository's
code and documents, not the models the workflows call, nor the art you generate with them.
