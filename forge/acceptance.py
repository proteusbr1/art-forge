"""Acceptance criteria of a humanoid piece's variants, apart from their ranking.

An order (family or piece) may give "accept": hard limits per measure (ACCEPT). Every
variant of a decision (the review sheet, the final pick) is classified "acceptable" or
"rejected", with the limits it violates and the stage held responsible for each (a small,
conservative table: RESPONSIBLE, MESH_FLAG_STAGES; "unknown" rather than a guess). The
final job picks only among acceptable variants (their ranking orders them); with none it
writes the outcome "no_variant_passed".
"""

import json
import math
import os
import re

# The motion measures of the motion report (tools/blender/humanoid_export.py) and its
# default thresholds (which flag, not gate).
MOTION_DEFAULTS = {"foot_slide_cm": 2.0, "hand_socket_cm": 2.0, "reach_shortfall_cm": 1.0, "stretch_p95": 0.5,
                   "penetration_cm": 2.0}
# accept key -> (type, default, meaning). By default only structural defects reject (a failed
# job, any mesh flag, missing clips, an unreadable report, a non-finite motion value): a
# default None means "not gated" until the order sets that limit. Motion values still rank
# the acceptable variants and show on the sheet.
ACCEPT = {
    "mesh_flags": ("int", 0, "mesh flags a variant may have (mesh_report.json flags)"),
    "missing_clips": ("int", 0, "clips of the library the exported GLB may lack"),
    "foot_slide_cm": ("number", None, "largest foot slide in a declared stance, cm"),
    "hand_socket_cm": ("number", None, "largest distance from a wrist to its socket, cm"),
    "reach_shortfall_cm": ("number", None, "largest IK reach shortfall, cm"),
    "stretch_p95": ("number", None, "95th percentile of edge stretch, worst frame"),
    "penetration_cm": ("number", None, "deepest body vertex inside a held item's box, cm"),
    "bake_source_offset_m": ("number", None, "how far the bake source's bounds are from the game mesh's after "
                                             "any fit, metres (a source that does not line up is already a "
                                             "mesh flag)"),
}
# Measures only other recipes have: the creature's motion report measures how deep its body
# goes below the ground. Kept apart from ACCEPT so a humanoid's limits stay as they were.
EXTRA_ACCEPT = {
    "below_ground_cm": ("number", None, "deepest vertex below the ground over the clips, cm"),
}
# Measures of the conform report of a humanoid that wears an approved body (inputs.body):
# its own decision recipe, "humanoid_body", so a humanoid without a body keeps its limits.
CONFORM_ACCEPT = {
    "conform_flags": ("int", 0, "conform flags a variant may have (conform_report.json flags)"),
    "conform_stretch_p95": ("number", None, "95th percentile of the edges' stretch by the conform"),
    "body_outside_cm": ("number", None, "99th percentile of how far the approved body's surface lies outside "
                                        "the conformed outfit, cm"),
}
ALL_ACCEPT = dict(ACCEPT, **EXTRA_ACCEPT)
ALL_ACCEPT.update(CONFORM_ACCEPT)
# The limits each recipe with a decision has, in this order.
RECIPE_ACCEPT = {
    "humanoid": tuple(ACCEPT),
    "humanoid_body": tuple(ACCEPT) + tuple(CONFORM_ACCEPT),
    "creature": ("mesh_flags", "missing_clips", "foot_slide_cm", "reach_shortfall_cm", "stretch_p95",
                 "below_ground_cm"),
    "articulated": ("mesh_flags",),
    "held_item": ("mesh_flags",),
    "iso_tile": ("mesh_flags",),
}
# Every motion measure a report may hold (the humanoid's, then the creature's).
MOTION_MEASURES = dict(MOTION_DEFAULTS, below_ground_cm=3.0)
CLASSES = ("acceptable", "rejected")
NO_VARIANT_PASSED = "no_variant_passed"
# Stages in the order of the humanoid chain, then the motion library; "unknown" last. Ties of
# the most frequent responsible stage go to the earliest.
STAGE_ORDER = ("reference", "pose", "cutout", "mesh", "paint", "rig", "export", "parts", "item", "hold", "guide",
               "picture", "edges", "form", "sprite", "review", "library", "unknown")

