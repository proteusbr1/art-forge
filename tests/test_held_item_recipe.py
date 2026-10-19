"""The held-item recipe: how a piece expands into jobs, what the order check refuses, the
sample order (valid, and verified against a fake /object_info, also from the command line),
the review sheet's layout, and a night with the fake server and stand-ins for the local tools
(a failed variant, a flagged one, the pick by fit, the family folder of picked items, a failed
hold that does not reject its item, a manual frame or sockets file redoing only its own
variant)."""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from fake_comfy import FakeComfy  # noqa: E402
from forge import item_sheet_layout, local_gpu, stages  # noqa: E402
from forge.config import load_config  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import seeds_for, sha_obj  # noqa: E402
from forge.order import check_order, load_order  # noqa: E402
from forge.readiness import VERIFIED, check_environment  # noqa: E402
from forge.recipes import expand, refs_of  # noqa: E402
from test_readiness import object_info  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
LIBRARY = os.path.join(FIXTURES, "held_item", "library.json")
SAMPLE = os.path.join(FIXTURES, "order_held_item.json")
STYLE = {"global": "Plain test style.", "view_held_item": "A strict side view, front to the right, grey."}
ROLES = ("rgb", "mask", "rgba", "game_mesh", "raw_mesh", "painted_mesh", "metal_roughness", "glb", "sockets", "model",
         "mesh_report", "motion_report", "frame", "socket_map", "bone_map", "clips", "figure")


def stub_glb(path, skinned=True):
    """A GLB with a JSON chunk only (a skin or none): enough for the order check."""
    doc = {"asset": {"version": "2.0"}, "nodes": [{"name": "root"}]}
    if skinned:
        doc["skins"] = [{"joints": [0]}]
    body = json.dumps(doc).encode("utf-8")
    body += b" " * (-len(body) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(body)))
        f.write(struct.pack("<II", len(body), 0x4E4F534A))
        f.write(body)


