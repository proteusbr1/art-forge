"""The humanoid's motions list (section 13 of the design): every source ends in the canonical
joints of the motion library (docs/conventions.md), resampled to the entry's frames and fps,
with its contacts (given, or detected for planted feet) and the stage's corrections, each
with its threshold recorded and checked: the loop window and the closure of a loop, the hip
height on planted feet and the cleanup of foot contacts.

Sources made here: library (a clip of a motion library: the order's inputs.clips, a file, or
the factory's starter library "@starter"), bvh (a capture file, by joint names) and the
canonical part of text (HY-Motion's joints, forge/motion_text.py runs the model). No bpy;
NumPy only. Positions are [side, forward, up] in body heights (body coordinates), the ground
at up = 0.
"""

import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = ["pelvis", "l_hip", "r_hip", "spine", "chest", "neck", "head", "head_top",
         "l_collar", "l_shoulder", "l_elbow", "l_wrist", "l_hand",
         "r_collar", "r_shoulder", "r_elbow", "r_wrist", "r_hand",
         "l_knee", "l_ankle", "l_toe", "r_knee", "r_ankle", "r_toe"]
IDX = {n: i for i, n in enumerate(CANON)}
LIBRARY_FORMAT = "forge-motion-library"
REPORT_FORMAT = "forge-motion-clip-report"
FEET_MODES = ("clip", "walk", "lie", "free")
SIDES = {"left": "l", "right": "r"}
# The motion library the factory ships (data/motions/README.md); a motions entry names it
# as its library file "@starter".
STARTER = "@starter"
STARTER_PATH = os.path.join(ROOT, "data", "motions", "starter_humanoid.json")

# The stage's thresholds (body heights unless named otherwise; 0.01 body heights is 1.8 cm
# on a 1.8 m figure). Recorded in every clip report.
THRESHOLDS = {
    "contact_height": 0.02,    # a planted ankle is at most this above the foot's lowest height
    "contact_speed": 0.15,     # and moves at most this many body heights a second (ground frame)
    "contact_min_frames": 3,   # shorter planted runs are dropped, gaps of one frame filled
    "foot_slide": 0.004,       # largest move of a planted ankle within a run, after the cleanup
    "foot_slide_fix": 0.002,   # the cleanup acts when a run moves more than this
    "ground": 0.004,           # planted ankles off their standing height; joints below the ground
    "seam_factor": 2.0,        # a loop's seam jump may be this times its median frame step
    "seam_min": 0.004,         # or at least this
    "window_min": 0.8,         # a loop's window: 0.8 to 1.25 times the wanted duration
    "window_max": 1.25,
}

# ---- motions entries (the order) -------------------------------------------------------------

# The keys of an entry: the creature's (tools/blender/creature_motion.py MOTION_KEYS).
MOTION_KEYS = ("name", "source", "kind", "settings", "library", "prompt", "file", "frames", "fps", "loop", "holds",
               "contacts", "note")
SOURCES = ("library", "text", "bvh")
SOURCE_KEYS = {"library": ("library",), "text": ("prompt",), "bvh": ("file",)}
MADE = ("text", "bvh")  # sources made once per family by a job of their own
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
# Settings per source: name -> (type, default, meaning). Types: int:<lo>:<hi>, number:<lo>:<hi>,
# choice:<a>|<b>, range (two whole numbers), joints ({canonical joint: capture joint name}).
SETTINGS = {
    "library": {},
    "text": {
        "steps": ("int:1:200", 50, "HY-Motion's sampling steps (50: the Lite model's own setting)"),
        "seed": ("int:0:2147483647", None, "the seed (default: derived from family/name)"),
        "guidance": ("number:0:20", 5.0, "text guidance scale"),
        "duration": ("number:0.7:12", None, "seconds generated (default: the clip's for a one-shot; "
                                            "twice it plus a second, at least 3, for a loop to find a cycle in)"),
        "feet": ("choice:auto|clip|walk|lie|free", "auto", "how the exporter stands the feet"),
    },
    "bvh": {
        "range": ("range", None, "first and last frame of the capture taken (default: all)"),
        "up": ("choice:y|z", "y", "the capture's up axis"),
        "joints": ("joints", None, "capture joint names for canonical joints the name table misses"),
        "feet": ("choice:auto|clip|walk|lie|free", "auto", "how the exporter stands the feet"),
    },
}
CONTACT_KEYS = ("left", "right")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _setting_problem(kind, v):
    """None when v fits the setting type, else what it must be."""
    if kind.startswith("int:"):
        lo, hi = (int(x) for x in kind.split(":")[1:])
        return None if _int(v) and lo <= v <= hi else "a whole number from %d to %d" % (lo, hi)
    if kind.startswith("number:"):
        lo, hi = (float(x) for x in kind.split(":")[1:])
        return None if _num(v) and lo <= v <= hi else "a number from %g to %g" % (lo, hi)
    if kind.startswith("choice:"):
        choices = kind.split(":", 1)[1].split("|")
        return None if v in choices else "one of %s" % ", ".join(choices)
    if kind == "range":
        ok = isinstance(v, list) and len(v) == 2 and all(_int(x) and x >= 0 for x in v) and v[0] < v[1]
        return None if ok else "[first, last] whole frame numbers, first below last"
    if kind == "joints":
        ok = isinstance(v, dict) and all(k in IDX and isinstance(x, str) and x for k, x in v.items())
        return None if ok else "{canonical joint: capture joint name}"
    return None


def resolve_library_file(path):
    """A motions entry's library file: "@starter" is the factory's starter library."""
    return STARTER_PATH if path == STARTER else path


