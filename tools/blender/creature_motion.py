"""The clips of a creature, from the order's motions list (the list of section 13 of the
design: one entry per movement, each saying where it comes from), made in the roles of the
body plan (creature_bones.py), so the same order line animates every variant's own skeleton
with no human work.

An entry:

  {"name": "walk_none", "source": "procedural", "kind": "gait", "frames": 24, "fps": 24,
   "loop": true, "settings": {"stride": 0.3}}
  {"name": "idle_none", "source": "library", "library": {"clip": "idle"}, "frames": 40}

Keys (MOTION_KEYS): name (the clip name the game looks up), source (SOURCES), frames, fps,
loop, holds (what the hands hold: a held-item class, or null), contacts ({role: frames or
"all"}: where each foot is planted), settings (per source), note; and per source: kind
(procedural), library (library: {"file", "clip"}, defaults the piece's inputs.clips and the
entry's name), prompt (text), file (bvh). Sources made here: procedural (KINDS, its
settings are the kind's parameters; its contacts are made by the kind) and library (a
forge-creature-clips document in role space, resampled to the entry's frames). text and
bvh are names kept for a later phase.

Procedural settings: angles are degrees, distances body heights; a setting naming chains
takes chain ids of the body plan (a paired chain means all its roles) or roles (arm_r0).

Kinds (t runs over the clip; a loop's frames are t = i / frames, a one-shot's i / (frames - 1)):
  breathe   idle: the trunk bends a little forward and back, the other chains sway
  hover     floating idle or glide: the whole body bobs, the trunk tilts (lean: a constant
            forward tilt for a glide), the other chains sway
  undulate  a wave travels along chains (a tail, a serpent body): lateral (side to side) or
            pitch (up and down); the trunk counter-sways and the arms swing
  gait      legged walk in place for any number of legs: each foot follows a stance
            (planted, moving back at the body's speed) and a swing (lifted forward); the hip
            and knee are solved exactly, the bones below them (a long digitigrade foot, the
            toes) keep their rest orientation; the body lowers so every foot stays in reach;
            the clip's travel is the distance the body covers per pass
  strike    attack: chains wind up, strike forward and recover; the body lunges
  aim       chains raised forward and held, the trunk breathing
  flinch    hit: the trunk thrown back and recovering, the body pushed back
  collapse  death: the body falls over (back, front, left or right) to the ground, the limbs
            curl the way their joints bend; the last pose is held

"+pitch" moves a bone toward the front (toward the ground for a bone along the front-back
line); "lateral" toward the creature's left; a "flex" turns a bone the way its joint bends
(the plan's or template's bends). An arm is swung as a whole about its first bone.

Rotations are kept in armature space, about each bone's rest head, relative to the rest
pose; the exporter turns them into Blender pose rotations. No bpy.
"""

import math

import numpy as np

import creature_bones as cb

UP = np.array(cb.UP)
FRONT = np.array(cb.FRONT)
LEFT = np.array(cb.LEFT)
DOWN = -UP
FALLS = {"back": -FRONT, "front": FRONT, "left": LEFT, "right": -LEFT}

# kind -> {"loop": default, "params": {name: (type, default, meaning)}}. Types: number, int,
# chains (list of chain ids or roles; null: the kind's default chains), choice:<a>|<b>,
# numbers (a list of numbers or null), optional number ("number?": null means automatic).
KINDS = {
    "breathe": {"loop": True, "params": {
        "chains": ("chains", None, "chains that bend (default: the trunk)"),
        "amplitude": ("number", 3.0, "trunk bend, degrees"),
        "sway": ("number", 2.0, "sway of the other chains, degrees"),
        "cycles": ("int", 1, "breaths per clip")}},
    "hover": {"loop": True, "params": {
        "chains": ("chains", None, "chains that tilt (default: the trunk)"),
        "bob": ("number", 0.03, "up and down, body heights"),
        "tilt": ("number", 2.0, "trunk tilt, degrees"),
        "lean": ("number", 0.0, "constant forward tilt of the trunk (a glide), degrees"),
        "sway": ("number", 4.0, "sway of the other chains, degrees"),
        "cycles": ("int", 1, "bobs per clip")}},
    "undulate": {"loop": True, "params": {
        "chains": ("chains", None, "chains the wave runs along (default: the chains toward the back)"),
        "waves": ("number", 1.0, "wavelengths along each chain"),
        "amplitude": ("number", 18.0, "bend of each bone at the tip, degrees"),
        "base": ("number", 0.3, "share of the amplitude at the base of the chain"),
        "axis": ("choice:lateral|pitch", "lateral", "side to side or up and down"),
        "upper": ("number", 4.0, "counter-sway of the trunk, degrees"),
        "arms": ("number", 6.0, "swing of the side chains, degrees"),
        "travel": ("number", 0.0, "distance the body covers per pass, body heights (for the game)"),
        "cycles": ("int", 1, "wave periods per clip")}},
    "gait": {"loop": True, "params": {
        "legs": ("chains", None, "the legs (default: the chains toward down)"),
        "stride": ("number?", None, "foot travel per step, body heights (default: half the leg length)"),
        "lift": ("number?", None, "foot lift, body heights (default: 0.18 of the leg length)"),
        "duty": ("number?", None, "share of the cycle a foot is planted (default 0.6, 0.65 with more than 2 legs)"),
        "phases": ("numbers", None, "phase of each leg, 0 to 1 (default: alternating left and right, "
                                    "and along each side)"),
        "bob": ("number", 0.015, "body bob, body heights"),
        "sway": ("number", 3.0, "trunk counter-sway, degrees"),
        "arms": ("number", 10.0, "swing of the side chains, degrees"),
        "cycles": ("int", 1, "gait cycles per clip")}},
    "strike": {"loop": False, "params": {
        "chains": ("chains", None, "chains that strike (default: the side chains, else the trunk)"),
        "windup": ("number", 25.0, "wind-up backwards, degrees"),
        "reach": ("number", 55.0, "strike forwards, degrees"),
        "peak": ("number", 0.45, "share of the clip where the strike lands"),
        "lunge": ("number", 0.06, "forward lunge of the body, body heights"),
        "trunk": ("number", 10.0, "forward bend of the trunk at the strike, degrees")}},
    "aim": {"loop": True, "params": {
        "chains": ("chains", None, "chains raised (default: the holding chains, else the side chains)"),
        "pitch": ("number", 75.0, "raise forward, degrees"),
        "trunk": ("number", 0.0, "constant forward bend of the trunk, degrees"),
        "breathe": ("number", 1.0, "trunk breathing, degrees")}},
    "flinch": {"loop": False, "params": {
        "chains": ("chains", None, "chains thrown back (default: the trunk)"),
        "amplitude": ("number", 14.0, "backward bend, degrees"),
        "peak": ("number", 0.25, "share of the clip where the hit is strongest"),
        "push": ("number", 0.03, "push back of the body, body heights"),
        "arms": ("number", 10.0, "fling of the side chains, degrees")}},
    "collapse": {"loop": False, "params": {
        "fall": ("choice:back|front|left|right", "back", "direction of the fall"),
        "tilt": ("number", 80.0, "tilt of the whole body, degrees"),
        "lie": ("number", 0.06, "height of the lowest bone above the ground at the end, body heights"),
        "limp": ("number", 25.0, "curl of the limbs, degrees"),
        "end": ("number", 0.75, "share of the clip the fall takes (then held)")}},
}
# The keys of a motions entry (section 13 of the design) and its sources. Only SOURCES_MADE
# are made in this phase; the others are refused by the order check until they exist.
MOTION_KEYS = ("name", "source", "kind", "settings", "library", "prompt", "file", "frames", "fps", "loop", "holds",
               "contacts", "note")
