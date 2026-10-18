"""Runners of the local tools that use the graphics card: the rig (SkinTokens or UniRig), the
humanoid export (Blender, which bakes in Cycles) and the review sheet (Godot).

Each runner starts the external process without a window, keeps its stdout and stderr next
to its output (<out>/<step>.stdout.log, <out>/<step>.stderr.log), writes the provenance of
the step (<out>/<step>.provenance.json: tool, its version, script hashes, settings, seeds,
input and output hashes) and returns the files it produced {role: path}, or raises
ToolError naming the log. The scheduler decides when they may run (only with the ComfyUI
queue empty and after /free); the runners do not talk to ComfyUI.
"""

import json
import os
import subprocess
import time

from .config import missing_tools
from .factory_util import sha_file, sha_file_cached, write_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
EXPORT_SCRIPT = os.path.join(TOOLS, "blender", "humanoid_export.py")
BONES_SCRIPT = os.path.join(TOOLS, "blender", "humanoid_bones.py")
REVIEW_PROJECT = os.path.join(TOOLS, "review_godot")
REVIEW_FILES = ("project.godot", "main.tscn", "main.gd")
SKINTOKENS_ENTRY = os.path.join(TOOLS, "rig", "skintokens_entry.py")

RIG_METHODS = {
    "skintokens": ("skintokens_dir", "skintokens_python"),
    "unirig": ("unirig_dir", "unirig_venv", "unirig_hf", "unirig_shim"),
}
STEP_TOOLS = {"export": ("blender_python",), "review": ("godot",)}
MODEL_EXTENSIONS = (".ckpt", ".safetensors", ".pth", ".pt", ".bin")
# Exporter thresholds of the motion report: name -> command-line option.
THRESHOLDS = {"foot_slide_cm": "--max-foot-slide", "hand_socket_cm": "--max-hand-socket",
              "reach_shortfall_cm": "--max-reach", "stretch_p95": "--max-stretch",
              "penetration_cm": "--max-penetration"}
# Exit code of the exporter when the bone map is invalid.
EXIT_BONE_MAP = 3


class ToolError(RuntimeError):
    def __init__(self, message, log=None):
        RuntimeError.__init__(self, message)
        self.log = log


def tools_for(step, method=None):
    """Configuration keys a step needs: "rig" (with its method), "export" or "review"."""
    if step == "rig":
        method = method or "skintokens"
        if method not in RIG_METHODS:
            raise ValueError("unknown rig method %s (known: %s)" % (method, ", ".join(RIG_METHODS)))
        return RIG_METHODS[method]
    return STEP_TOOLS[step]


def check_tools(config, step, method=None):
    """Problems with the tools of a step on this machine (empty: ready)."""
    return missing_tools(config, tools_for(step, method))


def _require(config, step, method=None):
    problems = check_tools(config, step, method)
    if problems:
        raise ToolError("%s cannot run: %s" % (step, "; ".join(problems)))


def _venv_python(venv):
    if os.name == "nt":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def _git_revision(folder):
    """The checked-out commit of a git folder, read from .git without running git."""
    git = os.path.join(folder, ".git")
    try:
        if os.path.isfile(git):  # a worktree or submodule: "gitdir: <path>"
            with open(git, encoding="utf-8") as f:
                git = os.path.join(folder, f.read().split(":", 1)[1].strip())
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(" ", 1)[1]
        path = os.path.join(git, ref)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        packed = os.path.join(git, "packed-refs")
        if os.path.exists(packed):
            with open(packed, encoding="utf-8") as f:
                for line in f:
                    if line.strip().endswith(" " + ref):
                        return line.split()[0]
    except (OSError, IndexError):
        return None
    return None


def _model_files(folder, limit=50):
    """Model weight files under a folder, with sizes and hashes (cached by size and time)."""
    out = []
    for base, _, files in os.walk(folder):
        for fn in sorted(files):
            if fn.lower().endswith(MODEL_EXTENSIONS):
                path = os.path.join(base, fn)
                out.append({"file": os.path.relpath(path, folder).replace(os.sep, "/"),
                            "bytes": os.path.getsize(path), "sha256": sha_file_cached(path)})
                if len(out) >= limit:
                    return out
    return out


def _input(role, path):
    return {"role": role, "file": os.path.abspath(path).replace(os.sep, "/"), "sha256": sha_file(path),
            "factory_output": False}


def _folder_inputs(role, folder, ext=".json"):
    out = []
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(ext):
            out.append(_input("%s/%s" % (role, fn), os.path.join(folder, fn)))
    return out


