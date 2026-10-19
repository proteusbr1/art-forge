"""The factory: loads the order and the workflows, works out the state of every job from
the manifest, runs local steps, keeps the ComfyUI queue filled and writes its progress."""

import hashlib
import json
import os
import time
import traceback
import urllib.error

from . import gpu_lock
from .comfy_jobs import MAX_TRIES, ComfyJobs
from .comfy_server import Server
from .config import load_config
from .factory_util import (Log, env_seconds, folder_hashes, pid_alive, sha_file, sha_file_cached, sha_obj, slug,
                           write_json)
from .local_steps import LocalSteps
from .order import load_order
from .provenance import local_gpu_record
from .recipes import expand, refs_of

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(HERE, "workflows")

# Paid API nodes must never be used (they spend the owner's credit and send data out).
FORBIDDEN_MARKERS = ("Api", "Meshy", "Tripo", "Rodin", "Recraft", "Tencent", "Kling", "Luma",
                     "OpenAI", "Gemini", "Bria", "Flux2Pro", "FluxPro", "Ideogram", "Runway",
                     "Vidu", "Pixverse", "Minimax", "ByteDance", "Topaz", "Magnific", "ComfyCloud",
                     "Grok", "Reve", "Wavespeed", "HitPaw", "Quiver", "OpenRouter", "Stability", "Bfl")

# Exact audited native classes; similarly named remote API nodes remain forbidden.
LOCAL_NATIVE_CLASSES = frozenset(("TripoSplatPreprocessImage", "TripoSplatConditioning", "VAEDecodeTripoSplat"))

# Estimated seconds per job group on one consumer GPU (24 GB). Estimates: from earlier
# measurements of the same workflows (trellis2 42 s with its bake, hunyuan_paint 89 s,
# qwen_edit 23 s for a 1024x1536 pose edit), qwen21_base from the Turbo's time per step
# (60 steps at 1024x1536), local_gpu an average of a rig, an export with its bake and a
# sheet. Real nights replace them (timings.csv).
ESTIMATE = {"klein": 13, "qwen_t2i": 8, "qwen_hires": 16, "qwen_inpaint": 30, "qwen_edit": 23,
            "hy3d": 330, "local": 1, "qwen21_base": 90, "trellis2": 45, "hunyuan_paint": 90, "local_gpu": 60}
HEAVY_MIN_VRAM_GB = 3.0  # Hunyuan shape + Paint peaked at 7.6 and 8.8 GB; margin for 2048 textures
SAME_GROUP_BURST = 24  # jobs of one model in a row before others' ready jobs get a turn
STOP_FILE = "STOP"
ACTIVE = ("ready", "waiting", "queued")
# A job "held" (a reserve variant's, until its piece's first decision is "no_variant_passed")
# is not active: it neither runs nor keeps the night going.
HELD = "held"
# Words of an out-of-memory failure in a local tool's error or log.
OOM_MARKERS = ("out of memory", "outofmemoryerror", "cuda_error_out_of_memory", "cudaerrormemoryallocation",
               "failed to allocate", "not enough memory")


def out_of_memory(ex):
    """True when a failure's message or the tail of its log says the card ran out of memory."""
    texts = [str(ex)]
    log = getattr(ex, "log", None)
    if log and os.path.exists(log):
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(log) - 65536))
                texts.append(f.read())
        except OSError:
            pass
    text = "\n".join(texts).lower()
    return any(m in text for m in OOM_MARKERS)


def forbidden_class(cls):
    """True for a node class of a paid or remote API (audited local classes excepted)."""
    if cls.startswith("Hy3D") or cls in LOCAL_NATIVE_CLASSES:
        return False
    return any(m.lower() in cls.lower() for m in FORBIDDEN_MARKERS)


def load_workflow(name):
    """(workflow, SHA-256 of its exact bytes) of a file of the workflows folder; a workflow
    with a paid API node is refused."""
    path = os.path.join(WORKFLOWS, name)
    with open(path, "rb") as f:
        raw = f.read()
    wf = json.loads(raw)
    for nid, node in wf.items():
        cls = node["class_type"]
        if forbidden_class(cls):
            raise ValueError("workflow %s uses a forbidden API node %s (%s)" % (name, nid, cls))
    return wf, hashlib.sha256(raw).hexdigest()


