"""The humanoid's motions list in jobs (design, section 13).

- Made clips: each text or bvh entry of a family's humanoid pieces is one job of the family,
  <family>/_motions/<name> (<name>_<hash> when entries of the same name differ), shared by
  every piece and variant that lists it: text runs HY-Motion Lite (a local GPU job,
  forge/motion_text.py), bvh reads the capture (a local step). Each writes clip.json (a
  motion library of that one clip, canonical joints at the entry's frames and fps) and
  report.json (the corrections and their thresholds, forge/motion_clips.py).
- The piece's library: <family>/<piece>/motions (a local step) puts every entry of the piece
  in one motion library, in the list's order: library clips (resampled, contacts, the
  corrections) and the made clips, named and holding as the entries say. The export takes it
  as its clips. A piece without motions keeps inputs.clips as it is.

Job arguments name no paths (a library file is an input "lib<i>", a capture the made job's
input), so moving the order and its files redoes nothing.
"""

import json
import os

from . import motion_clips as mc
from .factory_util import seeds_for, sha_file, sha_obj, slug, write_json

MOTIONS_ASSET = "_motions"


def _is_humanoid_with_export(fam, a):
    from . import stages
    return a.get("recipe", fam.get("recipe")) == "humanoid" and "export" in stages.chosen_options("humanoid", a)


def made_motion_ids(fam):
    """{(name, spec hash): (job id, entry)} of the text and bvh entries of a family's humanoid
    pieces (those with an export stage)."""
    specs = {}
    for a in fam.get("assets", []):
        if not isinstance(a, dict) or not _is_humanoid_with_export(fam, a):
            continue
        for m in a.get("motions") or []:
            if isinstance(m, dict) and m.get("source") in mc.MADE and isinstance(m.get("name"), str):
                specs.setdefault(m["name"], {}).setdefault(sha_obj(mc.made_spec(m)), m)
    out = {}
    for name, by_key in specs.items():
        for key, m in by_key.items():
            step = name if len(by_key) == 1 else "%s_%s" % (name, key[:8])
            out[(name, key)] = ("%s/%s/%s" % (fam["id"], MOTIONS_ASSET, step), m)
    return out


def made_motion_jobs(fam):
    """The family's made-clip jobs (text: local GPU, tool motion_text; bvh: local step)."""
    jobs = []
    for (name, _), (jid, m) in made_motion_ids(fam).items():
        args = {"name": name, "frames": m["frames"], "fps": float(m.get("fps", 24)), "loop": bool(m.get("loop", False))}
        if "contacts" in m:
            args["contacts"] = m["contacts"]
        if m["source"] == "text":
            seed = seeds_for("%s/%s/%s" % (fam["id"], MOTIONS_ASSET, name), 1)[0]
            args.update(prompt=m["prompt"], settings=mc.text_settings(m, seed))
            jobs.append({"id": jid, "kind": "local_gpu", "tool": "motion_text", "group": "local_gpu",
                         "stage": "motions", "args": args})
        else:
            args["settings"] = dict(m.get("settings") or {})
            jobs.append({"id": jid, "kind": "motion", "group": "local", "stage": "motions",
                         "inputs": {"capture": {"file": m["file"]}}, "args": args})
    return jobs


def piece_motions_job(fam, a, inputs):
    """The job that makes a humanoid piece's motion library from its motions list."""
    made = made_motion_ids(fam)
    ins, entries, libs = {}, [], {}
    if inputs.get("clips"):
        ins["default"] = {"file": inputs["clips"]}
    for m in a["motions"]:
        e = {k: v for k, v in m.items() if k not in ("file", "note")}
        if m["source"] == "library":
            lib = dict(m.get("library") or {})
            if "file" in lib:
                path = mc.resolve_library_file(lib["file"])
                key = libs.setdefault(path, "lib%d" % len(libs))
                ins.setdefault("libraries", {})[key] = {"file": path}
                lib["file"] = key
            e["library"] = lib
        else:
            jid, _ = made[(m["name"], sha_obj(mc.made_spec(m)))]
            ins.setdefault("made", {})[m["name"]] = {"ref": jid, "role": "clip"}
        entries.append(e)
    return {"id": "%s/%s/motions" % (fam["id"], a["id"]), "kind": "motions", "group": "local",
            "stage": "motions", "inputs": ins, "args": {"motions": entries}}


def code_fingerprint():
    return {"motion_clips.py": sha_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "motion_clips.py"))}


# ---- the local steps (mixed into the factory through forge/local_steps.py) -------------------

def _rel(factory, path):
    return [os.path.relpath(path, factory.out), sha_file(path)]


