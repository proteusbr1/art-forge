"""The motion bench (forge/bench.py): the clip selection, the views, the bench's own measures
and the page without tools; a smoke run on the synthetic humanoid when forge.local.json points
to blender_python and godot (skipped otherwise).

The smoke run is the bench as a person runs it: Blender on the CPU and a short off-screen Godot
render, no GPU lock taken. Outputs go to local/smoke/bench/ (ignored by git).
"""

import json
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from forge import bench, local_gpu  # noqa: E402
from forge.config import load_config  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "humanoid")
SMOKE = os.path.join(ROOT, "local", "smoke", "bench")
CONFIG = load_config(os.path.join(ROOT, "forge.local.json"))
HAVE_TOOLS = not local_gpu.check_tools(CONFIG, "export") and not local_gpu.check_tools(CONFIG, "review")


def library(*names):
    return {"format": "forge-motion-library", "holds": {"box": {"follow": "chest"}},
            "anims": {n: {"frames": [{}], "fps": 24} for n in names}}


class ClipSelection(unittest.TestCase):
    LIB = library("idle_none", "walk_none", "idle_rifle", "walk_rifle", "walk_rifle_diag", "aim_rifle", "collapse")

    def test_every_clip_in_the_library_order_without_a_choice(self):
        self.assertEqual(bench.select_clips(self.LIB), list(self.LIB["anims"]))

    def test_names_and_patterns_in_the_order_given_each_once(self):
        got = bench.select_clips(self.LIB, ["collapse", "*_rifle*", "walk_rifle", " idle_none "])
        self.assertEqual(got, ["collapse", "idle_rifle", "walk_rifle", "walk_rifle_diag", "aim_rifle", "idle_none"])

    def test_an_exact_name_wins_over_the_pattern_it_also_is(self):
        lib = library("walk", "walk_fast")
        self.assertEqual(bench.select_clips(lib, ["walk"]), ["walk"])

    def test_what_matches_nothing_is_named(self):
        with self.assertRaises(bench.BenchError) as cm:
            bench.select_clips(self.LIB, ["idle_rifle", "run_*", "jump"])
        self.assertIn("run_*", str(cm.exception))
        self.assertIn("jump", str(cm.exception))

    def test_a_library_without_clips_is_refused(self):
        for doc in ({}, {"anims": {}}, {"anims": []}, []):
            with self.assertRaises(bench.BenchError):
                bench.select_clips(doc)

    def test_the_subset_keeps_the_holds(self):
        sub = bench.library_subset(self.LIB, ["aim_rifle", "idle_none"])
        self.assertEqual(list(sub["anims"]), ["aim_rifle", "idle_none"])
        self.assertEqual(sub["holds"], self.LIB["holds"])
        self.assertEqual(len(self.LIB["anims"]), 7)  # the library itself is not changed


class Views(unittest.TestCase):
    def test_views_tile_the_frame_without_overlap(self):
        size, views = bench.view_layout(1.8, yaws=(45, -90, -135), game_px=115, close_px=440)
        names = [v["name"] for v in views]
        self.assertEqual(names[0], "close")
        self.assertEqual(sum(v["scale"] == "game2x" for v in views), 3)
        self.assertEqual(sum(v["scale"] == "game" for v in views), 8)
        rects = [v["rect"] for v in views]
        for i, a in enumerate(rects):
            self.assertLessEqual(a[0] + a[2], size[0])
            self.assertLessEqual(a[1] + a[3], size[1])
            self.assertGreaterEqual(a[1], bench.LABEL)
            for b in rects[i + 1:]:
                apart = (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])
                self.assertTrue(apart, (a, b))

    def test_the_game_size_is_the_standing_figure_on_screen(self):
        _, views = bench.view_layout(2.0, pitch=30, game_px=100)
        game = next(v for v in views if v["scale"] == "game")
        double = next(v for v in views if v["scale"] == "game2x")
        # a vertical segment of the figure's height, seen 30 degrees from above
        self.assertAlmostEqual(game["ppm"] * 2.0 * 0.8660254, 100.0, delta=0.05)
        self.assertAlmostEqual(double["ppm"], 2 * game["ppm"], delta=1e-3)

    def test_no_direction_is_refused(self):
        with self.assertRaises(bench.BenchError):
            bench.view_layout(1.8, yaws=())


