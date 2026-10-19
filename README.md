# art-forge

A local, game-neutral art factory for ComfyUI. A game hands it an **order**, the list of
every art piece it wants. The factory queues everything on a local ComfyUI server and
local tools, then runs unattended overnight: 2D concepts, illustrations, seamless
materials, textured 3D objects and, with the `humanoid` recipe, rigged and animated
humanoid characters.

**Status: a factory of reproducible candidates for review.** Every output is a candidate
pending human review, reproducible from its recorded provenance, until a real night
proves that the whole chain delivers usable characters; no such night has run yet. The
factory core, the order format with its check, the launcher, the production workflows
and the `humanoid` recipe (reference, pose, cutout, mesh, paint, rig, export, review
sheet and final pick, each variant end to end) are in place and tested against a fake
server with stand-ins for the local tools. The local tools themselves are smoke-tested
without GPU inference: the humanoid exporter (bone map, Godot humanoid bone names,
retarget of a motion library, normal map, lossless technical maps checked in the written
GLB, mesh and motion reports) and the Godot review sheet; the rig runners (SkinTokens, UniRig) have not run while building. See the
[design](docs/superpowers/specs/2026-10-18-art-forge-design.md), the
[implementation plan](docs/superpowers/plans/2026-10-18-art-forge.md) and the formats in
[docs/conventions.md](docs/conventions.md).

## Ideas in one page

- **The factory knows recipes, not games.** A recipe is picked by how a thing moves, not by
  what it is. Implemented: `concept`, `illustration`, `material` (seamless), `object_3d`,
  `prop`, `vehicle` (rigid, seen whole) and `figure` (a figure with views and an unrigged
  mesh) and `humanoid` (skinned, human skeleton: every variant goes from a generated
  reference to an exported, animated GLB, then one review sheet and a provisional final
  pick among the variants that meet the order's acceptance limits, or the outcome
  `no_variant_passed`, which can release reserve variants). Planned: `articulated` (rigid parts on pivots) and `creature` (skinned, own
  skeleton) when an order needs them.
- **The order says everything else**, piece by piece: recipe, prompt, number of variants,
  budgets (triangles, texture size), input files, the model of each stage and its named
  settings (steps, seeds, guidance, sampler...). Format: [docs/order.md](docs/order.md).
- **One night, no stops.** Every job is resumable: a job whose specification (workflow,
  values, hashes of its inputs) did not change and whose output hashes still match never
  runs again; a failed job is retried once and only its dependants wait. Picks are
  automatic and marked for review; changing a pick redoes only what depends on it.
- **Local only.** Only the HTTP API of a local ComfyUI and local tools; workflows with
  paid API nodes are refused. Provenance (models, prompt, seeds, inputs, hashes, and each
  input as requested and as the server ran it) is kept for every output.
- **One card, one user.** A run holds a machine-wide lock, so a second factory refuses to
  start; local GPU jobs wait for the whole ComfyUI queue (any client) to empty and send
  nothing while they run.

## Quick start

Requirements: Windows, Windows PowerShell 5.1, Python 3 with NumPy and Pillow, and a
local ComfyUI with the custom nodes and models the workflows use (see the table below).

1. Copy `forge.local.example.json` to `forge.local.json` and point its keys to this
   machine's installs (every key is optional; only the tools an order's stages need must
   be present).
2. Write an order (start from [tests/fixtures/order_generic.json](tests/fixtures/order_generic.json),
   or [tests/fixtures/order_humanoid.json](tests/fixtures/order_humanoid.json) for characters).
3. Check it and see what it costs, then start it. The output folder must be outside this
   repository:

        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1 -Check
        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1 -Estimate
        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1
        .\Run-Forge.ps1 -Out D:\art\night1 -Status
        .\Run-Forge.ps1 -Out D:\art\night1 -Stop

`-Check` prints two results: the order (valid, or its problems) and the environment:
`verified`, `not ready` (node classes, model files missing from the loaders' lists, local
tools or their weight files) or `not verified` when the server does not answer (it reads
only `GET /object_info`). More in [AGENTS.md](AGENTS.md); tests:
`python -m unittest discover -s tests` (the smoke tests of the humanoid tools run when
`forge.local.json` points to Blender and Godot, and skip otherwise).

