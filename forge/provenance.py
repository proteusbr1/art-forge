"""Provenance of the factory's art: what each job used (workflow, model files, texts, seeds,
settings, input files) is recorded in the manifest when the job runs, and export() turns the
manifest into a provenance file the game can keep with its art (one record per output file).
Needs no server and no GPU."""

import json
import os
import time

from .factory_util import sha_file, slug, write_json

MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pth", ".gguf", ".bin")
SEED_INPUTS = ("seed", "noise_seed")
TEXT_INPUTS = ("text", "prompt")
NEGATIVE_INPUTS = ("negative_prompt",)
LOAD_IMAGE_INPUTS = ("image",)


def node_order(wf):
    return sorted(wf.items(), key=lambda kv: (0, int(kv[0])) if kv[0].isdigit() else (1, kv[0]))


def workflow_models(wf):
    """Model files named by the nodes of a built workflow: any input whose value ends in a
    model file extension, with its node id and class."""
    out = []
    for nid, node in node_order(wf):
        for name, v in sorted(node.get("inputs", {}).items()):
            if isinstance(v, str) and v.lower().endswith(MODEL_EXTENSIONS):
                out.append({"node": nid, "class": node.get("class_type"), "input": name, "file": v})
    return out


def workflow_seeds(wf):
    seeds = []
    for nid, node in node_order(wf):
        for name in SEED_INPUTS:
            v = node.get("inputs", {}).get(name)
            if isinstance(v, int) and not isinstance(v, bool) and v not in seeds:
                seeds.append(v)
    return seeds


def workflow_texts(wf, names):
    texts = []
    for nid, node in node_order(wf):
        for name in names:
            v = node.get("inputs", {}).get(name)
            if isinstance(v, str) and v.strip() and v not in texts:
                texts.append(v)
    return texts


def comfy_record(job, workflow_sha, wf, sets, inputs):
    """Provenance of a ComfyUI job, from its built prompt (models, seeds, texts) and its inputs."""
    values = job.get("values", {})
    prompt = values.get("$PROMPT$")
    if not isinstance(prompt, str):
        prompt = "\n".join(workflow_texts(wf, TEXT_INPUTS)) or None
    negative = values.get("$NEGATIVE$")
    if not isinstance(negative, str):
        negative = "\n".join(workflow_texts(wf, NEGATIVE_INPUTS)) or None
    inputs = [dict(i) for i in inputs]
    known = set(i.get("server_name") for i in inputs)
    # Images the job names directly on the server (raw tasks): not factory outputs.
    for nid, node in node_order(wf):
        if "LoadImage" not in node.get("class_type", ""):
            continue
        for name in LOAD_IMAGE_INPUTS:
            v = node.get("inputs", {}).get(name)
            if isinstance(v, str) and v not in known:
                inputs.append({"server_file": v, "factory_output": False, "sha256": None})
    return {"family": job["family"], "asset": job["asset"], "kind": job["kind"], "group": job["group"],
            "workflow": job["workflow"], "workflow_sha256": workflow_sha, "models": workflow_models(wf),
            "prompt": prompt, "negative": negative or None, "seeds": workflow_seeds(wf),
            "settings": sets, "inputs": inputs}


def local_record(job, inputs):
    """Provenance of a local step (pick, sheet, roll, unroll): no model, only its inputs."""
    return {"family": job["family"], "asset": job["asset"], "kind": job["kind"], "group": job["group"],
            "workflow": None, "workflow_sha256": None, "models": [], "prompt": None, "negative": None,
            "seeds": [], "settings": {k: job[k] for k in ("metric", "axes") if k in job}, "inputs": inputs}


def export(out, dest):
    """Write the provenance file for every output file of every finished job of <out>."""
    manifest_path = os.path.join(out, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    records = []
    for jid in sorted(manifest):
        e = manifest[jid]
        if e.get("status") != "done":
            continue
        prov = e.get("provenance")
        parts = jid.split("/")
        for role in sorted(e.get("files", {})):
            rel, sha = e["files"][role]
            path = os.path.join(out, rel)
            rec = {"asset": prov["asset"] if prov else (parts[1] if len(parts) > 2 else "general"),
                   "family": prov["family"] if prov else parts[0],
                   "job": jid, "step": slug(jid), "role": role, "file": rel.replace(os.sep, "/"),
                   "sha256": sha, "present": os.path.exists(path),
                   "finished": e.get("finished"), "provenance_recorded": prov is not None}
            if prov:
                rec.update({k: v for k, v in prov.items() if k not in ("family", "asset")})
                if e.get("chosen"):
                    rec["chosen"] = e["chosen"]
            records.append(rec)
    doc = {"format": "forge-provenance", "version": 1,
           "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "manifest_sha256": sha_file(manifest_path), "files": records}
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    write_json(dest, doc)
    return len(records)
