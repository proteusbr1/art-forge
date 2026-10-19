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
(a hand on each arm). "optional": true lets the map go without it. "holds" names the roles
that carry held items (the exporter adds an item bone at their tip).

The proposal (propose()):
- the root path: from the skeleton's root down its single children to the first bone that
  branches (the hub); the "root" role is that path;
- candidates of a chain: runs of single children starting at a child of its parent's bones
  (the root path for "root"); a short run that ends in a branch (a connector, under
  CONNECTOR of the body size) also offers its children's runs;
- each candidate is scored by how its direction (from its first head to its last tail)
  matches the chain's "toward" (a side chain also by side), and by its length; the best
  pairs (chain, candidate) are taken first; a paired chain's left and right are split by
  the side of the body;
- a chain with no candidate that hangs at the end of a single chain of 2 bones or more
  takes the last third of that chain (a neck that the skeleton grew as part of its spine);
- every chain is checked (validate()): a required chain is missing, a role's bones do not
  form a parent-child chain, a bone has two roles, a chain does not hang below its parent,
  a left or right chain is on the wrong side, a pair differs in length by more than
  SIDE_TOLERANCE.

A map is {"roles": {role: [bone, ...]}} (bones from the base to the tip); bone_map.json
holds it with its validation (format forge-creature-map). choose() applies the precedence:
a map given by the order, then a bone_map.json marked "manual": true, then the proposal.

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
CHAIN_KEYS = ("id", "from", "toward", "pairs", "optional", "joints", "bends", "lengths", "direction", "note")


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
    "root" or a role), toward, side ("l", "r" or None), index, optional."""
    out = []
    by_chain = {}
    for c in plan["chains"]:
        cid, src = c["id"], c.get("from", "root")
        pairs = c.get("pairs") or (by_chain.get(src, {}).get("pairs"))
        entry = {"pairs": pairs, "roles": []}
        common = {"chain": cid, "toward": c["toward"], "optional": bool(c.get("optional")),
                  "joints": c.get("joints"), "bends": c.get("bends"), "lengths": c.get("lengths"),
                  "direction": c.get("direction")}
        if pairs:
            for i in range(pairs):
                for side in ("l", "r"):
                    role = "%s_%s%d" % (cid, side, i)
                    if src == "root":
                        parent = "root"
                    elif by_chain[src]["pairs"]:
                        parent = "%s_%s%d" % (src, side, i)
                    else:
                        parent = src
                    out.append((role, dict(common, parent=parent, side=side, index=i)))
                    entry["roles"].append(role)
        else:
            out.append((cid, dict(common, parent=src, side=None, index=None)))
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
    its head falls along the chain, against the chain's relative joint lengths) and the
    direction that joint bends, for the chains that give joints."""
    tr = Tree(tree)
    out = {}
    for role, slot in plan_roles(plan):
        bones = roles.get(role) or []
        k = slot.get("joints")
        if not bones or not k:
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
            rows.append({"bone": b, "joint": j, "bend": (slot.get("bends") or ["none"] * k)[j]})
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
    if slot["side"]:
        x = _mean_x(tr, run, hub)
        if abs(x) < SIDE_MIN * tr.size or (x > 0) != (slot["side"] == "l"):
            return None
    if s < MIN_SCORE:
        return None
    ln = sum(tr.length(b) for b in run)
    return s * (0.6 + 0.4 * min(1.0, ln / max(0.25 * tr.size, 1e-9)))


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
    problems = validate(tree, plan, roles)
    return document(tree, plan, roles, "proposed", False, problems, notes, split)


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


def document(tree, plan, roles, source, manual, problems, notes, split):
    tr = Tree(tree)
    valid = not problems
    mapped = set(b for bones in roles.values() for b in bones) if isinstance(roles, dict) else set()
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
            "joints": joints, "front": list(FRONT), "left": list(LEFT), "split": split, "notes": notes,
            "problems": problems}


def load_map(path_or_map):
    data = path_or_map
    if isinstance(path_or_map, str):
        with open(path_or_map, encoding="utf-8-sig") as f:
            data = json.load(f)
    if isinstance(data, dict) and "roles" in data and isinstance(data["roles"], (dict, type(None))):
        return data["roles"] or {}
    return data


def choose(tree, plan, given=None, existing=None):
    """The map to use: `given` (the order's inputs.bone_map) wins, then `existing` (a
    bone_map.json path) marked "manual": true, then the proposal. Always validated."""
    if given:
        roles = load_map(given)
        return document(tree, plan, roles, "order", True, validate(tree, plan, roles), [], {})
    if existing and os.path.exists(existing):
        with open(existing, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("manual") and data.get("source") != "order":
            roles = load_map(data)
            return document(tree, plan, roles, "manual", True, validate(tree, plan, roles), [], {})
    return propose(tree, plan)
