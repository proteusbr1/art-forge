# art-forge: notes for agents

A local, game-neutral art factory for ComfyUI. Read [README.md](README.md), the order
format in [docs/order.md](docs/order.md) and the design in
[docs/superpowers/specs/2026-10-18-art-forge-design.md](docs/superpowers/specs/2026-10-18-art-forge-design.md).

## Rules

- **Public repository, game-neutral.** No game names, briefs, prompts, images, outputs,
  personal names, e-mail addresses or local user paths in anything committed. Examples
  and tests use generic subjects (a stone, a crate, a lantern). Each game keeps its orders
  in its own repository. Code seeded from earlier private work is described only as in
  [docs/provenance.md](docs/provenance.md).
- **Local only.** Only the HTTP API of a local ComfyUI server and local tools. No paid APIs
  or remote services; workflows with paid API nodes are refused on load
  (`FORBIDDEN_MARKERS` in `forge/factory_core.py`).
- **Never send a prompt to ComfyUI or run GPU inference while building or testing.** The
  tests use a fake server. `-Check` may read `GET /object_info` and nothing else.
- **Nothing generated enters git:** media, model weights, outputs and `forge.local.json`
  stay out (`.gitignore`). The output folder of a run must be outside this repository
  (the launcher and `python -m forge` refuse it).
- English for code, comments, identifiers and documents. PowerShell compatible with
  Windows PowerShell 5.1, ASCII only. System Python 3 with NumPy and Pillow; standard
  library otherwise.
- The workflows are stored byte for byte (`.gitattributes`): their SHA-256 enters every
  job's specification and provenance. Changing a workflow redoes the jobs that use it.
- Documents describe only what exists and was verified.
- **One checkout, one branch.** All work happens in this folder on `main`: no other
  branches and no git worktrees. Before starting, `git pull --ff-only`; commit small and
  push to `main` when a change is done and tested. Only one agent works on the repository at
  a time; if `git status` shows changes you did not make, stop and ask instead of
  committing, resetting or stashing them.

## Layout

- `Run-Forge.ps1`: the launcher (Windows).
- `forge/__main__.py`: the command line (`python -m forge`).
- `forge/order.py`: `load_order` (family defaults merged into pieces, input paths
  resolved) and `check_order` (every problem of an order, without the server).
- `forge/readiness.py`: `check_environment` (the second result of `-Check`: node classes,
  model files in the loaders' lists, local tools and weight files, Blender and Godot
  versions; `verified`, `not ready` or `not verified`).
- `forge/gpu_lock.py`: the machine-wide GPU lock file and the nvidia-smi process list.
- `forge/config.py`: `load_config` (the machine configuration, `forge.local.json`).
- `forge/stages.py`: the stage catalogue per recipe (shared `OPTIONS` with their named
  settings), inputs known to be overridden (`OVERRIDDEN`), params, inputs and review keys.
- `forge/recipes.py`: recipes that expand each piece into jobs (`expand`), including
  `recipe_humanoid` and its order check (`PIECE_CHECKS`).
