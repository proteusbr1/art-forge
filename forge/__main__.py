"""art-forge: keeps a local ComfyUI busy for hours with the art an order asks for, without
anyone watching it.

  python -m forge --order <order.json> --out <dir> [--config forge.local.json] [--limit 6]
                  [--estimate] [--check] [--only FAMILY[,FAMILY]] [--retry-failed] [--one-pass]
  python -m forge --out <dir> --provenance <file.json>

The order holds art-direction briefs per family and per piece and a recipe for each piece;
the recipes expand into jobs (recipes.py). Raw jobs may also be listed under "tasks".
The order format is documented in docs/order.md.

Behaviour:
- Only the HTTP API of the server is used (/prompt, /queue, /history, /view,
  /upload/image, /free, /system_stats, and /object_info for --check). Nothing in ComfyUI is
  installed, changed or restarted.
- At most --limit (6) of the factory's own prompts are in the server's queue at a time, so
  prompts of other users never wait behind hundreds of factory jobs. An interactive user
  that needs the GPU now posts its prompt with "front": true in the body of /prompt and is
  next after the running job.
- Hunyuan jobs are heavy: they go alone (nothing else of the factory in the queue) after a
  /free, because the Paint stage runs out of VRAM next to other models.
- Resumable and idempotent: <out>/manifest.json records each job with the hash of its
  full specification (workflow, values, hashes of its input files) and the hashes of the
  files it produced. A job whose specification did not change is never run again. Prompts
  still in the server's queue when the factory stopped are waited for, not resubmitted. A
  prompt the server lost (restart, crash) is resubmitted; while the server does not answer
  the factory waits and retries. A failed job is retried once; then it is marked failed and
  only its own dependants wait, the rest of the queue goes on.
- Picks are automatic (sharpness of the object and cleanliness of the background for
  concepts; detail and evenness of light for materials) and marked for review in
  <piece>/pick_<step>.json ("review": "pending"). To choose another seed, set "chosen" to
  another candidate job id and "manual": true in that file: the next pass redoes only the
  jobs that depend on the pick.
- Stop: create <out>/STOP (the launcher's -Stop does it). The factory submits nothing
  more, waits for its prompts in the queue and exits. Running it again resumes.
- Time per job: <out>/timings.csv (server execution seconds from its own timestamps and
  wall seconds); progress in <out>/state.json; log in <out>/factory.log.
- Provenance: what each job used is recorded in the manifest; --provenance writes the
  provenance file of every finished output (no server, no GPU).

Python standard library plus numpy and Pillow (picks, seams, sheets).
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def inside(path, folder):
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def server_url(a, config):
    """--server, else the COMFY_SERVER environment variable, else the configuration."""
    return a.server or os.environ.get("COMFY_SERVER") or config["comfy_url"]


def run_check(a, config):
    from .comfy_server import Server
    from .order import check_order, load_order
    order = load_order(a.order, a.order_base or None)
    server = Server(server_url(a, config))
    info = None
    try:
        info = server.get_json("/object_info", timeout=10)
    except Exception as ex:
        print("server %s not answering (%s): node classes not checked" % (server.url, ex))
    problems = check_order(order, config, info)
    for p in problems:
        print("PROBLEM " + p)
    if problems:
        print("%d problem(s) in %s" % (len(problems), a.order))
        return 1
    print("order %s: no problems%s" % (a.order, "" if info is not None else " (without the server)"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--order", "--list", dest="order", default="", help="the order (JSON)")
    ap.add_argument("--order-base", default="", help="folder that relative input paths of the order "
                                                     "are resolved against (default: the order's folder)")
    ap.add_argument("--out", required=True, help="output folder (outside this repository)")
    ap.add_argument("--config", default="", help="machine configuration (forge.local.json)")
    ap.add_argument("--server", default="", help="ComfyUI address (overrides the configuration)")
    ap.add_argument("--limit", type=int, default=6, help="max factory prompts in the server queue")
    ap.add_argument("--only", default="", help="only these families (comma separated)")
    ap.add_argument("--estimate", action="store_true", help="print job counts and GPU hours, run nothing")
    ap.add_argument("--check", action="store_true", help="check the order (and the server if it answers), run nothing")
    ap.add_argument("--retry-failed", action="store_true", help="try failed jobs again")
    ap.add_argument("--one-pass", action="store_true", help="one scheduling pass, then exit")
    ap.add_argument("--provenance", default="", metavar="FILE",
                    help="write the provenance file of <out>/manifest.json, run nothing")
    a = ap.parse_args(argv)
    if a.provenance:
        # Reads <out> and writes only FILE: allowed for any folder (the smoke tests keep
        # theirs under local/).
        from .provenance import export
        n = export(os.path.realpath(a.out), a.provenance)
        print("provenance of %d files written to %s" % (n, a.provenance))
        return 0
    if inside(a.out, ROOT):
        print("the output folder must be outside the factory's repository: %s" % a.out)
        return 2
    if not a.order:
        ap.error("--order is required")
    from .config import load_config
    config = load_config(a.config or None)
    if a.check:
        return run_check(a, config)
    from .factory_core import Factory
    f = Factory(a, server_url(a, config))
    f.load()
    if a.estimate:
        f.estimate()
        return 0
    return f.run()


if __name__ == "__main__":
    sys.exit(main())