# The stage held responsible for a mesh flag (mesh_report.json "flags"), by its text: the
# first matching row wins; no row: "unknown". "paint" is the stage that painted the mesh
# (the mesh stage itself when TRELLIS.2's own texture stays with paint skipped).
MESH_FLAG_STAGES = (
    (r"^material slot [a-z_]+: .*could not be carried", "paint",
     "a painted map (the paint stage's or the painted mesh's) the exporter cannot read"),
    (r"^material slot [a-z_]+: .*(is missing from the exported GLB|which is lossy)", "export",
     "the exporter writes the GLB's images"),
    (r"^material slot normal:", "export", "the exporter bakes and writes the normal map"),
    (r"^material slot ", "unknown", "any other material slot flag"),
    (r"^material source:", "paint", "the material source is the painted mesh"),
    (r"no base-colour texture", "paint", "the painted mesh has no base colour"),
    (r"vertices have no weight", "rig", "the rig's skin weights"),
    (r"weights are not normalised", "rig", "the rig's skin weights"),
    (r"bake source does not line up", "mesh", "the raw mesh and the game mesh both come from the mesh stage"),
    (r"has no UV layer", "unknown", "the UVs may be lost by the paint or the rig stage"),
    (r"materials, 1 is expected", "unknown", "the materials may be split by the paint or the rig stage"),
    (r"^part \S+ (is empty|is tiny)", "unknown", "the order's region or the generated layout may be at fault"),
    (r"^part \S+ ", "parts", "the parts stage cuts the parts and closes their cuts"),
    (r"^socket \S+ is (missing|off the mesh)", "unknown",
     "the generated shape or the class template (or a manual sockets file) may be at fault"),
    (r"^frame: ", "unknown", "the concept may not be a side view, or the mesh generator turned the item"),
    (r"^facing: ", "unknown", "the generated figure may stand twisted (mesh) or its predicted joints may (rig)"),
    # An iso_tile sprite's report (forge/tile_steps.py): the picture draws the shape and the light.
    (r"^footprint: ", "picture", "the picture (or the render of its mesh) gives the sprite's shape"),
    (r"^seam: ", "edges", "the edges stage joins the border to the seam group's material"),
    (r"^shading: ", "picture", "the picture painted light into the albedo"),
    (r"^render: ", "form", "the mesh's shape does not fit the original's silhouette"),
)
# The stage held responsible for a conform flag (conform_report.json "flags"), by its text:
# the conform follows the rig's joints and weights; the soles are the outfit's own shape.
CONFORM_FLAG_STAGES = (
    (r"^(joints|scale|outside|tear|bone map):", "rig", "the conform follows the predicted joints and weights"),
    (r"^floor:", "unknown", "the outfit's soles (the mesh) or its predicted ankles (the rig)"),
)
# The stage held responsible for the other kinds of violation.
RESPONSIBLE = {
    "bone_map": "rig",            # an export stopped by an invalid bone map: the rig's skeleton
    "motion": "rig",              # a motion limit (retargeted onto the rig's skeleton and weights)
    "motion_every_variant": "library",  # the same clip fails on every variant: the clip itself
    "missing_clip": "export",     # a clip the exporter wrote that the GLB does not have
    "bake_source_offset_m": "mesh",
    "report_unreadable": "export",  # a report the exporter wrote cannot be read
    "motion_not_finite": "unknown",  # a NaN or infinite motion value (degenerate skin or retarget)
    "not_finished": "unknown",    # a variant that stopped without a failed job
    "conform_stretch_p95": "rig",  # the conform moves the mesh by the rig's weights
    "body_outside_cm": "mesh",    # the outfit is thinner than the body: its generated shape
}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(where, accept, problems, recipe="humanoid"):
    """Problems of an order's "accept" object (unknown keys, values that are not limits)."""
    if not isinstance(accept, dict):
        problems.append("%s: accept must be an object {measure: limit}" % where)
        return
    known = RECIPE_ACCEPT.get(recipe, tuple(ACCEPT))
    for k, v in accept.items():
        if k not in known:
            problems.append("%s: unknown accept key %r (known: %s)" % (where, k, ", ".join(known)))
        elif ALL_ACCEPT[k][0] == "int" and not (is_int(v) and v >= 0):
            problems.append("%s: accept %s must be a whole number of at least 0, not %r" % (where, k, v))
        elif ALL_ACCEPT[k][0] == "number" and not (is_number(v) and v >= 0):
            problems.append("%s: accept %s must be a number of at least 0, not %r" % (where, k, v))