def _run(cmd, out, step, cwd=None, env=None, timeout=None):
    """Run one external process (no window), logs next to the output. Returns (seconds,
    stdout text). Raises ToolError naming the log when it fails."""
    os.makedirs(out, exist_ok=True)
    so = os.path.join(out, step + ".stdout.log")
    se = os.path.join(out, step + ".stderr.log")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    t0 = time.time()
    with open(so, "w", encoding="utf-8", errors="replace") as fo, \
            open(se, "w", encoding="utf-8", errors="replace") as fe:
        fo.write("$ %s\n" % subprocess.list2cmdline(cmd))
        fo.flush()
        proc = subprocess.Popen(cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, cwd=cwd, env=env, **flags)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()  # only the process this runner started
            proc.wait()
            raise ToolError("%s timed out after %s s, see %s" % (step, timeout, se), se)
    seconds = round(time.time() - t0, 2)
    with open(so, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if code != 0:
        raise ToolError("%s failed (exit %d), see %s" % (step, code, se), se) if code != EXIT_BONE_MAP else \
            ToolError("%s stopped: invalid bone map (see %s and %s)" % (step, os.path.join(out, "bone_map.json"), so), so)
    return seconds, text


def _write_provenance(out, step, record, files):
    record = dict(record)
    record["outputs"] = {role: {"file": os.path.basename(p), "sha256": sha_file(p)} for role, p in files.items()}
    write_json(os.path.join(out, step + ".provenance.json"), record)


def provenance(out, step):
    """The provenance record a runner wrote for `step` in `out`."""
    with open(os.path.join(out, step + ".provenance.json"), encoding="utf-8") as f:
        return json.load(f)


def record_files(base, files):
    """{role: [path relative to base, sha256]}: the manifest form of a runner's files."""
    return {role: [os.path.relpath(p, base), sha_file(p)] for role, p in files.items()}


def _record(workflow, scripts, tools, settings, inputs, seeds=(), models=()):
    return {"kind": "local_gpu", "group": "local_gpu", "workflow": _rel(workflow),
            "workflow_sha256": sha_file(workflow),
            "tool_files": {_rel(p): sha_file(p) for p in scripts},
            "tools": tools, "models": list(models), "prompt": None, "negative": None,
            "seeds": list(seeds), "settings": settings, "inputs": inputs}


# ---- rig -----------------------------------------------------------------------------------

def run_rig(mesh, out, config, seed=9001, method="skintokens", beams=10, postprocess=False, timeout=3600):
    """Skeleton and weights for a painted mesh. skintokens: the configured SkinTokens checkout
    run by its own Python through tools/rig/skintokens_entry.py (fixed seed, `beams` beams,
    skeleton and weights transferred onto the given mesh, offline). unirig: UniRig's python
    steps (extract, skeleton, extract, skin, merge). Returns {"rig": <out>/rigged.glb}."""
    method = method or "skintokens"
    _require(config, "rig", method)
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    mesh = os.path.abspath(mesh)
    rig = os.path.join(out, "rigged.glb")
    if os.path.exists(rig):
        os.remove(rig)  # this step's own output from an earlier attempt
    t0 = time.time()
    if method == "skintokens":
        checkout = config["skintokens_dir"]
        cmd = [config["skintokens_python"], "-u", SKINTOKENS_ENTRY, "--checkout", checkout, "--model", mesh,
               "--out", rig, "--seed", str(seed), "--beams", str(beams)] + (["--postprocess"] if postprocess else [])
        _run(cmd, out, "rig", cwd=checkout, timeout=timeout)
        tools = {"skintokens_dir": {"path": checkout, "revision": _git_revision(checkout)},
                 "skintokens_python": {"path": config["skintokens_python"]}}
        models = _model_files(os.path.join(checkout, "experiments"))
        workflow, scripts = SKINTOKENS_ENTRY, [SKINTOKENS_ENTRY]
        settings = {"method": method, "num_beams": beams, "use_transfer": True, "use_postprocess": postprocess}
    elif method == "unirig":
        unirig = config["unirig_dir"]
        python = _venv_python(config["unirig_venv"])
        env = dict(os.environ, PYTHONPATH=config["unirig_shim"], HF_HOME=config["unirig_hf"],
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        npz = os.path.join(out, "npz")
        skeleton = os.path.join(out, "skeleton.fbx")
        skin = os.path.join(out, "skin.fbx")
        suffix = "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm"
        extract = ["-m", "src.data.extract", "--config=configs/data/quick_inference.yaml", suffix,
                   "--force_override=true", "--num_runs=1", "--id=0", "--time=forge", "--faces_target_count=50000"]
        steps = [
            ("rig_extract", extract + ["--input=" + mesh, "--output_dir=" + npz]),
            ("rig_skeleton", ["run.py", "--task=configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml",
                              "--seed=%d" % seed, "--input=" + mesh, "--output=" + skeleton, "--npz_dir=" + npz]),
            ("rig_extract_skeleton", extract + ["--input=" + skeleton, "--output_dir=" + npz]),
            ("rig_skin", ["run.py", "--task=configs/task/quick_inference_unirig_skin.yaml", "--seed=%d" % seed,
                          "--input=" + skeleton, "--output=" + skin, "--npz_dir=" + npz, "--data_name=raw_data.npz"]),
            ("rig_merge", ["-m", "src.inference.merge", suffix, "--num_runs=1", "--id=0", "--source=" + skin,
                           "--target=" + mesh, "--output=" + rig]),
        ]
        for step, args in steps:
            _run([python] + args, out, step, cwd=unirig, env=env, timeout=timeout)
        tools = {"unirig_dir": {"path": unirig, "revision": _git_revision(unirig)},
                 "unirig_venv": {"path": config["unirig_venv"]}}
        models = _model_files(config["unirig_hf"])
        workflow, scripts = os.path.abspath(__file__), []
        settings = {"method": method, "faces_target_count": 50000,
                    "steps": [s for s, _ in steps]}
    else:
        raise ValueError("unknown rig method %s" % method)
    if not os.path.exists(rig):
        raise ToolError("rig (%s) returned without %s, see %s" % (method, rig, os.path.join(out, "rig.stdout.log")))
    files = {"rig": rig}
    rec = _record(workflow, scripts, tools, settings, [_input("mesh", mesh)], seeds=[seed], models=models)
    rec["exec_seconds"] = round(time.time() - t0, 2)
    _write_provenance(out, "rig", rec, files)
    return files


# ---- export --------------------------------------------------------------------------------

EXPORT_PARAMS = {"tris": "--tris", "height": "--height", "head": "--head", "hands": "--hands",
                 "feet": "--feet", "widen": "--widen"}


def run_export(rig, out, config, params=None, clips=None, items=None, name="model", bake_source=None,
               bone_map=None, glow=None, device=None, thresholds=None, timeout=7200):
    """The humanoid export (tools/blender/humanoid_export.py in blender_python).

    params: tris, height, head, hands, feet, widen (piece params), normal_map (normal map
    size; default texture, else 2048). clips: motion library; items: held-item folder;
    bake_source: the raw high mesh; bone_map: the order's bone map (a JSON file); glow: True
    or (low, high) hues; device: Cycles device (default: the configuration's bake_device);
    thresholds: motion report thresholds by name (THRESHOLDS).
    Returns {glb, model, regions_mask, bone_map, mesh_report[, motion_report]}."""
    _require(config, "export")
    params = dict(params or {})
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    device = (device or config.get("bake_device") or "GPU").upper()
    cmd = [config["blender_python"], EXPORT_SCRIPT, "--rig", os.path.abspath(rig), "--out", out, "--name", name,
           "--device", device]
    tex = params.pop("normal_map", None) or params.get("texture") or 2048
    cmd += ["--tex", str(int(tex))]
    settings = {"name": name, "normal_map": int(tex), "device": device}
    for k, opt in EXPORT_PARAMS.items():
        if params.get(k) is not None:
            cmd += [opt, str(params[k])]
            settings[k] = params[k]
    inputs = [_input("rig", rig)]
    if clips:
        cmd += ["--clips", os.path.abspath(clips)]
        inputs.append(_input("clips", clips))
    if items:
        cmd += ["--items", os.path.abspath(items)]
        inputs += _folder_inputs("items", items)
    if bake_source:
        cmd += ["--bake-source", os.path.abspath(bake_source)]
        inputs.append(_input("bake_source", bake_source))
    if bone_map:
        cmd += ["--bone-map", os.path.abspath(bone_map)]
        inputs.append(_input("bone_map", bone_map))
    if glow:
        cmd.append("--glow")
        settings["glow"] = True
        if isinstance(glow, (list, tuple)):
            cmd += ["--glow-hues", "%s,%s" % (glow[0], glow[1])]
            settings["glow_hues"] = list(glow)
    for k, v in (thresholds or {}).items():
        if k not in THRESHOLDS:
            raise ValueError("unknown motion threshold %s (known: %s)" % (k, ", ".join(THRESHOLDS)))
        cmd += [THRESHOLDS[k], str(v)]
        settings.setdefault("thresholds", {})[k] = v
    for stale in ("model.json", "motion_report.json", "mesh_report.json", name + ".glb"):
        if os.path.exists(os.path.join(out, stale)):
            os.remove(os.path.join(out, stale))
    seconds, text = _run(cmd, out, "export", timeout=timeout)
    files = {"glb": os.path.join(out, name + ".glb"), "model": os.path.join(out, "model.json"),
             "regions_mask": os.path.join(out, "regions_mask.png"), "mesh_report": os.path.join(out, "mesh_report.json")}
    bm = os.path.join(out, "bone_map.json")
    files["bone_map"] = bm if os.path.exists(bm) else os.path.join(out, "bone_map_report.json")
    if clips:
        files["motion_report"] = os.path.join(out, "motion_report.json")
    missing = [p for p in files.values() if not os.path.exists(p)]
    if missing:
        raise ToolError("export did not write %s, see %s" % (", ".join(missing), os.path.join(out, "export.stdout.log")))
    version = next((ln.split(" ", 1)[1].strip() for ln in text.splitlines() if ln.startswith("BPY ")), None)
    tools = {"blender_python": {"path": config["blender_python"], "version": "bpy " + version if version else None}}
    rec = _record(EXPORT_SCRIPT, [EXPORT_SCRIPT, BONES_SCRIPT], tools, settings, inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "export", rec, files)
    return files


# ---- review --------------------------------------------------------------------------------

def _failure(g):
    """The failure reason of a variant entry, or None for a GLB path."""
    if g is None:
        return "failed"
    if isinstance(g, dict):
        return str(g.get("failed") or "failed")
    if isinstance(g, str) and g.startswith("!"):
        return g[1:] or "failed"
    return None


def run_review(glbs, out, config, camera=None, labels=None, name="sheet", timeout=900):
    """One review sheet of a piece's variants (tools/review_godot in Godot, off screen).

    glbs: per variant a GLB path, or a failure mark (None, "!reason" or {"failed": reason}).
    camera: pitch (degrees, default 60), yaws (list of degrees) or directions (count, default
    8), figure_height (px, default 96), front (bool, default True), front_height (px),
    animation (default: the first idle), strip_frames (8), strip_yaw (degrees).
    Returns {"sheet": <out>/<name>.png, "layout": <out>/<name>.png.json}."""
    _require(config, "review")
    cam = dict(camera or {})
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    sheet = os.path.join(out, name + ".png")
    yaws = cam.get("yaws")
    if yaws is None:
        n = int(cam.get("directions", 8))
        yaws = [round(360.0 * i / n, 3) for i in range(n)]
    args = ["--out", sheet.replace(os.sep, "/"), "--pitch", str(cam.get("pitch", 60)),
            "--yaws", ",".join(str(y) for y in yaws), "--figure", str(int(cam.get("figure_height", 96))),
            "--front", "1" if cam.get("front", True) else "0"]
    for key, opt in (("front_height", "--front-height"), ("animation", "--animation"),
                     ("strip_frames", "--strip-frames"), ("strip_yaw", "--strip-yaw")):
        if cam.get(key) is not None:
            args += [opt, str(cam[key])]
    inputs = []
    labels = list(labels or [])
    for i, g in enumerate(glbs):
        label = labels[i] if i < len(labels) else "v%d" % (i + 1)
        reason = _failure(g)
        if reason is not None:
            args += ["--variant", "%s=!%s" % (label, reason)]
        else:
            args += ["--variant", "%s=%s" % (label, os.path.abspath(g).replace(os.sep, "/"))]
            inputs.append(_input("variant/" + label, g))
            for side in ("model.json", "motion_report.json", "mesh_report.json"):
                p = os.path.join(os.path.dirname(os.path.abspath(g)), side)
                if os.path.exists(p):
                    inputs.append(_input("variant/%s/%s" % (label, side), p))
    for stale in (sheet, sheet + ".json"):
        if os.path.exists(stale):
            os.remove(stale)
    cmd = [config["godot"], "--path", REVIEW_PROJECT, "--audio-driver", "Dummy", "--windowed",
           "--position", "-20000,-20000", "--resolution", "64x64", "--"] + args
    seconds, text = _run(cmd, out, "review", timeout=timeout)
    files = {"sheet": sheet, "layout": sheet + ".json"}
    for p in files.values():
        if not os.path.exists(p):
            raise ToolError("review did not write %s, see %s" % (p, os.path.join(out, "review.stderr.log")))
    version = next((ln.split(" - ")[0].replace("Godot Engine ", "").strip() for ln in text.splitlines()
                    if ln.startswith("Godot Engine ")), None)
    tools = {"godot": {"path": config["godot"], "version": version}}
    scripts = [os.path.join(REVIEW_PROJECT, f) for f in REVIEW_FILES]
    settings = {"pitch": cam.get("pitch", 60), "yaws": yaws, "figure_height": int(cam.get("figure_height", 96)),
                "front": bool(cam.get("front", True))}
    for key in ("front_height", "animation", "strip_frames", "strip_yaw"):
        if cam.get(key) is not None:
            settings[key] = cam[key]
    rec = _record(os.path.join(REVIEW_PROJECT, "main.gd"), scripts, tools, settings, inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "review", rec, files)
    return files


if __name__ == "__main__":  # a manual check of this machine's configuration
    import sys
    from .config import load_config
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "forge.local.json"))
    for step, method in (("rig", "skintokens"), ("rig", "unirig"), ("export", None), ("review", None)):
        print(step, method or "", check_tools(cfg, step, method) or "ready")
