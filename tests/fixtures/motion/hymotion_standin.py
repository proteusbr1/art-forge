"""A stand-in for tools/motion/hymotion_entry.py in the tests (FORGE_HYMOTION_ENTRY): the same
command line, no model. It writes HY-Motion's arrays of a synthetic walk (or, when the prompt
says "stands", a figure standing still) at 30 fps for the requested duration, and a record."""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, TESTS)
sys.path.insert(0, os.path.dirname(TESTS))

import numpy as np  # noqa: E402
from motion_synth import smpl_output, walk  # noqa: E402

ap = argparse.ArgumentParser()
for name in ("checkout", "model", "config", "text-encoder", "clip", "prompt-file", "out", "record"):
    ap.add_argument("--" + name, required=True)
ap.add_argument("--seed", type=int, required=True)
ap.add_argument("--steps", type=int, default=50)
ap.add_argument("--guidance", type=float, default=5.0)
ap.add_argument("--duration", type=float, required=True)
a = ap.parse_args()
with open(a.prompt_file, encoding="utf-8") as f:
    text = f.read()
if "fail" in text:
    sys.exit("the stand-in was asked to fail")
pos = walk(a.duration, 30.0, still="stands" in text)
kp, transl = smpl_output(pos)
np.savez_compressed(a.out, keypoints3d=kp, transl=transl)
with open(a.record, "w", encoding="utf-8") as f:
    json.dump({"model": "stand-in", "text": text, "seed": a.seed, "requested_steps": a.steps,
               "guidance_scale": a.guidance, "duration_seconds": a.duration, "fps": 30, "frames": len(pos),
               "checkpoint_sha256": None}, f)
print("HYMOTION done (stand-in)")