def read_library(path):
    """(document, problems) of a motion library file (forge-motion-library: "anims")."""
    try:
        with open(resolve_library_file(path), encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError) as ex:
        return None, ["%s cannot be read: %s" % (path, ex)]
    return doc, check_library(doc)


def check_library(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("anims"), dict):
        return ["a motion library is a JSON object with \"anims\""]
    problems = []
    for name, clip in doc["anims"].items():
        frames = clip.get("frames") if isinstance(clip, dict) else None
        if not isinstance(frames, list) or not frames:
            problems.append("clip %s has no frames" % name)
        elif any(not isinstance(f, dict) or any(j not in f for j in CANON) for f in frames):
            problems.append("clip %s: every frame must have the %d canonical joints" % (name, len(CANON)))
    return problems


def check_motions(motions, default_library=None, libraries=None):
    """Problems of a humanoid piece's motions list (empty: valid). default_library: the
    document of the piece's inputs.clips (or None); libraries: {file: document} of the files
    entries name (a missing file is the caller's problem)."""
    if not isinstance(motions, list):
        return ["motions must be a list of entries"]
    problems, names = [], set()
    for i, m in enumerate(motions):
        where = "motions[%d]" % i
        if not isinstance(m, dict):
            problems.append("%s must be an object" % where)
            continue
        name = m.get("name")
        if not isinstance(name, str) or not name or not set(name) <= NAME_CHARS or len(name) > 48:
            problems.append("%s: name must be a lower-case clip name (letters, digits, _), not %r" % (where, name))
            continue
        where = "motion %s" % name
        if name in names:
            problems.append("%s appears twice" % where)
        names.add(name)
        unknown = sorted(k for k in m if k not in MOTION_KEYS)
        if unknown:
            problems.append("%s: unknown keys %s (keys: %s)" % (where, unknown, ", ".join(MOTION_KEYS)))
        source = m.get("source")
        if source not in SOURCES:
            what = "procedural motion kinds are the creature's" if source == "procedural" else ""
            problems.append("%s: source must be one of %s, not %r%s" % (
                where, ", ".join(SOURCES), source, " (%s)" % what if what else ""))
            continue
        for other, keys in SOURCE_KEYS.items():
            for k in keys:
                if other != source and k in m:
                    problems.append("%s: %s belongs to source %s" % (where, k, other))
        if "frames" in m and not (_int(m["frames"]) and 2 <= m["frames"] <= 600):
            problems.append("%s: frames must be a whole number from 2 to 600" % where)
        if "fps" in m and not (_num(m["fps"]) and 1 <= m["fps"] <= 120):
            problems.append("%s: fps must be a number from 1 to 120" % where)
        if "loop" in m and not isinstance(m["loop"], bool):
            problems.append("%s: loop must be true or false" % where)
        if "kind" in m and not (isinstance(m["kind"], str) and m["kind"] and set(m["kind"]) <= NAME_CHARS):
            problems.append("%s: kind must be a lower-case word (idle, walk, start, stop or another)" % where)
        if "holds" in m and m["holds"] is not None and not (isinstance(m["holds"], str) and m["holds"]):
            problems.append("%s: holds must be a hold kind (<class> or <class>_<mode>) or null" % where)
        if "contacts" in m:
            c = m["contacts"]
            ok = isinstance(c, dict) and all(k in CONTACT_KEYS and (v == "all" or (isinstance(v, list) and all(
                _int(x) and x >= 0 for x in v))) for k, v in c.items())
            if not ok:
                problems.append("%s: contacts must be {\"left\": [frame, ...] or \"all\", \"right\": ...}" % where)
        settings = m.get("settings", {})
        if not isinstance(settings, dict):
            problems.append("%s: settings must be an object" % where)
            settings = {}
        decl = SETTINGS[source]
        for k, v in settings.items():
            if k not in decl:
                problems.append("%s: source %s has no setting %r%s" % (
                    where, source, k, " (settings: %s)" % ", ".join(decl) if decl else ""))
                continue
            why = _setting_problem(decl[k][0], v)
            if why:
                problems.append("%s: setting %s must be %s, not %r" % (where, k, why, v))
        if source == "library":
            lib = m.get("library", {})
            if not isinstance(lib, dict) or any(k not in ("file", "clip") for k in lib):
                problems.append("%s: library must be {\"file\": path, \"clip\": name}" % where)
                continue
            clip = lib.get("clip", name)
            doc = (libraries or {}).get(lib["file"]) if "file" in lib else default_library
            if "file" not in lib and default_library is None:
                problems.append("%s: a library clip without library.file needs inputs.clips" % where)
            elif doc is not None and clip not in (doc.get("anims") or {}):
                problems.append("%s: the library %s has no clip %s" % (
                    where, lib.get("file", "inputs.clips"), clip))
            continue
        if not _int(m.get("frames")):
            if "frames" not in m:
                problems.append("%s: frames is required for source %s" % (where, source))
            continue
        if source == "text":
            p = m.get("prompt")
            if not isinstance(p, str) or not p.strip():
                problems.append("%s: prompt must be a plain description of the movement" % where)
            elif len(p.split()) > 60:
                problems.append("%s: prompt has %d words; keep it under 60 (the model reads short "
                                "descriptions)" % (where, len(p.split())))
            if m.get("loop") and settings.get("duration") is not None:
                need = m["frames"] / float(m.get("fps", 24)) * THRESHOLDS["window_max"]
                if settings["duration"] < need:
                    problems.append("%s: a loop's duration must hold one window of %.2f s at least" % (where, need))
        else:
            f = m.get("file")
            if not isinstance(f, str) or not f:
                problems.append("%s: file must be the path of a BVH capture" % where)
            elif not os.path.isfile(f):
                problems.append("%s: capture file not found: %s" % (where, f))
            else:
                try:
                    _, names_found, _, nframes, _ = read_bvh_header(f)
                except (OSError, ValueError) as ex:
                    problems.append("%s: %s cannot be read as BVH: %s" % (where, f, ex))
                else:
                    missing = [c for c, src in bvh_joint_map(names_found, settings.get("joints")).items() if src is None
                               and c not in OPTIONAL_BVH]
                    if missing:
                        problems.append("%s: the capture has no joint for %s (give settings.joints)" % (
                            where, ", ".join(missing)))
                    r = settings.get("range")
                    if isinstance(r, list) and len(r) == 2 and _int(r[1]) and r[1] >= nframes:
                        problems.append("%s: range ends at frame %d; the capture has %d frames" % (where, r[1], nframes))
    return problems