def decision_recipe(recipe, piece):
    """The recipe whose limits a piece's decision has: "humanoid_body" for a humanoid that
    wears an approved body (inputs.body), else the recipe."""
    inputs = piece.get("inputs") if isinstance(piece, dict) else None
    if recipe == "humanoid" and isinstance(inputs, dict) and inputs.get("body"):
        return "humanoid_body"
    return recipe


def limits(accept=None, recipe="humanoid"):
    """The full limits of a piece: its "accept" over the defaults (None: not gated)."""
    accept = accept if isinstance(accept, dict) else {}
    return {k: accept.get(k, ALL_ACCEPT[k][1]) for k in RECIPE_ACCEPT.get(recipe, tuple(ACCEPT))}


def motion_gated(lim):
    """The motion measures a set of limits gates (the others only rank and show)."""
    return [k for k in MOTION_MEASURES if lim.get(k) is not None]


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
               "reserve": bool(v.get("reserve")), "failure": None, "mesh_report": None, "motion_report": None,
               "conform_report": None}
        if rec["complete"]:
            files = factory.entry(v["job"]).get("files", {})
            for role in ("mesh_report", "motion_report", "conform_report"):
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
                if len(flags) > lim.get("mesh_flags", 0):
                    for f in flags:
                        vs.append(_violation("mesh_flags", mesh_flag_stage(f, paint_stage), "mesh: " + f,
                                             len(flags), lim.get("mesh_flags", 0)))
                off = mesh.get("bake_source_offset_m")
                if lim.get("bake_source_offset_m") is not None and is_number(off) and off > lim["bake_source_offset_m"]:
                    vs.append(_violation("bake_source_offset_m", RESPONSIBLE["bake_source_offset_m"],
                                         "bake source %.3f m off, over %s" % (off, lim["bake_source_offset_m"]),
                                         off, lim["bake_source_offset_m"]))
            vs += _conform_violations(r.get("conform_report"), lim)
            motion = r.get("motion_report")
            if motion == "unreadable":
                vs.append(_violation("motion_report", RESPONSIBLE["report_unreadable"], "motion report unreadable"))
            elif isinstance(motion, dict):
                missing = sorted(c for c, e in (motion.get("clips") or {}).items() if not e.get("found", True))
                missing += sorted(set(f.get("clip") for f in motion.get("flags") or []
                                      if isinstance(f, dict) and f.get("measure") == "missing") - set(missing))
                if len(missing) > lim.get("missing_clips", 0):
                    for c in missing:
                        vs.append(_violation("missing_clips", RESPONSIBLE["missing_clip"], "clip %s missing" % c,
                                             len(missing), lim.get("missing_clips", 0), c))
                over = []
                for clip, e in sorted(_clip_values(motion).items()):
                    for k in MOTION_MEASURES:
                        v = e.get(k)
                        if is_number(v) and not math.isfinite(v):
                            vs.append(_violation(k, RESPONSIBLE["motion_not_finite"], "%s %s is %s" % (clip, k, v),
                                                 v, lim.get(k), clip))
                        elif is_number(v) and lim.get(k) is not None and v > lim[k]:
                            over.append(_violation(k, RESPONSIBLE["motion"], "%s %s %.3g over %s" % (clip, k, v, lim[k]),
                                                   v, lim[k], clip))
                # The worst first (the furthest over its limit, relatively): a label shows the first.
                over.sort(key=lambda x: -(x["value"] / x["limit"]) if x["limit"] > 0 else -math.inf)
                vs += over
        verdicts.append({"job": r["job"], "variant": r["variant"], "violations": vs})
    # The same clip over a limit on every variant that has a motion report (at least two):
    # the library's clip, not the variants' rigs.
    measured = [v for v, r in zip(verdicts, records) if isinstance(r.get("motion_report"), dict)]
    if len(measured) >= 2:
        failing = None
        for v in measured:
            clips = set(x["clip"] for x in v["violations"] if _gated_motion(x))
            failing = clips if failing is None else failing & clips
        for v in measured:
            for x in v["violations"]:
                if _gated_motion(x) and x["clip"] in failing:
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
               "responsible_stage": top[0] if top else None, "motion_gated": motion_gated(lim)}
    return verdicts, summary


