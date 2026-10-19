"""The humanoid roles of a rig's bones, found from the skeleton's tree, and their names in
Godot's humanoid profile (SkeletonProfileHumanoid).

The rig tools (SkinTokens, UniRig) name their bones bone_0, bone_1, ...; the roles come
from the tree and the rest geometry:

- hips: the first bone, from the root down, whose children branch into a spine (going up)
  and two legs (reaching down); bones above it (an extra root) keep their names;
- spine: from the hips' upward child, following the child with the most descendants, up
  to the first bone with three or more children (the top: neck and two arms branch there);
- arms: of the top's children, the two that reach farthest to either side (across the
  body, by the front the feet give); the hand is the first bone of the arm that branches
  (the fingers), else the fourth bone (shoulder, upper arm, lower arm, hand); the fingers
  keep their names;
- neck and head: of the top's other children, the one that goes up rather than out and
  down, lies between the arms and ends in no fan of leaves (not the one whose end point is
  highest: a clavicle's tail can point higher than a short neck under a big helmet), then
  its own highest child;
- legs: the two children of the hips (other than the spine) that reach lowest: upper leg,
  lower leg, foot and toes (optional);
- left and right: the front is where the toes point (snapped to the nearest horizontal
  axis), the left is up x front; each hand and each foot is put on its side by its rest
  position.

A bone map is {role: bone name}. propose_bone_map() finds one and validates it;
validate_bone_map() checks any map (a manual one too) against the tree; choose_bone_map()
applies the precedence: a map given by the order, then a bone_map.json marked
"manual": true, then the proposal. Bones without a role keep their names.

This module does not import bpy: it works over a tree of points,
{bone: {"parent": name or None, "head": (x, y, z), "tail": (x, y, z)}} with z up, and
tree_from_armature() builds that tree from a Blender armature.
"""

import json
import math
import os

FORMAT = "forge-bone-map"

ROLES = (
    "hips", "spine", "chest", "upper_chest", "neck", "head",
    "left_shoulder", "left_upper_arm", "left_lower_arm", "left_hand",
    "right_shoulder", "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot", "left_toes",
    "right_upper_leg", "right_lower_leg", "right_foot", "right_toes",
)
REQUIRED = (
    "hips", "spine", "chest", "head",
    "left_upper_arm", "left_lower_arm", "left_hand",
    "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot",
    "right_upper_leg", "right_lower_leg", "right_foot",
)
SIDES = ("left", "right")
# Largest relative difference between the lengths of a segment on the two sides.
SIDE_TOLERANCE = 0.15


def godot_name(role):
    """The SkeletonProfileHumanoid bone name of a role: left_upper_arm -> LeftUpperArm."""
    if role not in ROLES:
        raise KeyError("unknown humanoid role: %s" % role)
    return "".join(w.capitalize() for w in role.split("_"))


# ---- small vector helpers (tuples) -------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len(a):
    return math.sqrt(_dot(a, a))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


# ---- the tree ----------------------------------------------------------------------------

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


def _as_tree(armature_or_tree):
    if isinstance(armature_or_tree, dict):
        return armature_or_tree
    return tree_from_armature(armature_or_tree)


class _Tree:
    def __init__(self, tree):
        self.t = tree
        self.children = {n: [] for n in tree}
        for n in tree:  # dict order: the rig's own order
            p = tree[n]["parent"]
            if p in self.children:
                self.children[p].append(n)
        self._desc = {}
        self._low = {}
        self._high = {}
        zs = [b[k][2] for b in tree.values() for k in ("head", "tail")]
        self.span = (max(zs) - min(zs)) if zs else 0.0

    def roots(self):
        return [n for n in self.t if self.t[n]["parent"] not in self.t]

    def head(self, n):
        return tuple(self.t[n]["head"])

    def tail(self, n):
        return tuple(self.t[n]["tail"])

    def parent(self, n):
        p = self.t[n]["parent"]
        return p if p in self.t else None

    def descendants(self, n):
        if n not in self._desc:
            self._desc[n] = sum(1 + self.descendants(c) for c in self.children[n])
        return self._desc[n]

    def lowest(self, n):
        if n not in self._low:
            z = min(self.head(n)[2], self.tail(n)[2])
            self._low[n] = min([z] + [self.lowest(c) for c in self.children[n]])
        return self._low[n]

    def highest(self, n):
        if n not in self._high:
            z = max(self.head(n)[2], self.tail(n)[2])
            self._high[n] = max([z] + [self.highest(c) for c in self.children[n]])
        return self._high[n]

    def is_ancestor(self, a, b):
        """a is b or one of b's ancestors."""
        while b is not None:
            if b == a:
                return True
            b = self.parent(b)
        return False

    def chain(self, n, key=None):
        """n, then repeatedly the child with the most descendants (or the highest key)."""
        out = [n]
        while self.children[out[-1]]:
            kids = self.children[out[-1]]
            out.append(max(kids, key=key or self.descendants))
        return out