def made_spec(entry):
    """What a made (text or bvh) clip depends on: two entries with the same spec share one job."""
    keep = ("source", "prompt", "file", "settings", "frames", "fps", "loop", "contacts")
    return {k: entry[k] for k in keep if k in entry}


def text_settings(entry, seed):
    """The effective settings of a text entry (defaults filled in)."""
    s = dict(entry.get("settings") or {})
    frames, fps, loop = int(entry["frames"]), float(entry.get("fps", 24)), bool(entry.get("loop", False))
    want = frames / fps
    out = {"steps": s.get("steps", 50), "seed": s.get("seed", seed), "guidance": float(s.get("guidance", 5.0)),
           "feet": s.get("feet", "auto")}
    if s.get("duration") is not None:
        out["duration"] = float(s["duration"])
    elif loop:
        out["duration"] = round(min(12.0, max(3.0, 2 * want + 1.0)), 3)
    else:
        out["duration"] = round(min(12.0, max(0.7, want)), 3)
    return out


# ---- frames <-> arrays -----------------------------------------------------------------------

def to_array(frames):
    return np.array([[f[j] for j in CANON] for f in frames], dtype=np.float64)


def to_frames(arr):
    return [{j: [round(float(x), 5) for x in arr[i, k]] for k, j in enumerate(CANON)} for i in range(len(arr))]


# ---- BVH captures ----------------------------------------------------------------------------

def _norm(name):
    name = name.split(":")[-1].lower()
    return "".join(c for c in name if c.isalnum() or c == "/")


# Normalised capture joint names per canonical joint, in order of preference: the CMU
# library as converted to BVH (Hips, LeftUpLeg, Spine1, Neck1, LeftFingerBase, ...), the
# Mixamo naming (prefix removed) and a few common others. "<joint>/end" is an end site.
BVH_NAMES = {
    "pelvis": ["hips", "pelvis", "hip", "root"],
    "spine": ["spine", "spine0", "lowerback", "abdomen"],
    "chest": ["spine2", "spine1", "chest", "upperchest", "spine3"],
    "neck": ["neck1", "neck", "neck0"],
    "head": ["head"],
    "head_top": ["head/end", "headtopend", "headtop", "headend", "headtop/end", "headtopend/end"],
}
for _s, _side in (("l", "left"), ("r", "right")):
    _c = _side[0]
    BVH_NAMES.update({
        _s + "_hip": [_side + "upleg", _side + "thigh", _side + "upperleg", _side + "hip", _c + "thigh"],
        _s + "_knee": [_side + "leg", _side + "lowerleg", _side + "shin", _side + "knee", _side + "calf",
                       _c + "shin"],
        _s + "_ankle": [_side + "foot", _side + "ankle", _c + "foot"],
        _s + "_toe": [_side + "toebase", _side + "toe", _side + "toes", _side + "toebase/end", _side + "foot/end"],
        _s + "_collar": [_side + "shoulder", _side + "collar", _side + "clavicle", _c + "collar"],
        _s + "_shoulder": [_side + "arm", _side + "upperarm", _c + "shldr", _c + "uparm"],
        _s + "_elbow": [_side + "forearm", _side + "lowerarm", _side + "elbow", _c + "forearm"],
        _s + "_wrist": [_side + "hand", _side + "wrist", _c + "hand"],
        _s + "_hand": [_side + "fingerbase", _side + "handmiddle1", _side + "handindex1", _side + "middle1",
                       _side + "hand/end"],
    })
# Joints derived when the capture has none: the top of the head extends neck -> head, a hand
# extends the forearm. The other joints are required.
OPTIONAL_BVH = ("head_top", "l_hand", "r_hand", "l_collar", "r_collar", "chest", "neck")


