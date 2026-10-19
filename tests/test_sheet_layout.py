"""The layout of the articulated and creature review sheet (forge/sheet_layout.py), without
Godot: cells per direction, the front view, a strip per joint or clip, failure crosses,
verdict lines, flag lines and the banner."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from forge import sheet_layout  # noqa: E402

ARTICULATED = {"format": "forge-articulated", "size_m": [3.0, 2.25, 6.0], "center_m": [0.0, 1.125, 0.0],
               "joints": [{"part": "turret", "kind": "turn", "axis": [0, 1, 0], "range": [-180, 180], "unit": "degrees"},
                          {"part": "mount", "kind": "fixed", "axis": None, "range": [0, 0]},
                          {"part": "door", "kind": "slide", "axis": [0, 0, 1], "range": [0, 1.2], "unit": "metres"}]}
CREATURE = {"format": "forge-creature", "size_m": [1.2, 1.9, 1.4], "center_m": [0.0, 0.95, -0.3],
            "animations": [{"name": "walk_none", "kind": "undulate", "frames": 24, "fps": 24, "loop": True},
                           {"name": "idle_none", "kind": "breathe", "frames": 20, "fps": 20, "loop": True},
                           {"name": "collapse", "kind": "collapse", "frames": 30, "fps": 20, "loop": False}]}


def variant(label, model=None, failed=None, flags=(), motion_flags=(), verdict=None):
    v = {"label": label, "verdict": verdict}
    if failed:
        v.update(failed=True, reason=failed)
    else:
        v.update(failed=False, glb="x/%s.glb" % label, model=model, mesh_report={"flags": list(flags)},
                 motion_report={"flags": list(motion_flags)} if model and model["format"] == "forge-creature" else None)
    return v


class Layout(unittest.TestCase):
    def test_articulated(self):
        lay = sheet_layout.layout([variant("v1", ARTICULATED, verdict={"class": "acceptable", "reasons": []}),
                                   variant("v2", failed="parts failed: x",
                                           verdict={"class": "rejected", "reasons": ["parts failed"]})],
                                  {"pitch": 30, "directions": 4, "strip_frames": 3}, banner=None)
        v1, v2 = lay["variants"]
        kinds = [c["kind"] for c in v1["cells"]]
        self.assertEqual(kinds, ["small"] * 4 + ["front"] + ["strip"] * 6)  # the fixed joint has no strip
        turret = [c for c in v1["cells"] if c.get("joint") == "turret"]
        self.assertEqual([c["pose"][0]["value"] for c in turret], [-180.0, 0.0, 180.0])
        door = [c for c in v1["cells"] if c.get("joint") == "door"]
        self.assertEqual([c["pose"][0]["value"] for c in door], [0.0, 0.6, 1.2])
        self.assertEqual(door[0]["pose"][0]["kind"], "slide")
        self.assertEqual([c["yaw"] for c in v1["cells"][:4]], [0.0, 90.0, 180.0, 270.0])
        self.assertEqual(v1["cells"][4]["pitch"], 0.0)
        self.assertEqual((v1["pose"], v1["verdict"]), ("rest", "acceptable"))
        self.assertEqual(v2["cells"][0]["kind"], "failure")
        self.assertEqual(lay["failures"], [v2["cells"][0]["rect"]])
        texts = [t["text"] for t in lay["texts"]]
        self.assertIn("joint turret (turn -180 to 180 degrees)", texts)
        self.assertIn("joint door (slide 0 to 1.2 metres)", texts)
        self.assertIn("no flags", texts)
        self.assertTrue(any(t.startswith("v2   FAILED: parts failed") for t in texts))
        self.assertTrue(all(c["variant"] == 0 for c in lay["cells"]))
        w, h = lay["size"]
        for c in lay["cells"] + [{"rect": r} for r in lay["failures"]]:
            x, y, cw, ch = c["rect"]
            self.assertTrue(0 <= x and x + cw <= w and 0 <= y and y + ch <= h, c)

    def test_creature(self):
        lay = sheet_layout.layout([variant("v1", CREATURE, flags=["3 vertices have no weight"],
                                           motion_flags=[{"clip": "collapse", "measure": "below_ground_cm",
                                                          "value": 4.0, "threshold": 3.0}])],
                                  {"pitch": 60, "directions": 8, "strip_frames": 4, "front": False},
                                  banner="no variant passed: most often the rig (1 of 1)")
        [v] = lay["variants"]
        self.assertEqual(v["pose"], "idle_none")  # the first idle clip, not the first clip
        small = [c for c in v["cells"] if c["kind"] == "small"]
        self.assertEqual((len(small), small[0]["anim"], small[0]["target"]), (8, "idle_none", [0.0, 0.95, -0.3]))
        self.assertFalse([c for c in v["cells"] if c["kind"] == "front"])
        walk = [c for c in v["cells"] if c.get("clip") == "walk_none"]
        self.assertEqual([c["time"] for c in walk], [0.0, 0.25, 0.5, 0.75])
        collapse = [c for c in v["cells"] if c.get("clip") == "collapse"]
        self.assertEqual(collapse[-1]["time"], 1.45)  # a one-shot ends on its last frame
        self.assertEqual(lay["texts"][0]["text"], "NO VARIANT PASSED: MOST OFTEN THE RIG (1 OF 1)")
        self.assertEqual(v["flags"], ["mesh: 3 vertices have no weight",
                                      "motion: below_ground_cm over 3.0 in 1 clip(s), worst collapse 4.00"])
        self.assertEqual(lay["camera"]["yaws"][1], 45.0)

    def test_missing_reports(self):
        lay = sheet_layout.layout([{"label": "v1", "failed": False, "glb": "x.glb", "model": CREATURE,
                                    "mesh_report": None, "motion_report": None, "verdict": None}], {})
        self.assertEqual(lay["variants"][0]["flags"], ["mesh: no report", "motion: no report"])


if __name__ == "__main__":
    unittest.main()