def run_bvh(factory, job, folder, files):
    """A bvh entry's clip: the capture's range in canonical joints, then make_clip."""
    args = job["args"]
    path, _ = factory.resolve(job["inputs"]["capture"])
    settings = args.get("settings") or {}
    pos, fps, up, joints = mc.bvh_canon(path, settings)
    entry = dict(args)
    r = settings.get("range")
    text = "capture %s%s" % (os.path.basename(path), " frames %d-%d" % tuple(r) if r else "")
    clip, report = mc.make_clip(pos, fps, up, entry, text)
    report["joints"] = joints
    report["settings"] = {"range": r, "up": settings.get("up", "y"), "feet": settings.get("feet", "auto")}
    os.makedirs(folder, exist_ok=True)
    write_json(os.path.join(folder, "clip.json"), mc.clip_document(args["name"], clip))
    write_json(os.path.join(folder, "report.json"), report)
    files["clip"] = _rel(factory, os.path.join(folder, "clip.json"))
    files["report"] = _rel(factory, os.path.join(folder, "report.json"))
    if not report["valid"]:
        factory.log("motion %s: %s" % (job["id"], "; ".join(report["flags"])))


def run_library(factory, job, folder, files):
    """A piece's motion library from its entries, in order."""
    ins = job.get("inputs") or {}

    def load(ref):
        with open(factory.resolve(ref)[0], encoding="utf-8-sig") as f:
            return json.load(f)
    default = load(ins["default"]) if "default" in ins else None
    libs = {k: load(r) for k, r in (ins.get("libraries") or {}).items()}
    names = {k: os.path.basename(factory.resolve(r)[0]) for k, r in (ins.get("libraries") or {}).items()}
    out = {"format": mc.LIBRARY_FORMAT, "version": 1, "canon": list(mc.CANON), "anims": {}, "holds": {}}
    reports = {}
    used = [default] if default is not None else []
    for e in job["args"]["motions"]:
        name = e["name"]
        if e["source"] == "library":
            key = (e.get("library") or {}).get("file")
            doc = libs[key] if key else default
            if doc is None:
                raise ValueError("motion %s: no library (inputs.clips) to take clip %s from" % (name, name))
            clip_name = (e.get("library") or {}).get("clip", name)
            if clip_name not in (doc.get("anims") or {}):
                raise ValueError("motion %s: the library %s has no clip %s" % (
                    name, names.get(key, "inputs.clips"), clip_name))
            clip, report = mc.library_entry_clip(doc, e, names.get(key, "inputs.clips"))
            if doc not in used:
                used.append(doc)
        else:
            made = load(ins["made"][name])
            clip = dict(next(iter(made["anims"].values())))
            mpath = factory.resolve(ins["made"][name])[0]
            rpath = os.path.join(os.path.dirname(mpath), "report.json")
            report = {}
            if os.path.isfile(rpath):
                with open(rpath, encoding="utf-8") as f:
                    report = json.load(f)
            report = {"made_by": factory.producer(ins["made"][name]), "flags": report.get("flags", []),
                      "valid": report.get("valid", True)}
            clip.pop("holds", None)
        if "holds" in e:
            clip["holds"] = e["holds"]
        if "kind" in e:
            clip["kind"] = e["kind"]
        out["anims"][name] = clip
        reports[name] = report
    for doc in used:  # hold placements: the default library's first, then the files' in order
        for k, v in (doc.get("holds") or {}).items():
            out["holds"].setdefault(k, v)
    if not out["holds"]:
        del out["holds"]
    os.makedirs(folder, exist_ok=True)
    write_json(os.path.join(folder, "library.json"), out)
    flags = ["%s: %s" % (n, f) for n, r in reports.items() for f in r.get("flags", [])]
    write_json(os.path.join(folder, "report.json"), {"format": "forge-motions-report", "version": 1,
                                                     "clips": reports, "flags": flags, "valid": not flags})
    files["library"] = _rel(factory, os.path.join(folder, "library.json"))
    files["report"] = _rel(factory, os.path.join(folder, "report.json"))
    if flags:
        factory.log("motions %s: %s" % (job["id"], "; ".join(flags[:6])))


def run_local(factory, job, d, files):
    folder = os.path.join(d, slug(job["id"]))
    if job["kind"] == "motion":
        run_bvh(factory, job, folder, files)
        return [factory.input_record(job["inputs"]["capture"])]
    run_library(factory, job, folder, files)
    ins = job.get("inputs") or {}
    refs = ([ins["default"]] if "default" in ins else []) + list((ins.get("libraries") or {}).values()) \
        + list((ins.get("made") or {}).values())
    return [factory.input_record(r) for r in refs]
