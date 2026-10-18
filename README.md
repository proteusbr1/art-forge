# art-forge

A local, game-neutral art factory for ComfyUI. A game hands it an **order**, the list of
every art piece it wants. The factory queues everything on a local ComfyUI server and
local tools, then runs unattended overnight and delivers files a game engine can use:
2D concepts, illustrations, seamless materials and textured 3D objects. Rigged and
animated humanoid characters are the next milestone and are not implemented yet.

**Status:** the factory core, the order format with its check, the launcher and the
production workflows are in place and tested against a fake server. The `humanoid`
recipe (rig, export, review sheets) is not written yet. See the
[design](docs/superpowers/specs/2026-10-18-art-forge-design.md) and the
[implementation plan](docs/superpowers/plans/2026-10-18-art-forge.md).

## Ideas in one page

- **The factory knows recipes, not games.** A recipe is picked by how a thing moves, not by
  what it is. Implemented: `concept`, `illustration`, `material` (seamless), `object_3d`,
  `prop`, `vehicle` (rigid, seen whole) and `unit` (a figure with views and an unrigged
  mesh). Planned: `humanoid` (skinned, human skeleton), then `articulated` (rigid parts on
  pivots) and `creature` (skinned, own skeleton) when an order needs them.
- **The order says everything else**, piece by piece: recipe, prompt, number of variants,
  budgets (triangles, texture size), input files, and, for recipes with swappable stages,
  which model to use at each stage. Format: [docs/order.md](docs/order.md).
- **One night, no stops.** Every job is resumable: a job whose specification (workflow,
  values, hashes of its inputs) did not change never runs again; a failed job is retried
  once and only its dependants wait. Picks are automatic and marked for review; changing
  a pick redoes only what depends on it.
- **Local only.** Only the HTTP API of a local ComfyUI and local tools; workflows with
  paid API nodes are refused. Provenance (models, prompt, seeds, inputs, hashes) is kept
  for every output.

## Quick start

Requirements: Windows, Windows PowerShell 5.1, Python 3 with NumPy and Pillow, and a
local ComfyUI with the custom nodes and models the workflows use (see the table below).

1. Copy `forge.local.example.json` to `forge.local.json` and point its keys to this
   machine's installs (every key is optional; only the tools an order's stages need must
   be present).
2. Write an order (start from [tests/fixtures/order_generic.json](tests/fixtures/order_generic.json)).
3. Check it and see what it costs, then start it. The output folder must be outside this
   repository:

        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1 -Check
        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1 -Estimate
        .\Run-Forge.ps1 -Order my_order.json -Out D:\art\night1
        .\Run-Forge.ps1 -Out D:\art\night1 -Status
        .\Run-Forge.ps1 -Out D:\art\night1 -Stop

`-Check` reads the server's node classes (`GET /object_info`) when it answers and checks
without them otherwise. More in [AGENTS.md](AGENTS.md); tests:
`python -m unittest discover -s tests`.

## Machine configuration

`forge.local.json` at the repository root (ignored by git):

| Key | Meaning |
|---|---|
| `comfy_url` | address of the local ComfyUI server (default `http://127.0.0.1:8188`) |
| `python` | Python 3 with NumPy and Pillow that runs the factory (default: `python` on the PATH) |
| `blender_python` | a Python that can import `bpy` (Blender as a module) |
| `unirig_dir`, `unirig_venv`, `unirig_hf`, `unirig_shim` | UniRig's source folder, its virtual environment, its Hugging Face cache (weights) and the folder put on its `PYTHONPATH` |
| `godot` | Godot 4 executable |

Relative paths are relative to the file. The Blender, UniRig and Godot keys are for the
coming `humanoid` recipe; the current recipes need only ComfyUI.

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
| [UniRig](https://huggingface.co/VAST-AI/UniRig) | coming `humanoid` recipe | MIT |

The text and image encoders and VAEs the workflows load (named in each workflow and
recorded in every job's provenance) come with their own models' terms too.

## Licence

Licensed under the [Apache License 2.0](LICENSE). The licence covers this repository's
code and documents, not the models the workflows call, nor the art you generate with them.
Code origin: [docs/provenance.md](docs/provenance.md).