def _hips_split(tr, n):
    """(spine child, [two leg children]) when n branches into a spine and two legs, else None."""
    kids = tr.children[n]
    if len(kids) < 3:
        return None
    z = tr.head(n)[2]
    ups = [c for c in kids if tr.highest(c) > z + 0.2 * tr.span]
    if not ups:
        return None
    spine = max(ups, key=tr.descendants)
    downs = [c for c in kids if c != spine and tr.lowest(c) < z - 0.25 * tr.span]
    if len(downs) < 2:
        return None
    return spine, sorted(downs, key=tr.lowest)[:2]


def _snap_horizontal(v):
    """The horizontal axis (+-x, +-y) nearest to v, or None."""
    if abs(v[0]) < 1e-9 and abs(v[1]) < 1e-9:
        return None
    if abs(v[0]) > abs(v[1]):
        return (1.0 if v[0] > 0 else -1.0, 0.0, 0.0)
    return (0.0, 1.0 if v[1] > 0 else -1.0, 0.0)


def _front_of_feet(tr, feet):
    """The front from where the feet point: (foot head -> toes tail) or the foot's own axis."""
    total = (0.0, 0.0, 0.0)
    for foot, toes in feet:
        end = tr.tail(toes) if toes else tr.tail(foot)
        d = _sub(end, tr.head(foot))
        total = (total[0] + d[0], total[1] + d[1], 0.0)
    return _snap_horizontal(total)


def _arm_parts(tr, start):
    """[shoulder or None, upper arm, lower arm, hand] of the arm that starts at `start`."""
    run = [start]
    while len(tr.children[run[-1]]) == 1:
        run.append(tr.children[run[-1]][0])
    if len(tr.children[run[-1]]) >= 2 and len(run) >= 3:
        pre = run  # the last bone branches: the hand with its fingers
    else:
        pre = run[:4]
    if len(pre) < 3:
        return None
    if len(pre) == 3:
        return [None] + pre
    return [pre[0]] + pre[-3:]


def _subtree(tr, n):
    """n and every bone below it."""
    out, stack = [], [n]
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(tr.children[x])
    return out


def _has_fan(tr, n):
    """A bone below n (or n) whose two or more children start chains that end without
    branching again: a hand with its fingers."""
    for x in _subtree(tr, n):
        kids = tr.children[x]
        if len(kids) >= 2 and all(max(len(tr.children[y]) for y in _subtree(tr, k)) <= 1 for k in kids):
            return True
    return False


def _neck_and_arms(tr, top, kids, hips_head, across):
    """(neck, [two arm starts]) among the children of the spine's top. The arms are the two
    children that reach farthest across the body, one to each side (every head and tail of
    their bones, along `across`). The neck is, of the others, the child that goes up rather
    than out and down, lies between the arms and ends in no fan of leaves. Several cues, so
    that no single one decides: a clavicle whose tail points up reaches higher than a short
    neck under a big helmet. With fewer than three children: the highest is the neck."""
    if len(kids) < 3:
        neck = max(kids, key=tr.highest)
        return neck, [c for c in kids if c != neck]

    def lateral(c):
        vals = [_dot(_sub(p, hips_head), across) for b in _subtree(tr, c) for p in (tr.head(b), tr.tail(b))]
        return min(vals), max(vals)
    reach = {c: lateral(c) for c in kids}
    left = max(kids, key=lambda c: (reach[c][1], tr.descendants(c)))
    right = min((c for c in kids if c != left), key=lambda c: (reach[c][0], -tr.descendants(c)))
    rest = [c for c in kids if c not in (left, right)]
    lo, hi = reach[right][0], reach[left][1]
    z0 = tr.head(top)[2]
    span = tr.span or 1.0

    def neck_score(c):
        a, b = reach[c]
        mid = (a + b) / 2.0
        between = 0.0 if lo < mid < hi else 1.0
        rise = tr.highest(c) - z0
        drop = z0 - tr.lowest(c)
        return (-between, -int(_has_fan(tr, c)), (rise - max(drop, 0.0) - abs(mid)) / span)
    neck = max(rest, key=neck_score)
    return neck, [left, right]


