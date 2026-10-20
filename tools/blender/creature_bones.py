"""The roles of a creature rig's bones, found from the skeleton's tree and the order's body
plan, with no human work per variant (the rig tools predict a different skeleton for every
variant).

A body plan (the order's "body"):

  {"chains": [{"id": "spine", "from": "root", "toward": "up"},
              {"id": "neck", "from": "spine", "toward": "up", "optional": true},
              {"id": "tail", "from": "root", "toward": "back"},
              {"id": "arm", "from": "spine", "toward": "side", "pairs": 1},
              {"id": "leg", "from": "root", "toward": "down", "pairs": 1}],
   "holds": {"right": "arm_r0"}}

Each chain hangs from the root (the first branching bone of the skeleton) or from a chain
listed before it, and points "toward" up, down, front, back or side. A chain with "pairs"
n is 2n chains, one per side and index: its roles are <id>_l<i> and <id>_r<i>, index 0 the
highest (then the frontmost). A chain hanging from a paired chain is paired the same way
(a hand on each arm). "optional": true lets the map go without it; "min_pairs" k lets a
paired chain go without its pairs from index k on (a four-armed body on a rig predicted
with two arms), and a role hanging from an optional role is optional too. "holds" names
the roles that carry held items (the exporter adds an item bone at their tip).

The proposal (propose()):
- the root path: from the skeleton's root down its single children to the first bone that
  branches (the hub); the "root" role is that path;
- candidates of a chain: runs of single children starting at a child of its parent's bones
  (the root path for "root"); a short run that ends in a branch (a connector, under
  CONNECTOR of the body size) also offers its children's runs;
- each candidate is scored by how its direction (from its first head to its last tail)
  matches the chain's "toward" (a side chain also by side; a single chain runs along the
  middle, unless it points its way and has a mirror twin), and by its length; the best
  pairs (chain, candidate) are taken first; a paired chain's left and right are split by
  the side of the body;
- a chain with no candidate that hangs at the end of a single chain of 2 bones or more
  takes the last third of that chain (a neck that the skeleton grew as part of its spine);
- a required single chain still missing takes the nearest remaining run of its parent by
  direction (a serpent's tail the rig predicted as legs pointing down), if it is a limb's
  length and runs along the middle or has a mirror twin;
- a single chain whose mirror run hangs unmapped beside it gets that run as its twin
  ("twins": {role: [bones]}): the exporter turns the twin's bones as the role's;
- every chain is checked (validate()): a required chain is missing, a role's bones do not
  form a parent-child chain, a bone has two roles, a chain does not hang below its parent,
  a left or right chain is on the wrong side, a pair differs in length by more than
  SIDE_TOLERANCE.

A map is {"roles": {role: [bone, ...]}} (bones from the base to the tip); bone_map.json
holds it with its validation (format forge-creature-map). choose() applies the precedence:
a map given by the order, then a bone_map.json marked "manual": true, then the proposal.

skeleton_facing() tells a skeleton predicted a quarter turn off (its mirrored legs and arms
lying front to back) and which way its feet point; the exporter turns it before the map.

This module does not import bpy: it works over a tree of points,
{bone: {"parent": name or None, "head": (x, y, z), "tail": (x, y, z)}} in the exporter's
model axes (z up, front -Y, left +X), and tree_from_armature() builds that tree.
"""

import json
import math
import os
import re

FORMAT = "forge-creature-map"
UP = (0.0, 0.0, 1.0)
FRONT = (0.0, -1.0, 0.0)
LEFT = (1.0, 0.0, 0.0)
TOWARD = {"up": UP, "down": (0.0, 0.0, -1.0), "front": FRONT, "back": (0.0, 1.0, 0.0), "side": LEFT}
ID = re.compile(r"^[a-z][a-z0-9]{0,23}$")
MIN_SCORE = 0.2          # a candidate whose direction is further than ~78 degrees off is no match
CONNECTOR = 0.12         # a branching run shorter than this share of the body is a connector
SPLIT_SHARE = 1.0 / 3.0  # the share of a chain a missing child chain takes from its end
SIDE_TOLERANCE = 0.35    # largest relative length difference of the two sides of a pair
SIDE_MIN = 0.02          # a side chain's mean |x| from the hub, as a share of the body size
MIRROR_MIN_SHARE = 0.2   # a run of a mirrored pair is at least this share of the body size (no fingers)
MIRROR_TOLERANCE = 0.15  # a mirrored run lands on its pair within this share of their length
FEET_LIMIT_DEG = 45.0    # feet within this of the side axis tell a quarter-turned skeleton's front
FALLBACK_MIN_SHARE = 0.12  # a nearest remaining run taken for a missing chain is at least this long
MIDLINE = 0.08           # a single chain's mean |x| from the hub stays under this share of the body size
TWIN_SCORE = 0.7         # ... unless it points within ~45 degrees of the chain's way and has a mirror twin


# ---- vectors -------------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


# ---- the plan ------------------------------------------------------------------------------

BENDS = ("front", "back", "up", "down", "out", "in", "none")
CHAIN_KEYS = ("id", "from", "toward", "pairs", "min_pairs", "optional", "joints", "bends", "lengths", "direction",
              "note")


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def body_plan(body):
    """The plan of an order's body: its chains, or the plan its template expands into."""
    if isinstance(body, dict) and "template" in body:
        plan = expand_template(body["template"])
        if "holds" in body:
            plan["holds"] = body["holds"]
        return plan
    return body