def read_bvh_header(path):
    """(joints [(name, parent index, offset, channels)], names, frame_time, frames, data line)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    joints, stack, pending = [], [], None
    i = 0
    while i < len(lines):
        tok = lines[i].split()
        i += 1
        if not tok:
            continue
        if tok[0] in ("ROOT", "JOINT"):
            joints.append([" ".join(tok[1:]), stack[-1] if stack else -1, None, []])
            pending = len(joints) - 1
        elif tok[0] == "End":
            joints.append([joints[stack[-1]][0] + "/end", stack[-1], None, []])
            pending = len(joints) - 1
        elif tok[0] == "{":
            if pending is None:
                raise ValueError("a block without a joint")
            stack.append(pending)
            pending = None
        elif tok[0] == "}":
            stack.pop()
        elif tok[0] == "OFFSET":
            joints[stack[-1]][2] = np.array([float(x) for x in tok[1:4]])
        elif tok[0] == "CHANNELS":
            joints[stack[-1]][3] = tok[2:]
        elif tok[0] == "MOTION":
            break
    if not joints:
        raise ValueError("no HIERARCHY joints")
    while i < len(lines) and not lines[i].strip():
        i += 1
    frames = int(lines[i].split(":")[1])
    frame_time = float(lines[i + 1].split(":")[1])
    if frame_time <= 0:
        raise ValueError("Frame Time must be above 0")
    return joints, [j[0] for j in joints], frame_time, frames, (lines, i + 2)


def _rot(order, angles):
    m = np.eye(3)
    for axis, a in zip(order, angles):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        m = m @ {"X": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
                 "Y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
                 "Z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]
    return m


def read_bvh(path, first=None, last=None):
    """(positions (frames, joints, 3) of every joint and end site by forward kinematics,
    joint names, frames per second) of the frames first..last (inclusive)."""
    joints, names, frame_time, nframes, (lines, at) = read_bvh_header(path)
    first = 0 if first is None else first
    last = nframes - 1 if last is None else min(last, nframes - 1)
    data = np.array([[float(x) for x in lines[at + f].split()] for f in range(first, last + 1)])
    col, starts = 0, []
    for j in joints:
        starts.append(col)
        col += len(j[3])
    if data.shape[1] < col:
        raise ValueError("a frame has %d values for %d channels" % (data.shape[1], col))
    pos = np.zeros((len(data), len(joints), 3))
    for f in range(len(data)):
        mats = [None] * len(joints)
        for k, (name, parent, offset, chans) in enumerate(joints):
            vals = data[f, starts[k]:starts[k] + len(chans)]
            offset = np.zeros(3) if offset is None else offset
            rot_axes = [c[0].upper() for c in chans if c.lower().endswith("rotation")]
            rot_vals = [v for c, v in zip(chans, vals) if c.lower().endswith("rotation")]
            low = [c.lower() for c in chans]
            trans = np.array([vals[low.index(ax + "position")] if ax + "position" in low else offset["xyz".index(ax)]
                              for ax in "xyz"])
            r = _rot(rot_axes, rot_vals) if rot_axes else np.eye(3)
            if parent < 0:
                mats[k] = (r, trans)
            else:
                pr, pt = mats[parent]
                mats[k] = (pr @ r, pt + pr @ trans)
            pos[f, k] = mats[k][1]
    return pos, names, 1.0 / frame_time


def bvh_joint_map(names, given=None):
    """{canonical joint: capture joint name or None}."""
    by_norm = {}
    for n in names:
        by_norm.setdefault(_norm(n), n)
    out = {}
    for c in CANON:
        if given and c in given and given[c] in names:
            out[c] = given[c]
            continue
        out[c] = next((by_norm[x] for x in BVH_NAMES.get(c, []) if x in by_norm), None)
    return out


def bvh_canon(path, settings=None):
    """(positions (frames, 24, 3), fps, up axis index, joint map) of a capture's range."""
    settings = settings or {}
    r = settings.get("range")
    pos, names, fps = read_bvh(path, *(r if r else (None, None)))
    jm = bvh_joint_map(names, settings.get("joints"))
    missing = [c for c, s in jm.items() if s is None and c not in OPTIONAL_BVH]
    if missing:
        raise ValueError("%s has no joint for %s" % (path, ", ".join(missing)))
    out = np.zeros((len(pos), len(CANON), 3))
    for c, s in jm.items():
        if s is not None:
            out[:, IDX[c]] = pos[:, names.index(s)]
    derive(out, {c for c, s in jm.items() if s is None})
    return out, fps, {"y": 1, "z": 2}[settings.get("up", "y")], jm


def derive(out, missing):
    """Fill canonical joints a source lacks from their neighbours (in place)."""
    if "chest" in missing:
        out[:, IDX["chest"]] = (out[:, IDX["spine"]] + out[:, IDX["neck"] if "neck" not in missing else IDX["head"]]) / 2
    if "neck" in missing:
        out[:, IDX["neck"]] = out[:, IDX["chest"]] + (out[:, IDX["head"]] - out[:, IDX["chest"]]) * 0.5
    for s in "lr":
        if s + "_collar" in missing:
            out[:, IDX[s + "_collar"]] = out[:, IDX["chest"]] + (out[:, IDX[s + "_shoulder"]] - out[:, IDX["chest"]]) * 0.3
        if s + "_hand" in missing:
            w, e = out[:, IDX[s + "_wrist"]], out[:, IDX[s + "_elbow"]]
            out[:, IDX[s + "_hand"]] = w + (w - e) * 0.35
    if "head_top" in missing:
        h, n = out[:, IDX["head"]], out[:, IDX["neck"]]
        out[:, IDX["head_top"]] = h + (h - n) * 0.8


# ---- HY-Motion's joints (SMPL-H order: 22 body joints, then 15 per hand) ----------------------

SMPL_BODY = {"pelvis": 0, "l_hip": 1, "r_hip": 2, "spine": 3, "chest": 9, "neck": 12, "head": 15,
             "l_collar": 13, "l_shoulder": 16, "l_elbow": 18, "l_wrist": 20,
             "r_collar": 14, "r_shoulder": 17, "r_elbow": 19, "r_wrist": 21,
             "l_knee": 4, "l_ankle": 7, "l_toe": 10, "r_knee": 5, "r_ankle": 8, "r_toe": 11}
SMPL_HANDS = {"l_hand": 25, "r_hand": 40}  # the middle fingers' first joints of the 52


def smpl_canon(keypoints, transl=None):
    """Canonical positions (frames, 24, 3), y up, from SMPL-ordered joints (frames, 22 or 52,
    3); transl (frames, 3) is added when given (HY-Motion's keypoints3d leave it out)."""
    k = np.asarray(keypoints, dtype=np.float64)
    if k.ndim == 4:
        k = k[0]
    if transl is not None:
        t = np.asarray(transl, dtype=np.float64)
        if t.ndim == 3:
            t = t[0]
        k = k + t[:, None, :]
    out = np.zeros((k.shape[0], len(CANON), 3))
    for c, i in SMPL_BODY.items():
        out[:, IDX[c]] = k[:, i]
    missing = {"head_top"}
    if k.shape[1] >= 52:
        for c, i in SMPL_HANDS.items():
            out[:, IDX[c]] = k[:, i]
    else:
        missing |= {"l_hand", "r_hand"}
    derive(out, missing)
    return out