def _find(tree):
    """(roles found, problems): the proposal before validation."""
    tr = _Tree(tree)
    roles, problems = {}, []
    roots = tr.roots()
    if not roots:
        return roles, ["the rig has no bones"]
    start = max(roots, key=tr.descendants)
    hips, split = start, _hips_split(tr, start)
    while split is None:
        kids = tr.children[hips]
        if not kids:
            return roles, ["no bone branches into a spine and two legs (hips not found)"]
        hips = max(kids, key=tr.descendants)
        split = _hips_split(tr, hips)
    roles["hips"] = hips
    spine_first, legs = split
    # Spine up to the top, where the neck and the arms branch.
    spine = [spine_first]
    while len(tr.children[spine[-1]]) < 3:
        kids = tr.children[spine[-1]]
        if not kids:
            problems.append("the spine ends at %s without branching into a neck and two arms" % spine[-1])
            return roles, problems
        spine.append(max(kids, key=tr.descendants))
    top = spine[-1]
    if len(spine) < 2:
        problems.append("the spine chain has %d bone(s) between the hips and the neck; at least 2 are needed"
                        % len(spine))
    names = ("spine",) if len(spine) == 1 else ("spine", "chest") if len(spine) == 2 else None
    if names:
        roles.update(zip(names, spine))
    else:
        roles.update({"spine": spine[0], "chest": spine[-2], "upper_chest": spine[-1]})
    hips_head = tr.head(hips)
    leg_parts = []
    for c in legs:
        ch = tr.chain(c)
        if len(ch) >= 3:
            leg_parts.append(ch[:4] if len(ch) >= 4 else ch[:3] + [None])
    feet_front = _front_of_feet(tr, [(p[2], p[3]) for p in leg_parts]) if len(leg_parts) == 2 else None
    across = _cross((0.0, 0.0, 1.0), feet_front) if feet_front else (1.0, 0.0, 0.0)
    kids = tr.children[top]
    neck, arm_starts = _neck_and_arms(tr, top, kids, hips_head, across)
    head_chain = tr.chain(neck, key=tr.highest)
    if len(head_chain) >= 2:
        roles["neck"], roles["head"] = head_chain[0], head_chain[1]
    else:
        roles["head"] = head_chain[0]
    arms = []
    for c in arm_starts:
        parts = _arm_parts(tr, c)
        if parts is None:
            problems.append("the arm starting at %s has fewer than 3 bones" % c)
        else:
            arms.append(parts)
    if len(arm_starts) < 2:
        problems.append("found %d arm(s) at the top of the spine (%s); 2 are needed" % (len(arm_starts), top))
    for c in legs:
        if len(tr.chain(c)) < 3:
            problems.append("the leg starting at %s has fewer than 3 bones" % c)
    front = _front_of_feet(tr, [(p[2], p[3]) for p in leg_parts])
    if front is None:
        problems.append("the feet point nowhere: the front cannot be found")
        return roles, problems
    left = _cross((0.0, 0.0, 1.0), front)
    for parts, slots in ((arms, ("shoulder", "upper_arm", "lower_arm", "hand")),
                         (leg_parts, ("upper_leg", "lower_leg", "foot", "toes"))):
        end = 3 if slots[0] == "shoulder" else 2
        ordered = sorted(parts, key=lambda p: -_dot(_sub(tr.head(p[end]), hips_head), left))
        for side, p in zip(SIDES, ordered):
            for slot, bone in zip(slots, p):
                if bone is not None:
                    roles["%s_%s" % (side, slot)] = bone
    return roles, problems


