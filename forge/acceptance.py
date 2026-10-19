"""Acceptance criteria of a humanoid piece's variants, apart from their ranking.

An order (family or piece) may give "accept": hard limits per measure (ACCEPT). Every
variant of a decision (the review sheet, the final pick) is classified "acceptable" or
"rejected", with the limits it violates and the stage held responsible for each (a small,
conservative table: RESPONSIBLE, MESH_FLAG_STAGES; "unknown" rather than a guess). The
final job picks only among acceptable variants (their ranking orders them); with none it
writes the outcome "no_variant_passed".
"""

import json
import os
import re

# The exporter's default thresholds of the motion report (tools/blender/humanoid_export.py):
# the default limit of each motion measure unless the piece's export stage sets its own.
MOTION_DEFAULTS = {"foot_slide_cm": 2.0, "hand_socket_cm": 2.0, "reach_shortfall_cm": 1.0, "stretch_p95": 0.5,
                   "penetration_cm": 2.0}
# accept key -> (type, default, meaning). A motion measure's default None means: the motion
# report's threshold for it (the piece's export setting, else MOTION_DEFAULTS).
ACCEPT = {
    "mesh_flags": ("int", 0, "mesh flags a variant may have (mesh_report.json flags)"),
    "missing_clips": ("int", 0, "clips of the library the exported GLB may lack"),
    "foot_slide_cm": ("number", None, "largest foot slide in a declared stance, cm"),
    "hand_socket_cm": ("number", None, "largest distance from a wrist to its socket, cm"),
    "reach_shortfall_cm": ("number", None, "largest IK reach shortfall, cm"),
    "stretch_p95": ("number", None, "95th percentile of edge stretch, worst frame"),
    "penetration_cm": ("number", None, "deepest body vertex inside a held item's box, cm"),
    "bake_source_offset_m": ("number", 0.05, "how far the bake source's bounds are from the game mesh's after "
                                             "any fit, metres"),
}
CLASSES = ("acceptable", "rejected")
NO_VARIANT_PASSED = "no_variant_passed"
# Stages in the order of the humanoid chain, then the motion library; "unknown" last. Ties of
# the most frequent responsible stage go to the earliest.
STAGE_ORDER = ("reference", "pose", "cutout", "mesh", "paint", "rig", "export", "review", "library", "unknown")

# The stage held responsible for a mesh flag (mesh_report.json "flags"), by its text: the
# first matching row wins; no row: "unknown". "paint" is the stage that painted the mesh
# (the mesh stage itself when TRELLIS.2's own texture stays with paint skipped).
MESH_FLAG_STAGES = (
    (r"^material slot normal:", "export", "the exporter bakes and writes the normal map"),
    (r"^material slot (base_color|metallic_roughness|occlusion|emissive|opacity):", "paint",
     "the painted maps come from the paint stage"),
    (r"^material source:", "paint", "the material source is the painted mesh"),
    (r"no base-colour texture", "paint", "the painted mesh has no base colour"),
    (r"vertices have no weight", "rig", "the rig's skin weights"),
    (r"weights are not normalised", "rig", "the rig's skin weights"),
    (r"bake source does not line up", "mesh", "the raw mesh and the game mesh both come from the mesh stage"),
    (r"has no UV layer", "unknown", "the UVs may be lost by the paint or the rig stage"),
    (r"materials, 1 is expected", "unknown", "the materials may be split by the paint or the rig stage"),
)
# The stage held responsible for the other kinds of violation.
RESPONSIBLE = {
    "bone_map": "rig",            # an export stopped by an invalid bone map: the rig's skeleton
    "motion": "rig",              # a motion limit (retargeted onto the rig's skeleton and weights)
    "motion_every_variant": "library",  # the same clip fails on every variant: the clip itself
    "missing_clip": "export",     # a clip the exporter wrote that the GLB does not have
    "bake_source_offset_m": "mesh",
    "report_unreadable": "export",  # a report the exporter wrote cannot be read
    "not_finished": "unknown",    # a variant that stopped without a failed job
}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(where, accept, problems):
    """Problems of an order's "accept" object (unknown keys, values that are not limits)."""
    if not isinstance(accept, dict):
        problems.append("%s: accept must be an object {measure: limit}" % where)
        return
    for k, v in accept.items():
        if k not in ACCEPT:
            problems.append("%s: unknown accept key %r (known: %s)" % (where, k, ", ".join(ACCEPT)))
        elif ACCEPT[k][0] == "int" and not (is_int(v) and v >= 0):
            problems.append("%s: accept %s must be a whole number of at least 0, not %r" % (where, k, v))
        elif ACCEPT[k][0] == "number" and not (is_number(v) and v >= 0):
            problems.append("%s: accept %s must be a number of at least 0, not %r" % (where, k, v))