def check_body(body):
    """Problems of an order's body: a plan ({"chains"}) or a template ({"template"})."""
    if isinstance(body, dict) and "template" in body:
        extra = sorted(k for k in body if k not in ("template", "holds", "note"))
        problems = ["body: unknown keys %s (with a template: template, holds, note)" % extra] if extra else []
        problems += check_template(body["template"])
        return problems or check_plan(body_plan(body))
    return check_plan(body)


def check_plan(plan):
    """Problems of an order's body plan (empty: valid)."""
    if not isinstance(plan, dict) or not isinstance(plan.get("chains"), list) or not plan["chains"]:
        return ["body must be {\"chains\": [chain, ...]} or {\"template\": ...} with at least one chain"]
    problems = []
    extra = sorted(k for k in plan if k not in ("chains", "holds", "note"))
    if extra:
        problems.append("body: unknown keys %s (known: chains, holds, note)" % extra)
    seen = {}
    for i, c in enumerate(plan["chains"]):
        where = "body.chains[%d]" % i
        if not isinstance(c, dict):
            problems.append("%s must be an object" % where)
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not ID.match(cid) or cid == "root":
            problems.append("%s: id must be a lower-case name of letters and digits (not root), not %r" % (where, cid))
            continue
        where = "body chain %s" % cid
        if cid in seen:
            problems.append("%s appears twice" % where)
        unknown = sorted(k for k in c if k not in CHAIN_KEYS)
        if unknown:
            problems.append("%s: unknown keys %s (known: %s)" % (where, unknown, ", ".join(CHAIN_KEYS)))
        joints = c.get("joints")
        if joints is not None and not (isinstance(joints, int) and not isinstance(joints, bool) and 1 <= joints <= 12):
            problems.append("%s: joints must be a whole number from 1 to 12, not %r" % (where, joints))
            joints = None
        for key, ok, what in (("bends", lambda v: v in BENDS, "one of " + ", ".join(BENDS)),
                              ("lengths", lambda v: _number(v) and v > 0, "a number above 0")):
            if key in c:
                v = c[key]
                if not isinstance(v, list) or not v or not all(ok(x) for x in v):
                    problems.append("%s: %s must be a list, each %s" % (where, key, what))
                elif joints is None:
                    problems.append("%s: %s needs joints (one value per joint)" % (where, key))
                elif len(v) != joints:
                    problems.append("%s: %s has %d values for %d joints" % (where, key, len(v), joints))
        if "direction" in c and not (isinstance(c["direction"], list) and len(c["direction"]) == 3
                                     and all(_number(x) for x in c["direction"])
                                     and sum(x * x for x in c["direction"]) > 1e-12):
            problems.append("%s: direction must be [side, forward, up]" % where)
        src = c.get("from", "root")
        if src != "root" and src not in seen:
            problems.append("%s: from must be root or a chain listed before it, not %r" % (where, src))
        if c.get("toward") not in TOWARD:
            problems.append("%s: toward must be one of %s, not %r" % (where, ", ".join(TOWARD), c.get("toward")))
        pairs = c.get("pairs")
        parent_pairs = seen.get(src)
        if pairs is not None and not (isinstance(pairs, int) and not isinstance(pairs, bool) and 1 <= pairs <= 4):
            problems.append("%s: pairs must be a whole number from 1 to 4, not %r" % (where, pairs))
        elif parent_pairs and pairs not in (None, parent_pairs):
            problems.append("%s: hangs from the paired chain %s, so it has its %d pair(s)" % (where, src, parent_pairs))
        if "optional" in c and not isinstance(c["optional"], bool):
            problems.append("%s: optional must be true or false" % where)
        if "min_pairs" in c:
            mp, own = c["min_pairs"], pairs if isinstance(pairs, int) and not isinstance(pairs, bool) else None
            if not own:
                problems.append("%s: min_pairs needs pairs of its own" % where)
            elif not (isinstance(mp, int) and not isinstance(mp, bool) and 0 <= mp <= own):
                problems.append("%s: min_pairs must be a whole number from 0 to its pairs (%d), not %r" % (
                    where, own, mp))
        if c.get("toward") == "side" and not (pairs or parent_pairs):
            problems.append("%s: a chain toward side needs pairs (one chain on each side)" % where)
        seen[cid] = pairs or parent_pairs
    holds = plan.get("holds")
    if holds is not None:
        roles = set(r for r, _ in plan_roles(plan)) if not problems else set()
        if not isinstance(holds, dict) or any(k not in ("right", "left") for k in holds):
            problems.append("body.holds must be {\"right\": role, \"left\": role}")
        elif roles:
            for k, r in holds.items():
                if r not in roles:
                    problems.append("body.holds.%s: %r is no role of the plan (roles: %s)" % (k, r, ", ".join(sorted(roles))))
    return problems