def needed_outputs(jobs):
    """{job id: set of roles}: the outputs of each job that a later job reads (a reference
    {"ref", "role"}, a pick's candidates, the variants of a review or final job). A reference
    to a pick needs the role from every candidate of the pick."""
    by_id = {j["id"]: j for j in jobs}
    out = {}

    def need(jid, role):
        j = by_id.get(jid)
        if j is None:
            return
        if j["kind"] == "pick":
            for c in j["from"]:
                need(c, role)
        else:
            out.setdefault(jid, set()).add(role)

    def walk(v):
        if isinstance(v, dict):
            if "ref" in v:
                need(v["ref"], v.get("role", "rgb"))
            else:
                for x in v.values():
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for j in jobs:
        for key in ("values", "images", "inputs", "input", "rolled", "painted"):
            if key in j:
                walk(j[key])
        if j["kind"] == "pick":
            for c in j["from"]:
                need(c, j["role"])
        for v in j.get("variants", []):
            need(v["job"], v["role"])
    return out


class Factory(ComfyJobs, LocalSteps):
    def __init__(self, a, server_url):
        self.a = a
        self.out = os.path.realpath(a.out)
        os.makedirs(self.out, exist_ok=True)
        self.log = Log(os.path.join(self.out, "factory.log"))
        self.server = Server(server_url)
        self.config = load_config(getattr(a, "config", "") or None, getattr(a, "config_base", "") or None)
        self.manifest_path = os.path.join(self.out, "manifest.json")
        self.manifest = {}
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path, encoding="utf-8") as f:
                self.manifest = json.load(f)
        self.workflows = {}
        self.last_group = None
        self.burst = 0
        self.down_since = None
        self.heavy_wait_since = None
        # Waiting times; the tests shorten them through the environment.
        self.pass_sleep = env_seconds("FORGE_PASS_SLEEP", 4)
        self.error_sleep = env_seconds("FORGE_ERROR_SLEEP", 30)
        self.lost_after = env_seconds("FORGE_LOST_AFTER", 20)
        self.heavy_wait = env_seconds("FORGE_HEAVY_WAIT", 300)
        # Local GPU jobs: how long they wait while another process holds more than
        # gpu_busy_mb of VRAM (forge.local.json; FORGE_GPU_WAIT for the tests).
        self.gpu_wait = env_seconds("FORGE_GPU_WAIT", self.config["gpu_wait"])
        self.gpu_busy_mb = self.config["gpu_busy_mb"]
        self.gpu_since = None
        self.gpu_waiting = None
        self.gpu_processes = gpu_lock.gpu_processes
        self.gpu_memory = gpu_lock.gpu_memory
        self.gpu_used_busy_mb = self.config["gpu_used_busy_mb"]
        # The wait of a ready local GPU job (state.json "gpu_wait", the log every
        # wait_log_every seconds, "stalled" after queue_wait_warn seconds).
        self.wait = None
        self.wait_log_every = env_seconds("FORGE_WAIT_LOG_EVERY", 900)
        self.queue_wait_warn = env_seconds("FORGE_QUEUE_WAIT_WARN", self.config["queue_wait_warn"])
        self.running_local = None
        self.spec_errors = {}  # job id -> why its specification cannot be worked out

    # --- order and workflows
    def load(self):
        self.job_list = load_order(self.a.order, getattr(self.a, "order_base", "") or None)
        only = set(self.a.only.split(",")) if self.a.only else None
        self.jobs = expand(self.job_list, only)
        self.by_id = {j["id"]: j for j in self.jobs}
        self.needed = needed_outputs(self.jobs)
        for j in self.jobs:
            for r in refs_of(j):
                if r not in self.by_id:
                    raise ValueError("job %s refers to unknown job %s" % (j["id"], r))
            if j["kind"] == "comfy":
                self.workflow(j["workflow"])

    def workflow(self, name):
        if name not in self.workflows:
            self.workflows[name] = load_workflow(name)
        return self.workflows[name]

    def save_manifest(self):
        write_json(self.manifest_path, self.manifest)

    def asset_dir(self, job):
        return os.path.join(self.out, job["family"], job["asset"])

    # --- references
    def entry(self, jid):
        return self.manifest.get(jid, {})

    def producer(self, ref):
        """Job id whose files a reference uses (a pick resolves to its chosen candidate)."""
        if self.by_id[ref["ref"]]["kind"] == "pick":
            return self.entry(ref["ref"])["chosen"]
        return ref["ref"]

    def resolve(self, ref):
        """(absolute path, sha) of a reference {"ref": job, "role": r} to a finished job, or
        of a file the order supplies {"file": path}."""
        if "file" in ref:
            path = ref["file"]
            if not os.path.isfile(path):
                raise KeyError("input file not found: %s" % path)
            return path, sha_file_cached(path)
        e = self.entry(self.producer(ref))
        files = e.get("files", {})
        role = ref.get("role", "rgb")
        if role not in files:
            raise KeyError("job %s has no output with role %s" % (ref["ref"], role))
        rel, sha = files[role]
        return os.path.join(self.out, rel), sha

    def input_record(self, ref):
        """Provenance of one input file: the factory job that produced it, its file and sha
        (or, for a file the order supplies, its path and sha)."""
        path, sha = self.resolve(ref)
        if "file" in ref:
            return {"file": path.replace(os.sep, "/"), "sha256": sha, "factory_output": False, "order_input": True}
        rec = {"job": self.producer(ref), "role": ref.get("role", "rgb"),
               "file": os.path.relpath(path, self.out).replace(os.sep, "/"), "sha256": sha,
               "factory_output": True}
        if rec["job"] != ref["ref"]:
            rec["pick"] = ref["ref"]
        return rec

    def done_ok(self, jid):
        e = self.entry(jid)
        return e.get("status") == "done"

    # --- specifications
    def spec(self, job):
        """Full specification of a job whose inputs are all finished (hash used for idempotency)."""
        def subst(v):
            if isinstance(v, dict) and "ref" in v:
                return {"sha": self.resolve(v)[1]}
            if isinstance(v, dict) and "folder" in v:
                # A folder the order supplies: the sorted relative paths and contents of its files.
                path = v["folder"]
                files = folder_hashes(path) if os.path.isdir(path) else "missing"
                return {"folder_files": files, **{k: subst(x) for k, x in v.items() if k != "folder"}}
            if isinstance(v, dict) and "file" in v:
                # The content of a file the order supplies, not its path: moving the order
                # redoes nothing, changing the file redoes the jobs that use it.
                path = v["file"]
                sha = sha_file_cached(path) if os.path.isfile(path) else "missing"
                return {"file_sha": sha, **{k: subst(x) for k, x in v.items() if k != "file"}}
            if isinstance(v, dict):
                return {k: subst(x) for k, x in v.items()}
            if isinstance(v, list):
                return [subst(x) for x in v]
            return v
        base = {k: subst(v) for k, v in job.items() if k not in ("family", "asset", "heavy", "group")}
        if job["kind"] == "comfy":
            base["workflow_sha"] = self.workflow(job["workflow"])[1]
        if job["kind"] == "local_gpu":
            # The local equivalent of the workflow hash: the scripts the runner uses (and the
            # rig checkout's revision), so a fixed tool redoes its jobs.
            from . import local_gpu
            base["tool_sha"] = local_gpu.tool_fingerprint(job, self.config)
            if job.get("tool") == "export":
                # A bone map corrected by hand next to the export ("manual": true) redoes it.
                base["manual_bone_map"] = local_gpu.manual_bone_map(
                    os.path.join(self.asset_dir(job), slug(job["id"])))
        if job["kind"] == "pick":
            base["candidates"] = {c: self.entry(c).get("files", {}).get(job["role"], [None, None])[1]
                                  for c in job["from"] if self.done_ok(c)}
            base["manual"] = self.manual_choice(job)
        if job.get("partial"):
            # A job over the variants that finished (review, final): their files, by hash.
            base["candidates"] = {c: sorted(sha for _, sha in self.entry(c).get("files", {}).values())
                                  for c in job["from"] if self.done_ok(c)}
        if job["kind"] == "final":
            base["manual"] = self.manual_choice(job)
        return sha_obj(base)

    # --- state of a job: done, failed, blocked, waiting, ready, queued
    def state(self, job, cache):
        jid = job["id"]
        if jid in cache:
            return cache[jid]
        e = self.entry(jid)
        if e.get("status") == "queued":
            cache[jid] = "queued"
            return "queued"
        hold = job.get("hold")
        if hold:
            # Held until the job it waits for is done with the outcome it waits for.
            gate = self.by_id.get(hold["until"])
            if gate is None or self.state(gate, cache) != "done" or                     self.entry(hold["until"]).get("outcome") != hold["outcome"]:
                cache[jid] = HELD
                return HELD
        deps = [d for d in refs_of(job) if not hold or d != hold["until"]]
        st = None
        if job["kind"] == "pick" or job.get("partial"):
            # Over candidates: waits while one still runs, goes with those that finished
            # (a decision, "even_if_none", also when every candidate failed).
            sts = [self.state(self.by_id[d], cache) for d in deps]
            if any(s in ACTIVE for s in sts):
                st = "waiting"
            elif any(s == HELD for s in sts):
                st = HELD
            elif not any(s == "done" for s in sts) and not job.get("even_if_none"):
                st = "blocked"
        else:
            for d in deps:
                s = self.state(self.by_id[d], cache)
                if s == HELD:
                    st = HELD
                    break
                if s in ("failed", "blocked"):
                    st = "blocked"
                    break
                if s != "done":
                    st = "waiting"
        if st is None:
            try:
                sp = self.spec(job)
            except (KeyError, ValueError, OSError) as ex:
                # Its inputs cannot be worked out (an output a finished job should have
                # made is missing, an input file vanished): this job fails, only its
                # dependants wait; the rest of the factory goes on.
                msg = "its specification cannot be worked out: %s" % (ex.args[0] if ex.args else ex)
                if self.spec_errors.get(jid) != msg:
                    self.log("FAILED %s: %s" % (jid, msg))
                self.spec_errors[jid] = msg
                cache[jid] = "failed"
                return "failed"
            self.spec_errors.pop(jid, None)
            if e.get("status") == "done" and e.get("spec") == sp and self.files_present(e, job):
                st = "done"
            elif e.get("status") == "failed" and e.get("spec") == sp:
                st = "failed"
            else:
                st = "ready"
        cache[jid] = st
        return st

    def files_present(self, e, job=None):
        # The export's bone_map.json is also an editable control file. Its meaningful
        # manual changes already enter spec(); proposals and order copies are ignored.
        editable_map = (job or {}).get("kind") == "local_gpu" and job.get("tool") == "export"
        for role, (rel, sha) in e.get("files", {}).items():
            path = os.path.join(self.out, rel)
            try:
                if not os.path.isfile(path):
                    return False
                if editable_map and role == "bone_map" and os.path.basename(path) == "bone_map.json":
                    continue
                if sha_file_cached(path) != sha:
                    return False
            except OSError:
                return False
        return True

    def states(self):
        cache = {}
        return {j["id"]: self.state(j, cache) for j in self.jobs}

    # --- scheduling
    def choose(self, ready):
        """Next ComfyUI job: the same model as the last one while a burst lasts (model loads
        are slow), else the first ready job in list order."""
        if not ready:
            return None
        if self.last_group and self.burst < SAME_GROUP_BURST:
            for j in ready[:200]:
                if j["group"] == self.last_group:
                    return j
        return ready[0]

    def vram_ok(self):
        """Free VRAM for a Hunyuan job: ComfyUI's own models are unloaded first (/free); if
        other programs still hold the card, wait (up to heavy_wait seconds in total)."""
        now = time.time()
        if self.heavy_wait_since is None:
            self.heavy_wait_since = now
            self.server.free()
            return False  # measured on the next pass, after the unload
        try:
            dev = self.server.get_json("/system_stats")["devices"][0]
            free_gb = dev.get("vram_free", 0) / 2 ** 30
        except Exception:
            free_gb = 0
        if free_gb >= HEAVY_MIN_VRAM_GB or now - self.heavy_wait_since > self.heavy_wait:
            if free_gb < HEAVY_MIN_VRAM_GB:
                self.log("only %.1f GB of VRAM free after %d s; heavy job goes anyway" % (free_gb, now - self.heavy_wait_since))
            self.heavy_wait_since = None
            return True
        if int(now - self.heavy_wait_since) % 300 < 5:
            self.log("waiting for VRAM for a heavy job (%.1f GB free)" % free_gb)
        return False

    def run_ready_local(self, states):
        """Local steps right away (they are cheap); they may make more jobs ready."""
        ran = True
        while ran:
            ran = False
            for j in self.jobs:
                if states[j["id"]] == "ready" and j["kind"] not in ("comfy", "local_gpu"):
                    try:
                        self.run_local(j)
                    except Exception as ex:
                        self.log("FAILED local %s: %s" % (j["id"], ex))
                        try:
                            sp = self.spec(j)
                        except Exception:
                            sp = None
                        self.manifest[j["id"]] = {"status": "failed", "spec": sp, "error": str(ex)}
                        self.save_manifest()
                    ran = True
            if ran:
                states = self.states()
        return states

    def pass_once(self):
        queued_ids, running, pending = self.server.queue_ids()
        self.down_since = None
        for j in self.jobs:
            if self.entry(j["id"]).get("status") == "queued":
                self.poll(j, queued_ids)
        states = self.run_ready_local(self.states())
        inflight = [j for j in self.jobs if states[j["id"]] == "queued"]
        heavy_inflight = any(j.get("heavy") for j in inflight)
        stopping = os.path.exists(os.path.join(self.out, STOP_FILE))
        ready = [j for j in self.jobs if states[j["id"]] == "ready" and j["kind"] == "comfy"]
        gpu_ready = [j for j in self.jobs if states[j["id"]] == "ready" and j["kind"] == "local_gpu"]
        if gpu_ready and not stopping:
            # A local GPU job has the card to itself: nothing more is sent to ComfyUI until
            # it has run (it waits for the whole queue, /free and the other processes).
            self.local_gpu_turn(gpu_ready[0], inflight, running, pending, queued_ids, states)
            ready = []
        elif self.wait is not None:
            self.log("local GPU job %s no longer waits (after %d s): %s" % (
                self.wait["job"], time.time() - self.wait["since"], "stop requested" if stopping else "not ready"))
            self.wait = None
            self.gpu_since = None
        while not stopping and not heavy_inflight and len(inflight) < self.a.limit and ready:
            j = self.choose(ready)
            if j.get("heavy") and inflight:
                break  # heavy jobs go alone: let the queue drain first
            if j.get("heavy") and not self.vram_ok():
                # Another program holds the VRAM (the Paint stage would run out of memory):
                # light jobs go meanwhile; after heavy_wait the heavy one goes anyway.
                light = [r for r in ready if not r.get("heavy")]
                if not light:
                    break
                j = self.choose(light)
            if j["group"] == self.last_group:
                self.burst += 1
            else:
                self.last_group, self.burst = j["group"], 1
            self.submit(j)
            ready.remove(j)
            if self.entry(j["id"]).get("status") == "queued":
                inflight.append(j)
                if j.get("heavy"):
                    break
        states = self.states()
        self.write_state(states, running, pending)
        active = sum(1 for s in states.values() if s in ACTIVE)
        inflight_n = sum(1 for s in states.values() if s == "queued")
        if stopping and inflight_n == 0:
            return "stopped"
        if active == 0:
            return "finished"
        return None

    # --- local GPU jobs
    def local_gpu_turn(self, job, inflight, running, pending, queued_ids=(), states=None):
        """Run a ready local GPU job when the card is free: no prompt of any client in the
        ComfyUI queue (queue_running and queue_pending), then /free and one pass for the
        server to unload, then the other processes on the card listed from nvidia-smi and
        logged (it waits up to gpu_wait seconds while one holds more than gpu_busy_mb of
        VRAM, or, when the driver reports no amount per process, while the card has more than
        gpu_used_busy_mb in use; then goes). The wait is recorded in state.json ("gpu_wait")
        and logged again every wait_log_every seconds. Returns True when it ran."""
        jid = job["id"]
        if inflight or running or pending:
            why = "%d running, %d pending" % (running, pending)
            own = sorted(self.entry(j["id"]).get("prompt_id") for j in inflight)
            others = sorted(p for p in queued_ids if p not in own)
            self.note_wait(jid, "the ComfyUI queue is not empty (%s)" % why, running, pending,
                           {"factory_prompts": len(inflight), "other_prompts": others[:10]})
            if self.gpu_waiting != (jid, why):
                self.log("local GPU job %s waits for an empty ComfyUI queue (%s)" % (jid, why))
                self.gpu_waiting = (jid, why)
            self.gpu_since = None
            return False
        now = time.time()
        if self.gpu_since is None:
            self.server.free()
            self.gpu_since = now
            procs = self.gpu_processes()
            self.log("local GPU job %s: /free sent; %s" % (jid, gpu_lock.describe_processes(procs)))
            # Measured on the next pass, once the server has unloaded its models.
            self.note_wait(jid, "/free sent; the server unloads its models", 0, 0, {})
            return False
        procs = self.gpu_processes()
        busy = gpu_lock.busy_processes(procs, self.gpu_busy_mb)
        why = None
        if busy:
            why = "busy: " + ", ".join("%s (%d, %d MB)" % (p["name"], p["pid"], p["mb"]) for p in busy)
        elif procs is None or any(p["mb"] is None for p in procs):
            # Windows display drivers report no VRAM per process: the card's total in use.
            mem = self.gpu_memory(self.server)
            if mem and mem["used_mb"] > self.gpu_used_busy_mb:
                why = "busy: the card has %d of %d MB in use (%s; over gpu_used_busy_mb %d)" % (
                    mem["used_mb"], mem["total_mb"], mem["source"], self.gpu_used_busy_mb)
        if why and now - self.gpu_since < self.gpu_wait:
            self.note_wait(jid, why, 0, 0, {})
            if self.gpu_waiting != (jid, why):
                self.log("local GPU job %s waits (up to %d s) while other processes hold the card: %s" % (
                    jid, self.gpu_wait, why))
                self.gpu_waiting = (jid, why)
            return False
        if why:
            self.log("local GPU job %s goes after %d s although other processes hold the card" % (
                jid, now - self.gpu_since))
        # The queue again, right before the job takes the card (another client may have sent
        # a prompt since the start of this pass).
        _, running, pending = self.server.queue_ids()
        if running or pending:
            self.note_wait(jid, "the ComfyUI queue is not empty (%d running, %d pending)" % (running, pending),
                           running, pending, {})
            self.gpu_since = None
            return False
        waited = round(now - self.wait["since"], 1) if self.wait and self.wait["job"] == jid else 0.0
        self.wait = None
        self.gpu_since = None
        self.gpu_waiting = None
        self.running_local = {"job": jid, "tool": job.get("tool"), "since": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "waited_seconds": waited}
        if states is not None:
            # A long job (an export may take two hours) leaves the state saying what runs.
            self.write_state(states, 0, 0)
        try:
            self.run_local_gpu(job, waited)
        finally:
            self.running_local = None
        return True

    def note_wait(self, jid, reason, running, pending, holders):
        """Record why a ready local GPU job waits (state.json "gpu_wait"); the log repeats it
        every wait_log_every seconds with the time waited, "stalled" after queue_wait_warn."""
        now = time.time()
        if self.wait is None or self.wait["job"] != jid:
            self.wait = {"job": jid, "since": now, "logged": now}
        w = self.wait
        w.update({"reason": reason, "running": running, "pending": pending, "holders": holders})
        w["stalled"] = now - w["since"] >= self.queue_wait_warn
        if now - w["logged"] >= self.wait_log_every:
            w["logged"] = now
            self.log("local GPU job %s has waited %d min: %s%s" % (
                jid, (now - w["since"]) / 60, reason,
                "; STALLED (over queue_wait_warn, %d s)" % self.queue_wait_warn if w["stalled"] else ""))

    def wait_state(self):
        """The "gpu_wait" entry of state.json, or None."""
        w = self.wait
        if w is None:
            return None
        return {"job": w["job"], "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(w["since"])),
                "seconds": round(time.time() - w["since"], 1), "reason": w.get("reason"),
                "running": w.get("running"), "pending": w.get("pending"), "holders": w.get("holders") or {},
                "stalled": bool(w.get("stalled"))}

    def run_local_gpu(self, job, waited=0.0):
        """One local GPU job, synchronously: nothing is sent to the server while it runs.
        Its runner is forge.local_gpu.RUNNERS[job["tool"]](factory, job, out_dir), which
        returns {"files": {role: path}, "passed": {argument: value}, "provenance": record}.
        A failure is tried once more (after the queue is empty again); the log names an
        out-of-memory cause and an external prompt that started meanwhile."""
        from . import local_gpu
        jid = job["id"]
        sp = self.spec(job)
        prev = self.entry(jid)
        tries = (prev.get("tries", 0) if prev.get("spec") == sp else 0) + 1
        out_dir = os.path.join(self.asset_dir(job), slug(jid))
        t0 = time.time()
        self.log("local GPU job %s started (try %d, %s)" % (jid, tries, job.get("tool")))
        try:
            runner = local_gpu.RUNNERS.get(job.get("tool"))
            if runner is None:
                raise ValueError("unknown local GPU tool %r (known: %s)" % (
                    job.get("tool"), ", ".join(sorted(local_gpu.RUNNERS)) or "none"))
            with gpu_lock.hold("local GPU job %s" % jid, log=self.log):
                res = runner(self, job, out_dir)
            files = {role: [os.path.relpath(p, self.out), sha_file(p)] for role, p in res["files"].items()}
        except Exception as ex:
            seconds = round(time.time() - t0, 1)
            oom = out_of_memory(ex)
            try:
                _, running, pending = self.server.queue_ids()
            except Exception:
                running = pending = 0
            notes = []
            if oom:
                notes.append("cause: out of memory on the card")
            if running or pending:
                notes.append("an external ComfyUI prompt started meanwhile (%d running, %d pending)" % (
                    running, pending))
            # A failure the tool knows another try would repeat (an invalid bone map) is final.
            status = "failed" if tries >= MAX_TRIES or getattr(ex, "retry", True) is False else "retry"
            self.log("FAILED local GPU job %s (try %d) after %s s: %s%s%s" % (
                jid, tries, seconds, ex, "; " if notes else "", "; ".join(notes)))
            if status == "retry":
                self.log("local GPU job %s will be tried again once the ComfyUI queue is empty" % jid)
            self.manifest[jid] = {"status": status, "spec": sp, "error": str(ex)[:2000], "tries": tries,
                                  "out_of_memory": oom, "exec_seconds": seconds,
                                  "log": getattr(ex, "log", None)}
            self.save_manifest()
            self.record(job, {"status": "failed"}, seconds, wait=waited)
            return
        seconds = round(time.time() - t0, 1)
        rec = {"status": "done", "spec": sp, "kind": "local_gpu", "files": files, "exec_seconds": seconds,
               "tries": tries, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
               "waited_seconds": waited, "provenance": local_gpu_record(job, res, job.get("ignored"))}
        self.manifest[jid] = rec
        self.save_manifest()
        self.record(job, rec, seconds, wait=waited)
        self.log("done local GPU job %s in %s s (%d files)" % (jid, seconds, len(files)))

    def write_state(self, states, running, pending):
        count = {}
        left = held = 0.0
        for j in self.jobs:
            s = states[j["id"]]
            count[s] = count.get(s, 0) + 1
            if s in ACTIVE:
                left += ESTIMATE.get(j["group"], 30)
            elif s == HELD:
                held += ESTIMATE.get(j["group"], 30)
        review = sorted(j["id"] for j in self.jobs if j["kind"] in ("pick", "final") and states[j["id"]] == "done"
                        and not self.manual_choice(j))
        write_json(os.path.join(self.out, "state.json"), {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "counts": count,
            "estimated_gpu_hours_left": round(left / 3600, 2),
            "estimated_gpu_hours_if_needed": round(held / 3600, 2),
            "server_queue": {"running": running, "pending": pending},
            "gpu_wait": self.wait_state(), "local_gpu_running": self.running_local,
            "picks_to_review": len(review),
            "failures": sorted(j["id"] for j in self.jobs if states[j["id"]] == "failed"),
            "specification_errors": dict(sorted(self.spec_errors.items()))})

    def run(self):
        lock = os.path.join(self.out, "factory.pid")
        if os.path.exists(lock):
            try:
                with open(lock) as f:
                    other = int(f.read().strip() or 0)
            except ValueError:
                other = 0
            if other != os.getpid() and pid_alive(other):
                print("another factory is running (pid %d); stop it first" % other)
                return 2
        try:
            gpu = gpu_lock.acquire("art-forge run of %s" % self.out, log=self.log)
        except gpu_lock.GpuBusy as ex:
            print("another art-forge process holds the GPU lock (%s, lock file %s); stop it first" % (
                gpu_lock.describe(ex.info), ex.path))
            return 2
        with open(lock, "w") as f:
            f.write(str(os.getpid()))
        self.log("factory started (pid %d), %d jobs in the list, limit %d" % (os.getpid(), len(self.jobs), self.a.limit))
        errors = 0
        try:
            if self.a.retry_failed:
                # Reopen only failures present at startup, once. Leaving the CLI flag
                # in state() would also reopen each new failure forever.
                retried = False
                for job in self.jobs:
                    entry = self.entry(job["id"])
                    if entry.get("status") == "failed":
                        entry.update(status="retry", tries=0)
                        self.log("retry requested: %s (new attempt cycle)" % job["id"])
                        retried = True
                if retried:
                    self.save_manifest()
            while True:
                try:
                    res = self.pass_once()
                    errors = 0
                except (urllib.error.URLError, ConnectionError, TimeoutError) as ex:
                    if self.down_since is None:
                        self.down_since = time.time()
                        self.log("server not answering (%s); waiting and retrying" % ex)
                    time.sleep(self.error_sleep)
                    continue
                except Exception:
                    # Anything else (a file locked by an antivirus, a bad answer): log, wait, go
                    # on; give up only if it keeps happening.
                    errors += 1
                    self.log("pass failed (%d in a row):\n%s" % (errors, traceback.format_exc()))
                    if errors >= 20:
                        raise
                    time.sleep(self.error_sleep)
                    continue
                if res:
                    self.log("factory %s" % ("stopped on request" if res == "stopped" else "finished: nothing left to do"))
                    if res == "stopped":
                        os.remove(os.path.join(self.out, STOP_FILE))
                    return 0
                if self.a.one_pass:
                    return 0
                time.sleep(self.pass_sleep)
        except Exception:
            self.log("factory crashed:\n" + traceback.format_exc())
            raise
        finally:
            gpu_lock.release(path=gpu)
            try:
                with open(lock) as f:
                    mine = f.read().strip() == str(os.getpid())
                if mine:
                    os.remove(lock)
            except Exception:
                pass

    def estimate(self):
        cache = {}
        groups = {}
        for j in self.jobs:
            try:
                s = self.state(j, cache)
            except Exception:
                s = "waiting"
            g = groups.setdefault(j["group"], {"total": 0, "left": 0, "held": 0})
            g["total"] += 1
            if s in ACTIVE:
                g["left"] += 1
            elif s == HELD:
                g["held"] += 1  # reserve jobs: counted apart, run only if needed
        tot = held_h = 0.0
        held_n = 0
        fams = {}
        for j in self.jobs:
            if cache.get(j["id"]) == HELD:
                continue
            n, h = fams.get(j["family"], (0, 0.0))
            fams[j["family"]] = (n + 1, h + ESTIMATE.get(j["group"], 30) / 3600)
        for g, c in sorted(groups.items()):
            h = c["left"] * ESTIMATE.get(g, 30) / 3600
            tot += h
            line = "%-13s %5d jobs, %5d left, ~%.2f GPU h" % (g, c["total"], c["left"], h)
            if c["held"]:
                hh = c["held"] * ESTIMATE.get(g, 30) / 3600
                held_h += hh
                held_n += c["held"]
                line += ", %d held (if needed, ~%.2f GPU h)" % (c["held"], hh)
            print(line)
        for fam, (n, h) in fams.items():
            print("  %-18s %5d jobs, ~%.2f GPU h in all" % (fam, n, h))
        print("total %d jobs, ~%.1f GPU hours left (estimate)" % (len(self.jobs), tot))
        if held_n:
            print("if needed: %d held reserve jobs, ~%.1f GPU hours more (they run only when a piece's first "
                  "decision is no_variant_passed)" % (held_n, held_h))
