"""ComfyUI jobs of the factory: build the prompt from a workflow, submit it, collect its
outputs from the history. Mixed into Factory (factory_core.py)."""

import json
import os
import time
import urllib.error
import urllib.parse

from .factory_util import sha_file, slug
from .provenance import comfy_record, effective_parameters, executed_prompt, requested_parameters
from .stages import OVERRIDDEN

PREFIX_ROOT = "forge"
SAVE_CLASSES = ("SaveImage", "SaveGLB", "Hy3D21ExportMesh", "Save3DAdvanced")
MAX_TRIES = 2


def drop_nodes(wf, drop, name="workflow"):
    """Remove the nodes listed in a job's "drop" (an optional branch of a workflow, such as a
    texture bake); a node left behind that still uses a removed node is an error."""
    for nid in drop:
        if nid not in wf:
            raise ValueError("%s has no node %s to drop" % (name, nid))
        del wf[nid]
    for nid, node in wf.items():
        for key, v in node.get("inputs", {}).items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in drop:
                raise ValueError("%s: node %s.%s uses dropped node %s" % (name, nid, key, v[0]))


class ComfyJobs:
    def needed_roles(self, job):
        """The output roles of a job that must be there when its prompt finishes: those later
        jobs use (Factory.load works them out) and its painted mesh (mesh_role)."""
        roles = set(getattr(self, "needed", {}).get(job["id"], ()))
        if job.get("mesh_role"):
            roles.add(job["mesh_role"])
        return roles

    def build(self, job, tries=0):
        """The prompt of a job; also its applied settings and its input files (provenance)."""
        wf0, _ = self.workflow(job["workflow"])
        wf = json.loads(json.dumps(wf0))
        drop_nodes(wf, job.get("drop", []), job["workflow"])
        prefix = "%s/%s/%s/%s" % (PREFIX_ROOT, job["family"], job["asset"], slug(job["id"]))
        inputs = []
        values = {}
        for k, v in job.get("values", {}).items():
            if isinstance(v, dict) and ("ref" in v or "file" in v):
                transport = v.get("transport", "upload")
                path, sha = self.resolve(v)
                inputs.append(self.input_record(v))
                if transport == "local_path":
                    if urllib.parse.urlparse(self.server.url).hostname not in ("127.0.0.1", "localhost", "::1"):
                        raise ValueError("local_path references require a loopback ComfyUI server")
                    if sha_file(path) != sha:
                        raise ValueError("Local input hash mismatch: " + path)
                    v = os.path.abspath(path)
                    inputs[-1]["transport"] = "local_path"
                elif transport == "upload":
                    v = self.server.upload(path, sha)
                    inputs[-1]["server_name"] = v
                else:
                    raise ValueError("Unknown reference transport: " + transport)
            values[k] = v
        # Hy3DInPaint writes <temp>/<name>.obj without creating subfolders: a flat name.
        mesh_name = "factory_%s_%s_%s_painted" % (job["family"], job["asset"], slug(job["id"]))
        for nid, node in wf.items():
            ins = node["inputs"]
            if node["class_type"] in SAVE_CLASSES and "filename_prefix" in ins:
                ins["filename_prefix"] = "%s_n%s" % (prefix, nid)
            if node["class_type"] == "Hy3DInPaint":
                ins["output_mesh_name"] = mesh_name

        def walk(v):
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            if isinstance(v, str) and v in values:
                return values[v]
            return v
        wf = walk(wf)
        for nid, ref in job.get("images", {}).items():
            path, sha = self.resolve(ref)
            inputs.append(self.input_record(ref))
            wf[nid]["inputs"]["image"] = self.server.upload(path, sha)
            inputs[-1]["server_name"] = wf[nid]["inputs"]["image"]
        sets = dict(job.get("set", {}))
        if tries >= 1:
            sets.update(job.get("set_on_failure", {}))
        for key, v in sets.items():
            nid, name = key.split(".", 1)
            if nid not in wf:
                raise ValueError("%s: %s lands on node %s, which the workflow (after drop) does not have"
                                 % (job["workflow"], key, nid))
            wf[nid]["inputs"][name] = v
        left = [s for s in json.dumps(wf).split('"') if s.startswith("$") and s.endswith("$") and len(s) > 2]
        if left:
            raise ValueError("unfilled placeholders %s" % sorted(set(left)))
        self.last_build = {"sets": sets, "inputs": inputs}
        return wf, mesh_name

    def submit(self, job):
        sp = self.spec(job)
        prev = self.entry(job["id"])
        tries = prev.get("tries", 0) if prev.get("spec") == sp else 0
        try:
            wf, mesh_name = self.build(job, tries)
            if job.get("heavy"):
                self.server.free()
            pid = self.server.submit(wf)
        except (ValueError, KeyError) as ex:
            self.log("REFUSED %s: %s" % (job["id"], ex))
            self.manifest[job["id"]] = {"status": "failed", "spec": sp, "error": str(ex)[:2000], "tries": tries + 1}
            self.save_manifest()
            return
        prov = comfy_record(job, self.workflow(job["workflow"])[1], wf, self.last_build["sets"],
                            self.last_build["inputs"])
        # What was requested, node by node; the effective values are read back from the
        # server's history when the prompt finishes (poll).
        prov["parameters"] = requested_parameters(wf)
        self.manifest[job["id"]] = {"status": "queued", "spec": sp, "prompt_id": pid, "submitted": time.time(),
                                    "tries": tries + 1, "mesh_name": mesh_name if job.get("mesh_role") else None,
                                    "provenance": prov}
        self.save_manifest()
        self.log("submitted %s (%s) as %s" % (job["id"], job["group"], pid))

    def poll(self, job, queued_ids):
        e = self.entry(job["id"])
        pid = e["prompt_id"]
        h = self.server.history(pid)
        if h is None:
            if pid in queued_ids:
                return
            # Neither queued nor in the history: the server lost it (restart). Submit again.
            if time.time() - e.get("submitted", 0) > self.lost_after:
                self.log("lost by the server, resubmitting: %s" % job["id"])
                e["status"] = "lost"
                self.submit(job)
            return
        st = h.get("status", {})
        if not (st.get("completed") or st.get("status_str") in ("success", "error")):
            return
        t0 = t1 = None
        err = None
        for kind, data in st.get("messages", []):
            if kind == "execution_start":
                t0 = data.get("timestamp")
            if kind in ("execution_success", "execution_error", "execution_interrupted"):
                t1 = data.get("timestamp")
            if kind == "execution_error":
                err = "%s (%s): %s" % (data.get("node_id"), data.get("node_type"), data.get("exception_message"))
            if kind == "execution_interrupted":
                err = "interrupted"
        exec_s = round((t1 - t0) / 1000.0, 1) if t0 and t1 else None
        wall = round(time.time() - e.get("submitted", time.time()), 1)
        if st.get("status_str") != "success":
            tries = e.get("tries", 1)
            self.log("FAILED %s (try %d): %s" % (job["id"], tries, err))
            e.update({"status": "failed" if tries >= MAX_TRIES else "retry", "error": err,
                      "exec_seconds": exec_s})
            self.save_manifest()
            self.record(job, {"status": "failed"}, exec_s, wall)
            return
        files = self.collect_outputs(job, e, h)
        missing = sorted(r for r in self.needed_roles(job) if r not in files)
        if missing:
            # A finished prompt without an output that a later job (or the job's own mesh
            # download) needs counts as a failure: tried once more, then failed, so that only
            # its own dependants wait.
            tries = e.get("tries", 1)
            err = "finished without the output(s) %s" % ", ".join(missing)
            self.log("FAILED %s (try %d): %s" % (job["id"], tries, err))
            e.update({"status": "failed" if tries >= MAX_TRIES else "retry", "error": err,
                      "exec_seconds": exec_s, "files": files})
            self.save_manifest()
            self.record(job, {"status": "failed"}, exec_s, wall)
            return
        rec = {"status": "done", "spec": e["spec"], "prompt_id": pid, "files": files, "exec_seconds": exec_s,
               "wall_seconds": wall, "tries": e.get("tries", 1),
               "finished": time.strftime("%Y-%m-%d %H:%M:%S")}
        if "provenance" in e:
            prov = dict(e["provenance"])
            if "parameters" in prov:
                prov["parameters"] = effective_parameters(prov["parameters"], executed_prompt(h),
                                                          OVERRIDDEN.get(job["workflow"], {}))
            rec["provenance"] = prov
        self.manifest[job["id"]] = rec
        self.save_manifest()
        self.record(job, rec, exec_s, wall)
        self.log("done %s in %s s (%d files)" % (job["id"], exec_s, len(files)))

    def collect_outputs(self, job, e, h):
        d = self.asset_dir(job)
        files = {}
        roles = job.get("roles", {})
        for nid, out in (h.get("outputs") or {}).items():
            items = []
            for key, lst in out.items():
                for it in lst or []:
                    if isinstance(it, dict) and "filename" in it and it.get("type") != "temp":
                        items.append(it)
            for n, it in enumerate(items):
                role = roles.get(nid, "n" + nid) + ("" if n == 0 else "_%d" % n)
                ext = os.path.splitext(it["filename"])[1]
                dest = os.path.join(d, "%s_%s%s" % (slug(job["id"]), role, ext))
                try:
                    self.server.download(it["filename"], it.get("subfolder", ""), it.get("type", "output"), dest)
                except urllib.error.HTTPError as ex:
                    self.log("output %s of %s not downloaded: %s" % (it["filename"], job["id"], ex))
                    continue
                files[role] = [os.path.relpath(dest, self.out), sha_file(dest)]
        if job.get("mesh_role") and e.get("mesh_name"):
            sub, base = e["mesh_name"].rsplit("/", 1) if "/" in e["mesh_name"] else ("", e["mesh_name"])
            dest = os.path.join(d, "%s_%s.glb" % (slug(job["id"]), job["mesh_role"]))
            try:
                self.server.download(base + ".glb", sub, "output", dest)
                files[job["mesh_role"]] = [os.path.relpath(dest, self.out), sha_file(dest)]
            except Exception as ex:
                self.log("painted mesh of %s not downloaded: %s" % (job["id"], ex))
        missing = [r for r in roles.values() if r not in files and r not in self.needed_roles(job)]
        if missing:
            self.log("WARNING %s finished without outputs %s (no later job uses them)" % (job["id"], missing))
        return files

    def record(self, job, rec, exec_s, wall=None, wait=None):
        """One line of timings.csv; wait_seconds: how long a local GPU job waited for the
        card (the ComfyUI queue, /free, other processes) before it ran."""
        path = os.path.join(self.out, "timings.csv")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write("finished;job;family;asset;group;status;exec_seconds;wall_seconds;wait_seconds\n")
            f.write("%s;%s;%s;%s;%s;%s;%s;%s;%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), job["id"], job["family"],
                                                      job["asset"], job["group"], rec.get("status"),
                                                      "" if exec_s is None else exec_s, "" if wall is None else wall,
                                                      "" if wait is None else wait))