def order_of(piece=None, family=None):
    base = {"id": "p", "brief": "A test carbine.", "item": {"class": "carbine"}, "params": {"length": 0.85},
            "inputs": {"figure": "figure.glb", "clips": "library.json"}}
    fam = {"id": "f", "recipe": "held_item", "variants": 2, "assets": [dict(base, **(piece or {}))]}
    fam.update(family or {})
    return {"version": 1, "style": dict(STYLE), "families": [fam]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_item_")
        self.config = load_config(None)
        for k in ("blender_python", "godot"):
            self.config[k] = sys.executable
        self.order_path = os.path.join(self.tmp, "order.json")
        stub_glb(os.path.join(self.tmp, "figure.glb"))
        shutil.copy(LIBRARY, os.path.join(self.tmp, "library.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, order):
        with open(self.order_path, "w", encoding="utf-8") as f:
            json.dump(order, f)
        return load_order(self.order_path)

    def problems(self, *args, **kw):
        return check_order(self.load(order_of(*args, **kw)), self.config)

    def jobs(self, *args, **kw):
        return {j["id"]: j for j in expand(self.load(order_of(*args, **kw)))}

    def assertProblem(self, problems, text):
        self.assertTrue(any(text in p for p in problems), "no problem with %r in %s" % (text, problems))

    def specs(self, order):
        self.load(order)
        a = SimpleNamespace(order=self.order_path, order_base="", out=os.path.join(self.tmp, "out"), only="",
                            retry_failed=False)
        f = Factory(a, "http://127.0.0.1:1")
        f.config = self.config
        f.load()
        out = {}
        while len(out) < len(f.jobs):
            for j in f.jobs:
                if j["id"] in out or any(r not in out for r in refs_of(j)):
                    continue
                sp = f.spec(j)
                out[j["id"]] = sp
                f.manifest[j["id"]] = {"status": "done", "spec": sp,
                                       "files": {r: ["x/%s.bin" % r, sha_obj([sp, r])] for r in ROLES}}
        return out

    def sample(self):
        """The sample order, its figure replaced by a stub when the synthetic humanoid of the
        smoke tests has not been made yet (a copy in the temporary folder)."""
        with open(SAMPLE, encoding="utf-8") as f:
            doc = json.load(f)
        figure = os.path.normpath(os.path.join(FIXTURES, "../../local/fixtures/humanoid/humanoid.glb"))
        stub = os.path.join(self.tmp, "figure.glb")
        for fam in doc["families"]:
            for holder in [fam] + fam["assets"]:
                ins = holder.get("inputs") or {}
                if "figure" in ins:
                    ins["figure"] = figure if os.path.isfile(figure) else stub
                if "clips" in ins:
                    ins["clips"] = LIBRARY
        path = os.path.join(self.tmp, "order_held_item.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path


class Expansion(Base):
    def test_chain_review_and_final(self):
        jobs = self.jobs()
        seeds = seeds_for("f/p", 2)
        chain = ("reference", "cutout", "mesh", "paint", "item", "hold")
        self.assertEqual(list(jobs), ["f/p/v%d/%s" % (s, st) for s in seeds for st in chain] + ["f/p/review", "f/p/final"])
        v = "f/p/v%d/" % seeds[0]
        ref = jobs[v + "reference"]["values"]
        self.assertEqual((ref["$WIDTH$"], ref["$HEIGHT$"]), (1536, 1024))
        self.assertIn("front to the right", ref["$PROMPT$"])
        item = jobs[v + "item"]
        self.assertEqual((item["kind"], item["tool"], item["group"]), ("local_gpu", "item", "local_gpu"))
        self.assertEqual(item["inputs"], {"mesh": {"ref": v + "paint", "role": "painted_mesh"},
                                          "metal_roughness": {"ref": v + "paint", "role": "metal_roughness"}})
        self.assertEqual(item["args"], {"name": "p", "item": {"class": "carbine"}, "length": 0.85})
        hold = jobs[v + "hold"]
        self.assertEqual(hold["inputs"]["glb"], {"ref": v + "item", "role": "glb"})
        self.assertEqual(hold["inputs"]["figure"], {"file": os.path.join(self.tmp, "figure.glb")})
        self.assertEqual(hold["args"], {"name": "p", "class": "carbine"})
        review, final = jobs["f/p/review"], jobs["f/p/final"]
        self.assertEqual(review["tool"], "item_sheet")
        self.assertEqual(review["from"], ["f/p/v%d/item" % s for s in seeds] + ["f/p/v%d/hold" % s for s in seeds])
        self.assertEqual([(x["job"], x["role"], x["hold"]) for x in review["variants"]],
                         [("f/p/v%d/item" % s, "glb", "f/p/v%d/hold" % s) for s in seeds])
        self.assertEqual(final["from"], ["f/p/v%d/item" % s for s in seeds])
        self.assertEqual((final["items_folder"], final["accept"]), ("items", {"mesh_flags": 0}))
        for name in ("item", "hold", "item_sheet"):
            self.assertIn(name, local_gpu.RUNNERS)

    def test_skip_settings_and_reserve(self):
        jobs = self.jobs({"skip": ["hold", "paint"], "inputs": {},
                          "stages": {"item": {"settings": {"front": "-z", "top": "+y", "snap": False}},
                                     "review": {"settings": {"side_width": 800}}},
                          "item": {"class": "carbine", "clips": ["idle_carbine"]}},
                         family={"reserve": 1})
        seeds = seeds_for("f/p", 3)
        self.assertNotIn("f/p/v%d/hold" % seeds[0], jobs)
        item = jobs["f/p/v%d/item" % seeds[0]]
        self.assertEqual(item["inputs"], {"mesh": {"ref": "f/p/v%d/mesh" % seeds[0], "role": "painted_mesh"}})
        self.assertEqual((item["args"]["front"], item["args"]["top"], item["args"]["snap"]), ("-z", "+y", False))
        self.assertEqual(jobs["f/p/review"]["args"]["side_width"], 800)
        self.assertEqual(jobs["f/p/review"]["from"], ["f/p/v%d/item" % s for s in seeds[:2]])
        self.assertTrue(jobs["f/p/v%d/item" % seeds[2]]["hold"])
        self.assertIn("f/p/final_reserve", jobs)
        self.assertEqual(jobs["f/p/final_reserve"]["items_folder"], "items")

    def test_a_changed_template_redoes_only_the_items(self):
        a = self.specs(order_of())
        b = self.specs(order_of({"item": {"class": "carbine", "sockets": {"top": None}}}))
        seeds = seeds_for("f/p", 2)
        changed = sorted(j for j in a if a[j] != b[j])
        self.assertEqual(changed, sorted(["f/p/v%d/%s" % (s, st) for s in seeds for st in ("item", "hold")] +
                                         ["f/p/review", "f/p/final"]))


class OrderCheck(Base):
    def test_valid(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems({"skip": ["hold"], "inputs": {}}), [])

    def test_problems(self):
        self.assertProblem(self.problems({"item": None}), "item must be an object")
        piece = order_of()["families"][0]["assets"][0]
        del piece["item"]
        self.assertProblem(check_order(self.load({"version": 1, "style": STYLE, "families": [
            {"id": "f", "recipe": "held_item", "assets": [piece]}]}), self.config), "item is required")
        self.assertProblem(self.problems({"item": {"class": "hand"}}), "give item.template")
        self.assertProblem(self.problems({"item": {"class": "rifle_x"}}), "lower-case word")
        self.assertProblem(self.problems({"params": {}}), "params.length or params.height is required")
        self.assertProblem(self.problems({"params": {"length": 1, "height": 1}}), "not both")
        self.assertProblem(self.problems({"params": {"length": 1, "front": "+x"}}), "param 'front' is not used")
        self.assertProblem(self.problems({"inputs": {"clips": "library.json"}}), "needs inputs.figure")
        self.assertProblem(self.problems({"skip": ["hold"]}), "input 'figure' is not used")
        stub_glb(os.path.join(self.tmp, "plain.glb"), skinned=False)
        self.assertProblem(self.problems({"inputs": {"figure": "plain.glb", "clips": "library.json"}}), "has no skin")
        self.assertProblem(self.problems({"item": {"class": "lamp", "template": "device"}}),
                           "no clip of the motion library holds class lamp")
        self.assertProblem(self.problems({"item": {"class": "carbine", "clips": ["run"]}}), "has no clip run")
        self.assertProblem(self.problems({"item": {"class": "carbine", "clips": ["idle_pistol"]}}),
                           "does not hold class carbine")
        self.assertProblem(self.problems({"id": "items"}), "cannot be named 'items'")
        self.assertProblem(self.problems({"stages": {"item": {"settings": {"front": "+w"}}}}), "must be one of +x")
        self.assertProblem(self.problems({"accept": {"stretch_p95": 1}}), "unknown accept key")
        self.assertProblem(self.problems({"skip": ["item"]}), "cannot be skipped")
        order = order_of()
        del order["style"]["view_held_item"]
        self.assertProblem(check_order(self.load(order), self.config), "style.view_held_item is required")
        bad = {"version": 1, "style": {"view_articulated": "x"}, "families": [
            {"id": "f", "recipe": "articulated", "assets": [{"id": "p", "brief": "x", "parts": [{"id": "a"}],
                                                             "item": {"class": "carbine"}}]}]}
        self.assertProblem(check_order(self.load(bad), self.config), "does not use item")
        config = dict(self.config, godot=None)
        self.assertProblem(check_order(self.load(order_of()), config), "godot")

    def test_sample_order(self):
        order = load_order(self.sample())
        self.assertEqual(check_order(order, self.config), [])
        env = check_environment(order, self.config, object_info())
        self.assertEqual((env.status, env.problems), (VERIFIED, []))
        self.assertIsNone(re.search(r"\b(no|not|without|never|avoid)\b", json.dumps(order["style"]), re.I))
        jobs = {j["id"]: j for j in expand(order)}
        self.assertNotIn("gear/tool_case/review_reserve", jobs)
        self.assertFalse([j for j in jobs if j.startswith("gear/tool_case/") and j.endswith("/hold")])
        self.assertEqual(jobs["firearms/pistol/v%d/item" % seeds_for("firearms/pistol", 1)[0]]["args"]["tolerance_cm"],
                         1.5)

    def test_check_and_estimate_from_the_command_line(self):
        fake = FakeComfy()
        try:
            fake.object_info = object_info()
            config = os.path.join(self.tmp, "forge.local.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({k: self.config[k] for k in ("blender_python", "godot")}, f)
            base = [sys.executable, "-m", "forge", "--order", self.sample(), "--out", os.path.join(self.tmp, "out"),
                    "--config", config, "--server", fake.url]
            res = subprocess.run(base + ["--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("order: valid", res.stdout)
            self.assertIn("environment: verified", res.stdout)
            res = subprocess.run(base + ["--estimate"], cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("total 82 jobs", res.stdout)
            self.assertFalse(any(r[1] == "POST" for r in fake.requests))
        finally:
            fake.close()


class SheetLayout(unittest.TestCase):
    MODEL = {"format": "forge-held-item-model", "hold_class": "carbine", "template": "carbine", "length_m": 0.9,
             "box_m": [[-0.27, -0.025, -0.05], [0.63, 0.025, 0.17]],
             "socket_records": {"grip": {"used": [0.0, 0.0, 0.0]}, "muzzle": {"used": [0.63, 0.0, 0.05]},
                                "guard": {"used": [0.29, 0.0, 0.06]}, "top": {"used": [0.13, 0.0, 0.16]},
                                "butt": {"used": [-0.27, 0.0, 0.07]}}}

    def variant(self, **kw):
        v = {"label": "v1", "failed": False, "glb": "a.glb", "model": copy.deepcopy(self.MODEL),
             "mesh_report": {"flags": [], "fit": {"socket_shift": 0.05, "turned_deg": 3.0, "loose_share": 0.0,
                                                  "excess": 0.53}},
             "verdict": {"class": "acceptable", "reasons": []},
             "hold": {"failed": False, "glb": "fig.glb", "model": {"height": 1.8, "animations": [
                 {"name": "idle_carbine", "holds": "carbine", "fps": 12, "pose_frame": 3},
                 {"name": "aim_carbine", "holds": "carbine_aim", "fps": 12}]},
                 "motion_report": {"flags": [{"clip": "aim_carbine", "measure": "penetration_cm", "value": 3.0,
                                              "threshold": 2.0}]}}}
        v.update(kw)
        return v

    def test_cells_marks_and_lines(self):
        lay = item_sheet_layout.layout([self.variant()], {"pitch": 30, "directions": 4, "figure_height": 90})
        kinds = [c["kind"] for c in lay["cells"]]
        self.assertEqual(kinds, ["small"] * 4 + ["side"] + ["hold"] * 8)
        side = next(c for c in lay["cells"] if c["kind"] == "side")
        self.assertEqual((side["yaw"], side["pitch"], side["rect"][2]), (0.0, 0.0, 640))
        # Every socket is marked where the orthographic side camera puts it.
        x0, y0, w, h = side["rect"]
        t = side["target"]
        for m in lay["marks"]:
            used = self.MODEL["socket_records"][m["label"]]["used"]
            g = item_sheet_layout.to_gltf(used)
            self.assertAlmostEqual(m["at"][0], x0 + w / 2.0 + (g[0] - t[0]) * side["ppm"], delta=1.0)
            self.assertAlmostEqual(m["at"][1], y0 + h / 2.0 - (g[1] - t[1]) * side["ppm"], delta=1.0)
            self.assertTrue(x0 <= m["at"][0] <= x0 + w and y0 <= m["at"][1] <= y0 + h, m)
        self.assertEqual(sorted(m["label"] for m in lay["marks"]), sorted(self.MODEL["socket_records"]))
        holds = [c for c in lay["cells"] if c["kind"] == "hold"]
        self.assertEqual(holds[0]["subject"], "figure")
        self.assertAlmostEqual(holds[0]["time"], 0.25)
        self.assertEqual(sorted(set(c["yaw"] for c in holds)), [0.0, 90.0, 180.0, 270.0])
        small = lay["cells"][0]
        self.assertAlmostEqual(small["ppm"], 90 / 1.8)  # the game's scale
        text = [x["text"] for x in lay["texts"]]
        self.assertIn("hold: aim_carbine penetration_cm 3.00 over 2.0", text)
        self.assertTrue(any(x.startswith("fit (ranking only): socket shift 0.050") for x in text))
        self.assertTrue(any(x.startswith("sockets (m, item space): butt (-0.270") for x in text))
        labels = [x for x in lay["texts"] if x["color"] == "socket"]
        for a in labels:  # socket labels do not cover each other
            for b in labels:
                if a is not b and a["at"][1] == b["at"][1]:
                    self.assertTrue(abs(a["at"][0] - b["at"][0]) > 10)
        self.assertEqual(lay["variants"][0]["figure"], "fig.glb")
        for c in lay["cells"]:
            self.assertLessEqual(c["rect"][0] + c["rect"][2], lay["size"][0])
            self.assertLessEqual(c["rect"][1] + c["rect"][3], lay["size"][1])

    def test_failures_and_skipped_hold(self):
        lay = item_sheet_layout.layout(
            [self.variant(hold=None), self.variant(label="v2", hold={"failed": True, "reason": "hold failed: x"}),
             {"label": "v3", "failed": True, "reason": "item failed: y"}],
            {"directions": 2}, banner="no variant passed: most often the unknown (2 of 3)")
        self.assertEqual(lay["texts"][0]["text"], "NO VARIANT PASSED: MOST OFTEN THE UNKNOWN (2 OF 3)")
        self.assertFalse([c for c in lay["cells"] if c["kind"] == "hold"])
        self.assertIn("hold: FAILED: hold failed: x", [x["text"] for x in lay["texts"]])
        self.assertEqual(len(lay["failures"]), 1)
        self.assertEqual(lay["variants"][2]["cells"][0]["kind"], "failure")


# --- a night with the fake server and stand-ins for the local tools

class FakeTools:
    def __init__(self, fail=(), flagged=(), fit=None, hold_fail=()):
        self.fail, self.flagged, self.hold_fail = set(fail), set(flagged), set(hold_fail)
        self.fit = dict(fit or {})
        self.calls = []

    def variant(self, out):
        return os.path.basename(out).split("_")[0]

    def write(self, path, data, editable=False):
        if editable and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("manual"):
                    return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def item(self, mesh, out, config, item, name="model", **kw):
        v = self.variant(out)
        self.calls.append(("item", v, item, kw))
        os.makedirs(out, exist_ok=True)
        if v in self.fail:
            raise local_gpu.ToolError("item failed (exit 1), see x")
        files = {r: os.path.join(out, n) for r, n in (
            ("glb", name + ".glb"), ("sockets", name + ".json"), ("model", "model.json"),
            ("mesh_report", "mesh_report.json"), ("frame", "frame.json"), ("socket_map", "sockets.json"))}
        manual = {}
        for key in ("frame", "socket_map"):  # a manual file changes the item, as the real tool does
            if os.path.exists(files[key]):
                with open(files[key], encoding="utf-8") as f:
                    doc = json.load(f)
                if doc.get("manual"):
                    manual[key] = doc
        with open(files["glb"], "wb") as f:
            f.write(b"glTF item " + v.encode() + json.dumps(manual, sort_keys=True).encode())
        self.write(files["sockets"], {"name": name, "class": item["class"], "sockets": {"grip": [0, 0, 0]},
                                      "variant": v})
        self.write(files["model"], {"format": "forge-held-item-model", "variant": v})
        self.write(files["mesh_report"], {"flags": ["socket guard is off the mesh: 9.0 cm"] if v in self.flagged else [],
                                          "fit": {"excess": self.fit.get(v, 1.0)}})
        self.write(files["frame"], {"manual": False}, editable=True)
        self.write(files["socket_map"], {"manual": False}, editable=True)
        return files

    def hold(self, glb, sockets, figure, clips, out, config, cls, **kw):
        v = self.variant(out)
        self.calls.append(("hold", v, cls, kw))
        os.makedirs(out, exist_ok=True)
        if v in self.hold_fail:
            raise local_gpu.ToolError("hold stopped: invalid bone map (see x)", retry=False)
        files = {r: os.path.join(out, n) for r, n in (
            ("glb", "figure.glb"), ("model", "model.json"), ("mesh_report", "mesh_report.json"),
            ("motion_report", "motion_report.json"), ("bone_map", "bone_map.json"), ("clips", "clips.json"),
            ("figure", "figure.json"))}
        for role, path in files.items():
            if role == "glb":
                with open(path, "wb") as f:
                    f.write(b"glTF figure")
            else:
                self.write(path, {"flags": [], "variant": v})
        return files

    def sheet(self, glbs, out, config, camera=None, labels=None, holds=None, verdicts=None, banner=None):
        self.calls.append(("sheet", list(glbs), list(labels), dict(camera), list(holds), verdicts, banner))
        os.makedirs(out, exist_ok=True)
        files = {"sheet": os.path.join(out, "sheet.png"), "layout": os.path.join(out, "sheet.png.json")}
        Image.new("RGBA", (8, 8)).save(files["sheet"])
        with open(files["layout"], "w") as f:
            json.dump({"variants": labels}, f)
        return files


class Night(Base):
    def setUp(self):
        Base.setUp(self)
        self.fake = FakeComfy()
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        self.fake.close()
        Base.tearDown(self)

    def run_night(self, tools, order):
        self.load(order)
        a = SimpleNamespace(order=self.order_path, order_base="", out=self.out, only="", retry_failed=False,
                            limit=6, one_pass=False, config="")
        f = Factory(a, self.fake.url)
        f.load()
        f.config = self.config
        f.pass_sleep, f.error_sleep = 0.02, 0.1
        f.gpu_processes = lambda: []
        with patch.object(local_gpu, "run_item", tools.item), patch.object(local_gpu, "run_hold", tools.hold), \
                patch.object(local_gpu, "run_item_sheet", tools.sheet), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(f.run(), 0)
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_held_item_night(self):
        v = ["v%d" % s for s in seeds_for("f/p", 4)]
        tools = FakeTools(fail=[v[0]], flagged=[v[1]], fit={v[2]: 0.8, v[3]: 0.3}, hold_fail=[v[3]])
        order = order_of(family={"variants": 4})
        m = self.run_night(tools, order)
        self.assertEqual((m["f/p/%s/item" % v[0]]["status"], m["f/p/%s/item" % v[0]]["tries"]), ("failed", 2))
        self.assertEqual((m["f/p/%s/hold" % v[3]]["status"], m["f/p/%s/hold" % v[3]]["tries"]), ("failed", 1))
        [sheet] = [c for c in tools.calls if c[0] == "sheet"]
        self.assertTrue(sheet[1][0].startswith("!item failed"))
        self.assertTrue(sheet[4][3].startswith("!hold failed"))  # the hold's failure, shown on the sheet
        self.assertTrue(sheet[4][2].endswith("figure.glb"))
        self.assertEqual([x["class"] for x in sheet[5]], ["rejected", "rejected", "acceptable", "acceptable"])
        call = [c for c in tools.calls if c[:2] == ("hold", v[2])][0]
        self.assertEqual(call[2], "carbine")
        with open(os.path.join(self.out, "f", "p", "pick_final.json"), encoding="utf-8") as fh:
            pick = json.load(fh)
        self.assertEqual((pick["outcome"], pick["variant"], pick["review"]), ("picked", v[3], "pending"))
        self.assertIn("best fit", pick["metric"])
        self.assertEqual(pick["candidates"][1]["responsible"], ["unknown"])
        items = os.path.join(self.out, "f", "items")
        self.assertEqual(sorted(os.listdir(items)), ["p.glb", "p.json"])
        with open(os.path.join(items, "p.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["variant"], v[3])
        self.assertIn("p.json", os.listdir(os.path.join(self.out, "f", "p", "final")))
        # A manual pick redoes only the copies (final/ and the family folder).
        with open(os.path.join(self.out, "f", "p", "pick_final.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(pick, chosen="f/p/%s/item" % v[2], manual=True), fh)
        tools.calls.clear()
        self.run_night(tools, order)
        self.assertEqual([c for c in tools.calls if c[0] != "sheet"], [])
        with open(os.path.join(items, "p.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["variant"], v[2])
        # A frame corrected by hand redoes only that variant's item and hold (and the sheet).
        with open(os.path.join(self.out, "f", "p", "%s_item" % v[2], "frame.json"), "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "front": "-x", "top": "+y"}, fh)
        tools.calls.clear()
        self.run_night(tools, order)
        self.assertEqual(sorted((c[0], c[1]) for c in tools.calls if c[0] != "sheet"), [("hold", v[2]), ("item", v[2])])
        # So does a sockets file corrected by hand; a proposal edited without "manual" does nothing.
        with open(os.path.join(self.out, "f", "p", "%s_item" % v[1], "sockets.json"), "w", encoding="utf-8") as fh:
            json.dump({"manual": False, "sockets": {"grip": [0.1, 0, 0]}}, fh)
        tools.calls.clear()
        self.run_night(tools, order)
        self.assertEqual([c for c in tools.calls if c[0] != "sheet"], [])
        with open(os.path.join(self.out, "f", "p", "%s_item" % v[1], "sockets.json"), "w", encoding="utf-8") as fh:
            json.dump({"manual": True, "sockets": {"grip": [0.0, 0, 0]}}, fh)
        tools.calls.clear()
        self.run_night(tools, order)
        self.assertEqual(sorted((c[0], c[1]) for c in tools.calls if c[0] != "sheet"), [("hold", v[1]), ("item", v[1])])

    def test_no_variant_passed_removes_the_family_copy(self):
        v = ["v%d" % s for s in seeds_for("f/p", 2)]
        tools = FakeTools()
        order = order_of({"skip": ["hold"], "inputs": {}})
        self.run_night(tools, order)
        items = os.path.join(self.out, "f", "items")
        self.assertEqual(sorted(os.listdir(items)), ["p.glb", "p.json"])
        tools.flagged = set(v)
        order["families"][0]["assets"][0]["item"] = {"class": "carbine", "sockets": {"top": None}}  # redo the items
        self.run_night(tools, order)
        with open(os.path.join(self.out, "f", "p", "pick_final.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["outcome"], "no_variant_passed")
        self.assertEqual(os.listdir(items), [])
        self.assertTrue([c for c in tools.calls if c[0] == "sheet"][-1][4] == [None, None])


if __name__ == "__main__":
    unittest.main()