SOURCES = ("procedural", "library", "text", "bvh")
SOURCES_MADE = ("procedural", "library")
SOURCE_KEYS = {"procedural": ("kind",), "library": ("library",), "text": ("prompt",), "bvh": ("file",)}
PREBEND = 12.0  # degrees each leg joint starts flexed before the gait's inverse kinematics
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


# ---- the order's clips ---------------------------------------------------------------------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _frames_ok(v):
    return isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 600


def check_motions(motions, plan, library=None):
    """Problems of an order's motions for a valid body plan (empty: valid). library: the
    forge-creature-clips document of the piece's inputs.clips, when it has one (its clips are
    checked by name)."""
    if not isinstance(motions, list):
        return ["motions must be a list of entries"]
    problems, names = [], set()
    roles = [r for r, _ in cb.plan_roles(plan)]
    chains = [c["id"] for c in plan["chains"]]
    for i, m in enumerate(motions):
        where = "motions[%d]" % i
        before = len(problems)
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
            problems.append("%s: source must be one of %s, not %r" % (where, ", ".join(SOURCES), source))
            continue
        if source not in SOURCES_MADE:
            problems.append("%s: source %s is not available yet (available: %s)" % (
                where, source, ", ".join(SOURCES_MADE)))
            continue
        for other, keys in SOURCE_KEYS.items():
            for k in keys:
                if other != source and k in m:
                    problems.append("%s: %s belongs to source %s" % (where, k, other))
        if "frames" in m and not _frames_ok(m["frames"]):
            problems.append("%s: frames must be a whole number from 2 to 600" % where)
        if "fps" in m and not (_num(m["fps"]) and 1 <= m["fps"] <= 120):
            problems.append("%s: fps must be a number from 1 to 120" % where)
        if "loop" in m and not isinstance(m["loop"], bool):
            problems.append("%s: loop must be true or false" % where)
        if "holds" in m and m["holds"] is not None and not (isinstance(m["holds"], str) and m["holds"]):
            problems.append("%s: holds must be a held-item class (text) or null" % where)
        settings = m.get("settings", {})
        if not isinstance(settings, dict):
            problems.append("%s: settings must be an object" % where)
            settings = {}
        if "contacts" in m:
            c = m["contacts"]
            ok = isinstance(c, dict) and all(r in roles and (v == "all" or (isinstance(v, list) and all(
                isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v))) for r, v in c.items())
            if not ok:
                problems.append("%s: contacts must be {role: [frame, ...] or \"all\"} with roles of the plan" % where)
            elif source == "procedural":
                problems.append("%s: a procedural clip makes its own contacts" % where)
        if source == "library":
            lib = m.get("library", {})
            if not isinstance(lib, dict) or any(k not in ("file", "clip") for k in lib):
                problems.append("%s: library must be {\"file\": path, \"clip\": name}" % where)
                continue
            if settings:
                problems.append("%s: a library clip has no settings" % where)
            clip = lib.get("clip", name)
            if library is not None and "file" not in lib and clip not in (library.get("anims") or {}):
                problems.append("%s: the library (inputs.clips) has no clip %s" % (where, clip))
            continue
        if not _frames_ok(m.get("frames")):
            if "frames" not in m:
                problems.append("%s: frames must be a whole number from 2 to 600" % where)
            continue
        kind = m.get("kind")
        if kind not in KINDS:
            problems.append("%s: kind must be one of %s, not %r" % (where, ", ".join(KINDS), kind))
            continue
        decl = KINDS[kind]["params"]
        for k, v in settings.items():
            if k not in decl:
                problems.append("%s: unknown setting %r of kind %s (settings: %s)" % (where, k, kind, ", ".join(decl)))
                continue
            why = _param_problem(decl[k][0], v, chains, roles)
            if why:
                problems.append("%s: setting %s must be %s, not %r" % (where, k, why, v))
        if len(problems) == before:
            p = params_of(m)
            if kind == "gait" and p["phases"] is not None:
                n = len(default_roles(plan, kind, p))
                if len(p["phases"]) != n:
                    problems.append("%s: phases has %d values for %d legs" % (where, len(p["phases"]), n))
            if kind in ("undulate", "gait") and not default_roles(plan, kind, p):
                problems.append("%s: no chain to move (give %s)" % (where, "legs" if kind == "gait" else "chains"))
    return problems


