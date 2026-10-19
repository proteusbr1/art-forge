"""Machine configuration: where the ComfyUI server and the local tools of this machine are.

forge.local.json at the repository root (ignored by git; forge.local.example.json is the
committed example). Every key is optional; a missing tool only matters when an order has a
stage that needs it (forge.order.check_order names it). Relative paths are resolved against
the folder of the configuration file, or against `base` when given (the launcher runs a
copy of the file from its snapshot folder and passes the repository: --config-base).
"""

import glob
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
    "skintokens_dir": "SkinTokens checkout (its demo.py and experiments/ checkpoints)",
    "skintokens_python": "Python of SkinTokens' own environment",
    "godot": "Godot 4 executable (the review sheets; the console build on Windows)",
    "bake_device": "Cycles device for normal-map baking: GPU (default) or CPU",
    "gpu_wait": "seconds a local GPU job waits while another process holds the card (default 300)",
    "gpu_busy_mb": "VRAM in MB another process may hold before a local GPU job waits (default 2048)",
    "gpu_used_busy_mb": "VRAM in MB the whole card may have in use before a local GPU job waits, when the "
                        "driver reports no amount per process (default 4096)",
    "queue_wait_warn": "seconds a local GPU job may wait for the card before the log and state.json call it "
                       "stalled (default 3600)",
    "hymotion_python": "Python with PyTorch (CUDA) and HY-Motion's requirements (the humanoid motions' text source)",
    "hymotion_dir": "HY-Motion checkout (its hymotion package, stats/ and bundled body assets)",
    "hymotion_model": "HY-Motion checkpoint file (HY-Motion-1.0-Lite/latest.ckpt; its config.yml next to it)",
    "hymotion_text_encoder": "folder of HY-Motion's language model text encoder (Qwen3-8B)",
    "hymotion_clip": "folder of HY-Motion's sentence encoder (CLIP ViT-L/14)",
    "hymotion_pythonpath": "optional folder put first on HY-Motion's Python path (packages its Python lacks)",
}
# Keys that are values, not paths on this machine; NUMBER_KEYS are numbers of at least 0.
VALUE_KEYS = ("comfy_url", "bake_device", "gpu_wait", "gpu_busy_mb", "gpu_used_busy_mb", "queue_wait_warn")
NUMBER_KEYS = {"gpu_wait": 300, "gpu_busy_mb": 2048, "gpu_used_busy_mb": 4096, "queue_wait_warn": 3600}
TOOL_KEYS = tuple(k for k in KEYS if k not in VALUE_KEYS)
# Files that must exist inside a tool folder for the tool to run.
TOOL_FILES = {
    "skintokens_dir": ("demo.py",),
    "unirig_dir": ("run.py",),
    "hymotion_dir": (os.path.join("hymotion", "pipeline", "motion_diffusion.py"), os.path.join("stats", "Mean.npy"),
                     os.path.join("stats", "Std.npy")),
    "hymotion_text_encoder": ("config.json",),
    "hymotion_clip": ("config.json",),
    "unirig_venv": (os.path.join("Scripts", "python.exe") if os.name == "nt" else os.path.join("bin", "python"),),
}
# Weight files a tool needs, relative to its configured folder (glob patterns): the
# environment check (forge.readiness) reports the missing ones.
TOOL_WEIGHTS = {
    "skintokens_dir": ("experiments/skin_vae_2_10_32768/last.ckpt",
                       "experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt",
                       "models/Qwen3-0.6B/config.json"),
    "unirig_hf": ("hub/models--VAST-AI--UniRig/snapshots/*/skeleton/articulation-xl_quantization_256/model.ckpt",
                  "hub/models--VAST-AI--UniRig/snapshots/*/skin/articulation-xl/model.ckpt",
                  "hub/models--facebook--opt-350m/snapshots/*/config.json"),
}
BAKE_DEVICES = ("GPU", "CPU")


def load_config(path=None, base=None):
    """The configuration as a dictionary with every key of KEYS (None when not set).
    path None or a missing file gives the defaults (only comfy_url and bake_device set).
    Relative tool paths are resolved against base, else the file's folder."""
    cfg = {k: None for k in KEYS}
    cfg["comfy_url"] = DEFAULT_SERVER
    cfg["bake_device"] = "GPU"
    cfg.update(NUMBER_KEYS)
    if not path or not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("%s: the configuration must be a JSON object" % path)
    unknown = sorted(k for k in data if k not in KEYS and not k.startswith("_") and k != "note")
    if unknown:
        raise ValueError("%s: unknown keys %s (known: %s)" % (path, unknown, ", ".join(KEYS)))
    base = os.path.abspath(base) if base else os.path.dirname(os.path.abspath(path))
    for k in KEYS:
        v = data.get(k)
        if v in (None, ""):
            continue
        if k in NUMBER_KEYS:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError("%s: %s must be a number of at least 0" % (path, k))
            cfg[k] = v
            continue
        if not isinstance(v, str):
            raise ValueError("%s: %s must be a string" % (path, k))
        if k == "bake_device":
            v = v.upper()
            if v not in BAKE_DEVICES:
                raise ValueError("%s: bake_device must be one of %s" % (path, ", ".join(BAKE_DEVICES)))
        elif k not in VALUE_KEYS:
            v = os.path.normpath(os.path.join(base, os.path.expandvars(os.path.expanduser(v))))
        cfg[k] = v
    return cfg


def missing_weights(config, keys):
    """Problems for the weight files (TOOL_WEIGHTS) of the tool keys that are set."""
    out = []
    for k in keys:
        folder = config.get(k)
        if not folder or not os.path.isdir(folder):
            continue
        for pattern in TOOL_WEIGHTS.get(k, ()):
            if not glob.glob(os.path.join(folder, *pattern.split("/"))):
                out.append("%s has no weight file %s: %s" % (k, pattern, folder))
    return out


def missing_tools(config, keys):
    """Problems for the tool keys a stage needs that are not set or do not exist."""
    out = []
    for k in keys:
        v = config.get(k)
        if not v:
            out.append("%s is not set in the configuration (%s)" % (k, KEYS.get(k, k)))
        elif not os.path.exists(v):
            out.append("%s points to a missing path: %s" % (k, v))
        else:
            for rel in TOOL_FILES.get(k, ()):
                if not os.path.exists(os.path.join(v, rel)):
                    out.append("%s has no %s: %s" % (k, rel, os.path.join(v, rel)))
    return out