def _segment(tr, a, b):
    return _len(_sub(tr.head(b), tr.head(a)))


def validate_bone_map(tree, roles):
    """Problems of a bone map {role: bone} on this tree (empty: valid)."""
    tree = _as_tree(tree)
    tr = _Tree(tree)
    problems = []
    if not isinstance(roles, dict):
        return ["the bone map must be an object {role: bone}"]
    for r in roles:
        if r not in ROLES:
            problems.append("unknown role %s" % r)
    for r in REQUIRED:
        if not roles.get(r):
            problems.append("role %s is missing" % r)
    for r, b in roles.items():
        if b and b not in tree:
            problems.append("role %s names bone %s, which the rig does not have" % (r, b))
    seen = {}
    for r, b in roles.items():
        if b in seen:
            problems.append("bone %s has two roles: %s and %s" % (b, seen[b], r))
        seen[b] = r
    if problems:
        return problems
    g = roles.get

    def child_of(child, parent):
        if g(child) and g(parent) and tr.parent(g(child)) != g(parent):
            problems.append("%s (%s) must be a child of %s (%s)" % (child, g(child), parent, g(parent)))

    def under(child, parent):
        if g(child) and g(parent) and (g(child) == g(parent) or not tr.is_ancestor(g(parent), g(child))):
            problems.append("%s (%s) must hang below %s (%s)" % (child, g(child), parent, g(parent)))

    spine_roles = [r for r in ("spine", "chest", "upper_chest") if g(r)]
    under("spine", "hips")
    for a, b in zip(spine_roles, spine_roles[1:]):
        under(b, a)
    top = spine_roles[-1]
    if g("neck"):
        under("neck", top)
        under("head", "neck")
    else:
        under("head", top)
    for side in SIDES:
        s = side + "_"
        under(s + "upper_arm", "spine")
        if g(s + "shoulder"):
            under(s + "shoulder", "spine")
            child_of(s + "upper_arm", s + "shoulder")
        child_of(s + "lower_arm", s + "upper_arm")
        child_of(s + "hand", s + "lower_arm")
        under(s + "upper_leg", "hips")
        if tr.is_ancestor(g("spine"), g(s + "upper_leg")):
            problems.append("%supper_leg (%s) hangs below the spine" % (s, g(s + "upper_leg")))
        child_of(s + "lower_leg", s + "upper_leg")
        child_of(s + "foot", s + "lower_leg")
        if g(s + "toes"):
            child_of(s + "toes", s + "foot")
    if problems:
        return problems
    front = _front_of_feet(tr, [(g(s + "_foot"), g(s + "_toes")) for s in SIDES])
    if front is None:
        return problems + ["the feet point nowhere: left and right cannot be checked"]
    left = _cross((0.0, 0.0, 1.0), front)
    hips = tr.head(g("hips"))
    side_of = {}
    for part in ("hand", "foot"):
        for side in SIDES:
            d = _dot(_sub(tr.head(g("%s_%s" % (side, part))), hips), left)
            side_of[(side, part)] = "left" if d > 0 else "right"
            if side_of[(side, part)] != side:
                problems.append("%s_%s (%s) is on the %s of the body (front %s)"
                                % (side, part, g("%s_%s" % (side, part)), side_of[(side, part)], list(front)))
    for side in SIDES:
        if side_of[(side, "hand")] != side_of[(side, "foot")]:
            problems.append("%s_hand and %s_foot are on different sides" % (side, side))
    for name, a, b in (("upper arm", "upper_arm", "lower_arm"), ("lower arm", "lower_arm", "hand"),
                       ("upper leg", "upper_leg", "lower_leg"), ("lower leg", "lower_leg", "foot")):
        ln = _segment(tr, g("left_" + a), g("left_" + b))
        rn = _segment(tr, g("right_" + a), g("right_" + b))
        big = max(ln, rn)
        if big <= 0 or abs(ln - rn) / big > SIDE_TOLERANCE:
            problems.append("the %s is %.3f long on the left and %.3f on the right (more than %d%% apart)"
                            % (name, ln, rn, round(SIDE_TOLERANCE * 100)))
    return problems


