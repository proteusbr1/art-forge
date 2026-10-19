"""The humanoid exporter (humanoid_export.py, unchanged) run on a rig that wears an approved
body (humanoid_conform.py's conformed.glb): the rig already stands in the body's frame, so it
is not stood, turned or scaled again, and the clips are scaled by the body's height and floor,
not by the outfit's mesh (a helmet or a thick sole would change them). Every variant that wears
the same body then gets the body's skeleton and the same retargeted motions.

Runs with a Python that imports bpy (Blender as a module):

  python humanoid_body_export.py --frame FRAME.json <the arguments of humanoid_export.py>

FRAME.json is humanoid_conform.py's frame.json: {"height", "floor", ...}; the caller passes the
body's widen, head, hands and feet to the exporter (--widen, --head, --hands, --feet), its
height (--height) and the body's roles as the bone map (--bone-map body_map.json).

The exporter is a script, not a module, and its file is in the specification of every humanoid
export: it is read and run here with three statements replaced (each must be found exactly
once, or this stops with exit code 2):
- the stand, turn, scale and widen of the rig (TRANSFORM) becomes the identity;
- the floor (zmin) and the height (H) of the figure become the body's.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTER = os.path.join(HERE, "humanoid_export.py")
REPLACEMENTS = (
    ("TRANSFORM = (Matrix.Diagonal((a.widen, a.widen, 1.0, 1.0)) @ Matrix.Diagonal((_k, _k, _k, 1.0)) @ _turn\n"
     "             @ Matrix.Translation(Vector((-_hips.x, -_hips.y, -_floor))))\n",
     "TRANSFORM = Matrix.Identity(4)  # humanoid_body_export.py: the rig stands in the body's frame\n"),
    ("zmin = min(c.z for c in coords)\n",
     "zmin = BODY_FRAME[\"floor\"]  # humanoid_body_export.py: the body's floor\n"),
    ("H = zmax - zmin\n",
     "H = BODY_FRAME[\"height\"]  # humanoid_body_export.py: the body's height\n"),
)


def patched_source(text):
    """The exporter's source with REPLACEMENTS applied; ValueError when one is not found
    exactly once."""
    text = text.replace("\r\n", "\n")
    for old, new in REPLACEMENTS:
        n = text.count(old)
        if n != 1:
            raise ValueError("humanoid_export.py has %d copies of %r (1 expected): this wrapper must be updated"
                             % (n, old.splitlines()[0]))
        text = text.replace(old, new)
    return text


def main(argv):
    if "--frame" not in argv or argv.index("--frame") + 1 >= len(argv):
        print("FAILED --frame FRAME.json is required", flush=True)
        return 2
    i = argv.index("--frame")
    with open(argv[i + 1], encoding="utf-8-sig") as f:
        frame = json.load(f)
    rest = argv[:i] + argv[i + 2:]
    with open(EXPORTER, encoding="utf-8") as f:
        source = f.read()
    try:
        code = compile(patched_source(source), EXPORTER, "exec")
    except ValueError as ex:
        print("FAILED", ex, flush=True)
        return 2
    print("BODY FRAME height %.6f floor %.6f" % (frame["height"], frame["floor"]), flush=True)
    sys.argv = [EXPORTER] + rest
    glb = {"__name__": "__main__", "__file__": EXPORTER,
           "BODY_FRAME": {"height": float(frame["height"]), "floor": float(frame["floor"])}}
    exec(code, glb)  # the exporter ends the process itself (os._exit)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
