"""Writes clips.json next to this file: a small forge-creature-clips library in role space (an
idle of the synthetic four-legged beast of tests/creature_skeletons.py), the sample creature
order's library source.

  python make_clips.py [--check]   (--check: exit 1 when the committed file differs)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools", "blender"))
import creature_bones as cb  # noqa: E402
import creature_motion as cm  # noqa: E402
import creature_skeletons as sk  # noqa: E402

OUT = os.path.join(HERE, "clips.json")


def build():
    tree, _ = sk.beast().tree()
    doc = cb.propose(tree, sk.BEAST_PLAN)
    rig = cm.Rig(tree, doc["roles"], doc["root"], 1.2, doc["joints"])
    clip = cm.generate(rig, sk.BEAST_PLAN, {"name": "idle", "source": "procedural", "kind": "breathe", "frames": 6,
                                             "fps": 12})
    lib = cm.library(rig, [clip], source="synthetic test library")
    return json.dumps(lib, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8") as f:
            sys.exit(0 if f.read() == text else 1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote", OUT)
