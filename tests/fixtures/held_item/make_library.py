"""The motion library of the held-item tests and sample order: the synthetic humanoid's idle
pose (tests/fixtures/humanoid/make_fixtures.py) as one idle clip and one aim clip per hold
class of the sample (carbine, pistol, lantern), each holding its class; no hold placements,
so each item follows the right palm by its grip.

  python make_library.py      (writes library.json next to this file; committed)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "humanoid"))
import make_fixtures as mf  # noqa: E402

CLASSES = ("carbine", "pistol", "lantern")


def library():
    frames = mf.idle_frames(6)
    anims = {}
    for cls in CLASSES:
        for kind, holds in (("idle", cls), ("aim", cls + "_aim")):
            anims["%s_%s" % (kind, cls)] = {
                "kind": kind, "fps": 12.0, "loop": True, "holds": holds, "feet": "clip",
                "pelvis_stand": mf.REST["pelvis"][2], "stance": {"left": "all", "right": "all"},
                "source": "synthetic idle pose", "frames": frames}
    return {"format": "forge-motion-library", "version": 1, "canon": mf.CANON, "anims": anims}


if __name__ == "__main__":
    with open(os.path.join(HERE, "library.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(library(), f, indent=1)
        f.write("\n")
    print("wrote", os.path.join(HERE, "library.json"))
