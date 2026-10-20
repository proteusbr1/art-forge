"""Pinned specification hashes of the sample orders' jobs. A job whose specification hash
changes runs again on the next resume of a night, so the humanoid and core recipes' jobs
must keep their hashes when other recipes are added: this test fails when any of them
changes. Change a pin only on purpose (a changed workflow, tool or recipe), with the reason
in the commit."""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import fake_comfy  # noqa: E402,F401  (FORGE_LOCK_DIR and FORGE_NVIDIA_SMI for the tests)
from forge.config import load_config  # noqa: E402
from forge.factory_core import Factory  # noqa: E402
from forge.factory_util import sha_obj  # noqa: E402
from forge.recipes import refs_of  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
ROLES = ("rgb", "mask", "rgba", "game_mesh", "raw_mesh", "painted_mesh", "metal_roughness", "rig", "glb", "model",
         "mesh_report", "motion_report", "image", "pick", "albedo", "paint_views", "shape", "background_mask")
# sha_obj({job id: specification hash}) of each sample order. The humanoid's moved on purpose
# on 2026-10-19 with the exporter's fix (bones aimed by their joints, exact two-bone IK, the
# figure turned to face glTF +z): its script enters the export's specification, so a resumed
# night redoes its exports, sheets and decisions. The hashes of the local tools' scripts are
# taken from the files as checked out, so the pins hold for a checkout with CRLF line endings
# (Windows, core.autocrlf true, which is also what `git archive` gives a night's snapshot);
# the earlier humanoid pin had been taken from files with mixed line endings. It moved again on
# purpose on 2026-10-19 with the bone map's fix (the neck chosen among the spine top's children
# by several cues, not by the highest end point): the bone map module is in the export's
# specification, so a resumed night redoes its exports, sheets and decisions. It moved again on
# purpose on 2026-10-19 with the facing fix (the figure squared by its hips' and shoulders' lines,
# not by the axis its feet point to; the body frame an exporter option): the exporter and the
# bone map module are in the export's specification, so a resumed night redoes its exports,
# sheets and decisions. It moved again on purpose on 2026-10-20 with the glow (what glows on the
# painted texture found from the colours the piece's text names, written into the GLB's
# emissive slot): the exporter and tools/blender/glow_regions.py are in the export's
# specification. The sample order's texts name no glowing colour, so its exports write the same
# files as before; a resumed night redoes them, their sheets and decisions all the same.
PINS = {
    "order_generic.json": "a148805423689b29e1f8fa3cff06720115ec0753be4f28d322bc879f25c78e44",
    "order_humanoid.json": "5e754c9e077c89aa959025f97ad77ae77d21999c6bb2a2c8518f1c20b3564c97",
}


def specs(order_path, tmp):
    """Every job's specification hash, each job "finished" in dependency order with output
    hashes derived from its own specification (a changed job changes its dependants)."""
    config = load_config(None)
    for k in ("blender_python", "godot", "skintokens_python"):
        config[k] = sys.executable
    config["skintokens_dir"] = tmp
    a = SimpleNamespace(order=order_path, order_base="", out=os.path.join(tmp, "out"), only="", retry_failed=False)
    f = Factory(a, "http://127.0.0.1:1")
    f.config = config
    f.load()
    out = {}
    while len(out) < len(f.jobs):
        grew = False
        for j in f.jobs:
            if j["id"] in out or any(r not in out for r in refs_of(j)):
                continue
            sp = f.spec(j)
            out[j["id"]] = sp
            grew = True
            entry = {"status": "done", "spec": sp, "files": {r: ["x/%s.bin" % r, sha_obj([sp, r])] for r in ROLES}}
            if j["kind"] == "pick":
                entry["chosen"] = j["from"][0]
            f.manifest[j["id"]] = entry
        if not grew:
            raise AssertionError("jobs that never become ready: %s" % sorted(set(x["id"] for x in f.jobs) - set(out)))
    return out


class SpecificationPins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge_pins_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sample_orders_keep_their_specification_hashes(self):
        for name, pin in PINS.items():
            got = sha_obj(specs(os.path.join(FIXTURES, name), self.tmp))
            self.assertEqual(got, pin, "the specification hashes of %s changed" % name)


if __name__ == "__main__":
    unittest.main()
