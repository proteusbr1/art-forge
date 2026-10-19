"""The text source of the humanoid's motions: HY-Motion Lite, run locally from a plain
description, once per family and motion (forge/motion_jobs.py).

It is a local GPU job (tool "motion_text", forge/local_gpu.py RUNNERS): the factory runs it
only with the whole ComfyUI queue empty, after /free and the wait for other processes on the
card, holding the machine-wide GPU lock, as every local GPU tool. The model runs in a
process of its own (tools/motion/hymotion_entry.py in the configured Python, the HY-Motion
checkout on its path, offline): it encodes the text with the language model, frees it,
generates with the motion model and exits, so all its VRAM is released when the job ends.
Then the joints it wrote go through the canonical steps of forge/motion_clips.py (the loop
window, closure, contacts, hip height, foot cleanup) into clip.json and report.json.

The configuration keys are forge.stages.MOTION_TEXT_TOOLS (forge.local.json) and the
optional hymotion_pythonpath. FORGE_HYMOTION_ENTRY replaces the entry script (the tests run a
stand-in that writes synthetic joints; never a model).
"""

import json
import os
import subprocess
import time

import numpy as np

from . import motion_clips as mc
from .config import missing_tools
from .factory_util import write_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "tools", "motion", "hymotion_entry.py")
TOOLS = ("hymotion_python", "hymotion_dir", "hymotion_model", "hymotion_text_encoder", "hymotion_clip")
# What the readiness check imports in HY-Motion's Python: the modules the entry uses. No CUDA
# context is made and nothing is loaded on the card.
IMPORT_CODE = ("import torch, yaml; from hymotion.utils.loaders import load_object; "
               "from hymotion.network.text_encoders import text_encoder; "
               "print('HYMOTION torch', torch.__version__, 'cuda', torch.version.cuda, flush=True)")


def entry_script():
    return os.environ.get("FORGE_HYMOTION_ENTRY") or ENTRY


def model_config(config):
    return os.path.join(os.path.dirname(config["hymotion_model"] or ""), "config.yml")


def readiness_problems(config):
    """What is missing for the text source on this machine (empty: the files are there)."""
    problems = missing_tools(config, TOOLS)
    if problems:
        return problems
    if not os.path.isfile(config["hymotion_model"]):
        problems.append("hymotion_model is not a file: %s" % config["hymotion_model"])
    if not os.path.isfile(model_config(config)):
        problems.append("hymotion_model has no config.yml next to it: %s" % model_config(config))
    enc = config["hymotion_text_encoder"]
    if not any(f.endswith(".safetensors") for f in os.listdir(enc)):
        problems.append("hymotion_text_encoder has no .safetensors weights: %s" % enc)
    clip = config["hymotion_clip"]
    if not any(f.endswith((".safetensors", ".bin")) for f in os.listdir(clip)):
        problems.append("hymotion_clip has no weights: %s" % clip)
    pp = config.get("hymotion_pythonpath")
    if pp and not os.path.isdir(pp):
        problems.append("hymotion_pythonpath points to a missing folder: %s" % pp)
    return problems


def environment(config):
    """The environment of HY-Motion's process: the checkout (and the optional folder) on its
    path, offline, no bytecode written into the checkout."""
    path = [p for p in (config.get("hymotion_pythonpath"), config["hymotion_dir"]) if p]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(path), PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
    return env


def import_check(config, timeout=600):
    """(version text, None) when HY-Motion's Python imports the entry's modules, else (None,
    why). Imports only: no model is loaded and the card is not used."""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        res = subprocess.run([config["hymotion_python"], "-B", "-c", IMPORT_CODE], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL, env=environment(config),
                             cwd=config["hymotion_dir"], **flags)
    except (OSError, subprocess.SubprocessError) as ex:
        return None, str(ex)
    for line in (res.stdout or "").splitlines():
        if line.startswith("HYMOTION "):
            return line[len("HYMOTION "):].strip(), None
    return None, "exit %d: %s" % (res.returncode, (res.stderr or res.stdout or "").strip()[-400:])