## Machine configuration

`forge.local.json` at the repository root (ignored by git):

| Key | Meaning |
|---|---|
| `comfy_url` | address of the local ComfyUI server (default `http://127.0.0.1:8188`) |
| `python` | Python 3 with NumPy and Pillow that runs the factory (default: `python` on the PATH) |
| `blender_python` | a Python that can import `bpy` (Blender as a module) |
| `unirig_dir`, `unirig_venv`, `unirig_hf`, `unirig_shim` | UniRig's source folder, its virtual environment, its Hugging Face cache (weights) and the folder put on its `PYTHONPATH` |
| `skintokens_dir`, `skintokens_python` | the SkinTokens checkout and the Python of its own environment |
| `godot` | Godot 4 executable (on Windows the console build) |
| `bake_device` | Cycles device of the normal-map bake: `GPU` (default; falls back to the CPU when Cycles finds no GPU) or `CPU` |
| `gpu_wait` | seconds a local GPU job waits while another process holds more than `gpu_busy_mb` of VRAM (default 300; then it goes) |
| `gpu_busy_mb` | VRAM in MB another process may hold before a local GPU job waits (default 2048) |
| `gpu_used_busy_mb` | when the driver reports no VRAM per process (Windows display drivers): the VRAM in MB the whole card may have in use before a local GPU job waits (default 4096; measured with `nvidia-smi --query-gpu`, else the server's `/system_stats`) |
| `queue_wait_warn` | seconds a local GPU job may wait for the card before the log and `state.json` call it stalled (default 3600; it keeps waiting) |

Relative paths are relative to the file (the launcher runs a copy of it from its snapshot
folder and passes `--config-base` with this repository, so they resolve as with `-Check`). The Blender, rig and Godot keys are for the
humanoid recipe's rig, export and review stages; the other recipes need only ComfyUI. `python -m forge.local_gpu` says
which local tools of this machine are ready.

## Models

The repository ships ComfyUI workflows only, never model weights. Each model has its own
licence and terms, and some restrict commercial use or territories. The table summarises
the model pages as read on 2026-10-18; it is not legal advice, terms change, and the
licence text on each model's page is what counts. Check it before using the outputs.

| Model | Used by | Licence (as stated on its page) |
|---|---|---|
| [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) and [Qwen-Image-2.1-Turbo](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo) | `qwen_image_21_*` | Qwen Research License Agreement: non-commercial research or evaluation; commercial use needs a separate licence |
| [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | `qwen_edit_*` | Apache 2.0 |
| [Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) (LoRA) | `qwen_edit_*` | Apache 2.0 |
| [Qwen-Image-Edit-2511 Multiple Angles LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | `qwen_edit_view_angles.json` | Apache 2.0 |
| [FLUX.2 Klein 9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) | `klein_t2i.json` | FLUX Non-Commercial License |
| [Hunyuan3D 2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) (shape and Paint) | `hunyuan21_*` | Tencent Hunyuan Community License; its territory excludes the European Union, the United Kingdom and South Korea |
| [TRELLIS.2](https://huggingface.co/microsoft/TRELLIS.2-4B) | `trellis2_mesh.json` | MIT |
| [BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) (background removal) | `klein_t2i.json`, `qwen_*` | MIT |
| [UniRig](https://huggingface.co/VAST-AI/UniRig) | `humanoid` rig option `unirig` | MIT |
| [SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) | `humanoid` rig (default) | the code is MIT (its repository's LICENSE file); check the model page for the weights |

The text and image encoders and VAEs the workflows load (named in each workflow and
recorded in every job's provenance) come with their own models' terms too.

## Licence

Licensed under the [Apache License 2.0](LICENSE). The licence covers this repository's
code and documents, not the models the workflows call, nor the art you generate with them.
Code origin: [docs/provenance.md](docs/provenance.md).