def _param_problem(kind, v, chains, roles):
    if kind == "number":
        return None if _num(v) else "a number"
    if kind == "number?":
        return None if v is None or (_num(v) and v > 0) else "a number above 0 or null"
    if kind == "int":
        return None if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 16 else "a whole number from 1 to 16"
    if kind == "numbers":
        return None if v is None or (isinstance(v, list) and v and all(_num(x) for x in v)) else "a list of numbers"
    if kind == "chains":
        if v is None:
            return None
        ok = isinstance(v, list) and v and all(isinstance(x, str) and (x in chains or x in roles) for x in v)
        return None if ok else "a list of chains (%s) or roles" % ", ".join(chains)
    if kind.startswith("choice:"):
        opts = kind[7:].split("|")
        return None if v in opts else "one of " + ", ".join(opts)
    return None


def params_of(clip):
    """A procedural entry's settings with the kind's defaults."""
    decl = KINDS[clip["kind"]]["params"]
    given = clip.get("settings") or {}
    return {k: given.get(k, d[1]) for k, d in decl.items()}


def expand_roles(plan, names):
    out = []
    for n in names:
        for r in cb.chain_roles(plan, n):
            if r not in out:
                out.append(r)
    return out


def role_groups(plan):
    """{"trunk", "side", "legs", "back", "other"}: the plan's roles by what they are."""
    slots = cb.plan_roles(plan)
    trunk = []
    for r, s in slots:
        if s["side"] is None and s["toward"] in ("up", "front") and (s["parent"] == "root" or s["parent"] in trunk):
            trunk.append(r)
    side = [r for r, s in slots if s["toward"] == "side"]
    legs = [r for r, s in slots if s["toward"] == "down"]
    back = [r for r, s in slots if s["toward"] == "back" and s["side"] is None]
    other = [r for r, _ in slots if r not in trunk]
    return {"trunk": trunk, "side": side, "legs": legs, "back": back, "other": other}


def default_roles(plan, kind, p):
    """The roles a clip moves when its chains parameter is not given."""
    g = role_groups(plan)
    given = p.get("legs") if kind == "gait" else p.get("chains")
    if given:
        return expand_roles(plan, given)
    if kind == "gait":
        return g["legs"]
    if kind == "undulate":
        return g["back"]
    if kind == "strike":
        return g["side"] or g["trunk"]
    if kind == "aim":
        holds = list((plan.get("holds") or {}).values())
        return holds or g["side"]
    return g["trunk"]


# ---- rotations -----------------------------------------------------------------------------

def rot(axis, deg):
    """Rotation matrix about a unit axis (right-hand rule), degrees."""
    a = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(a)
    if n < 1e-12 or deg == 0.0:
        return np.eye(3)
    x, y, z = a / n
    t = math.radians(deg)
    c, s, C = math.cos(t), math.sin(t), 1.0 - math.cos(t)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def between(u, v, limit=None):
    """The rotation turning direction u toward v (at most `limit` radians)."""
    u = u / np.linalg.norm(u)
    v = v / np.linalg.norm(v)
    axis = np.cross(u, v)
    s = np.linalg.norm(axis)
    c = float(np.clip(np.dot(u, v), -1.0, 1.0))
    ang = math.atan2(s, c)
    if s < 1e-9:
        return np.eye(3)
    if limit is not None:
        ang = min(ang, limit)
    return rot(axis / s, math.degrees(ang))


def quaternion(m):
    """(w, x, y, z) of a rotation matrix."""
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = ((m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s)
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s)
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s)
    n = math.sqrt(sum(x * x for x in q))
    return [x / n for x in q]


def pitch_axis(d):
    """The axis about which +angle turns direction d toward the front (toward the ground
    for a direction along the front-back line)."""
    g = FRONT if abs(float(np.dot(d, FRONT))) < 0.8 else DOWN
    a = np.cross(d, g)
    n = np.linalg.norm(a)
    return a / n if n > 1e-6 else LEFT.copy()


def lateral_axis(d):
    """The axis about which +angle turns direction d toward the creature's left."""
    g = LEFT if abs(float(np.dot(d, LEFT))) < 0.8 else FRONT
    a = np.cross(d, g)
    n = np.linalg.norm(a)
    return a / n if n > 1e-6 else UP.copy()


def smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


# ---- the skeleton --------------------------------------------------------------------------