# ---- body coordinates -------------------------------------------------------------------------

def _chain(arr, names):
    return sum(np.linalg.norm(arr[:, IDX[b]] - arr[:, IDX[a]], axis=-1) for a, b in zip(names, names[1:]))


def to_body(pos, up):
    """Positions of a source (any units, `up` the index of its up axis) in body coordinates:
    [side (+ left), forward, up] over the ground, divided by the figure's standing height.
    Returns (array, info)."""
    pos = np.asarray(pos, dtype=np.float64)
    upv = np.zeros(3)
    upv[up] = 1.0
    left = (pos[:, IDX["l_hip"]] - pos[:, IDX["r_hip"]]).mean(0)
    left = left - upv * left.dot(upv)
    if np.linalg.norm(left) < 1e-9:
        raise ValueError("the hips are not apart: no body frame")
    left /= np.linalg.norm(left)
    fwd = np.cross(left, upv)
    toes = (pos[:, IDX["l_toe"]] - pos[:, IDX["l_ankle"]] + pos[:, IDX["r_toe"]] - pos[:, IDX["r_ankle"]]).mean(0)
    flipped = bool(np.dot(fwd, toes) < 0)
    if flipped:
        fwd = -fwd
    feet = [IDX[j] for j in ("l_ankle", "l_toe", "r_ankle", "r_toe")]
    floor = float(pos[:, feet, up].min())
    # The standing height: the tallest head over the floor, unless the clip never stands
    # (then the bones' lengths say it).
    tall = float((pos[:, IDX["head_top"], up] - floor).max())
    legs = np.median(_chain(pos, ["l_hip", "l_knee", "l_ankle"]) + _chain(pos, ["r_hip", "r_knee", "r_ankle"])) / 2
    trunk = np.median(_chain(pos, ["pelvis", "spine", "chest", "neck", "head", "head_top"]))
    ankle = float(np.median(np.minimum(pos[:, IDX["l_ankle"], up], pos[:, IDX["r_ankle"], up]) - floor))
    bones = legs + trunk + ankle
    how = "tallest head"
    height = tall
    if tall < 0.85 * bones:
        height, how = 0.97 * bones, "bone lengths (the clip never stands up)"
    if height <= 0:
        raise ValueError("the figure has no height")
    rel = pos - np.array([0.0, 0.0, 0.0])
    out = np.stack([rel @ left, rel @ fwd, rel[..., up] - floor], axis=-1) / height
    origin = out[0, IDX["pelvis"], :2].copy()
    out[..., 0] -= origin[0]
    out[..., 1] -= origin[1]
    return out, {"height_source_units": round(height, 6), "height_from": how, "mirrored": flipped}


def pelvis_stand(arr):
    """The clip's standing pelvis height: its highest pelvis, or the legs' length when it never
    stands up."""
    legs = np.median(_chain(arr, ["l_hip", "l_knee", "l_ankle"]) + _chain(arr, ["r_hip", "r_knee", "r_ankle"])) / 2
    hips = np.median(arr[:, IDX["pelvis"], 2] - (arr[:, IDX["l_hip"], 2] + arr[:, IDX["r_hip"], 2]) / 2)
    ankle = float(np.percentile(np.minimum(arr[:, IDX["l_ankle"], 2], arr[:, IDX["r_ankle"], 2]), 10))
    return float(max(arr[:, IDX["pelvis"], 2].max(), legs + hips + ankle))


# ---- time ---------------------------------------------------------------------------------------

def sample(arr, times, loop=False):
    """Positions at fractional frame times (linear; a loop wraps)."""
    n = len(arr)
    out = np.zeros((len(times),) + arr.shape[1:])
    for i, t in enumerate(times):
        a = int(math.floor(t))
        s = t - a
        if loop:
            a0, a1 = a % n, (a + 1) % n
        else:
            a0, a1 = min(max(a, 0), n - 1), min(max(a + 1, 0), n - 1)
            if a >= n - 1:
                s = 0.0
        out[i] = arr[a0] * (1 - s) + arr[a1] * s
    return out


def _pose(arr):
    """Positions relative to the pelvis on the ground plane (what makes two frames the same pose)."""
    p = arr.copy()
    p[..., :2] -= arr[:, IDX["pelvis"], None, :2]
    return p


def loop_window(arr, fps_src, seconds):
    """(start, length) in source frames of the best loop: the window whose ends are the most
    alike in pose and in speed, 0.8 to 1.25 times the wanted length."""
    n = len(arr)
    want = seconds * fps_src
    lo = max(2, int(round(want * THRESHOLDS["window_min"])))
    hi = min(n - 2, int(round(want * THRESHOLDS["window_max"])))
    pose = _pose(arr)
    vel = np.gradient(pose, axis=0)
    best = None
    for length in range(lo, hi + 1):
        for a in range(0, n - length - 1):
            b = a + length
            cost = np.linalg.norm(pose[a] - pose[b], axis=-1).mean() + 0.5 * np.linalg.norm(
                vel[a] - vel[b], axis=-1).mean() + 0.02 * abs(length - want) / want
            if best is None or cost < best[0]:
                best = (cost, a, length)
    if best is None:
        return 0.0, float(n - 1), None
    return float(best[1]), float(best[2]), float(best[0])


def step_stats(arr, loop):
    steps = np.linalg.norm(np.diff(arr, axis=0), axis=-1).max(axis=-1)
    seam = float(np.linalg.norm(arr[0] - arr[-1], axis=-1).max()) if loop else None
    return float(np.median(steps)) if len(steps) else 0.0, seam