- `forge/factory_core.py`: job states, scheduling, `state.json`, estimate.
- `forge/comfy_jobs.py`: builds a workflow's prompt, submits it, collects its outputs.
- `forge/local_steps.py`, `forge/scoring.py`: local steps without GPU (picks, sheets,
  seamless materials, a humanoid's cutout and final decision) and the pick scores.
- `forge/acceptance.py`: a humanoid's acceptance limits (`accept`), the classification of
  its variants (acceptable or rejected, with the stage held responsible) and the outcome
  `no_variant_passed` that releases reserve variants.
- `forge/provenance.py`: provenance records and export.
- `forge/body.py`: an approved body (`inputs.body`): its check, and the skeletons and motions
  of GLB files read and compared without `bpy`; `forge/local_gpu.py` `run_body_export` and the
  runner `body_export` conform a variant to it and export it.
- `forge/comfy_server.py`, `forge/factory_util.py`: HTTP client and helpers.
- `forge/local_gpu.py`: runners of the local tools that use the card (`run_rig`,
  `run_export`, `run_review`), their logs and provenance, and `RUNNERS` (the tools of the
  `local_gpu` job kind: `rig`, `export`, `review`).
  `RUNNERS` also has `parts` (articulated), `creature_export` and `sheet` (the layout sheet
  of both new recipes), and `item`, `hold` and `item_sheet` (held_item).
- `forge/tool_specs.py`: the order check of part lists, body plans and motions, by the
  tools' own modules; `forge/sheet_layout.py`: the layout of the articulated and creature
  review sheet (Godot only renders it); `forge/item_sheet_layout.py`: the layout of the
  held-item review sheet.
- The humanoid's motions list: `forge/motion_clips.py` (every source to canonical joints, the
  corrections and their thresholds, the BVH and HY-Motion readers, the list's check; no
  `bpy`), `forge/motion_jobs.py` (the family's made clips and the piece's library as jobs, and
  their local steps), `forge/motion_text.py` (the `motion_text` runner: HY-Motion Lite in its
  own Python, its readiness and import check) and `tools/motion/hymotion_entry.py` (the
  model's entry, run by that Python). `data/motions/` holds the starter motion library
  (`@starter`) and its provenance note; `data/` is stored byte for byte.
- `forge/bench.py`: the motion bench (`python -m forge.bench`, [docs/bench.md](docs/bench.md)): a
  figure, a motion library and one held item exported and rendered clip by clip
  (`tools/review_godot/bench.gd`) into previews and a page of measures; no GPU lock, nothing
  sent to ComfyUI.
- `forge/workflows/`: production ComfyUI workflows in API format.
- `tools/blender/`: `humanoid_bones.py` (bone map from the skeleton's tree and the way the
  figure faces, `body_facing`, no `bpy`) and
  `humanoid_export.py` (the humanoid export, run by `blender_python`; `--body-frame` runs it in
  an approved body's frame) and
  `glb_materials.py` (what a written GLB's material holds, read from the file; no `bpy`).
  `articulated_parts.py` (part lists and region geometry, no `bpy`) and
  `articulated_export.py` (the parts); `creature_bones.py` (body plans, templates and the
  role map) and `creature_motion.py` (motion kinds, gaits, role-space clips), both without
  `bpy`, and `creature_export.py`; `forge_blender.py` (helpers of the two new exporters).
  `humanoid_fit.py` (the conform of a variant to an approved body, no `bpy`),
  `humanoid_conform.py` (the conform in Blender).
  `held_item_geometry.py` (the item frame, class templates, socket proposal and checks, no
  `bpy`), `held_item_export.py` (the item tool) and `held_item_figure.py` (a reference figure
  made ready for the humanoid exporter).
  The humanoid's scripts are in the humanoid jobs' specifications: changing one redoes those
  jobs (the exporter changed on purpose on 2026-10-19, see the design's sections 13 and 15). The
  hashes are taken from the files as checked out (CRLF here, as `git archive` gives a night's
  snapshot): keep those scripts' line endings as a fresh checkout has them.
- `tools/review_godot/`: the Godot project of the review sheets (off screen): `main.gd`
  (humanoid), `sheet.gd` / `sheet.tscn` (articulated and creature) and `item_sheet.gd` /
  `item_sheet.tscn` (held_item); `bench.gd` / `bench.tscn` (the motion bench).
- `tools/rig/skintokens_entry.py`: SkinTokens with a fixed seed, run by its own Python.
- `docs/conventions.md`: motion library, held items, exporter outputs and reports, sheet.
- `tests/`: unit tests and the fake ComfyUI server; `tests/fixtures/` holds the sample
  orders (`order_generic.json`, `order_humanoid.json`, `order_articulated.json`,
  `order_creature.json`, `order_held_item.json`, `order_humanoid_motions.json`),
  `tests/fixtures/humanoid/` the synthetic humanoid (generator, library, item, the pose
  checker), `tests/fixtures/motion/` the HY-Motion stand-in and a synthetic capture,
  `tests/motion_synth.py` the synthetic walk, `tests/fixtures/articulated/` and
  `tests/fixtures/creature/` the generators of the new tools' Blender fixtures and a
  role-space library, `tests/fixtures/held_item/` the synthetic carbine's generator and the
  held items' library;
  `tests/creature_skeletons.py` the synthetic creature skeletons. `tests/humanoid_synth.py`
  (capsule figures by role, NumPy), `tests/glb_synth.py` (small GLBs and stub bodies without
  Blender) and `tests/fixtures/humanoid/make_body_fixtures.py` (those figures rigged in Blender)
  serve the approved body's tests (`order_humanoid_body.json` is its sample order);
  `tests/fixtures/humanoid/bone_trees.json` holds synthetic skeletons of bulky armour for the
  bone map. `tests/test_spec_pins.py`
  pins the specification hashes of the sample humanoid and core orders: a change that
  moves them would redo a running night's jobs.

## Running

    .\Run-Forge.ps1 -Order <order.json> -Out <folder> [-Check] [-Estimate] [-Foreground]
                    [-RetryFailed] [-Only <families>] [-Limit <n>] [-Server <url>]
    .\Run-Forge.ps1 -Out <folder> -Status
    .\Run-Forge.ps1 -Out <folder> -Stop [-Now]

`-Order` is required except with `-Status` and `-Stop`; `-Out` is always required. The
launcher reads `forge.local.json` at the repository root (its `python`, else the `python`
on the PATH), copies the program, the workflows, the order and the configuration to
`<Out>\program` and runs that copy detached (WMI, `pythonw`), so editing the repository or
the order does not change a run in progress.

Without the launcher:

    python -m forge --order <order.json> --out <folder> [--config forge.local.json] [--config-base DIR]
                    [--check] [--estimate] [--limit 6] [--only F1,F2] [--retry-failed] [--one-pass]
                    [--server URL] [--lock-dir DIR]
    python -m forge --out <folder> --provenance <file.json>

The server address is `--server`, else the `COMFY_SERVER` environment variable, else
`comfy_url` of the configuration (default `http://127.0.0.1:8188`). Waiting times (the
tests shorten them): `FORGE_PASS_SLEEP` (4 s between passes), `FORGE_ERROR_SLEEP` (30 s
after an error or with the server down), `FORGE_LOST_AFTER` (20 s before resubmitting a
prompt the server lost), `FORGE_HEAVY_WAIT` (300 s waiting for VRAM for a heavy job),
`FORGE_GPU_WAIT` (the configuration's `gpu_wait`, 300 s: a local GPU job waiting while
another process holds the card), `FORGE_QUEUE_WAIT_WARN` (the configuration's
`queue_wait_warn`, 3600 s: a waiting local GPU job is called stalled) and
`FORGE_WAIT_LOG_EVERY` (900 s between the log lines of a waiting local GPU job).
`FORGE_LOCK_DIR` (or `--lock-dir`) moves the GPU lock file (the tests use a temporary folder;
`tests/fake_comfy.py` sets it on import) and `FORGE_NVIDIA_SMI` replaces the `nvidia-smi`
program (`tests/fake_comfy.py` points it to a missing file, so tests never ask the card).

`-Check` exits with 0 (order valid, environment verified), 1 (order problems or
environment not ready) or 3 (environment not verified: the server did not answer).

## GPU rules

- Hunyuan3D jobs (group `hy3d`, `"heavy": true`) go alone: sent only with no other factory
  prompt in the queue, after `/free`, and after `/system_stats` shows at least 3 GB of
  free VRAM (waits up to 300 s, then goes anyway). While a heavy job is queued nothing
  else of the factory is sent.
- Never run Hunyuan outside the ComfyUI server while the server is open (both compete for
  the VRAM).
- Machine-wide lock: a factory run holds `%USERPROFILE%\.art-forge\gpu.lock` (an
  operating-system lock, released when the process ends) for its whole run, and each local
  GPU job and each runner of `forge/local_gpu.py` takes it too (re-entrant in one process).
  A second factory refuses to start (exit 2); a lock left by a process that is gone is
  taken over and logged. The folder is outside AppData on purpose: a packaged (MSIX)
  desktop application redirects the AppData writes of the processes it starts (an agent's
  shell, the tests) into a private copy, so a detached night and such a process locked two
  different files (found on 2026-10-19); a lock file that resolves elsewhere than its path
  is refused. The launcher passes its lock folder (`FORGE_LOCK_DIR` or the default) and its
  server (`-Server`, else `COMFY_SERVER`) to the detached factory on the command line
  (`--lock-dir`, `--server`): a process started through WMI does not inherit the console's
  environment.
- Local GPU jobs (kind `local_gpu`) run alone: only when the whole ComfyUI queue is empty
  (`queue_running` and `queue_pending`, any client), after `/free` and one more pass (the
  server unloads its models), after logging the processes `nvidia-smi` lists (waiting up
  to `gpu_wait` while one holds more than `gpu_busy_mb`; when the driver reports no
  per-process amount, while the whole card has more than `gpu_used_busy_mb` in use, read
  from `nvidia-smi --query-gpu` or the server's `/system_stats`; without either it logs
  that and goes). While one is ready or running nothing is sent to ComfyUI. The wait for
  an empty queue has no time limit (the contract), but it is visible: `state.json`
  `gpu_wait` (job, since, reason, queue counts, the other clients' prompt ids, `stalled`
  after `queue_wait_warn`), `-Status` prints it, the log repeats it every 15 minutes, and
  `timings.csv` records the seconds waited (`wait_seconds`). A failure is tried once more
  after the queue is empty again; the log names an out-of-memory cause and an external
  prompt that started meanwhile.
- Interactive users jump the queue by posting to `/prompt` with `"front": true`; the
  factory never keeps more than `--limit` prompts in the queue for that reason.
- The same model goes in bursts (up to 24 jobs of one group in a row), then the first
  ready job in order.

## Resume, failures, stop

- Each job's manifest entry holds the hash of its full specification (workflow and its
  hash, values, hashes of its input files, including files the order supplies). A job
  whose specification did not change and whose files still match their recorded SHA-256
  never runs again. Missing or changed outputs make their producer ready again.
  The export's editable `bone_map.json` follows the manual-correction rule instead:
  only a manual correction (not a proposal or the order's copy) changes its specification.
- Prompts queued when the factory stopped are waited for, not resubmitted; a prompt the
  server lost is resubmitted; with the server down the factory waits and retries.
- A failed job is tried once more (the Hunyuan mesh with a 2048 texture and 512 views),
  then marked `failed`; only its dependants wait (a humanoid export stopped by an invalid
  bone map is not tried twice). A prompt that finishes without an output a later job
  reads (or without its painted mesh), or without any output files, counts as a failure
  too. A job whose specification cannot be worked out (an input missing from a finished
  job's record, a vanished file)
  is `failed` in `state.json` (`specification_errors`) and only its dependants wait.
  `-RetryFailed` reopens only already-failed jobs selected for this run, once at startup,
  with a fresh attempt budget. It does not keep reopening new failures indefinitely.
- `<out>/STOP` (the launcher's `-Stop`): nothing more is sent, queued prompts are
  collected, the file is removed and the factory exits. Running again resumes.

## Tests

    python -m unittest discover -s tests

Run from the repository root. They start a fake ComfyUI (`tests/fake_comfy.py`, an
`http.server` thread that returns small PNGs and a fake `.glb`) and run the real factory
as a process. No GPU inference and no visible window; about a minute.

`tests/test_new_tools_smoke.py` does the same for the parts exporter, the creature
exporter and the layout sheet (fixtures under `local/fixtures/new_tools/`, outputs in
`local/smoke/new_tools/`), and `tests/test_held_item_smoke.py` for the item tool, the hold and
the item sheet (`local/fixtures/held_item/`, `local/smoke/held_item/`).
`tests/test_local_tools_smoke.py` runs the humanoid exporter and the review sheet when
`forge.local.json` points to `blender_python` and `godot` (skipped otherwise): the fixture
GLBs are made once under `local/fixtures/`, outputs go to `local/smoke/`, normal maps are
baked on the CPU, Godot renders in a window at (-20000, -20000) with the dummy audio
driver and exits by itself. They hold this user's machine-wide GPU lock while they run
and are skipped when a factory run holds it. With `FORGE_SMOKE_RIG` (a rigged GLB) and `FORGE_SMOKE_CLIPS`
(a motion library), and optionally `FORGE_SMOKE_ITEMS`, it also exports that rig and makes
its sheet in `local/smoke/real/`. Never commit those files. Never run SkinTokens or UniRig
from a test (they need the GPU). `tests/test_export_pose_smoke.py` runs only Blender, on the
CPU and without a bake (outputs in `local/smoke/pose/`): the synthetic figure facing -y, +x and
-x and with its bones' tails off their children must export an idle with straight legs, and the
figure turned 30 degrees, with its feet or its body askew, must face glTF +z in the written GLB
(a twisted one must carry a `facing:` mesh flag).
`tests/test_humanoid_body_smoke.py` also runs only Blender on the CPU: it exports two synthetic
approved bodies (`local/fixtures/humanoid_body/`, the sample order's) and conforms two variants
of other proportions to one (`local/smoke/humanoid_body/`).
`tests/test_bench.py` tests the bench's clip selection, views, measures and page, and runs it
on the synthetic humanoid when Blender and Godot are configured (no GPU lock; outputs in
`local/smoke/bench/`).
`tests/test_humanoid_motions.py` and `tests/test_launcher.py` need no tool: HY-Motion is
replaced by `tests/fixtures/motion/hymotion_standin.py` (`FORGE_HYMOTION_ENTRY`), and the
launcher test starts a factory detached (WMI) against the fake server with a private lock
folder.