def conform_flag_stage(flag):
    """The stage responsible for one conform flag (CONFORM_FLAG_STAGES)."""
    for pattern, stage, _ in CONFORM_FLAG_STAGES:
        if re.search(pattern, flag):
            return stage
    return "unknown"


def _conform_violations(conform, lim):
    """The violations of a conform report (a variant that wears an approved body)."""
    if conform is None:
        return []
    if conform == "unreadable" or not isinstance(conform, dict):
        return [_violation("conform_report", RESPONSIBLE["report_unreadable"], "conform report unreadable")]
    vs = []
    flags = [f if isinstance(f, str) else json.dumps(f, sort_keys=True) for f in conform.get("flags") or []]
    if len(flags) > lim.get("conform_flags", 0):
        for f in flags:
            vs.append(_violation("conform_flags", conform_flag_stage(f), "conform: " + f, len(flags),
                                 lim.get("conform_flags", 0)))
    measures = (("conform_stretch_p95", conform.get("stretch_p95"), "conform stretch p95 %.3g over %s"),
                ("body_outside_cm", (conform.get("body_outside") or {}).get("p99_cm"),
                 "body %.3g cm outside the outfit, over %s"))
    for key, value, text in measures:
        if is_number(value) and not math.isfinite(value):
            vs.append(_violation(key, RESPONSIBLE["motion_not_finite"], "%s is %s" % (key, value), value, lim.get(key)))
        elif is_number(value) and lim.get(key) is not None and value > lim[key]:
            vs.append(_violation(key, RESPONSIBLE[key], text % (value, lim[key]), value, lim[key]))
    return vs


def conform_line(conform):
    """One line of a conform report's measures for the review sheet, or None."""
    if not isinstance(conform, dict) or not conform.get("valid"):
        return None
    out = (conform.get("body_outside") or {}).get("p99_cm")
    return "conform: joints %.1f cm, stretch p95 %.2f%s, %d flags" % (
        conform.get("joint_error_cm") or 0.0, conform.get("stretch_p95") or 0.0,
        "" if out is None else ", body out %.1f cm" % out, len(conform.get("flags") or []))


def _gated_motion(x):
    """A violation of a motion limit the order set (not a non-finite value)."""
    return x["measure"] in MOTION_MEASURES and x["stage"] == RESPONSIBLE["motion"] and x["clip"] is not None


def _clip_values(motion):
    """{clip: {measure: value}} of a motion report: its clips' measured values, and the
    values of its flags (a report may list only flags)."""
    out = {c: dict(e) for c, e in (motion.get("clips") or {}).items() if isinstance(e, dict)}
    for f in motion.get("flags") or []:
        if isinstance(f, dict) and f.get("measure") in MOTION_MEASURES and f.get("clip") is not None:
            e = out.setdefault(f["clip"], {})
            if not is_number(e.get(f["measure"])):
                e[f["measure"]] = f.get("value")
    return out


def _stage_key(s):
    return STAGE_ORDER.index(s) if s in STAGE_ORDER else len(STAGE_ORDER)


def first_reasons(verdict, n=2):
    """The first reasons of a rejected verdict, for a label."""
    return verdict["reasons"][:n] + (["+%d more" % (len(verdict["reasons"]) - n)] if len(verdict["reasons"]) > n else [])