class Rig:
    """A skeleton at rest with forward kinematics. Rotations R[name] are 3x3 in armature
    space about the bone's rest head; offset moves the root."""

    def __init__(self, tree, roles, root_path, height, joints=None):
        self.tree = tree
        # The way each bone bends (its chain's joint bend), as a model vector: from the map's
        # "joints" (creature_bones.joint_map).
        self.bends = {}
        for role, rows in (joints or {}).items():
            side = role.rsplit("_", 1)[-1][:1] if "_" in role else None
            out = LEFT if side == "l" else (-LEFT if side == "r" else None)
            vecs = {"front": FRONT, "back": -FRONT, "up": UP, "down": DOWN,
                    "out": out, "in": None if out is None else -out}
            for row in rows:
                v = vecs.get(row.get("bend"))
                if v is not None:
                    self.bends[row["bone"]] = v
        tr = cb.Tree(tree)
        order, seen = [], set()
        for n in tree:
            stack = [n]
            while tr.parent(stack[-1]) is not None and tr.parent(stack[-1]) not in seen:
                stack.append(tr.parent(stack[-1]))
            for x in reversed(stack):
                if x not in seen:
                    seen.add(x)
                    order.append(x)
        self.order = order
        self.index = {n: i for i, n in enumerate(order)}
        self.parent = [self.index.get(tr.parent(n), -1) for n in order]
        self.head = np.array([tree[n]["head"] for n in order], dtype=np.float64)
        self.tail = np.array([tree[n]["tail"] for n in order], dtype=np.float64)
        self.roles = roles
        self.root = root_path[0] if root_path else order[0]
        self.height = float(height)

    def dir(self, name):
        i = self.index[name]
        d = self.tail[i] - self.head[i]
        n = np.linalg.norm(d)
        return d / n if n > 1e-12 else UP.copy()

    def fk(self, R, offset):
        """(rotations, translations, heads, tails) posed: x' = rot @ x + trans per bone."""
        n = len(self.order)
        rots = np.empty((n, 3, 3))
        trans = np.empty((n, 3))
        for i, name in enumerate(self.order):
            r = R.get(name)
            r = np.eye(3) if r is None else r
            p = self.parent[i]
            if p < 0:
                rp, tp = np.eye(3), np.asarray(offset, dtype=np.float64)
            else:
                rp, tp = rots[p], trans[p]
            h = self.head[i]
            rots[i] = rp @ r
            trans[i] = rp @ (h - r @ h) + tp
        heads = np.einsum("nij,nj->ni", rots, self.head) + trans
        tails = np.einsum("nij,nj->ni", rots, self.tail) + trans
        return rots, trans, heads, tails


def flex_axis(rig, b):
    """The axis about which +angle flexes bone b the way its joint bends (the plan's or the
    template's bend), else its pitch axis."""
    d = rig.dir(b)
    v = rig.bends.get(b)
    if v is not None:
        a = np.cross(d, v)
        n = np.linalg.norm(a)
        if n > 1e-6:
            return a / n
    return pitch_axis(d)


def _swing(R, rig, bones, deg, toward=None):
    """The whole chain swung about its first bone's head, its direction (first head to last
    tail) turned toward `toward` (the front) by deg: an arm raised forward, whatever its
    shoulder bone points at."""
    if not bones or deg == 0.0:
        return
    toward = FRONT if toward is None else toward
    i0, i1 = rig.index[bones[0]], rig.index[bones[-1]]
    d = rig.tail[i1] - rig.head[i0]
    d = d / max(np.linalg.norm(d), 1e-12)
    a = np.cross(d, toward)
    n = np.linalg.norm(a)
    axis = a / n if n > 1e-6 else pitch_axis(d)
    R[bones[0]] = R.get(bones[0], np.eye(3)) @ rot(axis, deg)


def _bend(R, rig, bones, deg, axis_fn, weights=None):
    """Each bone of a chain turned by its share of `deg` about its own axis."""
    if not bones or deg == 0.0:
        return
    w = weights or [1.0 / len(bones)] * len(bones)
    for b, k in zip(bones, w):
        axis = flex_axis(rig, b) if axis_fn is flex_axis else axis_fn(rig.dir(b))
        R[b] = R.get(b, np.eye(3)) @ rot(axis, deg * k)


# ---- the kinds -----------------------------------------------------------------------------

def _sway(R, rig, roles, deg, phase):
    for k, role in enumerate(roles):
        bones = rig.roles.get(role) or []
        n = len(bones)
        for i, b in enumerate(bones):
            u = (i + 1) / n
            R[b] = R.get(b, np.eye(3)) @ rot(lateral_axis(rig.dir(b)), deg * u / n * 2.0 * math.sin(phase + 0.7 * k))


def _mapped(rig, roles):
    return [r for r in roles if rig.roles.get(r)]


def make_breathe(rig, plan, p, t, ctx):
    R = {}
    w = 2 * math.pi * p["cycles"] * t
    for role in _mapped(rig, ctx["roles"]):
        _bend(R, rig, rig.roles[role], p["amplitude"] * math.sin(w), pitch_axis)
    _sway(R, rig, _mapped(rig, [r for r in ctx["groups"]["other"] if r not in ctx["roles"]]), p["sway"], w)
    return R, np.zeros(3)


def make_hover(rig, plan, p, t, ctx):
    R = {}
    w = 2 * math.pi * p["cycles"] * t
    for role in _mapped(rig, ctx["roles"]):
        _bend(R, rig, rig.roles[role], p["lean"] + p["tilt"] * math.cos(w), pitch_axis)
    _sway(R, rig, _mapped(rig, [r for r in ctx["groups"]["other"] if r not in ctx["roles"]]), p["sway"], w)
    return R, UP * (p["bob"] * rig.height * math.sin(w))


def make_undulate(rig, plan, p, t, ctx):
    R = {}
    w = 2 * math.pi * p["cycles"] * t
    axis_fn = lateral_axis if p["axis"] == "lateral" else pitch_axis
    for role in _mapped(rig, ctx["roles"]):
        bones = rig.roles[role]
        n = len(bones)
        for i, b in enumerate(bones):
            u = i / (n - 1) if n > 1 else 0.0
            amp = p["amplitude"] * (p["base"] + (1.0 - p["base"]) * u)
            ang = amp * math.sin(w - 2 * math.pi * p["waves"] * u)
            R[b] = R.get(b, np.eye(3)) @ rot(axis_fn(rig.dir(b)), ang)
    for role in _mapped(rig, [r for r in ctx["groups"]["trunk"] if r not in ctx["roles"]]):
        _bend(R, rig, rig.roles[role], -p["upper"] * math.sin(w), lateral_axis)
    for role in _mapped(rig, [r for r in ctx["groups"]["side"] if r not in ctx["roles"]]):
        sign = 1.0 if role.split("_")[-1].startswith("l") else -1.0
        _swing(R, rig, rig.roles[role], p["arms"] * sign * math.sin(w))
    return R, np.zeros(3)