def limits(accept=None, export_settings=None):
    """The full limits of a piece: its "accept", else the defaults (a motion measure's default
    is the motion report's threshold: the export stage's setting, else MOTION_DEFAULTS)."""
    export_settings = export_settings or {}
    accept = accept if isinstance(accept, dict) else {}
    out = {}
    for k, (_, default, _) in ACCEPT.items():
        if default is None:
            default = export_settings.get(k, MOTION_DEFAULTS[k])
        out[k] = (accept or {}).get(k, default)
    return out


def mesh_flag_stage(flag, paint_stage="paint"):
    """The stage responsible for one mesh flag (MESH_FLAG_STAGES)."""
    for pattern, stage, _ in MESH_FLAG_STAGES:
        if re.search(pattern, flag):
            return paint_stage if stage == "paint" else stage
    return "unknown"


def failure_stage(stage, error):
    """The stage responsible for a failed job of `stage`: an export stopped by an invalid
    bone map is the rig's; any other failure is its own stage's."""
    if stage == "export" and "invalid bone map" in (error or ""):
        return RESPONSIBLE["bone_map"]
    return stage or RESPONSIBLE["not_finished"]


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return "unreadable"


def variant_records(factory, job):
    """Per variant of a review or final job: what classify needs (its failure, or its
    reports read from the files of its last stage)."""
    out = []
    for v in job["variants"]:
        rec = {"job": v["job"], "variant": v["variant"], "complete": factory.done_ok(v["job"]),
               "reserve": bool(v.get("reserve")), "failure": None, "mesh_report": None, "motion_report": None}
        if rec["complete"]:
            files = factory.entry(v["job"]).get("files", {})
            for role in ("mesh_report", "motion_report"):
                if role in files:
                    rec[role] = _read(os.path.join(factory.out, files[role][0]))
        else:
            rec["failure"] = first_failure(factory, v)
        out.append(rec)
    return out


def first_failure(factory, variant):
    """{"stage", "error"} of the first job of a variant's chain that failed (or whose
    specification could not be worked out), or a "not finished" record."""
    for jid in variant["chain"]:
        why = getattr(factory, "spec_errors", {}).get(jid)
        e = factory.entry(jid)
        if why or e.get("status") == "failed":
            stage = factory.by_id[jid].get("stage") or jid.rsplit("/", 1)[-1]
            err = why or (e.get("error") or "").strip()
            return {"stage": stage, "error": err}
    return {"stage": None, "error": "not finished"}


def _violation(measure, stage, text, value=None, limit=None, clip=None):
    return {"measure": measure, "stage": stage, "text": text, "value": value, "limit": limit, "clip": clip}


