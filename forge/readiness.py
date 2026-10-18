"""The environment check: can this machine run an order that forge.order.check_order found
valid? Separate from the order check, and read-only: the server is only asked for
GET /object_info (nothing is sent, no model is loaded, no GPU inference runs).

check_environment(order, config, server) gives one of three results:
  verified     the server answered and nothing is missing
  not ready    the list of what is missing
  not verified the server did not answer: the local part is checked (tools, weight files,
               versions) but the environment is not, and it is never reported as ready
It covers: the node classes of every workflow the order's jobs use; every model file a
workflow names is in its loader's option list from /object_info; every setting or `set`
value that lands on a list input is in the server's list (samplers, schedulers); the local
tools the chosen stages need, with their weight files (forge.config.TOOL_WEIGHTS); and the
versions of Blender (bpy) and Godot when they are configured.
"""

import os
import subprocess

from . import gpu_lock, stages
from .config import missing_tools, missing_weights

VERIFIED, NOT_READY, NOT_VERIFIED = "verified", "not ready", "not verified"
MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pth", ".pt", ".gguf", ".bin", ".sft", ".onnx")


class Environment:
    def __init__(self):
        self.status = None
        self.problems = []
        self.notes = []
        self.versions = {}
        self.reason = None

    def lines(self):
        """The text -Check prints."""
        if self.status == NOT_VERIFIED:
            out = ["environment: not verified (%s: local validation done, environment not checked)" % self.reason]
            out += ["  local problem: " + p for p in self.problems]
        elif self.status == NOT_READY:
            out = ["environment: not ready (%d problem(s))" % len(self.problems)]
            out += ["  problem: " + p for p in self.problems]
        else:
            out = ["environment: verified"]
        out += ["  version: %s %s" % (k, v) for k, v in sorted(self.versions.items())]
        out += ["  note: " + n for n in self.notes]
        return out


def combo_options(spec):
    """The option list of an input spec of /object_info, or None when it is not a list input.
    Both forms: [[options...], {...}] and ["COMBO", {"options": [...]}]."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        return opts if isinstance(opts, list) else None
    return None


def input_spec(info, cls, name):
    ins = (info.get(cls) or {}).get("input") or {}
    for part in ("required", "optional"):
        if name in (ins.get(part) or {}):
            return ins[part][name]
    return None


def _run_version(cmd, prefix=None, timeout=120):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **flags)
    except (OSError, subprocess.SubprocessError) as ex:
        return None, str(ex)
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if prefix is None and line:
            return line, None
        if prefix and line.startswith(prefix):
            return line[len(prefix):].strip(), None
    return None, "exit %d: %s" % (res.returncode, (res.stderr or res.stdout or "").strip()[-300:])


def tool_versions(config):
    """{tool: version} of Blender (bpy) and Godot when configured; problems as notes."""
    versions, notes = {}, []
    if config.get("blender_python") and os.path.exists(config["blender_python"]):
        v, err = _run_version([config["blender_python"], "-c",
                               "import bpy, os, sys; print('BPY', bpy.app.version_string, flush=True); os._exit(0)"],
                              "BPY ")
        if v:
            versions["blender_python"] = "bpy " + v
        else:
            notes.append("blender_python did not give its bpy version (%s)" % err)
    if config.get("godot") and os.path.exists(config["godot"]):
        v, err = _run_version([config["godot"], "--version"], None, timeout=60)
        if v:
            versions["godot"] = v
        else:
            notes.append("godot did not give its version (%s)" % err)
    return versions, notes


def _applied(wf, job):
    """The workflow with the job's plain values and its "set" applied (what the server would
    be asked for, apart from uploaded files and other jobs' outputs)."""
    values = {k: v for k, v in job.get("values", {}).items() if not isinstance(v, dict)}

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and v in values:
            return values[v]
        return v
    wf = walk(wf)
    for key, v in job.get("set", {}).items():
        nid, name = key.split(".", 1)
        if nid in wf:
            wf[nid]["inputs"][name] = v
    return wf


def _check_server(jobs, info, env):
    from .comfy_jobs import drop_nodes
    from .factory_core import load_workflow
    seen = set()
    models = set()
    for j in jobs:
        if j.get("kind", "comfy") != "comfy":
            continue
        name = j.get("workflow")
        try:
            wf, _ = load_workflow(name)
            drop_nodes(wf, j.get("drop", []), name)
        except Exception as ex:
            env.problems.append("job %s: workflow %s: %s" % (j["id"], name, ex))
            continue
        wf = _applied(wf, j)
        set_keys = set(j.get("set", {}))
        for nid, node in sorted(wf.items()):
            cls = node["class_type"]
            if cls not in info:
                if ("class", name, cls) not in seen:
                    seen.add(("class", name, cls))
                    env.problems.append("node class %s (workflow %s) is not on the server" % (cls, name))
                continue
            for inp, v in sorted(node.get("inputs", {}).items()):
                if not isinstance(v, str) or (v.startswith("$") and v.endswith("$")):
                    continue
                is_model = v.lower().endswith(MODEL_EXTENSIONS)
                key = "%s.%s" % (nid, inp)
                if not is_model and key not in set_keys:
                    continue
                opts = combo_options(input_spec(info, cls, inp))
                tag = (name, key, v)
                if tag in seen:
                    continue
                seen.add(tag)
                if opts is None:
                    if is_model:
                        env.notes.append("model %s of %s (%s.%s) is not a list input on the server: not checked"
                                         % (v, name, cls, inp))
                    continue
                if is_model:
                    models.add(v)
                if v not in opts:
                    what = "model file %s" % v if is_model else "value %r" % v
                    env.problems.append("%s of workflow %s (node %s %s.%s) is not in the server's list" % (
                        what, name, nid, cls, inp) + (" (job %s)" % j["id"] if key in set_keys else ""))
    env.notes.append("%d model files checked against the loaders' lists" % len(models))


def check_environment(order, config, server):
    """The environment result of an order (an Environment). server: a client with get_json
    (forge.comfy_server.Server), the /object_info answer (a dictionary) or None (not
    reachable). Only GET /object_info is read."""
    from .recipes import expand
    env = Environment()
    jobs = expand(order)
    # Local part: the tools the chosen stages need and their weight files.
    keys = set()
    for fam in order.get("families", []):
        for piece in fam.get("assets", []):
            keys.update(stages.tools_needed(piece.get("recipe"), piece))
    env.problems += missing_tools(config, sorted(keys))
    env.problems += missing_weights(config, sorted(keys))
    env.versions, notes = tool_versions(config)
    env.notes += notes
    try:
        who = gpu_lock.holder()
        if who is not None:
            env.notes.append("the GPU lock is held now by %s" % gpu_lock.describe(who))
    except OSError:
        pass
    info = None
    if isinstance(server, dict):
        info = server
    elif server is not None:
        try:
            info = server.get_json("/object_info", timeout=30)
        except Exception as ex:
            env.reason = "server %s did not answer (%s)" % (getattr(server, "url", "?"), ex)
    else:
        env.reason = "no server"
    if info is None:
        env.status = NOT_VERIFIED
        return env
    env.notes.insert(0, "%d node classes on the server" % len(info))
    _check_server(jobs, info, env)
    env.status = NOT_READY if env.problems else VERIFIED
    return env
