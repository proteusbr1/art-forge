"""Synthetic creature skeletons for the tests, in the exporter's model axes (metres, z up,
front -Y, left +X), named bone_0, bone_1, ... as the rig tools name them, with their body
plans: a serpent with a humanoid torso, a four-legged beast, a four-armed biped and a
legless torso over a propulsion unit. make_tree() also shuffles the bone order and can
mirror or extend chains, so a test can make a variant's own skeleton."""

SERPENT_PLAN = {"chains": [{"id": "spine", "from": "root", "toward": "up"},
                           {"id": "neck", "from": "spine", "toward": "up", "optional": True},
                           {"id": "tail", "from": "root", "toward": "back"},
                           {"id": "arm", "from": "spine", "toward": "side", "pairs": 1}],
                "holds": {"right": "arm_r0"}}
BEAST_PLAN = {"chains": [{"id": "spine", "from": "root", "toward": "front"},
                         {"id": "neck", "from": "spine", "toward": "front"},
                         {"id": "foreleg", "from": "spine", "toward": "down", "pairs": 1},
                         {"id": "hindleg", "from": "root", "toward": "down", "pairs": 1},
                         {"id": "tail", "from": "root", "toward": "back", "optional": True}]}
INSECT_PLAN = {"chains": [{"id": "spine", "from": "root", "toward": "up"},
                          {"id": "neck", "from": "spine", "toward": "up", "optional": True},
                          {"id": "arm", "from": "spine", "toward": "side", "pairs": 2},
                          {"id": "leg", "from": "root", "toward": "down", "pairs": 1}]}
HOVER_PLAN = {"chains": [{"id": "spine", "from": "root", "toward": "up"},
                         {"id": "neck", "from": "spine", "toward": "up", "optional": True},
                         {"id": "arm", "from": "spine", "toward": "side", "pairs": 1},
                         {"id": "base", "from": "root", "toward": "down", "optional": True}]}


class Builder:
    def __init__(self):
        self.bones = []  # (key, parent key, head, tail)

    def chain(self, parent, points, key):
        """Bones along points (head of the first to tail of the last); returns their keys."""
        keys = []
        for i in range(len(points) - 1):
            k = "%s%d" % (key, i)
            self.bones.append((k, parent, tuple(points[i]), tuple(points[i + 1])))
            parent = k
            keys.append(k)
        return keys

    def tree(self, order=None):
        """{bone_<n>: {"parent", "head", "tail"}} with names given in `order` of the keys
        (default: as built), and {key: bone name}."""
        keys = [b[0] for b in self.bones]
        order = order or keys
        names = {k: "bone_%d" % i for i, k in enumerate(order)}
        out = {}
        by_key = {b[0]: b for b in self.bones}
        for k in order:
            _, parent, h, t = by_key[k]
            out[names[k]] = {"parent": names.get(parent), "head": h, "tail": t}
        return out, names


def mirror(points):
    return [(-x, y, z) for x, y, z in points]


def serpent():
    b = Builder()
    hips = b.chain(None, [(0, 0, 0.85), (0, 0, 0.95)], "hips")
    spine = b.chain(hips[-1], [(0, 0, 0.95), (0, 0.01, 1.15), (0, 0.0, 1.38)], "spine")
    b.chain(spine[-1], [(0, 0, 1.38), (0, -0.01, 1.52), (0, -0.02, 1.85)], "neck")
    arm = [(0.06, 0, 1.36), (0.2, 0.0, 1.34), (0.42, -0.02, 1.16), (0.58, -0.04, 1.02)]
    b.chain(spine[-1], arm, "arml")
    b.chain(spine[-1], mirror(arm), "armr")
    b.chain(hips[-1], [(0, 0, 0.85), (0, 0.06, 0.5), (0, 0.12, 0.16), (0, 0.45, 0.06), (0, 0.85, 0.05),
                       (0, 1.25, 0.04)], "tail")
    return b


def beast():
    b = Builder()
    pelvis = b.chain(None, [(0, 0.45, 0.85), (0, 0.4, 0.9)], "pelvis")
    spine = b.chain(pelvis[-1], [(0, 0.4, 0.9), (0, 0.1, 0.95), (0, -0.25, 0.98)], "spine")
    b.chain(spine[-1], [(0, -0.25, 0.98), (0, -0.45, 1.15), (0, -0.7, 1.2)], "neck")
    fore = [(0.16, -0.25, 0.92), (0.18, -0.24, 0.55), (0.18, -0.26, 0.15), (0.18, -0.33, 0.02)]
    b.chain(spine[-1], fore, "forel")
    b.chain(spine[-1], mirror(fore), "forer")
    hind = [(0.16, 0.45, 0.82), (0.18, 0.4, 0.5), (0.18, 0.48, 0.15), (0.18, 0.41, 0.02)]
    b.chain(pelvis[0], hind, "hindl")
    b.chain(pelvis[0], mirror(hind), "hindr")
    b.chain(pelvis[0], [(0, 0.47, 0.86), (0, 0.8, 0.8), (0, 1.1, 0.7)], "tail")
    return b


def insect():
    b = Builder()
    hips = b.chain(None, [(0, 0, 0.95), (0, 0, 1.05)], "hips")
    spine = b.chain(hips[-1], [(0, 0, 1.05), (0, -0.03, 1.3), (0, -0.05, 1.5)], "spine")
    b.chain(spine[-1], [(0, -0.05, 1.5), (0, -0.08, 1.62), (0, -0.12, 1.8)], "neck")
    for i, z in enumerate((1.48, 1.32)):
        arm = [(0.07, -0.05, z), (0.22, -0.06, z - 0.03), (0.42, -0.12, z - 0.18), (0.55, -0.2, z - 0.3)]
        b.chain(spine[-1] if i == 0 else spine[0], arm, "arml%d_" % i)
        b.chain(spine[-1] if i == 0 else spine[0], mirror(arm), "armr%d_" % i)
    leg = [(0.12, 0, 0.95), (0.14, -0.04, 0.5), (0.14, 0.02, 0.08), (0.14, -0.1, 0.02)]
    b.chain(hips[0], leg, "legl")
    b.chain(hips[0], mirror(leg), "legr")
    return b


def hover():
    b = Builder()
    root = b.chain(None, [(0, 0, 0.75), (0, 0, 0.85)], "root")
    spine = b.chain(root[-1], [(0, 0, 0.85), (0, 0, 1.05), (0, 0, 1.25)], "spine")
    b.chain(spine[-1], [(0, 0, 1.25), (0, -0.01, 1.35), (0, -0.02, 1.6)], "neck")
    arm = [(0.08, 0, 1.22), (0.24, 0, 1.2), (0.42, -0.02, 1.02), (0.55, -0.05, 0.88)]
    b.chain(spine[-1], arm, "arml")
    b.chain(spine[-1], mirror(arm), "armr")
    b.chain(root[0], [(0, 0, 0.75), (0, 0.02, 0.45), (0, 0.03, 0.2)], "base")
    return b