# ---- contacts -------------------------------------------------------------------------------

def _runs(flags, loop):
    """[(start, end inclusive)] of True runs; a loop's run may wrap (end < start)."""
    n = len(flags)
    if all(flags):
        return [(0, n - 1)]
    runs, i = [], 0
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    if loop and len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == n - 1:
        last = runs.pop()
        runs[0][0] = last[0]
    return [tuple(r) for r in runs]


def _run_frames(run, n):
    a, b = run
    return list(range(a, b + 1)) if a <= b else list(range(a, n)) + list(range(0, b + 1))


def _run_ramp(fr, n, travel):
    """The ground frame's offset of each frame of a run, counted on from the run's first frame
    (a loop's run that passes the seam goes on into the next pass)."""
    out = np.zeros((len(fr), 3))
    if travel:
        idx = np.array([i if i >= fr[0] else i + n for i in fr], dtype=np.float64)
        out[:, 0] = idx / n * travel[0]
        out[:, 1] = idx / n * travel[1]
    return out


def detect_contacts(arr, fps, loop, travel):
    """{"left": [frames], "right": [frames]} where the ankle is low and still in the ground frame."""
    n = len(arr)
    out = {}
    for side, s in SIDES.items():
        a = arr[:, IDX[s + "_ankle"]]
        low = a[:, 2] - a[:, 2].min() <= THRESHOLDS["contact_height"]
        if loop:
            d = (np.roll(a, -1, axis=0) - np.roll(a, 1, axis=0))[:, :2] / 2.0
            if travel:
                d = d + np.array(travel[:2]) / n  # the body's own move: the ground frame
        else:
            d = np.gradient(a[:, :2], axis=0) if n > 1 else np.zeros((n, 2))
        still = np.linalg.norm(d, axis=-1) * fps <= THRESHOLDS["contact_speed"]
        flags = list(low & still)
        # fill one-frame gaps, then drop short runs
        for i in range(n):
            prev, nxt = (i - 1) % n if loop else i - 1, (i + 1) % n if loop else i + 1
            if not flags[i] and 0 <= prev < n and 0 <= nxt < n and flags[prev] and flags[nxt]:
                flags[i] = True
        keep = [False] * n
        for run in _runs(flags, loop):
            fr = _run_frames(run, n)
            if len(fr) >= THRESHOLDS["contact_min_frames"] or len(fr) == n:
                for i in fr:
                    keep[i] = True
        out[side] = [i for i in range(n) if keep[i]]
    return out


def estimate_travel(arr, fps):
    """[side, forward] body heights a walk in place covers per pass, from its low feet's speed
    (the stance foot moves back at the body's speed), or None when it stands still."""
    n = len(arr)
    vs = []
    for s in "lr":
        a = arr[:, IDX[s + "_ankle"]]
        low = a[:, 2] - a[:, 2].min() <= THRESHOLDS["contact_height"]
        d = (np.roll(a, -1, axis=0) - np.roll(a, 1, axis=0))[:, :2] / 2.0
        if low.sum() >= 3:
            vs.append(np.median(d[low], axis=0))
    if not vs:
        return None
    v = -np.mean(vs, axis=0) * n
    return [round(float(v[0]), 5), round(float(v[1]), 5)] if np.linalg.norm(v) > 0.02 else None


def _frame_list(v, n):
    return list(range(n)) if v == "all" else sorted(i for i in v if 0 <= i < n)


# ---- corrections ------------------------------------------------------------------------------

