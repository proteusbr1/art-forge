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
  resolved) and `check_order` (every problem of an order).
- `forge/config.py`: `load_config` (the machine configuration, `forge.local.json`).
- `forge/stages.py`: the stage catalogue per recipe, params, inputs and review keys.
- `forge/recipes.py`: recipes that expand each piece into jobs (`expand`).
- `forge/factory_core.py`: job states, scheduling, `state.json`, estimate.
- `forge/comfy_jobs.py`: builds a workflow's prompt, submits it, collects its outputs.
- `forge/local_steps.py`, `forge/scoring.py`: local steps without GPU (picks, sheets,
  seamless materials) and the pick scores.
- `forge/provenance.py`: provenance records and export.
- `forge/comfy_server.py`, `forge/factory_util.py`: HTTP client and helpers.
- `forge/workflows/`: production ComfyUI workflows in API format.
- `tests/`: unit tests and the fake ComfyUI server; `tests/fixtures/` holds the sample
  orders.

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

    python -m forge --order <order.json> --out <folder> [--config forge.local.json]
                    [--check] [--estimate] [--limit 6] [--only F1,F2] [--retry-failed] [--one-pass]
    python -m forge --out <folder> --provenance <file.json>

The server address is `--server`, else the `COMFY_SERVER` environment variable, else
`comfy_url` of the configuration (default `http://127.0.0.1:8188`). Waiting times (the
tests shorten them): `FORGE_PASS_SLEEP` (4 s between passes), `FORGE_ERROR_SLEEP` (30 s
after an error or with the server down), `FORGE_LOST_AFTER` (20 s before resubmitting a
prompt the server lost), `FORGE_HEAVY_WAIT` (300 s waiting for VRAM for a heavy job).

## GPU rules

- Hunyuan3D jobs (group `hy3d`, `"heavy": true`) go alone: sent only with no other factory
  prompt in the queue, after `/free`, and after `/system_stats` shows at least 3 GB of
  free VRAM (waits up to 300 s, then goes anyway). While a heavy job is queued nothing
  else of the factory is sent.
- Never run Hunyuan outside the ComfyUI server while the server is open (both compete for
  the VRAM).
- Interactive users jump the queue by posting to `/prompt` with `"front": true`; the
  factory never keeps more than `--limit` prompts in the queue for that reason.
- The same model goes in bursts (up to 24 jobs of one group in a row), then the first
  ready job in order.

## Resume, failures, stop

- Each job's manifest entry holds the hash of its full specification (workflow and its
  hash, values, hashes of its input files, including files the order supplies). A job
  whose specification did not change and whose files exist never runs again.
- Prompts queued when the factory stopped are waited for, not resubmitted; a prompt the
  server lost is resubmitted; with the server down the factory waits and retries.
- A failed job is tried once more (the Hunyuan mesh with a 2048 texture and 512 views),
  then marked `failed`; only its dependants wait. `-RetryFailed` tries again.
- `<out>/STOP` (the launcher's `-Stop`): nothing more is sent, queued prompts are
  collected, the file is removed and the factory exits. Running again resumes.

## Tests

    python -m unittest discover -s tests

Run from the repository root. They start a fake ComfyUI (`tests/fake_comfy.py`, an
`http.server` thread that returns small PNGs and a fake `.glb`) and run the real factory
as a process. No GPU and no window; about 20 seconds.