def _ease_curve(t, peak, a, b):
    """0 -> a (wind-up) -> b at peak -> 0 at the end."""
    w = peak * 0.6
    if t < w:
        return a * smooth(t / w)
    if t < peak:
        return a + (b - a) * smooth((t - w) / max(peak - w, 1e-6))
    return b * (1.0 - smooth((t - peak) / max(1.0 - peak, 1e-6)))


def _bump(t, peak):
    if t < peak:
        return smooth(t / max(peak, 1e-6))
    return 1.0 - smooth((t - peak) / max(1.0 - peak, 1e-6))


def make_strike(rig, plan, p, t, ctx):
    R = {}
    ang = _ease_curve(t, p["peak"], -p["windup"], p["reach"])
    side = set(ctx["groups"]["side"])
    for role in _mapped(rig, ctx["roles"]):
        if role in side:
            _swing(R, rig, rig.roles[role], ang)
        else:
            _bend(R, rig, rig.roles[role], ang, pitch_axis)
    b = _bump(t, p["peak"])
    for role in _mapped(rig, [r for r in ctx["groups"]["trunk"] if r not in ctx["roles"]]):
        _bend(R, rig, rig.roles[role], p["trunk"] * b, pitch_axis)
    return R, FRONT * (p["lunge"] * rig.height * b)


def make_aim(rig, plan, p, t, ctx):
    R = {}
    for role in _mapped(rig, ctx["roles"]):
        _swing(R, rig, rig.roles[role], p["pitch"])
    w = 2 * math.pi * t
    for role in _mapped(rig, [r for r in ctx["groups"]["trunk"] if r not in ctx["roles"]]):
        _bend(R, rig, rig.roles[role], p["trunk"] + p["breathe"] * math.sin(w), pitch_axis)
    return R, np.zeros(3)


def make_flinch(rig, plan, p, t, ctx):
    R = {}
    b = _bump(t, p["peak"])
    for role in _mapped(rig, ctx["roles"]):
        _bend(R, rig, rig.roles[role], -p["amplitude"] * b, pitch_axis)
    for role in _mapped(rig, [r for r in ctx["groups"]["side"] if r not in ctx["roles"]]):
        _swing(R, rig, rig.roles[role], -p["arms"] * b)
    return R, -FRONT * (p["push"] * rig.height * b)


def make_collapse(rig, plan, p, t, ctx):
    e = math.sin(0.5 * math.pi * min(1.0, t / max(p["end"], 1e-6))) ** 2
    R = {}
    axis = np.cross(UP, FALLS[p["fall"]])
    R[rig.root] = rot(axis, p["tilt"] * e)
    for role in _mapped(rig, ctx["groups"]["other"]):
        _bend(R, rig, rig.roles[role], p["limp"] * e, flex_axis)
    # The drop that puts the lowest bone `lie` body heights above the ground at the end.
    if "drop" not in ctx:
        Rf = {rig.root: rot(axis, p["tilt"])}
        for role in _mapped(rig, ctx["groups"]["other"]):
            _bend(Rf, rig, rig.roles[role], p["limp"], flex_axis)
        _, _, heads, tails = rig.fk(Rf, np.zeros(3))
        low = min(float(heads[:, 2].min()), float(tails[:, 2].min()))
        ctx["drop"] = low - p["lie"] * rig.height
    return R, UP * (-ctx["drop"] * e)


# ---- gait ----------------------------------------------------------------------------------

def _leg(rig, role):
    """A leg's parts: the two bones the inverse kinematics bends (hip and knee), the bones
    below them that keep their rest orientation in the world (a long digitigrade foot, then
    the toes), and the point that reaches the target (the head of the last bone, or the tail
    of a two-bone leg)."""
    bones = rig.roles[role]
    if len(bones) == 1:
        return {"role": role, "ik": bones, "rigid": [], "foot": None, "effector": ("tail", bones[0])}
    if len(bones) == 2:
        return {"role": role, "ik": bones, "rigid": [], "foot": None, "effector": ("tail", bones[1])}
    return {"role": role, "ik": bones[:2], "rigid": bones[2:-1], "foot": bones[-1], "effector": ("head", bones[-1])}


def _point(rig, kind, b, rots, trans):
    i = rig.index[b]
    if kind == "head":
        p = rig.parent[i]
        return rots[p] @ rig.head[i] + trans[p]
    return rots[i] @ rig.tail[i] + trans[i]


def _effector(rig, leg, rots, trans):
    return _point(rig, leg["effector"][0], leg["effector"][1], rots, trans)


def _two_bone(rig, R, offset, a, b, end_of, target):
    """Exact two-bone inverse kinematics (law of cosines), the knee kept on the side it bends
    to now (a straight leg: the side its joint bends)."""
    ia, ib = rig.index[a], rig.index[b]
    rots, trans, heads, _ = rig.fk(R, offset)
    hip, knee = heads[ia], heads[ib]
    end = end_of(rots, trans)
    la, lb = np.linalg.norm(knee - hip), np.linalg.norm(end - knee)
    to = target - hip
    d = float(np.clip(np.linalg.norm(to), abs(la - lb) + 1e-6, la + lb - 1e-6))
    u = to / max(np.linalg.norm(to), 1e-12)
    side = (knee - hip) - np.dot(knee - hip, u) * u
    if np.linalg.norm(side) < 1e-6 * rig.height:
        side = np.cross(rots[ib] @ flex_axis(rig, b), u)
        if np.linalg.norm(side) < 1e-9:
            side = np.cross(LEFT, u)
    w = side / np.linalg.norm(side)
    alpha = math.acos(float(np.clip((la * la + d * d - lb * lb) / (2 * la * d), -1.0, 1.0)))
    new_knee = hip + la * (math.cos(alpha) * u + math.sin(alpha) * w)
    pa = rig.parent[ia]
    rp = rots[pa] if pa >= 0 else np.eye(3)
    R[a] = rp.T @ between(knee - hip, new_knee - hip) @ rp @ R.get(a, np.eye(3))
    rots, trans, heads, _ = rig.fk(R, offset)
    knee, end = heads[ib], end_of(rots, trans)
    R[b] = rots[ia].T @ between(end - knee, target - knee) @ rots[ia] @ R.get(b, np.eye(3))


