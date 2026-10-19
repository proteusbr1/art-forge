"""Runners of the local tools that use the graphics card: the rig (SkinTokens or UniRig), the
humanoid export (Blender, which bakes in Cycles) and the review sheet (Godot).

Each runner starts the external process without a window, keeps its stdout and stderr next
to its output (<out>/<step>.stdout.log, <out>/<step>.stderr.log), writes the provenance of
the step (<out>/<step>.provenance.json: tool, its version, script hashes, settings, seeds,
input and output hashes) and returns the files it produced {role: path}, or raises
ToolError naming the log. The scheduler decides when they may run (only with the ComfyUI
queue empty and after /free); the runners do not talk to ComfyUI. Each runner holds the
machine-wide GPU lock (forge.gpu_lock) while it runs: re-entrant inside a factory run,
refused (GpuBusy) when another process holds it.

A job of kind "local_gpu" names its runner in "tool": RUNNERS[tool](factory, job, out_dir)
returns {"files": {role: path}, "passed": {argument: value}, "provenance": record}. "passed"
are the arguments the runner actually gave the tool (the effective parameters; the job's
"args" are the requested ones) and "provenance" the record the runner wrote.
"""

import functools
import json
import os
import subprocess
import time

from . import gpu_lock
from .config import missing_tools
from .factory_util import sha_file, sha_file_cached, write_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
EXPORT_SCRIPT = os.path.join(TOOLS, "blender", "humanoid_export.py")
BONES_SCRIPT = os.path.join(TOOLS, "blender", "humanoid_bones.py")
MATERIALS_SCRIPT = os.path.join(TOOLS, "blender", "glb_materials.py")
REVIEW_PROJECT = os.path.join(TOOLS, "review_godot")
REVIEW_FILES = ("project.godot", "main.tscn", "main.gd")
SKINTOKENS_ENTRY = os.path.join(TOOLS, "rig", "skintokens_entry.py")

PARTS_SCRIPT = os.path.join(TOOLS, "blender", "articulated_export.py")
PARTS_FILES = [PARTS_SCRIPT, os.path.join(TOOLS, "blender", "articulated_parts.py"),
               os.path.join(TOOLS, "blender", "forge_blender.py"), MATERIALS_SCRIPT]
CREATURE_SCRIPT = os.path.join(TOOLS, "blender", "creature_export.py")
CREATURE_FILES = [CREATURE_SCRIPT, os.path.join(TOOLS, "blender", "creature_bones.py"),
                  os.path.join(TOOLS, "blender", "creature_motion.py"),
                  os.path.join(TOOLS, "blender", "forge_blender.py"), MATERIALS_SCRIPT]
SHEET_FILES = ("project.godot", "sheet.tscn", "sheet.gd")
SHEET_LAYOUT = os.path.join(ROOT, "forge", "sheet_layout.py")

RIG_METHODS = {
    "skintokens": ("skintokens_dir", "skintokens_python"),
    "unirig": ("unirig_dir", "unirig_venv", "unirig_hf", "unirig_shim"),
}
STEP_TOOLS = {"export": ("blender_python",), "review": ("godot",), "parts": ("blender_python",),
              "creature_export": ("blender_python",), "sheet": ("godot",)}
# Creature motion report thresholds: name -> command-line option.
CREATURE_THRESHOLDS = {"foot_slide_cm": "--max-foot-slide", "reach_shortfall_cm": "--max-reach",
                       "stretch_p95": "--max-stretch", "below_ground_cm": "--max-below-ground"}
MODEL_EXTENSIONS = (".ckpt", ".safetensors", ".pth", ".pt", ".bin")
# Exporter thresholds of the motion report: name -> command-line option.
THRESHOLDS = {"foot_slide_cm": "--max-foot-slide", "hand_socket_cm": "--max-hand-socket",
              "reach_shortfall_cm": "--max-reach", "stretch_p95": "--max-stretch",
              "penetration_cm": "--max-penetration"}