def plan_roles(plan):
    """[(role, slot)] of a valid plan in plan order. A slot: chain, parent (the parent role,
    "root" or a role), toward, side ("l", "r" or None), index, optional. A role is optional
    when its chain is, when its pair index is at or past the chain's min_pairs, or when the
    role it hangs from is optional."""
    out = []
    by_chain = {}
    optional = {}
    for c in plan["chains"]:
        cid, src = c["id"], c.get("from", "root")
        pairs = c.get("pairs") or (by_chain.get(src, {}).get("pairs"))
        entry = {"pairs": pairs, "roles": []}
        common = {"chain": cid, "toward": c["toward"], "optional": bool(c.get("optional")),
                  "joints": c.get("joints"), "bends": c.get("bends"), "lengths": c.get("lengths"),
                  "direction": c.get("direction")}
        if pairs:
            least = c.get("min_pairs", pairs) if c.get("pairs") else pairs
            for i in range(pairs):
                for side in ("l", "r"):
                    role = "%s_%s%d" % (cid, side, i)
                    if src == "root":
                        parent = "root"
                    elif by_chain[src]["pairs"]:
                        parent = "%s_%s%d" % (src, side, i)
                    else:
                        parent = src
                    opt = common["optional"] or i >= least or optional.get(parent, False)
                    optional[role] = opt
                    out.append((role, dict(common, parent=parent, side=side, index=i, optional=opt)))
                    entry["roles"].append(role)
        else:
            opt = common["optional"] or optional.get(src, False)
            optional[cid] = opt
            out.append((cid, dict(common, parent=src, side=None, index=None, optional=opt)))
            entry["roles"].append(cid)
        by_chain[cid] = entry
    return out


# ---- templates: a full skeleton per species, as data -----------------------------------------

ROLE = re.compile(r"^([a-z][a-z0-9]{0,23})(?:_([lr])(\d))?$")


def check_template(template):
    """Problems of a skeleton template: {"bones": [{"name", "parent", "head", "tail", "role",
    "bend", "optional"}]} in body coordinates ([side, forward, up], body heights); roles are
    "root", a chain id, or <chain>_l<i> / <chain>_r<i>; a role's bones follow each other from
    parent to child."""
    if not isinstance(template, dict) or not isinstance(template.get("bones"), list) or not template["bones"]:
        return ["body.template must be {\"bones\": [bone, ...]}"]
    problems, names, roles = [], {}, {}
    for i, b in enumerate(template["bones"]):
        where = "body.template.bones[%d]" % i
        if not isinstance(b, dict):
            problems.append("%s must be an object" % where)
            continue
        name = b.get("name")
        if not isinstance(name, str) or not name or name in names:
            problems.append("%s: name must be a new non-empty name, not %r" % (where, name))
            continue
        where = "body.template bone %s" % name
        extra = sorted(k for k in b if k not in ("name", "parent", "head", "tail", "role", "bend", "optional", "note"))
        if extra:
            problems.append("%s: unknown keys %s" % (where, extra))
        if b.get("parent") is not None and b["parent"] not in names:
            problems.append("%s: parent must be a bone listed before it, not %r" % (where, b.get("parent")))
        for k in ("head", "tail"):
            if not (isinstance(b.get(k), list) and len(b[k]) == 3 and all(_number(x) for x in b[k])):
                problems.append("%s: %s must be [side, forward, up] in body heights" % (where, k))
        role = b.get("role")
        if role != "root" and not (isinstance(role, str) and ROLE.match(role)):
            problems.append("%s: role must be root, a chain id or <chain>_l<i> / <chain>_r<i>, not %r" % (where, role))
            continue
        if b.get("bend", "none") not in BENDS:
            problems.append("%s: bend must be one of %s" % (where, ", ".join(BENDS)))
        if role != "root" and role in roles:
            last = roles[role][-1]
            if b.get("parent") != last:
                problems.append("%s: the bones of role %s must follow each other (its parent must be %s)" % (
                    where, role, last))
        if role == "root" and b.get("parent") is not None and names.get(b["parent"]) != "root":
            problems.append("%s: a root bone hangs only from another root bone" % where)
        names[name] = role
        roles.setdefault(role, []).append(name)
    if "root" not in roles:
        problems.append("body.template: no bone has the role root")
    if not problems:
        chains = {}
        for role in roles:
            if role != "root":
                m = ROLE.match(role)
                chains.setdefault(m.group(1), set()).add((m.group(2), m.group(3)))
        for cid, keys in chains.items():
            sides = set(k[0] for k in keys)
            if sides == {None} or not (None in sides):
                if sides != {None}:
                    for k in keys:
                        mirror_side = "r" if k[0] == "l" else "l"
                        if (mirror_side, k[1]) not in keys:
                            problems.append("body.template: role %s_%s%s has no %s side" % (cid, k[0], k[1], mirror_side))
            else:
                problems.append("body.template: chain %s has both single and paired roles" % cid)
    return problems