def _doc(tree, roles, source, manual, problems):
    valid = not problems
    tr = _Tree(tree)
    front = left = None
    if roles.get("left_foot") and roles.get("right_foot") and all(b in tree for b in roles.values()):
        front = _front_of_feet(tr, [(roles[s + "_foot"], roles.get(s + "_toes")) for s in SIDES])
        if front is not None:
            left = _cross((0.0, 0.0, 1.0), front)
    mapped = set(roles.values())
    return {"format": FORMAT, "version": 1, "source": source, "manual": manual, "valid": valid,
            "roles": dict(roles) if valid else None, "found": dict(roles),
            "godot_names": {r: godot_name(r) for r in roles if r in ROLES} if valid else None,
            "unmapped": [n for n in tree if n not in mapped],
            "front": list(front) if front else None, "left": list(left) if left else None,
            "problems": problems}


def propose_bone_map(armature):
    """The proposed bone map with its validation report:
    {"roles": {role: bone} or None when invalid, "found": what was found, "valid",
     "problems", "unmapped", "front", "left", ...} (the content of bone_map.json)."""
    tree = _as_tree(armature)
    roles, problems = _find(tree)
    if not problems:
        problems = validate_bone_map(tree, roles)
    return _doc(tree, roles, "proposed", False, problems)


def load_bone_map(path_or_map):
    """A bone map given as a dictionary, a bone_map.json document or a path to either."""
    data = path_or_map
    if isinstance(path_or_map, str):
        with open(path_or_map, encoding="utf-8-sig") as f:
            data = json.load(f)
    if isinstance(data, dict) and "roles" in data and isinstance(data["roles"], (dict, type(None))):
        return data["roles"] or {}
    return data


def choose_bone_map(armature, given=None, existing=None):
    """The bone map to use: `given` (the order's inputs.bone_map: a path or a dictionary)
    wins, then `existing` (a bone_map.json path) when it is marked "manual": true, then the
    proposal. Always validated; an invalid result has "valid": false and its problems."""
    tree = _as_tree(armature)
    if given:
        roles = load_bone_map(given)
        return _doc(tree, roles, "order", True, validate_bone_map(tree, roles))
    if existing and os.path.exists(existing):
        with open(existing, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("manual"):
            roles = load_bone_map(data)
            return _doc(tree, roles, "manual", True, validate_bone_map(tree, roles))
    return propose_bone_map(tree)


def bone_roles(armature):
    """{bone: role} of the proposed map; ValueError naming the problems when it is invalid."""
    doc = propose_bone_map(armature)
    if not doc["valid"]:
        raise ValueError("invalid bone map: " + "; ".join(doc["problems"]))
    return {b: r for r, b in doc["roles"].items()}


def renames(roles):
    """{bone: new name} for a valid map {role: bone}: the Godot name of each role."""
    return {b: godot_name(r) for r, b in roles.items() if b}


def chains(tree, roles):
    """The chains the exporter poses, by bone name: spine (every bone from spine to the
    top, intermediates included), head ([neck, head] or [head]), arms and legs per side
    ([shoulder or None, upper, lower, hand] and [upper, lower, foot, toes or None])."""
    tree = _as_tree(tree)
    tr = _Tree(tree)
    top = roles.get("upper_chest") or roles.get("chest") or roles["spine"]
    path = [top]
    while path[-1] != roles["spine"]:
        p = tr.parent(path[-1])
        if p is None:
            raise ValueError("the spine role is not above the top of the spine")
        path.append(p)
    out = {"hips": roles["hips"], "spine": list(reversed(path)),
           "head": [b for b in (roles.get("neck"), roles["head"]) if b],
           "arms": {}, "legs": {}}
    for s in SIDES:
        out["arms"][s] = [roles.get(s + "_shoulder"), roles[s + "_upper_arm"], roles[s + "_lower_arm"],
                          roles[s + "_hand"]]
        out["legs"][s] = [roles[s + "_upper_leg"], roles[s + "_lower_leg"], roles[s + "_foot"],
                          roles.get(s + "_toes")]
    return out