def classify(records, lim, paint_stage="paint"):
    """(per-variant verdicts, summary) of a decision. A verdict: {"class", "violations",
    "reasons" (texts), "stages" (responsible, in chain order)}. The summary: per class the
    variants, the responsible stages counted once per rejected variant, and the most frequent."""
    verdicts = []
    for r in records:
        vs = []
        if not r["complete"]:
            f = r["failure"] or {"stage": None, "error": "not finished"}
            stage = failure_stage(f["stage"], f["error"])
            first = (f["error"] or "").splitlines()[0][:160] if f["error"] else ""
            vs.append(_violation("failed", stage, "%s failed%s" % (f["stage"] or "variant", ": " + first if first else "")))
        else:
            mesh = r.get("mesh_report")
            if mesh == "unreadable":
                vs.append(_violation("mesh_report", RESPONSIBLE["report_unreadable"], "mesh report unreadable"))
            elif isinstance(mesh, dict):
                flags = [f if isinstance(f, str) else json.dumps(f, sort_keys=True) for f in mesh.get("flags") or []]
                if len(flags) > lim["mesh_flags"]:
                    for f in flags:
                        vs.append(_violation("mesh_flags", mesh_flag_stage(f, paint_stage), "mesh: " + f,
                                             len(flags), lim["mesh_flags"]))
                off = mesh.get("bake_source_offset_m")
                if is_number(off) and off > lim["bake_source_offset_m"]:
                    vs.append(_violation("bake_source_offset_m", RESPONSIBLE["bake_source_offset_m"],
                                         "bake source %.3f m off, over %s" % (off, lim["bake_source_offset_m"]),
                                         off, lim["bake_source_offset_m"]))
            motion = r.get("motion_report")
            if motion == "unreadable":
                vs.append(_violation("motion_report", RESPONSIBLE["report_unreadable"], "motion report unreadable"))
            elif isinstance(motion, dict):
                missing = sorted(c for c, e in (motion.get("clips") or {}).items() if not e.get("found", True))
                missing += sorted(set(f.get("clip") for f in motion.get("flags") or []
                                      if isinstance(f, dict) and f.get("measure") == "missing") - set(missing))
                if len(missing) > lim["missing_clips"]:
                    for c in missing:
                        vs.append(_violation("missing_clips", RESPONSIBLE["missing_clip"], "clip %s missing" % c,
                                             len(missing), lim["missing_clips"], c))
                over = []
                for clip, e in sorted(_clip_values(motion).items()):
                    for k in MOTION_DEFAULTS:
                        v = e.get(k)
                        if is_number(v) and v > lim[k]:
                            over.append(_violation(k, RESPONSIBLE["motion"], "%s %s %.3g over %s" % (clip, k, v, lim[k]),
                                                   v, lim[k], clip))
                # The worst first (the furthest over its limit, relatively): a label shows the first.
                over.sort(key=lambda x: -(x["value"] / x["limit"]) if x["limit"] > 0 else -float("inf"))
                vs += over
        verdicts.append({"job": r["job"], "variant": r["variant"], "violations": vs})
    # The same clip over a limit on every variant that has a motion report (at least two):
    # the library's clip, not the variants' rigs.
    measured = [v for v, r in zip(verdicts, records) if isinstance(r.get("motion_report"), dict)]
    if len(measured) >= 2:
        failing = None
        for v in measured:
            clips = set(x["clip"] for x in v["violations"] if x["measure"] in MOTION_DEFAULTS)
            failing = clips if failing is None else failing & clips
        for v in measured:
            for x in v["violations"]:
                if x["measure"] in MOTION_DEFAULTS and x["clip"] in failing:
                    x["stage"] = RESPONSIBLE["motion_every_variant"]
    counts = {}
    for v in verdicts:
        v["class"] = "rejected" if v["violations"] else "acceptable"
        v["stages"] = sorted(set(x["stage"] for x in v["violations"]), key=_stage_key)
        v["reasons"] = [x["text"] for x in v["violations"]]
        for s in v["stages"]:
            counts[s] = counts.get(s, 0) + 1
    top = sorted(counts, key=lambda s: (-counts[s], _stage_key(s)))
    summary = {"acceptable": [v["job"] for v in verdicts if v["class"] == "acceptable"],
               "rejected": [v["job"] for v in verdicts if v["class"] == "rejected"],
               "responsible_counts": {s: counts[s] for s in top},
               "responsible_stage": top[0] if top else None}
    return verdicts, summary


def _clip_values(motion):
    """{clip: {measure: value}} of a motion report: its clips' measured values, and the
    values of its flags (a report may list only flags)."""
    out = {c: dict(e) for c, e in (motion.get("clips") or {}).items() if isinstance(e, dict)}
    for f in motion.get("flags") or []:
        if isinstance(f, dict) and f.get("measure") in MOTION_DEFAULTS and f.get("clip") is not None:
            e = out.setdefault(f["clip"], {})
            if not is_number(e.get(f["measure"])):
                e[f["measure"]] = f.get("value")
    return out


def _stage_key(s):
    return STAGE_ORDER.index(s) if s in STAGE_ORDER else len(STAGE_ORDER)


def first_reasons(verdict, n=2):
    """The first reasons of a rejected verdict, for a label."""
    return verdict["reasons"][:n] + (["+%d more" % (len(verdict["reasons"]) - n)] if len(verdict["reasons"]) > n else [])