def expand_template(template):
    """The body plan a valid template stands for: one chain per chain id, its parent, the
    direction it points in the template (also snapped to toward), its pairs, its joints with
    their bends and relative lengths."""
    bones = {b["name"]: b for b in template["bones"]}
    by_role = {}
    for b in template["bones"]:
        by_role.setdefault(b["role"], []).append(b)
    chains, done = [], {}
    for b in template["bones"]:
        role = b["role"]
        if role == "root" or role in done:
            continue
        m = ROLE.match(role)
        cid, side, index = m.group(1), m.group(2), m.group(3)
        done[role] = True
        if any(c["id"] == cid for c in chains):
            continue
        # The chain is described by its left (or single) first-index role.
        rep = by_role.get("%s_l0" % cid if side else cid) or by_role[role]
        parent = bones.get(rep[0].get("parent"))
        prole = parent["role"] if parent else "root"
        src = "root" if prole == "root" else ROLE.match(prole).group(1)
        h, t = rep[0]["head"], rep[-1]["tail"]
        d = (t[0] - h[0], t[1] - h[1], t[2] - h[2])
        toward = _snap(d, paired=bool(side))
        lens = [max(_len(_sub(x["tail"], x["head"])), 1e-6) for x in rep]
        chain = {"id": cid, "from": src, "toward": toward, "direction": [round(x, 6) for x in d],
                 "joints": len(rep), "bends": [x.get("bend", "none") for x in rep],
                 "lengths": [round(x / sum(lens), 6) for x in lens]}
        if side:
            chain["pairs"] = len(set(r for r in by_role if r != "root" and ROLE.match(r).group(1) == cid
                                     and ROLE.match(r).group(2) == "l"))
            # The pairs past the first whose bones are marked optional may be missing.
            needed = [i for i in range(chain["pairs"])
                      if not any(x.get("optional") for s in "lr" for x in by_role.get("%s_%s%d" % (cid, s, i), []))]
            if needed and len(needed) < chain["pairs"]:
                chain["min_pairs"] = max(needed) + 1
        if any(x.get("optional") for x in rep):
            chain["optional"] = True
        chains.append(chain)
    return {"chains": chains}


def _snap(d, paired):
    """toward of a direction [side, forward, up]: the axis it points most along."""
    s, f, u = d
    if paired and abs(s) >= max(abs(f), abs(u)) * 0.8:
        return "side"
    if abs(u) >= abs(f):
        return "up" if u > 0 else "down"
    return "front" if f > 0 else "back"


def joint_map(tree, plan, roles):
    """{role: [{"bone", "joint", "bend"}]}: each mapped bone's joint of its chain (by where
    its middle falls along the chain, against the chain's relative joint lengths) and the
    direction that joint bends, for the chains that give joints. A chain the rig predicted
    with fewer bones than the plan's joints gives its bones the joints in order (the first
    bone the first joint, the next bone the next): a two-bone leg of a three-joint plan
    still has a hip and a knee that bend their own ways, and only the last joints go
    without a bone."""
    tr = Tree(tree)
    out = {}
    for role, slot in plan_roles(plan):
        bones = roles.get(role) or []
        k = slot.get("joints")
        if not bones or not k:
            continue
        bends = slot.get("bends") or ["none"] * k
        if len(bones) < k:
            out[role] = [{"bone": b, "joint": j, "bend": bends[j]} for j, b in enumerate(bones)]
            continue
        lens = slot.get("lengths") or [1.0 / k] * k
        edges, acc = [], 0.0
        for x in lens:
            acc += x / sum(lens)
            edges.append(acc)
        total = sum(tr.length(b) for b in bones) or 1.0
        pos, rows = 0.0, []
        for b in bones:
            mid = (pos + tr.length(b) / 2.0) / total
            j = next((i for i, e in enumerate(edges) if mid <= e + 1e-9), k - 1)
            rows.append({"bone": b, "joint": j, "bend": bends[j]})
            pos += tr.length(b)
        out[role] = rows
    return out


def chain_roles(plan, name):
    """The roles of a chain id (its pairs expanded), or [name] when it is a role."""
    roles = plan_roles(plan)
    found = [r for r, s in roles if s["chain"] == name]
    return found or ([name] if any(r == name for r, _ in roles) else [])


# ---- the tree ------------------------------------------------------------------------------

def tree_from_armature(armature):
    """The tree of points of a Blender armature object (rest pose, world coordinates)."""
    mw = armature.matrix_world
    out = {}
    for b in armature.data.bones:
        h = mw @ b.head_local
        t = mw @ b.tail_local
        out[b.name] = {"parent": b.parent.name if b.parent else None,
                       "head": (h.x, h.y, h.z), "tail": (t.x, t.y, t.z)}
    return out


class Tree:
    def __init__(self, tree):
        self.t = tree
        self.children = {n: [] for n in tree}
        for n in tree:
            p = tree[n]["parent"]
            if p in self.children:
                self.children[p].append(n)
        self._desc = {}
        pts = [b[k] for b in tree.values() for k in ("head", "tail")]
        self.size = max((max(p[i] for p in pts) - min(p[i] for p in pts)) for i in range(3)) if pts else 0.0

    def roots(self):
        return [n for n in self.t if self.t[n]["parent"] not in self.t]

    def head(self, n):
        return tuple(self.t[n]["head"])

    def tail(self, n):
        return tuple(self.t[n]["tail"])

    def parent(self, n):
        p = self.t[n]["parent"]
        return p if p in self.t else None

    def length(self, n):
        return _len(_sub(self.tail(n), self.head(n)))

    def descendants(self, n):
        if n not in self._desc:
            self._desc[n] = sum(1 + self.descendants(c) for c in self.children[n])
        return self._desc[n]

    def is_ancestor(self, a, b):
        """a is b or one of b's ancestors."""
        while b is not None:
            if b == a:
                return True
            b = self.parent(b)
        return False

    def root_path(self):
        roots = self.roots()
        if not roots:
            return []
        path = [max(roots, key=self.descendants)]
        while len(self.children[path[-1]]) == 1:
            path.append(self.children[path[-1]][0])
        return path


