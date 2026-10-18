"""The factory: loads the order and the workflows, works out the state of every job from
the manifest, runs local steps, keeps the ComfyUI queue filled and writes its progress."""

import hashlib
import json
import os
import time
import traceback
import urllib.error

from .comfy_jobs import ComfyJobs
from .comfy_server import Server
from .factory_util import Log, env_seconds, pid_alive, sha_file_cached, sha_obj, write_json
from .local_steps import LocalSteps
from .order import load_order
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

# Estimated server seconds per job group (from earlier measurements on one consumer GPU).
ESTIMATE = {"klein": 13, "qwen_t2i": 8, "qwen_hires": 16, "qwen_inpaint": 30, "qwen_edit": 16,
            "hy3d": 330, "local": 1}
HEAVY_MIN_VRAM_GB = 3.0  # Hunyuan shape + Paint peaked at 7.6 and 8.8 GB; margin for 2048 textures
SAME_GROUP_BURST = 24  # jobs of one model in a row before others' ready jobs get a turn
STOP_FILE = "STOP"
ACTIVE = ("ready", "waiting", "queued")


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


class Factory(ComfyJobs, LocalSteps):
    def __init__(self, a, server_url):
        self.a = a
        self.out = os.path.realpath(a.out)
        os.makedirs(self.out, exist_ok=True)
        self.log = Log(os.path.join(self.out, "factory.log"))
        self.server = Server(server_url)
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

    # --- order and workflows
    def load(self):
        self.job_list = load_order(self.a.order, getattr(self.a, "order_base", "") or None)
        only = set(self.a.only.split(",")) if self.a.only else None
        self.jobs = expand(self.job_list, only)
        self.by_id = {j["id"]: j for j in self.jobs}
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
        if job["kind"] == "pick":
            base["candidates"] = {c: self.entry(c).get("files", {}).get(job["role"], [None, None])[1]
                                  for c in job["from"] if self.done_ok(c)}
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
        deps = refs_of(job)
        st = None
        if job["kind"] == "pick":
            sts = [self.state(self.by_id[d], cache) for d in deps]
            if any(s in ACTIVE for s in sts):
                st = "waiting"
            elif not any(s == "done" for s in sts):
                st = "blocked"
        else:
            for d in deps:
                s = self.state(self.by_id[d], cache)
                if s in ("failed", "blocked"):
                    st = "blocked"
                    break
                if s != "done":
                    st = "waiting"
        if st is None:
            sp = self.spec(job)
            if e.get("status") == "done" and e.get("spec") == sp and self.files_present(e):
                st = "done"
            elif e.get("status") == "failed" and e.get("spec") == sp and not self.a.retry_failed:
                st = "failed"
            else:
                st = "ready"
        cache[jid] = st
        return st

    def files_present(self, e):
        for rel, sha in e.get("files", {}).values():
            if not os.path.exists(os.path.join(self.out, rel)):
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
                if states[j["id"]] == "ready" and j["kind"] != "comfy":
                    try:
                        self.run_local(j)
                    except Exception as ex:
                        self.log("FAILED local %s: %s" % (j["id"], ex))
                        self.manifest[j["id"]] = {"status": "failed", "spec": self.spec(j), "error": str(ex)}
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

    def write_state(self, states, running, pending):
        count = {}
        left = 0.0
        for j in self.jobs:
            s = states[j["id"]]
            count[s] = count.get(s, 0) + 1
            if s in ACTIVE:
                left += ESTIMATE.get(j["group"], 30)
        review = sorted(j["id"] for j in self.jobs if j["kind"] == "pick" and states[j["id"]] == "done"
                        and not self.manual_choice(j))
        write_json(os.path.join(self.out, "state.json"), {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "counts": count,
            "estimated_gpu_hours_left": round(left / 3600, 2),
            "server_queue": {"running": running, "pending": pending},
            "picks_to_review": len(review),
            "failures": sorted(j["id"] for j in self.jobs if states[j["id"]] == "failed")})

    def run(self):
        lock = os.path.join(self.out, "factory.pid")
        if os.path.exists(lock):
            try:
                other = int(open(lock).read().strip() or 0)
            except ValueError:
                other = 0
            if other != os.getpid() and pid_alive(other):
                print("another factory is running (pid %d); stop it first" % other)
                return 2
        with open(lock, "w") as f:
            f.write(str(os.getpid()))
        self.log("factory started (pid %d), %d jobs in the list, limit %d" % (os.getpid(), len(self.jobs), self.a.limit))
        errors = 0
        try:
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
            try:
                if open(lock).read().strip() == str(os.getpid()):
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
            g = groups.setdefault(j["group"], {"total": 0, "left": 0})
            g["total"] += 1
            if s in ACTIVE:
                g["left"] += 1
        tot = 0.0
        fams = {}
        for j in self.jobs:
            n, h = fams.get(j["family"], (0, 0.0))
            fams[j["family"]] = (n + 1, h + ESTIMATE.get(j["group"], 30) / 3600)
        for g, c in sorted(groups.items()):
            h = c["left"] * ESTIMATE.get(g, 30) / 3600
            tot += h
            print("%-13s %5d jobs, %5d left, ~%.2f GPU h" % (g, c["total"], c["left"], h))
        for fam, (n, h) in fams.items():
            print("  %-18s %5d jobs, ~%.2f GPU h in all" % (fam, n, h))
        print("total %d jobs, ~%.1f GPU hours left (estimate)" % (len(self.jobs), tot))