class Measures(unittest.TestCase):
    def test_the_item_direction(self):
        m = bench.item_measures([{"yaw_deg": 10, "pitch_deg": -30}, None, {"yaw_deg": -20, "pitch_deg": -10}])
        self.assertEqual(m, {"yaw_mean_deg": -5.0, "yaw_max_deg": 20.0, "pitch_mean_deg": -20.0,
                             "pitch_min_deg": -30.0, "pitch_max_deg": -10.0})
        self.assertIsNone(bench.item_measures([None, None]))
        self.assertIsNone(bench.item_measures(None))

    def _walk(self, n=24, travel=0.75, height=2.0, cross=False, slip=0.0):
        """A walk in place: each foot planted half the cycle, going back at the travel's speed,
        then swung forward lifted."""
        v = travel * height / n
        frames = []
        for i in range(n):
            pair = []
            for k, off in ((0, 0), (1, n // 2)):
                j = (i + off) % n
                if j < n // 2:
                    fwd, up = v * (n / 4 - j) + slip * j, 0.1
                else:
                    fwd, up = v * (j - 3 * n / 4), 0.3
                side = (0.1 if k == 0 else -0.1) * (-1.5 if cross and up > 0.2 else 1)
                pair.append([side, fwd, up])
            frames.append(pair)
        return frames

    def test_planted_feet_do_not_slide_with_the_travel_added(self):
        m = bench.feet_measures(self._walk(), 2.0, [0.0, 0.75])
        self.assertLess(m["planted_slide_cm"], 0.01)
        self.assertAlmostEqual(m["ankle_gap_min_cm"], 20.0, delta=0.01)

    def test_a_slipping_foot_and_crossed_legs_are_measured(self):
        m = bench.feet_measures(self._walk(slip=0.01, cross=True), 2.0, [0.0, 0.75])
        self.assertGreater(m["planted_slide_cm"], 5.0)
        self.assertLess(m["ankle_gap_min_cm"], 0.0)

    def test_without_the_travel_a_walk_in_place_slides(self):
        self.assertGreater(bench.feet_measures(self._walk(), 2.0, None)["planted_slide_cm"], 30.0)

    def test_the_ends_of_a_planted_run_are_not_counted(self):
        frames = [[[0.1, 0.0, 0.1], [-0.1, 0.0, 0.1]] for _ in range(6)]
        frames[0][0] = [0.1, 0.5, 0.1]  # the heel comes down while it still moves
        self.assertEqual(bench.feet_measures(frames, 2.0, None, loop=False)["planted_slide_cm"], 0.0)

    def test_missing_ankles_give_no_measure(self):
        self.assertIsNone(bench.feet_measures([], 1.8))
        self.assertIsNone(bench.feet_measures([None, [[0, 0, 0], [0, 0, 0]]], 1.8))

    def test_rows_join_the_model_the_report_and_the_render(self):
        model = {"height": 2.0, "animations": [
            {"name": "walk", "kind": "walk", "holds": "box", "follow": "chest", "hands": {"left": "guard"},
             "frames": 24, "fps": 24, "loop": True, "travel": [0.0, 0.75]}]}
        report = {"thresholds": {"hand_socket_cm": 2.0}, "clips": {"walk": {"hand_socket_cm": 3.5}}}
        render = {"clips": {"walk": {"frames": 24, "item": [], "ankles": self._walk()}}}
        rows = bench.clip_rows(["walk", "gone"], model, report, render)
        self.assertEqual(rows[0]["over"], ["hand_socket_cm"])
        self.assertTrue(rows[0]["rendered"])
        self.assertLess(rows[0]["feet"]["planted_slide_cm"], 0.01)
        self.assertEqual((rows[1]["exported"], rows[1]["rendered"]), (False, False))


class Page(unittest.TestCase):
    def doc(self, title="round <2>"):
        return {"title": title, "rig": "C:/x/rig.glb", "item": "carbine", "made": "2026-01-01 00:00:00",
                "thresholds": {"hand_socket_cm": 2.0, "stretch_p95": 0.5},
                "clips": [
                    {"name": "idle", "kind": "idle", "holds": "carbine", "follow": "chest",
                     "hands": {"right": "grip", "left": "guard"}, "frames": 48, "fps": 24.0,
                     "measures": {"hand_socket_cm": 3.25, "stretch_p95": 0.2, "foot_slide_cm": None,
                                  "penetration_cm": 1.0, "reach_shortfall_cm": 0.0},
                     "over": ["hand_socket_cm"], "item": {"yaw_mean_deg": 1.0, "yaw_max_deg": 2.0,
                                                          "pitch_mean_deg": -30.0, "pitch_min_deg": -31.0,
                                                          "pitch_max_deg": -29.0},
                     "feet": {"ankle_gap_min_cm": -1.5, "planted_slide_cm": 0.4},
                     "rendered": True, "preview": "idle.webp", "sheet": "idle.png"},
                    {"name": "walk", "kind": "walk", "holds": None, "hands": {}, "frames": 24, "fps": 24.0,
                     "measures": {k: None for k in bench.MEASURES}, "over": [], "rendered": False}]}

    def test_one_row_per_clip_with_its_previews_and_measures(self):
        page = bench.page_html(self.doc())
        self.assertIn("<title>round &lt;2&gt;</title>", page)
        self.assertIn('<img src="idle.webp"', page)
        self.assertIn('href="idle.png"', page)
        self.assertIn("left on guard, right on grip", page)
        self.assertIn("<td class=over>3.25</td>", page)
        self.assertIn("<td class=over>-1.5</td>", page)  # crossed legs
        self.assertIn("-30 (-31 to -29)", page)
        self.assertIn("not rendered", page)
        self.assertIn("pending review", page)
        self.assertNotIn("before", page)

    def test_a_previous_bench_is_shown_under_each_clip(self):
        old = self.doc("old")
        old["clips"] = old["clips"][:1]
        page = bench.page_html(self.doc(), old, "../r01")
        self.assertIn('<img src="../r01/idle.webp" alt="before">', page)
        self.assertIn("before: not in ../r01", page)  # walk was not in the old bench

    def test_the_sheet_frames_spread_over_the_clip(self):
        self.assertEqual(bench.sheet_frames(1), [0])
        self.assertEqual(bench.sheet_frames(48), [0, 7, 13, 20, 27, 34, 40, 47])
        self.assertEqual(bench.sheet_frames(3), [0, 1, 2])


@unittest.skipUnless(HAVE_TOOLS, "forge.local.json does not point to blender_python and godot")
class SmokeRun(unittest.TestCase):
    def test_the_synthetic_humanoid_on_the_bench(self):
        import test_local_tools_smoke as smoke  # the fixture GLBs, made once
        cache = smoke.fixtures()
        out = os.path.join(SMOKE, "run")
        shutil.rmtree(out, ignore_errors=True)
        items = os.path.join(SMOKE, "items")
        shutil.rmtree(items, ignore_errors=True)
        os.makedirs(items)
        shutil.copyfile(os.path.join(FIX, "items", "box.json"), os.path.join(items, "box.json"))
        shutil.copyfile(os.path.join(cache, "box.glb"), os.path.join(items, "box.glb"))
        doc = bench.run_bench(os.path.join(cache, "humanoid.glb"), os.path.join(FIX, "library.json"), out, CONFIG,
                              items=items, item="box", only=["walk", "idle"], game_px=48, close_px=120,
                              title="smoke")
        self.assertEqual([r["name"] for r in doc["clips"]], ["walk", "idle"])
        for row in doc["clips"]:
            self.assertTrue(row["rendered"], row)
            for f in (row["preview"], row["sheet"]):
                self.assertTrue(os.path.getsize(os.path.join(out, f)) > 0, f)
            self.assertIsNotNone(row["feet"])
        idle, walk = doc["clips"][1], doc["clips"][0]
        self.assertEqual(idle["hands"], {"right": "grip", "left": "guard"})
        self.assertLess(idle["measures"]["hand_socket_cm"], 2.0)
        self.assertIsNotNone(idle["item"])
        self.assertIsNone(walk["item"])  # the walk holds nothing: the item bone is hidden
        self.assertLess(walk["measures"]["foot_slide_cm"], 2.0)
        self.assertGreater(walk["feet"]["ankle_gap_min_cm"], 0.0)
        from PIL import Image
        with Image.open(os.path.join(out, "walk.webp")) as im:
            self.assertEqual(list(im.size), doc["size"])
            self.assertEqual(getattr(im, "n_frames", 1), 8)
        with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('<img src="walk.webp"', page)
        self.assertFalse(os.path.exists(os.path.join(out, "frames")))
        with open(os.path.join(out, "bench.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "forge-bench")
        self.assertLess(doc["timings"]["total"], 120.0)
        # a second round compared with the first
        doc2 = bench.run_bench(os.path.join(cache, "humanoid.glb"), os.path.join(FIX, "library.json"),
                               os.path.join(SMOKE, "run2"), CONFIG, items=items, item="box", only=["idle"],
                               game_px=48, close_px=120, compare=out)
        with open(os.path.join(SMOKE, "run2", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('src="../run/idle.webp" alt="before"', page)
        self.assertEqual(len(doc2["clips"]), 1)


if __name__ == "__main__":
    unittest.main()