def solve_leg(rig, R, offset, leg, target):
    """Inverse kinematics of one leg toward a target: the bones below the knee keep their rest
    orientation in the world (so a reverse ankle stays a reverse ankle), the hip and knee are
    solved exactly. Returns the distance left (a target out of reach)."""
    if len(leg["ik"]) == 1:
        b = leg["ik"][0]
        i = rig.index[b]
        rots, trans, heads, _ = rig.fk(R, offset)
        p = rig.parent[i]
        rp = rots[p] if p >= 0 else np.eye(3)
        R[b] = rp.T @ between(_effector(rig, leg, rots, trans) - heads[i], target - heads[i]) @ rp @ R.get(b, np.eye(3))
    else:
        below = leg["rigid"] + ([leg["foot"]] if leg["foot"] else [])
        # The rigid bones' rest length from the knee bone's end to the effector.
        if leg["rigid"]:
            i0 = rig.index[leg["rigid"][0]]
            reach = _effector(rig, leg, np.repeat(np.eye(3)[None], len(rig.order), 0),
                              np.zeros((len(rig.order), 3))) - rig.head[i0]
            a, b = leg["ik"]

            def end_of(rots, trans):
                return _point(rig, "head", leg["rigid"][0], rots, trans)
            _two_bone(rig, R, offset, a, b, end_of, target - reach)
        else:
            _two_bone(rig, R, offset, leg["ik"][0], leg["ik"][1],
                      lambda rots, trans: _effector(rig, leg, rots, trans), target)
        for b in below:
            rots, trans, _, _ = rig.fk(R, offset)
            p = rig.parent[rig.index[b]]
            R[b] = rots[p].T  # keeps its rest orientation in the world
    rots, trans, _, _ = rig.fk(R, offset)
    return float(np.linalg.norm(target - _effector(rig, leg, rots, trans)))


def _phase_of(role, slots, plan):
    """The default phase of a leg: left and right alternate, and so do consecutive legs
    along a side (consecutive leg chains of the plan, then a chain's pairs): a biped walks, a
    four-legged body trots (diagonal legs together), six legs go in two tripods."""
    s = slots.get(role) or {}
    downs = [c["id"] for c in plan["chains"] if c.get("toward") == "down"]
    k = downs.index(s["chain"]) if s.get("chain") in downs else 0
    if s.get("side") is None:
        return 0.5 * (k % 2)
    return 0.5 * ((k + s["index"] + (0 if s["side"] == "l" else 1)) % 2)


def make_gait_clip(rig, plan, clip, p, ctx, n, loop):
    legs = [_leg(rig, r) for r in _mapped(rig, ctx["roles"])]
    if not legs:
        raise ValueError("gait %s: no leg is mapped" % clip["name"])
    slots = dict(cb.plan_roles(plan))
    _, trans0, heads0, tails0 = rig.fk({}, np.zeros(3))
    rots0 = np.repeat(np.eye(3)[None], len(rig.order), 0)
    lengths = []
    for leg in legs:
        leg["rest"] = _effector(rig, leg, rots0, trans0)
        lengths.append(sum(float(np.linalg.norm(rig.tail[rig.index[b]] - rig.head[rig.index[b]])) for b in leg["ik"]))
    L = sum(lengths) / len(lengths)
    H = rig.height
    stride = p["stride"] * H if p["stride"] else 0.5 * L
    lift = p["lift"] * H if p["lift"] else 0.18 * L
    duty = p["duty"] if p["duty"] else (0.6 if len(legs) <= 2 else 0.65)
    phases = p["phases"] or [_phase_of(leg["role"], slots, plan) for leg in legs]
    steps = max(1, len(set(round(x % 1.0, 3) for x in phases)))
    crouch = L - math.sqrt(max(L * L - (stride / 2.0) ** 2, 0.0)) + 0.01 * L
    side = [r for r in _mapped(rig, ctx["groups"]["side"]) if r not in ctx["roles"]]
    trunk = [r for r in _mapped(rig, ctx["groups"]["trunk"]) if r not in ctx["roles"]]
    poses, offsets, stance = [], [], {leg["role"]: [] for leg in legs}
    short = 0.0
    # The legs start a little flexed the way their joints bend (a knee forward unless the
    # plan says otherwise), so the solver keeps each joint on its own side.
    R_prev = {}
    for leg in legs:
        for b in leg["ik"][1:]:
            R_prev[b] = rot(flex_axis(rig, b), PREBEND) if b in rig.bends else rot(pitch_axis(rig.dir(b)), -PREBEND)
    for i in range(n):
        t = i / n if loop else i / max(n - 1, 1)
        w = 2 * math.pi * p["cycles"] * t
        R = {b: m.copy() for b, m in R_prev.items() if any(b in leg["ik"] for leg in legs)}
        for role in trunk:
            _bend(R, rig, rig.roles[role], p["sway"] * math.sin(w), lateral_axis)
        for role in side:
            sign = 1.0 if role.split("_")[-1].startswith("l") else -1.0
            _swing(R, rig, rig.roles[role], p["arms"] * sign * math.sin(w))
        bob = p["bob"] * H * (0.5 - 0.5 * math.cos(steps * w))
        offset = UP * (-crouch - bob)
        for k, leg in enumerate(legs):
            ph = (p["cycles"] * t + phases[k]) % 1.0
            if ph < duty:
                x, z = stride * (0.5 - ph / duty), 0.0
                stance[leg["role"]].append(i)
            else:
                s = (ph - duty) / (1.0 - duty)
                x, z = stride * (-0.5 + smooth(s)), lift * math.sin(math.pi * s)
            target = leg["rest"] + FRONT * x + UP * z
            short = max(short, solve_leg(rig, R, offset, leg, target))
        R_prev = R
        poses.append(R)
        offsets.append(offset)
    travel = p["cycles"] * stride / duty
    extra = {"stance": stance, "travel": [0.0, round(travel / H, 5)], "reach_shortfall_m": round(short, 5),
             "auto": {"stride": round(stride / H, 5), "lift": round(lift / H, 5), "duty": duty,
                      "phases": [round(x, 4) for x in phases], "crouch": round(crouch / H, 5)}}
    return poses, offsets, extra