def run_motion_text(out, config, name, prompt, settings, frames, fps=24.0, loop=False, contacts=None,
                    timeout=1800):
    """One text clip: HY-Motion writes motion.npz and hymotion.json (its own record), then the
    canonical clip.json and report.json. Returns {"clip", "report", "motion", "record"}."""
    from . import local_gpu
    problems = missing_tools(config, TOOLS)
    if problems:
        raise local_gpu.ToolError("motion_text cannot run: %s" % "; ".join(problems))
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    npz, record = os.path.join(out, "motion.npz"), os.path.join(out, "hymotion.json")
    for stale in (npz, record, os.path.join(out, "clip.json"), os.path.join(out, "report.json")):
        if os.path.exists(stale):
            os.remove(stale)
    prompt_file = os.path.join(out, "prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)
    entry = entry_script()
    cmd = [config["hymotion_python"], "-u", "-B", entry, "--checkout", config["hymotion_dir"],
           "--model", config["hymotion_model"], "--config", model_config(config),
           "--text-encoder", config["hymotion_text_encoder"], "--clip", config["hymotion_clip"],
           "--prompt-file", prompt_file, "--seed", str(int(settings["seed"])), "--steps", str(int(settings["steps"])),
           "--guidance", str(float(settings["guidance"])), "--duration", str(float(settings["duration"])),
           "--out", npz, "--record", record]
    t0 = time.time()
    local_gpu._run(cmd, out, "motion_text", cwd=config["hymotion_dir"], env=environment(config), timeout=timeout)
    if not os.path.isfile(npz):
        raise local_gpu.ToolError("motion_text wrote no %s, see %s" % (npz, os.path.join(out, "motion_text.stderr.log")),
                                  os.path.join(out, "motion_text.stderr.log"))
    data = np.load(npz)
    with open(record, encoding="utf-8") as f:
        rec = json.load(f)
    if "keypoints3d" not in data.files:
        raise local_gpu.ToolError("motion_text: %s has no keypoints3d" % npz, retry=False)
    pos = mc.smpl_canon(data["keypoints3d"], data["transl"] if "transl" in data.files else None)
    if not np.isfinite(pos).all():
        raise local_gpu.ToolError("motion_text: the generated joints are not finite")
    entry_spec = {"name": name, "frames": frames, "fps": fps, "loop": loop, "settings": {"feet": settings["feet"]}}
    if contacts:
        entry_spec["contacts"] = contacts
    clip, report = mc.make_clip(pos, float(rec.get("fps", 30)), 1, entry_spec,
                                "HY-Motion %s, seed %s: %s" % (rec.get("model", "?"), settings["seed"], prompt))
    report["generation"] = {k: rec.get(k) for k in ("model", "seed", "requested_steps", "guidance_scale",
                                                     "duration_seconds", "fps", "frames", "checkpoint_sha256",
                                                     "config_sha256", "inference_seconds")}
    write_json(os.path.join(out, "clip.json"), mc.clip_document(name, clip))
    write_json(os.path.join(out, "report.json"), report)
    files = {"clip": os.path.join(out, "clip.json"), "report": os.path.join(out, "report.json"), "motion": npz,
             "record": record}
    tools = {"hymotion_python": {"path": config["hymotion_python"]},
             "hymotion_dir": {"path": config["hymotion_dir"], "revision": local_gpu._git_revision(config["hymotion_dir"])}}
    models = []
    for key in ("hymotion_model",):
        p = config[key]
        models.append({"file": os.path.basename(p), "bytes": os.path.getsize(p),
                       "sha256": rec.get("checkpoint_sha256") or local_gpu.sha_file_cached(p)})
    prov = local_gpu._record(entry, [entry, os.path.join(ROOT, "forge", "motion_clips.py")], tools,
                             dict(settings, frames=frames, fps=fps, loop=loop), [], seeds=[settings["seed"]],
                             models=models)
    prov["prompt"] = prompt
    prov["exec_seconds"] = round(time.time() - t0, 2)
    prov["command"] = cmd
    prov["generation"] = report["generation"]
    local_gpu._write_provenance(out, "motion_text", prov, files)
    return files


def motion_text_runner(factory, job, out_dir):
    a = job["args"]
    files = run_motion_text(out_dir, factory.config, a["name"], a["prompt"], a["settings"], a["frames"],
                            a.get("fps", 24.0), a.get("loop", False), a.get("contacts"))
    from . import local_gpu
    rec = local_gpu._step_record(out_dir, "motion_text")
    try:
        with open(files["report"], encoding="utf-8") as f:
            report = json.load(f)
        if not report.get("valid", True):
            factory.log("motion %s: %s" % (job["id"], "; ".join(report.get("flags", []))))
    except (OSError, ValueError):
        pass
    passed = dict(a["settings"])
    return {"files": files, "passed": {"settings": passed, "frames": a["frames"]}, "provenance": rec}
