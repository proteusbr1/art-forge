"""Local steps of the factory (no GPU): seed picks, view sheets and the roll / unroll of
seamless materials. Mixed into Factory (factory_core.py)."""

import json
import os
import time

import numpy as np
from PIL import Image

from .factory_util import sha_file, slug, write_json
from .provenance import local_record
from .scoring import METRICS, cross_mask, load_rgba, save_rgb


class LocalSteps:
    def manual_choice(self, job):
        path = os.path.join(self.asset_dir(job), "pick_%s.json" % slug(job["id"]))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8-sig") as f:
                    d = json.load(f)
                if d.get("manual") and d.get("chosen") in job["from"]:
                    return d["chosen"]
            except Exception:
                return None
        return None

    def run_local(self, job):
        t0 = time.time()
        sp = self.spec(job)
        d = self.asset_dir(job)
        os.makedirs(d, exist_ok=True)
        files = {}
        rec = {"status": "done", "spec": sp, "kind": job["kind"]}
        if job["kind"] == "pick":
            inputs = self.pick(job, d, rec, files)
        elif job["kind"] == "sheet":
            inputs = [self.input_record(r) for r in job["inputs"]]
            self.sheet(job, d, files)
        elif job["kind"] == "roll":
            inputs = [self.input_record(job["input"])]
            self.roll(job, d, files)
        elif job["kind"] == "unroll":
            inputs = [self.input_record(job["rolled"]), self.input_record(job["painted"])]
            self.unroll(job, d, files)
        else:
            inputs = []
        rec["files"] = files
        rec["exec_seconds"] = round(time.time() - t0, 2)
        rec["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["provenance"] = local_record(job, inputs)
        self.manifest[job["id"]] = rec
        self.save_manifest()
        self.record(job, rec, rec["exec_seconds"])

    def pick(self, job, d, rec, files):
        scored = []
        inputs = []
        for c in job["from"]:
            if not self.done_ok(c):
                continue
            path = os.path.join(self.out, self.entry(c)["files"][job["role"]][0])
            inputs.append(self.input_record({"ref": c, "role": job["role"]}))
            fn = METRICS[job["metric"]]
            try:
                s, info = fn(path)
            except Exception as ex:
                s, info = -1.0, {"error": str(ex)}
            scored.append({"job": c, "score": round(s, 3), **info})
        scored.sort(key=lambda r: -r["score"])
        manual = self.manual_choice(job)
        chosen = manual or scored[0]["job"]
        pick_path = os.path.join(d, "pick_%s.json" % slug(job["id"]))
        write_json(pick_path, {"chosen": chosen, "manual": bool(manual),
                               "review": "pending" if not manual else "manual",
                               "metric": job["metric"], "candidates": scored,
                               "how_to_change": "set chosen to another candidate job id and manual to true; "
                                                "the factory redoes only what depends on this pick"})
        # A copy of the chosen picture next to the file, for a quick look.
        src = os.path.join(self.out, self.entry(chosen)["files"][job["role"]][0])
        dst = os.path.join(d, "%s_chosen%s" % (slug(job["id"]), os.path.splitext(src)[1]))
        with open(src, "rb") as f, open(dst + ".tmp", "wb") as g:
            g.write(f.read())
        os.replace(dst + ".tmp", dst)
        rec["chosen"] = chosen
        files["pick"] = [os.path.relpath(pick_path, self.out), sha_file(pick_path)]
        return inputs

    def sheet(self, job, d, files):
        ims = []
        for r in job["inputs"]:
            x = load_rgba(self.resolve(r)[0])
            rgb = x[..., :3] * x[..., 3:4] + (1 - x[..., 3:4])
            im = Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8))
            im = im.resize((int(im.width * 1024 / im.height), 1024), Image.LANCZOS)
            ims.append(im)
        sheet = Image.new("RGB", (sum(i.width for i in ims), 1024), (255, 255, 255))
        x0 = 0
        for im in ims:
            sheet.paste(im, (x0, 0))
            x0 += im.width
        p = os.path.join(d, "%s.png" % slug(job["id"]))
        sheet.save(p)
        files["rgb"] = [os.path.relpath(p, self.out), sha_file(p)]

    def roll(self, job, d, files):
        x = load_rgba(self.resolve(job["input"])[0])[..., :3]
        h, w = x.shape[:2]
        ry = 0 if job["axes"] == "x" else h // 2
        rolled = np.roll(x, (ry, w // 2), axis=(0, 1))
        mask = cross_mask(h, w, 0.16, 0.04, job["axes"])
        p1 = os.path.join(d, "%s_image.png" % slug(job["id"]))
        p2 = os.path.join(d, "%s_mask.png" % slug(job["id"]))
        save_rgb(p1, rolled)
        save_rgb(p2, np.repeat(mask[..., None], 3, -1))
        files["image"] = [os.path.relpath(p1, self.out), sha_file(p1)]
        files["mask"] = [os.path.relpath(p2, self.out), sha_file(p2)]

    def unroll(self, job, d, files):
        rolled = load_rgba(self.resolve(job["rolled"])[0])[..., :3]
        painted = load_rgba(self.resolve(job["painted"])[0])[..., :3]
        h, w = rolled.shape[:2]
        if painted.shape[:2] != (h, w):
            painted = np.asarray(Image.fromarray((painted * 255).astype(np.uint8)).resize((w, h), Image.LANCZOS)).astype(np.float32) / 255
        k = cross_mask(h, w, 0.14, 0.05, job["axes"])[..., None]
        tile = rolled * (1 - k) + painted * k
        ry = 0 if job["axes"] == "x" else h // 2
        tile = np.roll(tile, (-ry, -(w // 2)), axis=(0, 1))
        p = os.path.join(d, "%s.png" % job["asset"])
        save_rgb(p, tile)
        prev = np.tile(tile, (2, 2, 1))
        pv = os.path.join(d, "%s_tile2x2.png" % job["asset"])
        Image.fromarray(np.clip(prev * 255, 0, 255).astype(np.uint8)).resize((1536, 1536), Image.LANCZOS).save(pv)
        files["rgb"] = [os.path.relpath(p, self.out), sha_file(p)]
        files["preview"] = [os.path.relpath(pv, self.out), sha_file(pv)]