MAKERS = {"breathe": make_breathe, "hover": make_hover, "undulate": make_undulate, "strike": make_strike,
          "aim": make_aim, "flinch": make_flinch, "collapse": make_collapse}


def generate(rig, plan, clip):
    """One clip on a rig: {"name", "kind", "frames", "fps", "loop", "seconds", "root",
    "rotations" (per frame {bone: [w, x, y, z]}, armature space), "offsets" (per frame the
    root's move, model units), "stance", "travel", "reach_shortfall_m", "params"}."""
    kind = clip["kind"]
    p = params_of(clip)
    n = int(clip["frames"])
    loop = bool(clip.get("loop", KINDS[kind]["loop"]))
    fps = float(clip.get("fps", 24))
    ctx = {"groups": role_groups(plan), "roles": default_roles(plan, kind, p)}
    extra = {"stance": None, "travel": None, "reach_shortfall_m": 0.0, "auto": {}}
    if kind == "gait":
        poses, offsets, extra = make_gait_clip(rig, plan, clip, p, ctx, n, loop)
    else:
        poses, offsets = [], []
        for i in range(n):
            t = i / n if loop else i / max(n - 1, 1)
            R, off = MAKERS[kind](rig, plan, p, t, ctx)
            poses.append(R)
            offsets.append(off)
        if kind == "undulate" and p["travel"]:
            extra["travel"] = [0.0, float(p["travel"])]
        if "drop" in ctx:
            extra["auto"] = {"drop": round(ctx["drop"] / rig.height, 5)}
    rotations = [{b: quaternion(m) for b, m in R.items()} for R in poses]
    params = dict(p)
    params.update(extra["auto"])
    params["roles"] = ctx["roles"]
    return {"name": clip["name"], "kind": kind, "frames": n, "fps": fps, "loop": loop, "seconds": n / fps,
            "root": rig.root, "rotations": rotations, "offsets": [[float(x) for x in o] for o in offsets],
            "stance": extra["stance"], "travel": extra["travel"], "reach_shortfall_m": extra["reach_shortfall_m"],
            "params": params}


def posed_points(rig, clip_out, frame):
    """(heads, tails) of every bone at one frame of a generated clip."""
    R = {b: _matrix(q) for b, q in clip_out["rotations"][frame].items()}
    _, _, heads, tails = rig.fk(R, clip_out["offsets"][frame])
    return heads, tails


def _matrix(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# ---- clips in role space: the canonical form a library can hand over -------------------------

CLIPS_FORMAT = "forge-creature-clips"
_P = np.diag([1.0, -1.0, 1.0])  # model axes <-> body axes [side, forward, up]


def slerp(q0, q1, s):
    q0, q1 = np.asarray(q0, dtype=np.float64), np.asarray(q1, dtype=np.float64)
    d = float(np.dot(q0, q1))
    if d < 0.0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + s * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - s) * th) * q0 + math.sin(s * th) * q1) / math.sin(th)


def _tail_positions(rig, bones):
    """Where each bone of a chain ends along it, 0 to 1 by length."""
    lens = [float(np.linalg.norm(rig.tail[rig.index[b]] - rig.head[rig.index[b]])) for b in bones]
    total = sum(lens) or 1.0
    out, acc = [], 0.0
    for x in lens:
        acc += x
        out.append(round(acc / total, 6))
    return out


def _body_q(m):
    return [round(x, 7) for x in quaternion(_P @ m @ _P)]


def _model_m(q):
    return _P @ _matrix(q) @ _P


def to_role_space(rig, clip_out, source="procedural"):
    """A generated clip in role space: per frame the root's move (body heights, [side,
    forward, up]) and turn, and per role the chain's accumulated turn at the end of each of
    its bones ([position along the chain 0 to 1, quaternion], body axes). Another skeleton
    with the same roles plays it whatever its bone counts (from_role_space)."""
    frames = []
    for rots, off in zip(clip_out["rotations"], clip_out["offsets"]):
        R = {b: _matrix(q) for b, q in rots.items()}
        roles = {}
        for role, bones in rig.roles.items():
            acc, rows = np.eye(3), []
            for b, u in zip(bones, _tail_positions(rig, bones)):
                acc = acc @ R.get(b, np.eye(3))
                rows.append([u, _body_q(acc)])
            roles[role] = rows
        o = _P @ np.asarray(off, dtype=np.float64) / rig.height
        frames.append({"root": [round(float(x), 7) for x in o],
                       "root_turn": _body_q(R.get(rig.root, np.eye(3))), "roles": roles})
    return {"kind": clip_out["kind"], "fps": clip_out["fps"], "loop": clip_out["loop"], "frames": frames,
            "stance": clip_out.get("stance"), "travel": clip_out.get("travel"), "source": source}


def library(rig, clips, source="procedural"):
    """The forge-creature-clips document of generated clips."""
    return {"format": CLIPS_FORMAT, "version": 1,
            "axes": "body: [side, forward, up], side + is the creature's left; rotations relative to the rest "
                    "pose, about each bone's head; root moves in body heights",
            "roles": sorted(rig.roles), "anims": {c["name"]: to_role_space(rig, c, source) for c in clips}}


