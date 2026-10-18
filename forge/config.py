"""Machine configuration: where the ComfyUI server and the local tools of this machine are.

forge.local.json at the repository root (ignored by git; forge.local.example.json is the
committed example). Every key is optional; a missing tool only matters when an order has a
stage that needs it (forge.order.check_order names it). Relative paths are resolved against
the folder of the configuration file.
"""

import json
import os

from .comfy_server import DEFAULT_SERVER

# Key -> what it is (also the error text when a stage needs it and it is missing).
KEYS = {
    "comfy_url": "address of the local ComfyUI server",
    "python": "Python 3 with NumPy and Pillow, used to run the factory",
    "blender_python": "Python interpreter that can import bpy (Blender as a module)",
    "unirig_dir": "UniRig source folder",
    "unirig_venv": "virtual environment of UniRig",
    "unirig_hf": "Hugging Face cache folder with UniRig's weights (HF_HOME)",
    "unirig_shim": "folder put on UniRig's PYTHONPATH (stand-ins for modules without a Windows build)",
    "godot": "Godot 4 executable (the review sheets)",
}
TOOL_KEYS = tuple(k for k in KEYS if k != "comfy_url")


def load_config(path=None):
    """The configuration as a dictionary with every key of KEYS (None when not set).
    path None or a missing file gives the defaults (only comfy_url set)."""
    cfg = {k: None for k in KEYS}
    cfg["comfy_url"] = DEFAULT_SERVER
    if not path or not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("%s: the configuration must be a JSON object" % path)
    unknown = sorted(k for k in data if k not in KEYS and not k.startswith("_") and k != "note")
    if unknown:
        raise ValueError("%s: unknown keys %s (known: %s)" % (path, unknown, ", ".join(KEYS)))
    base = os.path.dirname(os.path.abspath(path))
    for k in KEYS:
        v = data.get(k)
        if v in (None, ""):
            continue
        if not isinstance(v, str):
            raise ValueError("%s: %s must be a string" % (path, k))
        if k != "comfy_url":
            v = os.path.normpath(os.path.join(base, os.path.expandvars(os.path.expanduser(v))))
        cfg[k] = v
    return cfg


def missing_tools(config, keys):
    """Problems for the tool keys a stage needs that are not set or do not exist."""
    out = []
    for k in keys:
        v = config.get(k)
        if not v:
            out.append("%s is not set in the configuration (%s)" % (k, KEYS.get(k, k)))
        elif not os.path.exists(v):
            out.append("%s points to a missing path: %s" % (k, v))
    return out
