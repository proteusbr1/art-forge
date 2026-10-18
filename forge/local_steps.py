"""Local steps of the factory (no GPU): seed picks, view sheets, the roll / unroll of
seamless materials, the cutout of a figure and the final copy of a humanoid's chosen
variant. Mixed into Factory (factory_core.py)."""

import json
import os
import shutil
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
        elif job["kind"] == "cutout":
            inputs = [self.input_record(r) for r in job["inputs"].values()]
            self.cutout(job, d, files)
        elif job["kind"] == "final":
            inputs = self.final(job, d, rec, files)
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

    def cutout(self, job, d, files):
        """The figure on a transparent background: the picture with the BiRefNet foreground
        mask its workflow saved as the alpha (white = the figure), or, without a mask, a
        picture that already has one."""
        path, _ = self.resolve(job["inputs"]["image"])
        im = Image.open(path)
        if "mask" in job["inputs"]:
            mask = Image.open(self.resolve(job["inputs"]["mask"])[0]).convert("L")
            if mask.size != im.size:
                mask = mask.resize(im.size, Image.LANCZOS)
            out = im.convert("RGB")
            out.putalpha(mask)
        else:
            if "A" not in im.getbands() and "transparency" not in im.info:
                raise ValueError("cutout of %s: the picture has no alpha channel and no mask" % path)
            out = im.convert("RGBA")
            if out.getchannel("A").getextrema()[0] == 255:
                raise ValueError("cutout of %s: the picture is fully opaque" % path)
        p = os.path.join(d, "%s_rgba.png" % slug(job["id"]))
        out.save(p)
        files["rgba"] = [os.path.relpath(p, self.out), sha_file(p)]

    def variant_flags(self, jid):
        """(flags, rank) of a finished variant from its export's reports: flags as text lines
        (None when its last stage wrote no report) and its rank, (mesh flags, motion flags,
        total excess): the number of mesh flags (a defective GLB) first, then the number of
        motion flags, then their size, the sum over motion flags of how far each value is over
        its threshold (value / threshold - 1). Lower is better."""
        files = self.entry(jid).get("files", {})
        flags, seen = [], False
        mesh_n = motion_n = 0
        excess = 0.0
        for role in ("mesh_report", "motion_report"):
            if role not in files:
                continue
            seen = True
            try:
                with open(os.path.join(self.out, files[role][0]), encoding="utf-8") as f:
                    found = json.load(f).get("flags") or []
            except (OSError, ValueError) as ex:
                flags.append("%s unreadable: %s" % (role, ex))
                mesh_n += 1
                continue
            for x in found:
                flags.append("%s: %s" % (role, x if isinstance(x, str) else json.dumps(x, sort_keys=True)))
                if role == "mesh_report":
                    mesh_n += 1
                    continue
                motion_n += 1
                try:
                    value, limit = float(x["value"]), float(x["threshold"])
                    excess += (value / limit - 1.0) if limit > 0 else value
                except (TypeError, KeyError, ValueError):
                    excess += 1.0  # a flag without a measure (a missing clip) counts as one
        return (flags if seen else None), (mesh_n, motion_n, round(excess, 4))

    def final(self, job, d, rec, files):
        """The chosen variant of a humanoid piece copied to <piece>/final/. The pick file
        pick_final.json works as every pick: "manual": true with another candidate in
        "chosen" changes it. The automatic pick is provisional: of the variants that
        finished every stage, the one with the fewest mesh flags, then the fewest motion
        flags, then the smallest excess over the thresholds (the first in order on a tie);
        it is always marked pending review."""
        cands, inputs = [], []
        for v in job["variants"]:
            c = v["job"]
            done = self.done_ok(c)
            flags, rank = self.variant_flags(c) if done else (None, None)
            cands.append({"job": c, "variant": v["variant"], "complete": done,
                          "flags": flags, "status": self.entry(c).get("status") or "not run",
                          "rank": None if rank is None else {"mesh_flags": rank[0], "motion_flags": rank[1],
                                                              "excess": rank[2]}})
            if done:
                inputs += [self.input_record({"ref": c, "role": r}) for r in sorted(self.entry(c)["files"])]
        manual = self.manual_choice(job)
        if manual and not self.done_ok(manual):
            raise ValueError("the manual pick %s in pick_%s.json has not finished" % (manual, slug(job["id"])))
        finished = [x for x in cands if x["complete"]]
        best = min(finished, key=lambda x: (x["rank"]["mesh_flags"], x["rank"]["motion_flags"], x["rank"]["excess"]))
        chosen = manual or best["job"]
        if manual:
            why = "manual"
        elif not best["flags"]:
            why = "complete variant without mesh or motion flags (the first such)"
        else:
            why = ("complete variant with the fewest and smallest flags (every one has flags): %d mesh, "
                   "%d motion, excess %.2f" % (best["rank"]["mesh_flags"], best["rank"]["motion_flags"],
                                              best["rank"]["excess"]))
        dest = os.path.join(d, "final")
        if os.path.isdir(dest):
            shutil.rmtree(dest)  # the copy of an earlier pick
        os.makedirs(dest)
        for role, (rel, _) in sorted(self.entry(chosen)["files"].items()):
            src = os.path.join(self.out, rel)
            p = os.path.join(dest, os.path.basename(src))
            shutil.copyfile(src, p)
            files[role] = [os.path.relpath(p, self.out), sha_file(p)]
        pick_path = os.path.join(d, "pick_%s.json" % slug(job["id"]))
        write_json(pick_path, {"chosen": chosen, "variant": next(x["variant"] for x in cands if x["job"] == chosen),
                               "manual": bool(manual), "review": "manual" if manual else "pending",
                               "metric": why, "candidates": cands,
                               "how_to_change": "set chosen to another candidate job id and manual to true; "
                                                "the factory redoes only the copy to final/"})
        files["pick"] = [os.path.relpath(pick_path, self.out), sha_file(pick_path)]
        rec["chosen"] = chosen
        return inputs