def _two_bone(hip, ankle, l1, l2, knee0):
    d = ankle - hip
    dist = float(np.linalg.norm(d))
    if dist < 1e-9:
        return knee0, ankle, 0.0
    u = d / dist
    short = max(0.0, dist - (l1 + l2 - 1e-6))
    dist = min(max(dist, abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
    ankle = hip + u * dist
    v = (knee0 - hip) - u * np.dot(knee0 - hip, u)
    if np.linalg.norm(v) < 1e-9:
        v = np.array([0.0, 1.0, 0.0]) - u * u[1]
    v /= np.linalg.norm(v)
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    return hip + u * a + v * h, ankle, short


def _set_ankle(arr, i, s, new_ankle, toe_shift):
    """Move frame i's ankle (and its toe by the same shift); the knee follows by two-bone IK."""
    hip, knee, ankle = arr[i, IDX[s + "_hip"]], arr[i, IDX[s + "_knee"]], arr[i, IDX[s + "_ankle"]]
    l1, l2 = np.linalg.norm(knee - hip), np.linalg.norm(ankle - knee)
    k, a, short = _two_bone(hip, new_ankle, l1, l2, knee)
    arr[i, IDX[s + "_knee"]] = k
    arr[i, IDX[s + "_toe"]] = arr[i, IDX[s + "_toe"]] + (a - ankle) if toe_shift else arr[i, IDX[s + "_toe"]]
    arr[i, IDX[s + "_ankle"]] = a
    return short


def foot_slide(arr, stance, loop, travel):
    """Largest move of a planted ankle within a run of its stance frames (ground frame)."""
    n = len(arr)
    worst = 0.0
    for side, s in SIDES.items():
        flags = [False] * n
        for i in _frame_list(stance.get(side, []), n):
            flags[i] = True
        for run in _runs(flags, loop):
            fr = _run_frames(run, n)
            a = arr[fr, IDX[s + "_ankle"]] + (_run_ramp(fr, n, travel) if loop else 0.0)
            worst = max(worst, float(np.linalg.norm(a - a[0], axis=-1).max()))
    return worst


def fix_hips(arr, stance, loop):
    """The hip height on planted feet: each frame with a planted foot is lifted or lowered so
    that its lowest planted ankle stands at the clip's planted ankle height (frames between
    are interpolated). Returns (before, after) the largest planted ankle height off it."""
    n = len(arr)
    planted = {side: set(_frame_list(stance.get(side, []), n)) for side in SIDES}
    heights = [arr[i, IDX[s + "_ankle"], 2] for side, s in SIDES.items() for i in planted[side]]
    if not heights:
        return None, None, False
    ref = float(np.median(heights))

    def off():
        out = np.full(n, np.nan)
        for i in range(n):
            hs = [arr[i, IDX[s + "_ankle"], 2] for side, s in SIDES.items() if i in planted[side]]
            if hs:
                out[i] = ref - min(hs)
        return out
    o = off()
    before = float(np.nanmax(np.abs(o)))
    if before <= THRESHOLDS["ground"]:
        return before, before, False
    known = np.where(~np.isnan(o))[0]
    xs = np.arange(n)
    if loop:
        k = np.concatenate([known - n, known, known + n])
        fill = np.interp(xs, k, np.tile(o[known], 3))
    else:
        fill = np.interp(xs, known, o[known])
    if n >= 3:
        sm = np.convolve(np.concatenate([fill[-1:], fill, fill[:1]]) if loop else np.pad(fill, 1, mode="edge"),
                         [0.25, 0.5, 0.25], mode="valid")
        fill = np.where(np.isnan(o), sm, fill)
    arr[..., 2] += fill[:, None]
    after = float(np.nanmax(np.abs(off())))
    return before, after, True


def clean_feet(arr, stance, loop, travel):
    """The foot contact cleanup: within each run of planted frames the ankle (and its toe)
    holds the run's median place in the ground frame, the neighbouring frames move half way,
    the knee follows by two-bone IK. Returns the reach shortfall it met (body heights)."""
    n = len(arr)
    short = 0.0
    step = np.array([travel[0], travel[1], 0.0]) / n if (loop and travel) else np.zeros(3)
    for side, s in SIDES.items():
        flags = [False] * n
        for i in _frame_list(stance.get(side, []), n):
            flags[i] = True
        for run in _runs(flags, loop):
            fr = _run_frames(run, n)
            r = _run_ramp(fr, n, travel) if loop else np.zeros((len(fr), 3))
            target = np.median(arr[fr, IDX[s + "_ankle"]] + r, axis=0)
            for k, i in enumerate(fr):
                short = max(short, _set_ankle(arr, i, s, target - r[k], True))
            if len(fr) < n:
                # the frames just before and after the run move half way to it
                for i, off in (((fr[0] - 1) % n if loop else fr[0] - 1, r[0] - step),
                               ((fr[-1] + 1) % n if loop else fr[-1] + 1, r[-1] + step)):
                    if 0 <= i < n and not flags[i]:
                        want = arr[i, IDX[s + "_ankle"]] * 0.5 + (target - off) * 0.5
                        want[2] = arr[i, IDX[s + "_ankle"], 2]
                        short = max(short, _set_ankle(arr, i, s, want, True))
    return short


def _record(name, applied, threshold, before, after, ok=None):
    passed = ok if ok is not None else (after is None or after <= threshold + 1e-12)
    return {"name": name, "applied": applied, "threshold": threshold,
            "before": None if before is None else round(float(before), 6),
            "after": None if after is None else round(float(after), 6), "passed": bool(passed)}


def make_clip(pos, src_fps, up, entry, source_text, given_contacts=None):
    """A source's positions (any units, up axis index) made the entry's canonical clip.
    Returns (clip for the motion library, report)."""
    frames, fps = int(entry["frames"]), float(entry.get("fps", 24))
    loop = bool(entry.get("loop", False))
    body, scale = to_body(pos, up)
    report = {"format": REPORT_FORMAT, "version": 1, "name": entry["name"], "source": source_text,
              "source_frames": len(body), "source_fps": round(float(src_fps), 4), "frames": frames, "fps": fps,
              "loop": loop, "scale": scale, "thresholds": dict(THRESHOLDS), "units": "body heights",
              "corrections": [], "flags": []}
    seconds = frames / fps
    if loop:
        start, length, cost = loop_window(body, src_fps, seconds)
        report["window"] = {"start": start, "frames": length, "seconds": round(length / src_fps, 4),
                            "cost": None if cost is None else round(cost, 6)}
        times = [start + length * i / frames for i in range(frames + 1)]
        arr = sample(body, times)
        drift = arr[-1, IDX["pelvis"], :2] - arr[0, IDX["pelvis"], :2]
        for i in range(frames + 1):
            arr[i, :, :2] -= drift * i / frames
        arr[..., :2] -= arr[0, IDX["pelvis"], :2]
        travel = [round(float(drift[0]), 5), round(float(drift[1]), 5)]
        resid = arr[-1] - arr[0]
        seam_before = float(np.linalg.norm(resid, axis=-1).max())
        for i in range(frames + 1):
            arr[i] -= resid * i / frames
        arr = arr[:frames]
        step, seam = step_stats(arr, True)
        limit = max(THRESHOLDS["seam_factor"] * step, THRESHOLDS["seam_min"])
        rec = _record("cycle_closure", seam_before > 1e-9, limit, seam_before, seam)
        rec["median_step"] = round(step, 6)
        report["corrections"].append(rec)
        if np.linalg.norm(travel) <= 0.02:
            travel = None
    else:
        times = [(len(body) - 1) * i / max(frames - 1, 1) for i in range(frames)]
        arr = sample(body, times)
        arr[..., :2] -= arr[0, IDX["pelvis"], :2]
        travel = None
    clip = finish(arr, fps, loop, travel, entry, report, given_contacts)
    clip["source"] = source_text
    return clip, report


def finish(arr, fps, loop, travel, entry, report, given_contacts=None, feet=None):
    """Contacts, hip height and foot cleanup on a clip in body coordinates; the clip's keys."""
    n = len(arr)
    contacts = entry.get("contacts") if "contacts" in entry else given_contacts
    if contacts:
        how = "given"
        stance = {side: _frame_list(contacts.get(side, []), n) for side in CONTACT_KEYS}
    else:
        how = "detected"
        stance = detect_contacts(arr, fps, loop, travel)
    report["contacts"] = {"how": how, "left": stance["left"], "right": stance["right"]}
    if stance["left"] or stance["right"]:
        before, after, applied = fix_hips(arr, stance, loop)
        report["corrections"].append(_record("hip_height", applied, THRESHOLDS["ground"], before, after))
        slide0 = foot_slide(arr, stance, loop, travel)
        short = 0.0
        if slide0 > THRESHOLDS["foot_slide_fix"]:
            short = clean_feet(arr, stance, loop, travel)
        slide1 = foot_slide(arr, stance, loop, travel)
        rec = _record("foot_contacts", slide0 > THRESHOLDS["foot_slide_fix"], THRESHOLDS["foot_slide"], slide0, slide1)
        rec["reach_shortfall"] = round(short, 6)
        report["corrections"].append(rec)
    else:
        report["corrections"].append(_record("hip_height", False, THRESHOLDS["ground"], None, None))
        report["corrections"].append(_record("foot_contacts", False, THRESHOLDS["foot_slide"], None, None))
    stand = pelvis_stand(arr)
    mode = feet or (entry.get("settings") or {}).get("feet", "auto")
    if mode == "auto":
        if loop and travel and np.linalg.norm(travel) > 0.05:
            mode = "walk"
        elif not loop and arr[-1, IDX["pelvis"], 2] < 0.35 * stand:
            mode = "lie"
        else:
            mode = "clip"
    lowest = float(arr[..., 2].min())
    ground = _record("below_ground", False, THRESHOLDS["ground"], None, max(0.0, -lowest),
                     ok=mode == "lie" or -lowest <= THRESHOLDS["ground"])
    report["corrections"].append(ground)
    if not np.isfinite(arr).all():
        report["flags"].append("the clip has values that are not finite")
    for rec in report["corrections"]:
        if not rec["passed"]:
            report["flags"].append("%s: %s over %s" % (rec["name"], rec["after"], rec["threshold"]))
    report["valid"] = not report["flags"]
    report["feet"] = mode
    report["travel"] = travel
    clip = {"frames": to_frames(arr), "fps": fps, "loop": loop, "feet": mode, "pelvis_stand": round(stand, 5)}
    if stance["left"] or stance["right"]:
        clip["stance"] = {side: ("all" if len(v) == n else v) for side, v in stance.items()}
    if travel:
        clip["travel"] = travel
    return clip


def library_entry_clip(doc, entry, path_text):
    """A library clip for a motions entry: resampled to its frames (by time; a loop wraps), its
    fps and loop when given, contacts (the entry's, the clip's stance, else detected), the
    stage's corrections. Returns (clip, report)."""
    name = (entry.get("library") or {}).get("clip", entry["name"])
    src = doc["anims"][name]
    arr = to_array(src["frames"])
    m = len(arr)
    loop = bool(entry.get("loop", src.get("loop", False)))
    fps = float(entry.get("fps", src.get("fps", 24.0)))
    n = int(entry.get("frames", m))
    report = {"format": REPORT_FORMAT, "version": 1, "name": entry["name"], "source": "library %s clip %s" % (
        path_text, name), "source_frames": m, "frames": n, "fps": fps, "loop": loop,
              "thresholds": dict(THRESHOLDS), "units": "body heights", "corrections": [], "flags": []}
    stance = src.get("stance")
    if n != m:
        times = [(i * m / n) if loop else (i * (m - 1) / max(n - 1, 1)) for i in range(n)]
        arr = sample(arr, times, loop)
        if stance:
            stance = {side: [i for i, t in enumerate(times) if int(round(t)) % m in set(_frame_list(v, m))]
                      for side, v in stance.items()}
    travel = src.get("travel")
    if loop and travel is None and not entry.get("contacts") and not stance:
        travel = estimate_travel(arr, fps)
        if travel:
            report["travel_estimated"] = travel
    if loop:
        step, seam = step_stats(arr, True)
        limit = max(THRESHOLDS["seam_factor"] * step, THRESHOLDS["seam_min"])
        # A library clip that does not close is closed by spreading its seam over the clip.
        if seam > limit:
            resid = arr[0] - (arr[-1] + (arr[-1] - arr[-2])) if n > 2 else arr[0] - arr[-1]
            g = arr.copy()
            for i in range(n):
                g[i] += resid * i / n
            arr = g
        rec = _record("cycle_closure", seam > limit, limit, seam, step_stats(arr, True)[1])
        rec["median_step"] = round(step, 6)
        report["corrections"].append(rec)
    feet = (entry.get("settings") or {}).get("feet") or src.get("feet", "clip")
    clip = finish(arr, fps, loop, travel, entry, report, given_contacts=stance, feet=feet)
    for k in ("kind", "holds", "hold", "pose_frame"):
        if k in src:
            clip[k] = src[k]
    if src.get("pelvis_stand") is not None and n == m:
        clip["pelvis_stand"] = src["pelvis_stand"]
    clip["source"] = src.get("source") or report["source"]
    return clip, report


def clip_document(name, clip):
    """A motion library holding one clip."""
    return {"format": LIBRARY_FORMAT, "version": 1, "canon": list(CANON), "anims": {name: clip}}
