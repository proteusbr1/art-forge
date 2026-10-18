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
  seamless materials, a humanoid's cutout and final copy) and the pick scores.
- `forge/provenance.py`: provenance records and export.
- `forge/comfy_server.py`, `forge/factory_util.py`: HTTP client and helpers.
- `forge/local_gpu.py`: runners of the local tools that use the card (`run_rig`,
  `run_export`, `run_review`), their logs and provenance, and `RUNNERS` (the tools of the
  `local_gpu` job kind: `rig`, `export`, `review`).
- `forge/workflows/`: production ComfyUI workflows in API format.
- `tools/blender/`: `humanoid_bones.py` (bone map from the skeleton's tree, no `bpy`) and
  `humanoid_export.py` (the humanoid export, run by `blender_python`).
- `tools/review_godot/`: the Godot project of the review sheets (off screen).
- `tools/rig/skintokens_entry.py`: SkinTokens with a fixed seed, run by its own Python.
- `docs/conventions.md`: motion library, held items, exporter outputs and reports, sheet.
- `tests/`: unit tests and the fake ComfyUI server; `tests/fixtures/` holds the sample
  orders (`order_generic.json`, `order_humanoid.json`) and `tests/fixtures/humanoid/` the
  synthetic humanoid (generator, library, item).

## Running

    .\Run-Forge.ps1 -Order <order.json> -Out <folder> [-Check] [-Estimate] [-Foreground]
                    [-RetryFailed] [-Only <families>] [-Limit <n>]
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
`FORGE_LOCK_DIR` moves the GPU lock file (the tests use a temporary folder;
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
- Machine-wide lock: a factory run holds `%LOCALAPPDATA%\art-forge\gpu.lock` (an
  operating-system lock, released when the process ends) for its whole run, and each local
  GPU job and each runner of `forge/local_gpu.py` takes it too (re-entrant in one process).
  A second factory refuses to start (exit 2); a lock left by a process that is gone is
  taken over and logged.
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

`tests/test_local_tools_smoke.py` runs the humanoid exporter and the review sheet when
`forge.local.json` points to `blender_python` and `godot` (skipped otherwise): the fixture
GLBs are made once under `local/fixtures/`, outputs go to `local/smoke/`, normal maps are
baked on the CPU, Godot renders in a window at (-20000, -20000) with the dummy audio
driver and exits by itself. They hold this user's machine-wide GPU lock while they run
and are skipped when a factory run holds it. With `FORGE_SMOKE_RIG` (a rigged GLB) and `FORGE_SMOKE_CLIPS`
(a motion library), and optionally `FORGE_SMOKE_ITEMS`, it also exports that rig and makes
its sheet in `local/smoke/real/`. Never commit those files. Never run SkinTokens or UniRig
from a test (they need the GPU).