# Exit codes of the exporter when the bone map is invalid and when the mesh cannot be
# exported (no UV layer or base colour): another try would fail the same way.
# Image formats of the exported GLB (params.image_format): "lossless" writes every map as
# PNG; "compact" writes the base colour as JPEG (quality 92) and the technical maps as PNG.
IMAGE_FORMATS = ("lossless", "compact")
EXIT_BONE_MAP = 3
EXIT_MESH = 4


# Runners of the "local_gpu" job kind: tool name -> function(factory, job, out_dir).
RUNNERS = {}


def _gpu_locked(step):
    """The runner holds the machine-wide GPU lock while it runs."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with gpu_lock.hold("local tool " + step):
                return fn(*args, **kwargs)
        return wrapper
    return deco


class ToolError(RuntimeError):
    """A local tool failed. log: the file that says why; retry False: another try would fail
    the same way (the scheduler marks the job failed at once)."""

    def __init__(self, message, log=None, retry=True):
        RuntimeError.__init__(self, message)
        self.log = log
        self.retry = retry


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


def tool_fingerprint(job, config):
    """What a local GPU job's specification takes from the tool it runs: the SHA-256 of the
    scripts its runner uses (rig: the SkinTokens entry; export: the exporter, the bone map
    module and the GLB materials reader; review: the Godot review project) and, for the rig, the checked-out git
    revision of the configured checkout. A fixed tool redoes its jobs; weight files are left
    to the provenance record. None for a runner this module does not know."""
    tool = job.get("tool")
    args = job.get("args") or {}
    if tool == "rig":
        method = args.get("method", "skintokens")
        scripts = [SKINTOKENS_ENTRY] if method == "skintokens" else []
        key = RIG_METHODS.get(method, (None,))[0]
        folder = (config or {}).get(key) if key else None
        extra = {"checkout_revision": _git_revision(folder) if folder else None}
    elif tool == "export":
        scripts, extra = [EXPORT_SCRIPT, BONES_SCRIPT, MATERIALS_SCRIPT], {}
    elif tool == "review":
        scripts, extra = [os.path.join(REVIEW_PROJECT, f) for f in REVIEW_FILES], {}
    elif tool == "parts":
        scripts, extra = list(PARTS_FILES), {}
    elif tool == "creature_export":
        scripts, extra = list(CREATURE_FILES), {}
    elif tool == "sheet":
        scripts, extra = [os.path.join(REVIEW_PROJECT, f) for f in SHEET_FILES] + [SHEET_LAYOUT], {}
    else:
        return None
    files = {os.path.basename(p): (sha_file_cached(p) if os.path.isfile(p) else "missing") for p in scripts}
    return dict(extra, scripts=files)


def manual_bone_map(out_dir):
    """SHA-256 of <out_dir>/bone_map.json when a person marked it "manual": true (it then
    wins over the proposal: a changed manual map redoes the export), else None. The file the
    exporter itself writes from the order's map ("source": "order") is not a correction: the
    order's file is already in the job's inputs, and counting it again would redo the export."""
    path = os.path.join(out_dir, "bone_map.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and doc.get("manual") and doc.get("source") != "order":
        return sha_file_cached(path)
    return None


def manual_frame(out_dir):
    """SHA-256 of <out_dir>/frame.json when a person marked it "manual": true (the parts
    tool then turns the object as it says: a changed manual frame redoes the parts), else
    None."""
    path = os.path.join(out_dir, "frame.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and doc.get("manual"):
        return sha_file_cached(path)
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
    if code == EXIT_BONE_MAP:
        report = os.path.join(out, "bone_map_report.json")
        raise ToolError("%s stopped: invalid bone map (see %s and %s)" % (
            step, report if os.path.exists(report) else os.path.join(out, "bone_map.json"), so), so, retry=False)
    if code == EXIT_MESH:
        raise ToolError("%s stopped: the mesh cannot be exported (see %s and %s)" % (
            step, os.path.join(out, "mesh_report.json"), so), so, retry=False)
    if code != 0:
        raise ToolError("%s failed (exit %d), see %s" % (step, code, se), se)
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

@_gpu_locked("rig")
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


@_gpu_locked("export")
def run_export(rig, out, config, params=None, clips=None, items=None, name="model", bake_source=None,
               bone_map=None, glow=None, device=None, thresholds=None, metal_roughness=None,
               material_source=None, image_format=None, timeout=7200):
    """The humanoid export (tools/blender/humanoid_export.py in blender_python).

    params: tris, height, head, hands, feet, widen (piece params), normal_map (normal map
    size; default texture, else 2048). clips: motion library; items: held-item folder;
    bake_source: the raw high mesh; bone_map: the order's bone map (a JSON file); glow: True
    or (low, high) hues; device: Cycles device (default: the configuration's bake_device);
    thresholds: motion report thresholds by name (THRESHOLDS); metal_roughness: the paint
    stage's metallic-roughness map (glTF convention: roughness green, metallic blue);
    material_source: the painted mesh the rig was made from (maps the rig's material lacks
    are carried from it); image_format: "lossless" (default: every map PNG) or "compact"
    (base colour JPEG at quality 92, the technical maps PNG).
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
    image_format = image_format or "lossless"
    if image_format not in IMAGE_FORMATS:
        raise ValueError("unknown image format %r (known: %s)" % (image_format, ", ".join(IMAGE_FORMATS)))
    cmd += ["--image-format", image_format]
    settings = {"name": name, "normal_map": int(tex), "device": device, "image_format": image_format}
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
    if metal_roughness:
        cmd += ["--metal-roughness", os.path.abspath(metal_roughness)]
        inputs.append(_input("metal_roughness", metal_roughness))
    if material_source:
        cmd += ["--material-source", os.path.abspath(material_source)]
        inputs.append(_input("material_source", material_source))
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
    for stale in ("model.json", "motion_report.json", "mesh_report.json", "bone_map_report.json", name + ".glb"):
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
    rec = _record(EXPORT_SCRIPT, [EXPORT_SCRIPT, BONES_SCRIPT, MATERIALS_SCRIPT], tools, settings, inputs)
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


@_gpu_locked("review")
def run_review(glbs, out, config, camera=None, labels=None, name="sheet", verdicts=None, banner=None, timeout=900):
    """One review sheet of a piece's variants (tools/review_godot in Godot, off screen).

    glbs: per variant a GLB path, or a failure mark (None, "!reason" or {"failed": reason}).
    camera: pitch (degrees, default 60), yaws (list of degrees) or directions (count, default
    8), figure_height (px, default 96), front (bool, default True), front_height (px),
    animation (default: the first idle), strip_frames (8), strip_yaw (degrees).
    verdicts: per variant {"class": "acceptable" | "rejected", "reasons": [text]} (the
    sheet labels it besides "pending review"); banner: a line over the sheet (the outcome
    "no_variant_passed").
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
    verdicts = list(verdicts or [])
    if banner:
        args += ["--banner", _arg_text(banner)]
    for i, g in enumerate(glbs):
        label = labels[i] if i < len(labels) else "v%d" % (i + 1)
        if i < len(verdicts) and verdicts[i]:
            v = verdicts[i]
            args += ["--verdict", "%s=%s%s" % (label, v["class"], (":" + "|".join(
                _arg_text(r) for r in v.get("reasons") or [])) if v.get("reasons") else "")]
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
    if verdicts:
        settings["verdicts"] = [v["class"] if v else None for v in verdicts]
    if banner:
        settings["banner"] = banner
    for key in ("front_height", "animation", "strip_frames", "strip_yaw"):
        if cam.get(key) is not None:
            settings[key] = cam[key]
    rec = _record(os.path.join(REVIEW_PROJECT, "main.gd"), scripts, tools, settings, inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "review", rec, files)
    return files


def _arg_text(text, limit=160):
    """A text for one Godot argument: one line, no "|" (the reasons' separator), not
    starting with "--"."""
    t = " ".join(str(text).split()).replace("|", "/")[:limit]
    return t.lstrip("-") or "-"


# ---- runners of the local_gpu job kind -------------------------------------------------------
# A humanoid's jobs (forge/recipes.py recipe_humanoid): "inputs" holds the references
# ({"ref": job, "role": r} to a factory output, {"file": path} or {"folder": path} the order
# supplies), "args" the requested arguments.

def _step_record(out, step):
    try:
        return provenance(out, step)
    except (OSError, ValueError):
        return {}


def rig_runner(factory, job, out_dir):
    """The rig of a variant's painted mesh (args: method, seed, beams, postprocess)."""
    args = job.get("args", {})
    mesh, _ = factory.resolve(job["inputs"]["mesh"])
    kw = {"method": args.get("method", "skintokens"), "seed": args.get("seed", 9001)}
    if kw["method"] == "skintokens":
        kw.update(beams=args.get("beams", 10), postprocess=bool(args.get("postprocess", False)))
    files = run_rig(mesh, out_dir, factory.config, **kw)
    return {"files": files, "passed": dict(kw), "provenance": _step_record(out_dir, "rig")}


def export_runner(factory, job, out_dir):
    """The humanoid export of a variant's rig, with its raw mesh as the bake source and the
    order's motion library, held items and bone map, the paint stage's metallic-roughness map
    and the painted mesh as the material source (args: name, tris, normal_map, height, head,
    hands, feet, widen, glow, image_format and the motion thresholds). An invalid bone map stops it
    with the report (ToolError, not retried)."""
    args = job.get("args", {})
    ins = job["inputs"]
    rig, _ = factory.resolve(ins["rig"])
    bake = factory.resolve(ins["bake_source"])[0] if "bake_source" in ins else None
    maps = {k: factory.resolve(ins[k])[0] for k in ("metal_roughness", "material_source") if k in ins}
    params = {k: args[k] for k in EXPORT_PARAMS if k in args}
    if args.get("normal_map"):
        params["normal_map"] = args["normal_map"]
    thresholds = {k: args[k] for k in THRESHOLDS if k in args}
    files = run_export(rig, out_dir, factory.config, params=params,
                       clips=ins["clips"]["file"] if "clips" in ins else None,
                       items=ins["items"]["folder"] if "items" in ins else None,
                       name=args.get("name", "model"), bake_source=bake,
                       bone_map=ins["bone_map"]["file"] if "bone_map" in ins else None,
                       glow=True if args.get("glow") else None, thresholds=thresholds or None,
                       metal_roughness=maps.get("metal_roughness"), material_source=maps.get("material_source"),
                       image_format=args.get("image_format"))
    rec = _step_record(out_dir, "export")
    settings = rec.get("settings", {})
    passed = {k: settings[k] for k in args if k in settings}
    passed.update({k: v for k, v in (settings.get("thresholds") or {}).items() if k in args})
    if "glow" in args:
        passed["glow"] = bool(settings.get("glow", False))
    return {"files": files, "passed": passed, "provenance": rec}


def variant_failure(factory, variant):
    """Why a variant did not finish: the first failed job of its chain and its error."""
    for jid in variant["chain"]:
        e = factory.entry(jid)
        why = getattr(factory, "spec_errors", {}).get(jid)
        if why:
            stage = factory.by_id[jid].get("stage") or jid.rsplit("/", 1)[-1]
            return "%s failed: %s" % (stage, why[:120])
        if e.get("status") == "failed":
            err = (e.get("error") or "").strip().splitlines()
            stage = factory.by_id[jid].get("stage") or jid.rsplit("/", 1)[-1]
            return ("%s failed: %s" % (stage, err[0][:120])) if err else "%s failed" % stage
    return "not finished"


def review_variants(factory, job):
    """(glbs, labels) of a review job: per variant its last stage's GLB, or a failure mark
    "!<reason>" for a variant that did not finish."""
    glbs, labels = [], []
    for v in job["variants"]:
        labels.append(v["variant"])
        if factory.done_ok(v["job"]):
            glbs.append(factory.resolve({"ref": v["job"], "role": v["role"]})[0])
        else:
            glbs.append("!" + variant_failure(factory, v))
    return glbs, labels


def review_runner(factory, job, out_dir):
    """One review sheet of every variant of a piece, failures marked, each variant labelled
    acceptable or rejected with its first reasons and, when none passed, a banner (args:
    the review camera: pitch, directions, figure_height, front, front_height, strip_frames)."""
    from . import acceptance
    glbs, labels = review_variants(factory, job)
    camera = dict(job.get("args", {}))
    verdicts, summary = factory.decision(job)
    shown = [{"class": v["class"], "reasons": acceptance.first_reasons(v)} for v in verdicts]
    if not summary["motion_gated"]:
        for x in shown:  # "acceptable" is not "motion-clean": the motion flags stay on the sheet
            if x["class"] == "acceptable":
                x["reasons"] = ["motion not gated"]
    banner = None
    if not summary["acceptable"]:
        top = summary["responsible_stage"] or "unknown"
        banner = "no variant passed: most often the %s (%d of %d)" % (
            top, summary["responsible_counts"].get(top, 0), len(verdicts))
    files = run_review(glbs, out_dir, factory.config, camera=camera, labels=labels, verdicts=shown, banner=banner)
    rec = _step_record(out_dir, "review")
    settings = dict(rec.get("settings", {}))
    if "yaws" in settings:
        settings["directions"] = len(settings["yaws"])
    return {"files": files, "passed": {k: settings[k] for k in camera if k in settings}, "provenance": rec}


RUNNERS.update({"rig": rig_runner, "export": export_runner, "review": review_runner})


# ---- articulated: the parts --------------------------------------------------------------------

def _params_cmd(params):
    cmd, settings = [], {}
    for k, opt in (("height", "--height"), ("length", "--length"), ("tris", "--tris"), ("front", "--front")):
        if params.get(k) is not None:
            cmd += [opt, str(params[k])]
            settings[k] = params[k]
    return cmd, settings


@_gpu_locked("parts")
def run_parts(mesh, out, config, parts, name="model", params=None, metal_roughness=None, image_format=None,
              min_share=None, snap=None, timeout=3600):
    """The articulated parts (tools/blender/articulated_export.py in blender_python): the
    painted mesh turned to face +Z, scaled, cut into `parts` (the order's part list), each
    part a node with its origin at its pivot. params: height or length, tris, front. Returns
    {glb, model, mesh_report, frame}."""
    _require(config, "parts")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    spec = os.path.join(out, "parts_spec.json")
    write_json(spec, parts)
    image_format = image_format or "lossless"
    if image_format not in IMAGE_FORMATS:
        raise ValueError("unknown image format %r (known: %s)" % (image_format, ", ".join(IMAGE_FORMATS)))
    cmd = [config["blender_python"], PARTS_SCRIPT, "--mesh", os.path.abspath(mesh), "--out", out, "--name", name,
           "--parts", spec, "--image-format", image_format]
    extra, settings = _params_cmd(params or {})
    cmd += extra
    settings.update(name=name, image_format=image_format, parts=parts)
    if min_share is not None:
        cmd += ["--min-share", str(min_share)]
        settings["min_share"] = min_share
    if snap is not None:
        cmd += ["--snap", "1" if snap else "0"]
        settings["snap"] = bool(snap)
    inputs = [_input("mesh", mesh)]
    if metal_roughness:
        cmd += ["--metal-roughness", os.path.abspath(metal_roughness)]
        inputs.append(_input("metal_roughness", metal_roughness))
    for stale in ("model.json", "mesh_report.json", name + ".glb"):
        if os.path.exists(os.path.join(out, stale)):
            os.remove(os.path.join(out, stale))
    seconds, text = _run(cmd, out, "parts", timeout=timeout)
    files = {"glb": os.path.join(out, name + ".glb"), "model": os.path.join(out, "model.json"),
             "mesh_report": os.path.join(out, "mesh_report.json"), "frame": os.path.join(out, "frame.json")}
    missing = [p for p in files.values() if not os.path.exists(p)]
    if missing:
        raise ToolError("parts did not write %s, see %s" % (", ".join(missing), os.path.join(out, "parts.stdout.log")))
    version = next((ln.split(" ", 1)[1].strip() for ln in text.splitlines() if ln.startswith("BPY ")), None)
    tools = {"blender_python": {"path": config["blender_python"], "version": "bpy " + version if version else None}}
    rec = _record(PARTS_SCRIPT, PARTS_FILES, tools, settings, inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "parts", rec, files)
    return files


def parts_runner(factory, job, out_dir):
    """The parts of a variant's painted mesh (args: name, parts, height, length, tris, front,
    image_format, min_share, snap)."""
    args = job.get("args", {})
    ins = job["inputs"]
    mesh, _ = factory.resolve(ins["mesh"])
    mr = factory.resolve(ins["metal_roughness"])[0] if "metal_roughness" in ins else None
    params = {k: args[k] for k in ("height", "length", "tris", "front") if k in args}
    files = run_parts(mesh, out_dir, factory.config, args["parts"], name=args.get("name", "model"), params=params,
                      metal_roughness=mr, image_format=args.get("image_format"), min_share=args.get("min_share"),
                      snap=args.get("snap"))
    rec = _step_record(out_dir, "parts")
    settings = rec.get("settings", {})
    return {"files": files, "passed": {k: settings[k] for k in args if k in settings}, "provenance": rec}


# ---- creature: the export ------------------------------------------------------------------------

@_gpu_locked("creature_export")
def run_creature_export(rig, out, config, body, motions=(), name="model", params=None, clips=None,
                        libraries=(), bone_map=None, metal_roughness=None, image_format=None, thresholds=None,
                        timeout=7200):
    """The creature export (tools/blender/creature_export.py in blender_python): the rig turned
    to face +Z and scaled, its bones mapped onto `body` (the order's plan or template), the
    motions made in those roles. clips: the piece's library (inputs.clips); libraries: other
    library files the motions name. Returns {glb, model, bone_map, mesh_report, clips[,
    motion_report]}."""
    _require(config, "creature_export")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    spec = os.path.join(out, "creature_spec.json")
    write_json(spec, {"body": body, "motions": list(motions or [])})
    image_format = image_format or "lossless"
    if image_format not in IMAGE_FORMATS:
        raise ValueError("unknown image format %r (known: %s)" % (image_format, ", ".join(IMAGE_FORMATS)))
    cmd = [config["blender_python"], CREATURE_SCRIPT, "--rig", os.path.abspath(rig), "--out", out, "--name", name,
           "--spec", spec, "--image-format", image_format]
    extra, settings = _params_cmd(params or {})
    cmd += extra
    settings.update(name=name, image_format=image_format, body=body, motions=list(motions or []))
    inputs = [_input("rig", rig)]
    if clips:
        cmd += ["--clips", os.path.abspath(clips)]
        inputs.append(_input("clips", clips))
    for path in libraries or ():
        cmd += ["--library", os.path.abspath(path)]
        inputs.append(_input("library", path))
    if bone_map:
        cmd += ["--bone-map", os.path.abspath(bone_map)]
        inputs.append(_input("bone_map", bone_map))
    if metal_roughness:
        cmd += ["--metal-roughness", os.path.abspath(metal_roughness)]
        inputs.append(_input("metal_roughness", metal_roughness))
    for k, v in (thresholds or {}).items():
        if k not in CREATURE_THRESHOLDS:
            raise ValueError("unknown motion threshold %s (known: %s)" % (k, ", ".join(CREATURE_THRESHOLDS)))
        cmd += [CREATURE_THRESHOLDS[k], str(v)]
        settings.setdefault("thresholds", {})[k] = v
    for stale in ("model.json", "motion_report.json", "mesh_report.json", "bone_map_report.json", "clips.json",
                  name + ".glb"):
        if os.path.exists(os.path.join(out, stale)):
            os.remove(os.path.join(out, stale))
    seconds, text = _run(cmd, out, "export", timeout=timeout)
    files = {"glb": os.path.join(out, name + ".glb"), "model": os.path.join(out, "model.json"),
             "mesh_report": os.path.join(out, "mesh_report.json"), "clips": os.path.join(out, "clips.json")}
    bm = os.path.join(out, "bone_map.json")
    files["bone_map"] = bm if os.path.exists(bm) else os.path.join(out, "bone_map_report.json")
    if motions:
        files["motion_report"] = os.path.join(out, "motion_report.json")
    missing = [p for p in files.values() if not os.path.exists(p)]
    if missing:
        raise ToolError("export did not write %s, see %s" % (", ".join(missing), os.path.join(out, "export.stdout.log")))
    version = next((ln.split(" ", 1)[1].strip() for ln in text.splitlines() if ln.startswith("BPY ")), None)
    tools = {"blender_python": {"path": config["blender_python"], "version": "bpy " + version if version else None}}
    rec = _record(CREATURE_SCRIPT, CREATURE_FILES, tools, settings, inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "export", rec, files)
    return files


def creature_export_runner(factory, job, out_dir):
    """The creature export of a variant's rig (args: name, body, motions, height, length,
    tris, front, image_format and the motion thresholds). An invalid bone map stops it with
    its report (ToolError, not retried)."""
    args = job.get("args", {})
    ins = job["inputs"]
    rig, _ = factory.resolve(ins["rig"])
    mr = factory.resolve(ins["metal_roughness"])[0] if "metal_roughness" in ins else None
    params = {k: args[k] for k in ("height", "length", "tris", "front") if k in args}
    thresholds = {k: args[k] for k in CREATURE_THRESHOLDS if k in args}
    files = run_creature_export(rig, out_dir, factory.config, args["body"], motions=args.get("motions") or [],
                                name=args.get("name", "model"), params=params,
                                clips=ins["clips"]["file"] if "clips" in ins else None,
                                libraries=[x["file"] for x in ins.get("libraries", [])],
                                bone_map=ins["bone_map"]["file"] if "bone_map" in ins else None,
                                metal_roughness=mr, image_format=args.get("image_format"),
                                thresholds=thresholds or None)
    rec = _step_record(out_dir, "export")
    settings = rec.get("settings", {})
    passed = {k: settings[k] for k in args if k in settings}
    passed.update({k: v for k, v in (settings.get("thresholds") or {}).items() if k in args})
    return {"files": files, "passed": passed, "provenance": rec}


# ---- the layout sheet (articulated and creature) ---------------------------------------------------

@_gpu_locked("sheet")
def run_sheet(glbs, out, config, camera=None, labels=None, name="sheet", verdicts=None, banner=None, timeout=900):
    """One review sheet of a piece's variants (articulated or creature): the factory lays it
    out (forge/sheet_layout.py, from each variant's model.json and reports) and Godot renders
    the cells (tools/review_godot/sheet.gd). glbs, camera, labels, verdicts and banner as
    run_review. Returns {"sheet": <out>/<name>.png, "layout": <out>/<name>.png.json}."""
    from . import sheet_layout
    _require(config, "sheet")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    sheet = os.path.join(out, name + ".png")
    variants, inputs = [], []
    labels = list(labels or [])
    verdicts = list(verdicts or [])
    for i, g in enumerate(glbs):
        label = labels[i] if i < len(labels) else "v%d" % (i + 1)
        reason = _failure(g)
        entry = {"label": label, "verdict": verdicts[i] if i < len(verdicts) else None}
        if reason is not None:
            entry.update(failed=True, reason=reason)
        else:
            g = os.path.abspath(g)
            folder = os.path.dirname(g)
            entry.update(failed=False, glb=g.replace(os.sep, "/"))
            inputs.append(_input("variant/" + label, g))
            for side in ("model.json", "motion_report.json", "mesh_report.json"):
                p = os.path.join(folder, side)
                key = side.split(".")[0]
                entry[key] = sheet_layout.read_json(p)
                if os.path.exists(p):
                    inputs.append(_input("variant/%s/%s" % (label, side), p))
        variants.append(entry)
    layout = sheet_layout.layout(variants, camera or {}, banner=banner)
    request = os.path.join(out, name + "_request.json")
    write_json(request, dict(layout, out=sheet.replace(os.sep, "/")))
    for stale in (sheet, sheet + ".json"):
        if os.path.exists(stale):
            os.remove(stale)
    cmd = [config["godot"], "--path", REVIEW_PROJECT, "--audio-driver", "Dummy", "--windowed",
           "--position", "-20000,-20000", "--resolution", "64x64", "res://sheet.tscn", "--",
           "--request", request.replace(os.sep, "/")]
    seconds, text = _run(cmd, out, "review", timeout=timeout)
    if not os.path.exists(sheet):
        raise ToolError("review did not write %s, see %s" % (sheet, os.path.join(out, "review.stderr.log")))
    load_failures = [ln.split(" ", 2)[2] for ln in text.splitlines() if ln.startswith("SHEET: failed ")]
    layout["load_failures"] = load_failures
    write_json(sheet + ".json", layout)
    files = {"sheet": sheet, "layout": sheet + ".json"}
    version = next((ln.split(" - ")[0].replace("Godot Engine ", "").strip() for ln in text.splitlines()
                    if ln.startswith("Godot Engine ")), None)
    tools = {"godot": {"path": config["godot"], "version": version}}
    scripts = [os.path.join(REVIEW_PROJECT, f) for f in SHEET_FILES] + [SHEET_LAYOUT]
    rec = _record(os.path.join(REVIEW_PROJECT, "sheet.gd"), scripts, tools, dict(layout["camera"]), inputs)
    rec["exec_seconds"] = seconds
    rec["command"] = cmd
    _write_provenance(out, "review", rec, files)
    return files


def sheet_runner(factory, job, out_dir):
    """The layout sheet of every variant of an articulated or creature piece, failures marked,
    verdicts and the no-variant banner as the humanoid's (args: the review camera)."""
    from . import acceptance
    glbs, labels = review_variants(factory, job)
    camera = dict(job.get("args", {}))
    verdicts, summary = factory.decision(job)
    shown = [{"class": v["class"], "reasons": acceptance.first_reasons(v)} for v in verdicts]
    if not summary["motion_gated"]:
        for x in shown:
            if x["class"] == "acceptable":
                x["reasons"] = ["motion not gated"]
    banner = None
    if not summary["acceptable"]:
        top = summary["responsible_stage"] or "unknown"
        banner = "no variant passed: most often the %s (%d of %d)" % (
            top, summary["responsible_counts"].get(top, 0), len(verdicts))
    files = run_sheet(glbs, out_dir, factory.config, camera=camera, labels=labels, verdicts=shown, banner=banner)
    rec = _step_record(out_dir, "review")
    settings = dict(rec.get("settings", {}))
    return {"files": files, "passed": {k: settings[k] for k in camera if k in settings}, "provenance": rec}


RUNNERS.update({"parts": parts_runner, "creature_export": creature_export_runner, "sheet": sheet_runner})


if __name__ == "__main__":  # a manual check of this machine's configuration
    import sys
    from .config import load_config
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "forge.local.json"))
    for step, method in (("rig", "skintokens"), ("rig", "unirig"), ("export", None), ("review", None)):
        print(step, method or "", check_tools(cfg, step, method) or "ready")
