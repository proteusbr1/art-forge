"""SkinTokens with a fixed seed, offline, on one mesh: skeleton and skin weights predicted
and transferred onto the given mesh. Runs with SkinTokens' own Python, from its checkout
(forge/local_gpu.py run_rig starts it):

  python skintokens_entry.py --checkout DIR --model MESH.glb --out RIGGED.glb
      [--seed 9001] [--beams 10] [--postprocess]

It seeds Python, NumPy and PyTorch, turns the Hugging Face and Gradio network access off
and runs the checkout's demo.py as its command line (--use_transfer, --num_beams).
"""
import argparse
import os
from pathlib import Path
import random
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=9001)
    parser.add_argument("--beams", type=int, default=10)
    parser.add_argument("--postprocess", action="store_true")
    args = parser.parse_args()
    source, output, checkout = args.model.resolve(), args.out.resolve(), args.checkout.resolve()
    if output.exists():
        raise FileExistsError("the output exists already: %s" % output)
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.chdir(checkout)
    sys.path.insert(0, str(checkout))
    import numpy as np
    import torch
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    sys.argv = ["demo.py", "--input", str(source), "--output", str(output),
                "--use_transfer", "--num_beams", str(args.beams)]
    if args.postprocess:
        sys.argv.append("--use_postprocess")
    print("SKINTOKENS seed:", args.seed, "beams:", args.beams, flush=True)
    runpy.run_path(str(checkout / "demo.py"), run_name="__main__")
    if not output.exists():
        raise RuntimeError("SkinTokens returned without the requested GLB")


if __name__ == "__main__":
    main()