def _run(tr, start, owned):
    run = [start]
    while len(tr.children[run[-1]]) == 1 and tr.children[run[-1]][0] not in owned:
        run.append(tr.children[run[-1]][0])
    return run


def candidates(tr, attach, owned):
    """Runs that may be a chain hanging from the bones `attach`: [(run, via)] where via is the
    connector run it hangs from (or None)."""
    out = []

    def offer(start, via):
        if start in owned:
            return
        run = _run(tr, start, owned)
        out.append((run, via))
        ln = sum(tr.length(b) for b in run)
        if len(tr.children[run[-1]]) >= 2 and ln < CONNECTOR * tr.size:
            for c in tr.children[run[-1]]:
                offer(c, (via or []) + run)
    for b in attach:
        for c in tr.children[b]:
            offer(c, None)
    return out


def continued(tr, run, want, owned):
    """A run carried on through its branches, each time into the child whose own run points
    most along `want` (a unit vector), while that child points within 60 degrees of it: the
    trunk of a chain that other chains hang from (a spine past the hips of the arms)."""
    run = list(run)
    while len(tr.children[run[-1]]) >= 2:
        best, best_s = None, 0.5
        for c in tr.children[run[-1]]:
            if c in owned:
                continue
            s = _dot(_direction(tr, _run(tr, c, owned)), want)
            if s > best_s:
                best, best_s = c, s
        if best is None:
            break
        run += _run(tr, best, owned)
    return run


def slot_vector(slot):
    """The direction a slot's chain should point (model axes): its template direction, else
    its "toward" (side: the slot's side)."""
    if slot.get("direction"):
        s, f, u = slot["direction"]
        v = (s, -f, u)
        if slot["side"] == "r":
            v = (-v[0], v[1], v[2])
        return _unit(v)
    if slot["toward"] == "side":
        return LEFT if slot["side"] != "r" else (-1.0, 0.0, 0.0)
    return TOWARD[slot["toward"]]


def _direction(tr, run):
    return _unit(_sub(tr.tail(run[-1]), tr.head(run[0])))


def _mean_x(tr, run, hub):
    xs = [tr.head(b)[0] for b in run] + [tr.tail(run[-1])[0]]
    return sum(xs) / len(xs) - tr.head(hub)[0]


def score(tr, run, slot, hub):
    """How well a run fits a slot (None: not at all)."""
    s = _dot(_direction(tr, run), slot_vector(slot))
    x = _mean_x(tr, run, hub)
    if slot["side"]:
        if abs(x) < SIDE_MIN * tr.size or (x > 0) != (slot["side"] == "l"):
            return None
    elif abs(x) > MIDLINE * tr.size:
        # A single chain (a spine, a neck, a tail) runs along the middle, not off one side,
        # unless it points the chain's way and its mirror hangs beside it (a propulsion unit
        # the rig predicted as a pair of legs).
        if s < TWIN_SCORE or not _twin(tr, run, set(run), hub):
            return None
    if s < MIN_SCORE:
        return None
    ln = sum(tr.length(b) for b in run)
    return s * (0.6 + 0.4 * min(1.0, ln / max(0.25 * tr.size, 1e-9)))