def _at(rows, u):
    """The accumulated turn of a chain at position u (slerp between its samples)."""
    if not rows:
        return np.array([1.0, 0.0, 0.0, 0.0])
    pts = [(0.0, [1.0, 0.0, 0.0, 0.0])] + [(float(a), q) for a, q in rows]
    for (u0, q0), (u1, q1) in zip(pts, pts[1:]):
        if u <= u1 + 1e-9:
            return slerp(q0, q1, 0.0 if u1 - u0 < 1e-9 else (u - u0) / (u1 - u0))
    return np.asarray(pts[-1][1], dtype=np.float64)


def from_role_space(rig, name, anim):
    """A role-space clip on this rig, in the form generate() returns: each mapped chain's
    bones take the accumulated turns at their own positions along the chain; roles the rig
    lacks are left out."""
    rotations, offsets = [], []
    for fr in anim["frames"]:
        R = {rig.root: _model_m(fr.get("root_turn", [1, 0, 0, 0]))}
        for role, rows in (fr.get("roles") or {}).items():
            bones = rig.roles.get(role)
            if not bones:
                continue
            prev = np.eye(3)
            for b, u in zip(bones, _tail_positions(rig, bones)):
                acc = _model_m(_at(rows, u))
                R[b] = prev.T @ acc if b not in R else R[b] @ prev.T @ acc
                prev = acc
        rotations.append({b: quaternion(m) for b, m in R.items()})
        offsets.append([float(x) for x in _P @ (np.asarray(fr.get("root", [0, 0, 0]), dtype=np.float64) * rig.height)])
    n = len(rotations)
    fps = float(anim.get("fps", 24))
    return {"name": name, "kind": anim.get("kind"), "frames": n, "fps": fps, "loop": bool(anim.get("loop", False)),
            "seconds": n / fps, "root": rig.root, "rotations": rotations, "offsets": offsets,
            "stance": anim.get("stance"), "travel": anim.get("travel"), "reach_shortfall_m": 0.0,
            "params": {"source": anim.get("source", "library")}}


def check_library(doc):
    """Problems of a forge-creature-clips document (a library for the motions' library source)."""
    if not isinstance(doc, dict) or doc.get("format") != CLIPS_FORMAT or not isinstance(doc.get("anims"), dict):
        return ["the library must be a %s document with \"anims\"" % CLIPS_FORMAT]
    problems = []
    for name, a in doc["anims"].items():
        if not isinstance(a, dict) or not isinstance(a.get("frames"), list) or len(a["frames"]) < 1:
            problems.append("the library's clip %s has no frames" % name)
    return problems


def resample(clip_out, n):
    """A clip resampled to n frames (rotations by slerp, root moves linearly; a loop's last
    frame leads back to its first); contacts follow by time."""
    m = clip_out["frames"]
    if n == m:
        return clip_out
    loop = clip_out["loop"]
    rots, offs = [], []
    for i in range(n):
        x = (i * m / n) if loop else (i * (m - 1) / max(n - 1, 1))
        a = int(math.floor(x)) % m
        b = (a + 1) % m if loop else min(a + 1, m - 1)
        s = x - math.floor(x)
        ra, rb = clip_out["rotations"][a], clip_out["rotations"][b]
        frame = {}
        for bone in set(ra) | set(rb):
            frame[bone] = [float(v) for v in slerp(ra.get(bone, [1, 0, 0, 0]), rb.get(bone, [1, 0, 0, 0]), s)]
        rots.append(frame)
        oa, ob = np.asarray(clip_out["offsets"][a]), np.asarray(clip_out["offsets"][b])
        offs.append([float(v) for v in oa + s * (ob - oa)])
    stance = None
    if clip_out.get("stance"):
        stance = {}
        for role, frames in clip_out["stance"].items():
            if frames == "all":
                stance[role] = "all"
                continue
            keep = set(frames)
            stance[role] = [i for i in range(n)
                            if int(round((i * m / n) if loop else (i * (m - 1) / max(n - 1, 1)))) % m in keep]
    out = dict(clip_out, frames=n, rotations=rots, offsets=offs, stance=stance,
               seconds=n / clip_out["fps"])
    return out


def library_clip(rig, entry, libraries, default_library=None):
    """A motions entry of source library on this rig: the clip of its library (entry
    "library": {"file", "clip"}; the file defaults to the piece's inputs.clips, the clip to the
    entry's name) in role space, resampled to the entry's frames, with its fps, loop, contacts
    and holds when the entry gives them. libraries: {path: forge-creature-clips document}."""
    lib = entry.get("library") or {}
    path = lib.get("file") or default_library
    doc = libraries.get(path)
    if doc is None:
        raise ValueError("motion %s: library %s is not loaded" % (entry["name"], path))
    name = lib.get("clip", entry["name"])
    anim = (doc.get("anims") or {}).get(name)
    if anim is None:
        raise ValueError("motion %s: the library %s has no clip %s" % (entry["name"], path, name))
    if "loop" in entry:
        anim = dict(anim, loop=entry["loop"])
    if "fps" in entry:
        anim = dict(anim, fps=entry["fps"])
    out = from_role_space(rig, entry["name"], anim)
    if entry.get("frames"):
        out = resample(out, int(entry["frames"]))
    if "contacts" in entry:
        out["stance"] = entry["contacts"]
    out["params"] = {"source": "library", "library": path, "clip": name}
    return out


def make_clip(rig, plan, entry, libraries=None, default_library=None):
    """One motions entry on this rig, from its source."""
    if entry["source"] == "library":
        return library_clip(rig, entry, libraries or {}, default_library)
    if entry["source"] == "procedural":
        out = generate(rig, plan, entry)
        out["params"]["source"] = "procedural"
        return out
    raise ValueError("motion %s: source %s is not available yet" % (entry["name"], entry["source"]))