def _points(tr, run, n=8):
    """n points spread along a run by length (heads and the last tail)."""
    pts = [tr.head(b) for b in run] + [tr.tail(run[-1])]
    seg = [_len(_sub(b, a)) for a, b in zip(pts, pts[1:])]
    total = sum(seg) or 1.0
    out, k, acc = [], 0, 0.0
    for i in range(n):
        s = total * i / (n - 1)
        while k < len(seg) - 1 and acc + seg[k] < s:
            acc += seg[k]
            k += 1
        t = 0.0 if seg[k] <= 1e-12 else min(1.0, max(0.0, (s - acc) / seg[k]))
        a, b = pts[k], pts[k + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
    return out


def _mirror_error(tr, r1, r2, plane_point, normal):
    """Mean distance between run r1 reflected through the plane (point, unit normal) and run
    r2, as a share of their mean length."""
    p1, p2 = _points(tr, r1), _points(tr, r2)
    err = 0.0
    for a, b in zip(p1, p2):
        d = _dot(_sub(a, plane_point), normal)
        m = (a[0] - 2 * d * normal[0], a[1] - 2 * d * normal[1], a[2] - 2 * d * normal[2])
        err += _len(_sub(m, b))
    ln = (sum(tr.length(b) for b in r1) + sum(tr.length(b) for b in r2)) / 2.0
    return err / len(p1) / max(ln, 1e-9)


def mirrored_pairs(tr, owned=None, min_share=MIRROR_MIN_SHARE):
    """[(run_a, run_b, across, bone)] runs hanging from one bone that mirror each other (a pair of
    legs, of arms): both at least min_share of the body size long, lengths within
    SIDE_TOLERANCE, run_a reflected through the plane between them lands on run_b within
    MIRROR_TOLERANCE of their length. across: the horizontal line from run_b to run_a (their
    centres); bone: the bone they hang from."""
    owned = owned or set()
    out = []
    for p in tr.t:
        kids = [c for c in tr.children[p] if c not in owned]
        runs = [_run(tr, c, owned) for c in kids]
        runs = [r for r in runs if sum(tr.length(b) for b in r) >= min_share * tr.size]
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                a, b = runs[i], runs[j]
                la, lb = sum(tr.length(x) for x in a), sum(tr.length(x) for x in b)
                if abs(la - lb) / max(la, lb) > SIDE_TOLERANCE:
                    continue
                ca = [sum(q[k] for q in _points(tr, a)) / 8.0 for k in range(3)]
                cb_ = [sum(q[k] for q in _points(tr, b)) / 8.0 for k in range(3)]
                across = (ca[0] - cb_[0], ca[1] - cb_[1], 0.0)
                if _len(across) < SIDE_MIN * tr.size:
                    continue
                normal = _unit(across)
                mid = tuple((ca[k] + cb_[k]) / 2.0 for k in range(3))
                if _mirror_error(tr, a, b, mid, normal) <= MIRROR_TOLERANCE:
                    out.append((a, b, across, p))
    return out


def skeleton_facing(tree):
    """Which way a predicted skeleton faces, from its mirrored pairs (legs and arms): their
    summed across line is its left-right axis. A rig tool may predict a skeleton turned a
    quarter turn from the order's front (the generated mesh came out facing glTF +-X); then
    the across line lies front to back. Only quarter turns are made: when the across line is
    more than 45 degrees from the side axis, the skeleton is turned so its across line lies
    along the side and its feet (the down-pointing pairs' last bones) point to the front.

    Returns {"turn_deg": 0, 90, -90 (a turn about the vertical, right-hand rule, that the
    exporter applies), "across_deg" (the across line's angle from the side axis, 0 to 90),
    "pairs" (how many mirrored pairs), "feet_deg" (where the feet point, degrees from the
    front about the vertical, or None), "problems"}. A problem means the skeleton lies turned
    and its front cannot be told."""
    tr = Tree(tree)
    pairs = mirrored_pairs(tr)
    res = {"turn_deg": 0, "across_deg": None, "pairs": len(pairs), "feet_deg": None, "problems": []}
    if not pairs:
        return res
    sx, sy = 0.0, 0.0
    ref = None
    for a, b, across, _ in pairs:
        v = (across[0], across[1])
        if ref is None:
            ref = v
        if v[0] * ref[0] + v[1] * ref[1] < 0:
            v = (-v[0], -v[1])
        sx, sy = sx + v[0], sy + v[1]
    ang = abs(math.degrees(math.atan2(abs(sy), abs(sx))))
    res["across_deg"] = round(ang, 2)
    fx, fy = 0.0, 0.0
    hips = set(tr.root_path())
    for a, b, across, p in pairs:
        if p not in hips:
            continue  # legs hang from the root path (the hips), arms from the spine
        for run in (a, b):
            d = _direction(tr, run)
            if d[2] < -0.5:  # a leg: where its last bone points
                last = _sub(tr.tail(run[-1]), tr.head(run[-1]))
                fx, fy = fx + last[0], fy + last[1]
    if math.hypot(fx, fy) > 1e-9:
        # degrees from the front (-Y) toward the left (+X)
        res["feet_deg"] = round(math.degrees(math.atan2(fx, -fy)), 2)
    if ang <= 45.0:
        return res
    if res["feet_deg"] is None or abs(abs(res["feet_deg"]) - 90.0) > FEET_LIMIT_DEG:
        res["problems"].append("facing: the skeleton's left and right lie front to back (%.0f degrees from the "
                               "side) and no feet tell its front" % ang)
        return res
    # Feet toward +X (feet_deg +90): turn -90 (about +Z) brings +X to -Y; toward -X: +90.
    res["turn_deg"] = -90 if res["feet_deg"] > 0 else 90
    return res


def turned_tree(tree, deg):
    """The tree turned about the vertical through the origin by deg (right-hand rule)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    def t(p):
        return (p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2])
    return {n: {"parent": b["parent"], "head": t(b["head"]), "tail": t(b["tail"])} for n, b in tree.items()}


def _twin(tr, run, owned, hub):
    """An unmapped run that mirrors `run` across the body's middle (x of the hub), hanging
    from the same bone: the other half of a single chain the rig predicted as a pair (a
    serpent's tail grown as two legs)."""
    p = tr.parent(run[0])
    if p is None:
        return None
    hx = tr.head(hub)[0]
    best, best_e = None, MIRROR_TOLERANCE
    for c in tr.children[p]:
        if c in owned or c == run[0]:
            continue
        r2 = _run(tr, c, owned)
        la, lb = sum(tr.length(b) for b in run), sum(tr.length(b) for b in r2)
        if abs(la - lb) / max(la, lb, 1e-9) > SIDE_TOLERANCE:
            continue
        e = _mirror_error(tr, run, r2, (hx, 0.0, 0.0), (1.0, 0.0, 0.0))
        if e <= best_e:
            best, best_e = r2, e
    return best


def propose(tree, plan):
    """The proposed map with its validation: the content of bone_map.json."""
    tr = Tree(tree)
    roles, notes, problems, split = {}, [], [], {}
    path = tr.root_path()
    if not path:
        return document(tree, plan, {}, "proposed", False, ["the rig has no bones"], [], {})
    hub = path[-1]
    owned = set(path)
    slots = plan_roles(plan)
    # Group the slots by parent role, in plan order (parents are always earlier).
    groups = []
    for role, slot in slots:
        for g in groups:
            if g[0] == slot["parent"]:
                g[1].append((role, slot))
                break
        else:
            groups.append((slot["parent"], [(role, slot)]))
    parents = set(slot["parent"] for _, slot in slots)
    for parent, members in groups:
        if parent == "root":
            attach = path
        elif parent in roles:
            attach = roles[parent]
        else:
            continue  # the parent was not found: its children cannot be either
        cands = candidates(tr, attach, owned)
        pairs = []
        for i, (run, via) in enumerate(cands):
            for role, slot in members:
                # A chain that others hang from carries on through its branches.
                r = continued(tr, run, slot_vector(slot), owned) if role in parents else run
                s = score(tr, r, slot, hub)
                if s is not None:
                    pairs.append((s, i, role, r))
        pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
        taken_c, taken_r = set(), set()
        used, crossed = set(), set()
        for s, i, role, run in pairs:
            if i in taken_c or role in taken_r:
                continue
            via = cands[i][1] or []
            # Two chains may hang from one connector, but no bone is in two chains.
            if set(run) & (used | crossed) or set(via) & used:
                continue
            taken_c.add(i)
            taken_r.add(role)
            roles[role] = run
            used.update(run)
            crossed.update(via)
            owned.update(run)
        # A paired chain's index: by height of its first bone (highest first), then front.
        by_chain = {}
        for role, slot in members:
            if slot["side"] and role in roles:
                by_chain.setdefault((slot["chain"], slot["side"]), []).append(roles.pop(role))
        for (chain, side), runs in by_chain.items():
            runs.sort(key=lambda r: (-round(tr.head(r[0])[2], 6), round(tr.head(r[0])[1], 6)))
            for i, run in enumerate(runs):
                roles["%s_%s%d" % (chain, side, i)] = run
        # A missing single chain at the end of a single parent chain: the parent's end, past
        # the last bone where other chains branch off (else its last third).
        for role, slot in members:
            if role in roles or slot["side"] or parent == "root" or parent not in roles:
                continue
            bones = roles[parent]
            if len(bones) < 2:
                continue
            branch = [j for j, b in enumerate(bones[:-1]) if any(c != bones[j + 1] for c in tr.children[b])]
            k = len(bones) - 1 - branch[-1] if branch else max(1, int(round(len(bones) * SPLIT_SHARE)))
            if branch and _dot(_direction(tr, bones[-k:]), slot_vector(slot)) < MIN_SCORE:
                k = max(1, int(round(len(bones) * SPLIT_SHARE)))
            roles[role], roles[parent] = bones[-k:], bones[:-k]
            split[role] = parent
            notes.append("%s: no branch of its own; it takes the last %d bone(s) of %s" % (role, k, parent))
        # A required single chain still missing takes the nearest remaining run of its parent
        # by direction, if it is long enough to be a limb and runs along the middle or has a
        # mirror twin (a tail the rig predicted as a pair of legs pointing down, a spine
        # standing up where the plan says front).
        for role, slot in members:
            if role in roles or slot["side"] or slot["optional"]:
                continue
            want = slot_vector(slot)
            best = None
            for i, (run, via) in enumerate(cands):
                if i in taken_c or set(run) & owned or set(via or []) & used:
                    continue
                r = continued(tr, run, want, owned) if role in parents else run
                ln = sum(tr.length(b) for b in r)
                if ln < FALLBACK_MIN_SHARE * tr.size:
                    continue
                if abs(_mean_x(tr, r, hub)) > MIDLINE * tr.size and not _twin(tr, r, owned | set(r), hub):
                    continue
                key = (_dot(_direction(tr, r), want), ln)
                if best is None or key > best[0]:
                    best = (key, i, r)
            if best is None:
                continue
            (s, _), i, r = best
            taken_c.add(i)
            roles[role] = r
            used.update(r)
            owned.update(r)
            notes.append("%s: no run points %s; it takes the nearest remaining run %s..%s (%.0f degrees off)" % (
                role, slot["toward"], r[0], r[-1], math.degrees(math.acos(max(-1.0, min(1.0, s))))))
    # A single chain whose mirror image hangs unmapped beside it (a tail or a propulsion unit
    # the rig predicted as a pair of legs): the mirror run is its twin and moves with it.
    twins = {}
    slots = dict(slots)
    for role, run in list(roles.items()):
        if slots.get(role, {}).get("side") is not None:
            continue
        twin = _twin(tr, run, owned, hub)
        if twin:
            twins[role] = twin
            owned.update(twin)
            notes.append("%s: the mirror run %s..%s hangs beside it unmapped; it moves as its twin" % (
                role, twin[0], twin[-1]))
    problems = validate(tree, plan, roles)
    problems += validate_twins(tree, roles, twins)
    return document(tree, plan, roles, "proposed", False, problems, notes, split, twins)


def validate_twins(tree, roles, twins):
    """Problems of a map's twins {role: [bones]}: a twin of a mapped single role, its bones a
    parent-child chain, none of them in a role or in two twins."""
    if not twins:
        return []
    if not isinstance(twins, dict):
        return ["twins must be an object {role: [bone, ...]}"]
    tr = Tree(tree)
    problems, seen = [], set(b for bones in roles.values() for b in bones)
    for r, bones in twins.items():
        if r not in roles:
            problems.append("twin of %s: the role is not mapped" % r)
            continue
        if not isinstance(bones, list) or not bones or not all(isinstance(b, str) and b in tree for b in bones):
            problems.append("twin of %s must list bones of the rig from the base to the tip" % r)
            continue
        for a, b in zip(bones, bones[1:]):
            if tr.parent(b) != a:
                problems.append("twin of %s: %s must be a child of %s" % (r, b, a))
        for b in bones:
            if b in seen:
                problems.append("twin of %s: bone %s already has a role or a twin" % (r, b))
            seen.add(b)
    return problems


def validate(tree, plan, roles):
    """Problems of a map {role: [bones]} on this tree (empty: valid)."""
    if not isinstance(roles, dict):
        return ["the map must be an object {role: [bone, ...]}"]
    tr = Tree(tree)
    problems = []
    slots = dict(plan_roles(plan))
    for r, bones in roles.items():
        if r not in slots:
            problems.append("unknown role %s (roles: %s)" % (r, ", ".join(slots)))
        elif not isinstance(bones, list) or not bones or not all(isinstance(b, str) for b in bones):
            problems.append("role %s must list its bones from the base to the tip" % r)
    for r, s in slots.items():
        if not s["optional"] and not roles.get(r):
            problems.append("role %s is missing" % r)
    if problems:
        return problems
    seen = {}
    for r, bones in roles.items():
        for b in bones:
            if b not in tree:
                problems.append("role %s names bone %s, which the rig does not have" % (r, b))
            elif b in seen:
                problems.append("bone %s has two roles: %s and %s" % (b, seen[b], r))
            seen[b] = r
    if problems:
        return problems
    path = tr.root_path()
    hub = path[-1]
    for r, bones in roles.items():
        for a, b in zip(bones, bones[1:]):
            if tr.parent(b) != a:
                problems.append("role %s: %s must be a child of %s" % (r, b, a))
        parent = slots[r]["parent"]
        above = path if parent == "root" else roles.get(parent)
        if above and not any(tr.is_ancestor(a, bones[0]) and a != bones[0] for a in above):
            problems.append("role %s (%s) must hang below %s" % (r, bones[0], parent))
        side = slots[r]["side"]
        if side:
            x = _mean_x(tr, bones, hub)
            if abs(x) < SIDE_MIN * tr.size or (x > 0) != (side == "l"):
                problems.append("role %s is not on the %s of the body" % (r, "left" if side == "l" else "right"))
    for r, s in slots.items():
        if s["side"] == "l":
            other = "%s_r%d" % (s["chain"], s["index"])
            if roles.get(r) and roles.get(other):
                ln = sum(tr.length(b) for b in roles[r])
                rn = sum(tr.length(b) for b in roles[other])
                big = max(ln, rn)
                if big <= 0 or abs(ln - rn) / big > SIDE_TOLERANCE:
                    problems.append("%s is %.3f long and %s %.3f (more than %d%% apart)" % (
                        r, ln, other, rn, round(SIDE_TOLERANCE * 100)))
    return problems


def document(tree, plan, roles, source, manual, problems, notes, split, twins=None):
    tr = Tree(tree)
    valid = not problems
    mapped = set(b for bones in roles.values() for b in bones) if isinstance(roles, dict) else set()
    if isinstance(twins, dict):
        mapped.update(b for bones in twins.values() if isinstance(bones, list) for b in bones)
    path = tr.root_path()
    joints = joint_map(tree, plan, roles) if valid else {}
    notes = list(notes)
    for role, rows in joints.items():
        k = dict(plan_roles(plan))[role]["joints"]
        if len(rows) != k:
            notes.append("%s: %d bone(s) for %d joint(s); each bone takes the joint its middle falls in" % (
                role, len(rows), k))
    return {"format": FORMAT, "version": 1, "source": source, "manual": manual, "valid": valid,
            "roles": dict(roles) if valid else None, "found": dict(roles) if isinstance(roles, dict) else roles,
            "root": path, "unmapped": [n for n in tree if n not in mapped and n not in path],
            "joints": joints, "front": list(FRONT), "left": list(LEFT), "split": split,
            "twins": dict(twins or {}) if valid else {}, "notes": notes,
            "problems": problems}


def load_map(path_or_map):
    data = path_or_map
    if isinstance(path_or_map, str):
        with open(path_or_map, encoding="utf-8-sig") as f:
            data = json.load(f)
    if isinstance(data, dict) and "roles" in data and isinstance(data["roles"], (dict, type(None))):
        return data["roles"] or {}
    return data


def load_twins(path_or_map):
    """The twins of a map document ({} for a bare {role: [bones]} map)."""
    data = path_or_map
    if isinstance(path_or_map, str):
        with open(path_or_map, encoding="utf-8-sig") as f:
            data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("roles"), (dict, type(None))):
        return data.get("twins") or {}
    return {}


def _given(tree, plan, roles, twins, source):
    problems = validate(tree, plan, roles) or validate_twins(tree, roles, twins)
    return document(tree, plan, roles, source, True, problems, [], {}, twins)


def choose(tree, plan, given=None, existing=None):
    """The map to use: `given` (the order's inputs.bone_map) wins, then `existing` (a
    bone_map.json path) marked "manual": true, then the proposal. Always validated. A given
    or manual document may also name twins."""
    if given:
        return _given(tree, plan, load_map(given), load_twins(given), "order")
    if existing and os.path.exists(existing):
        with open(existing, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("manual") and data.get("source") != "order":
            return _given(tree, plan, load_map(data), load_twins(data), "manual")
    return propose(tree, plan)
